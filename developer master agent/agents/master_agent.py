"""LangGraph master agent: LLM router -> sub-agent, with per-query token/cost/time stats."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Annotated, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from agents.code_modifier_agent import modify_file
from agents.devops_git_agent import GitAgentError, commit_and_open_pr
from agents.file_search_agent import extract_terms, search_files
from agents.llm import get_llm, message_text
from agents.usage import UsageTracker
from agents.workspace import WorkspaceError, load_router_context
from config.settings import get_settings

log = logging.getLogger(__name__)

Intent = Literal["file_search", "code_modifier", "git_ops", "chat"]
INTENTS: tuple[str, ...] = ("file_search", "code_modifier", "git_ops", "chat")


class RouteDecision(BaseModel):
    """Routing decision for a single user request."""

    intent: Intent = Field(description="Which sub-agent should handle the request.")
    target_file: Optional[str] = Field(None, description="Workspace-relative file to edit (code_modifier only).")
    search_terms: list[str] = Field(default_factory=list, description="File names/keywords to look for (file_search).")
    branch_name: Optional[str] = Field(None, description="Short kebab-case branch name (git_ops).")
    commit_message: Optional[str] = Field(None, description="Conventional commit message (git_ops).")


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    route: Optional[RouteDecision]


ROUTER_PROMPT = """You route requests for a DevOps assistant working on a code workspace.
Pick exactly one intent:
- file_search: locate files or paths
- code_modifier: create or change a file's contents
- git_ops: commit, push, branch, or open a pull request
- chat: anything else (questions, explanations)
Fill only the fields relevant to that intent. Never invent file paths the user did not mention or clearly imply."""

CHAT_PROMPT = "You are a concise, helpful DevOps assistant. Answer in Slack-friendly markdown."

# Fallback when the LLM router fails. Word boundaries fix "pr" matching "improve", etc.
# Order matters: "create a PR" must be git_ops, not code_modifier.
_KEYWORD_ROUTES: list[tuple[Intent, str]] = [
    ("git_ops", r"\b(git|commit|push|branch|pr|pull request|merge)\b"),
    ("file_search", r"\b(where|path|find|search|locate)\b"),
    ("code_modifier", r"\b(add|modify|edit|update|write|change|create|fix|refactor)\b"),
]
_FILE_RE = re.compile(r"[\w./\-]+\.[A-Za-z0-9]{1,8}\b")

FLOWCHART = """🗺️ *Agent workflow:*
```
Slack message --> Router (LLM, structured output)
                    |--> File Search Agent
                    |--> Code Modifier Agent (LLM)
                    |--> Git / PR Agent
                    `--> Chat (LLM)
              --> Response + token / cost / time stats
```"""


def _last_user_text(state: AgentState) -> str:
    return next(str(m.content) for m in reversed(state["messages"]) if isinstance(m, HumanMessage))


def keyword_route(text: str) -> RouteDecision:
    """Deterministic fallback router (also handy for tests / offline use)."""
    for intent, pattern in _KEYWORD_ROUTES:
        if re.search(pattern, text, re.IGNORECASE):
            file_match = _FILE_RE.search(text)
            return RouteDecision(
                intent=intent,
                search_terms=extract_terms(text),
                target_file=file_match.group(0) if file_match and intent == "code_modifier" else None,
            )
    return RouteDecision(intent="chat")


# --- nodes ------------------------------------------------------------------
def router_node(state: AgentState, config: RunnableConfig) -> dict:
    text = _last_user_text(state)
    context = load_router_context()
    system = ROUTER_PROMPT + (f"\n\nProject rules:\n{context}" if context else "")
    decision: RouteDecision | None = None
    try:
        decision = get_llm().with_structured_output(RouteDecision).invoke(
            [SystemMessage(content=system), HumanMessage(content=text)], config=config
        )
    except Exception:
        log.exception("LLM router failed; falling back to keyword routing")
    if not isinstance(decision, RouteDecision):
        decision = keyword_route(text)
    log.info("Routed to %s", decision.intent)
    return {"route": decision}


def file_search_node(state: AgentState) -> dict:
    route = state["route"]
    terms = route.search_terms or extract_terms(_last_user_text(state))
    return {"messages": [AIMessage(content=search_files(terms))]}


def code_modifier_node(state: AgentState, config: RunnableConfig) -> dict:
    route = state["route"]
    if not route.target_file:
        hint = "✏️ Which file should I change? e.g. `add a /version route to demo/app.py`"
        return {"messages": [AIMessage(content=hint)]}
    try:
        result = modify_file(route.target_file, _last_user_text(state), config=config)
    except (WorkspaceError, OSError, UnicodeDecodeError) as e:
        result = f"❌ Code modifier: {e}"
    return {"messages": [AIMessage(content=result)]}


def git_ops_node(state: AgentState) -> dict:
    route = state["route"]
    text = _last_user_text(state)
    try:
        result = commit_and_open_pr(
            route.branch_name or "slack-auto-update",
            route.commit_message or f"chore: {text[:60]}",
        )
    except GitAgentError as e:
        result = f"❌ Git agent: {e}"
    return {"messages": [AIMessage(content=result)]}


def chat_node(state: AgentState, config: RunnableConfig) -> dict:
    ai = get_llm().invoke([SystemMessage(content=CHAT_PROMPT), *state["messages"]], config=config)
    return {"messages": [AIMessage(content=message_text(ai))]}


def _build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("router", router_node)
    graph.add_node("file_search", file_search_node)
    graph.add_node("code_modifier", code_modifier_node)
    graph.add_node("git_ops", git_ops_node)
    graph.add_node("chat", chat_node)
    graph.set_entry_point("router")
    graph.add_conditional_edges("router", lambda s: s["route"].intent, {i: i for i in INTENTS})
    for node in INTENTS:
        graph.add_edge(node, END)
    return graph.compile()


_GRAPH = _build_graph()


# --- public API -------------------------------------------------------------
@dataclass
class QueryResult:
    response: str
    intent: str
    wall_seconds: float
    usage: UsageTracker

    def to_slack(self, include_flow: bool = False) -> str:
        parts = [self.response]
        if include_flow:
            parts.append(FLOWCHART)
        parts.append(f"🧭 Handled by: `{self.intent}`\n{self.usage.format_stats(self.wall_seconds)}")
        return "\n\n".join(parts)


def execute_master_agent(user_prompt: str) -> QueryResult:
    """Run one prompt through the graph and return the answer + usage stats."""
    tracker = UsageTracker(default_model=get_settings().llm_model)
    start = time.perf_counter()
    result = _GRAPH.invoke(
        {"messages": [HumanMessage(content=user_prompt)], "route": None},
        config={"callbacks": [tracker], "run_name": "master_agent"},
    )
    elapsed = time.perf_counter() - start
    log.info("Query done in %.2fs, %d tokens", elapsed, tracker.total_tokens)
    return QueryResult(
        response=str(result["messages"][-1].content),
        intent=result["route"].intent,
        wall_seconds=elapsed,
        usage=tracker,
    )
