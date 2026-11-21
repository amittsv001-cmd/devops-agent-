import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from devops_mcp_server import (
    create_github_pull_request,
    read_workspace_file,
    run_command,
    search_workspace_files,
    write_workspace_file,
)
from guardrails import is_safe_command, is_safe_workspace_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

STATS_FILE = Path("stats_history.json")
MAX_TOOL_OUTPUT_CHARS = 1800
MAX_TOOL_STEPS = 4
MAX_HISTORY_MESSAGES = 6
GREETING_WORDS = {"hi", "hii", "hiii", "hello", "hey", "namaste", "yo"}

SYSTEM_INSTRUCTION = (
    "You are a concise, reliable AI DevOps Master Agent. "
    "Execute tools directly when requested without unnecessary questions. "
    "Never suggest or run commands that download external installers or scripts. "
    "Always provide a clear text summary after running tools."
)


def _is_simple_greeting(prompt: str) -> bool:
    cleaned = re.sub(r"[^a-zA-Z\s]", "", prompt or "").strip().lower()
    return cleaned in GREETING_WORDS


def _trim_tool_output(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    half = limit // 2
    omitted = len(raw) - limit
    return f"{raw[:half]}\n... [trimmed {omitted} chars] ...\n{raw[-half:]}"


def _extract_token_usage(response: object) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return 0, 0
    if isinstance(meta, dict):
        return int(meta.get("input_tokens", 0) or 0), int(
            meta.get("output_tokens", 0) or 0
        )
    return int(getattr(meta, "input_tokens", 0) or 0), int(
        getattr(meta, "output_tokens", 0) or 0
    )


def _normalize_message_content(content: object) -> str:
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts).strip()
    if content is None:
        return ""
    return str(content).strip()


def load_last_run_stats() -> dict:
    default_stats = {
        "timestamp": None,
        "prompt": None,
        "model": None,
        "execution_time": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    if not STATS_FILE.exists():
        return default_stats
    try:
        data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("last_run"), dict):
            return {**default_stats, **data["last_run"]}
    except Exception as e:
        logger.error(f"Failed to load {STATS_FILE}: {e}")
    return default_stats


def save_last_run_stats(stats: dict) -> None:
    history: list[dict] = []
    if STATS_FILE.exists():
        try:
            existing = json.loads(STATS_FILE.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("history"), list):
                history = existing["history"]
        except Exception:
            history = []

    history.append(stats)
    payload = {
        "last_run": stats,
        "history": history[-50:],
    }
    try:
        STATS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to save {STATS_FILE}: {e}")


last_run_stats = load_last_run_stats()


@tool
def read_workspace_file_tool(file_path: str) -> str:
    """Read content of a file in the workspace."""
    ok, info = is_safe_workspace_path(file_path)
    if not ok:
        return f"Guardrail Blocked: {info}"
    return _trim_tool_output(read_workspace_file(file_path))


@tool
def write_workspace_file_tool(file_path: str, content: str) -> str:
    """Write or overwrite a file in the workspace."""
    ok, info = is_safe_workspace_path(file_path)
    if not ok:
        return f"Guardrail Blocked: {info}"
    return _trim_tool_output(write_workspace_file(file_path, content))


@tool
def search_workspace_files_tool(query: str, search_dir: str = ".") -> str:
    """Search workspace files by name or pattern."""
    ok, info = is_safe_workspace_path(search_dir)
    if not ok:
        return f"Guardrail Blocked: {info}"
    return _trim_tool_output(search_workspace_files(query, search_dir))


@tool
def run_command_tool(command: str) -> str:
    """Execute a safe shell or git command."""
    ok, info = is_safe_command(command)
    if not ok:
        return f"Guardrail Blocked: {info}"
    return _trim_tool_output(run_command(command))


@tool
def create_github_pull_request_tool(
    repo_name: str,
    branch_name: str,
    commit_message: str,
    pr_title: str,
    pr_body: str = "",
) -> str:
    """Commit branch changes and open a GitHub Pull Request."""
    resolved_repo = (repo_name or os.getenv("REPO_NAME") or "").strip()
    result = create_github_pull_request(
        resolved_repo, branch_name, commit_message, pr_title, pr_body
    )
    return _trim_tool_output(result)


tools = [
    read_workspace_file_tool,
    write_workspace_file_tool,
    search_workspace_files_tool,
    run_command_tool,
    create_github_pull_request_tool,
]
tool_map = {t.name: t for t in tools}

groq_api_key = os.getenv("GROQ_API_KEY")
if not groq_api_key:
    raise ValueError("Missing GROQ_API_KEY in environment.")

primary_model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
fallback_raw = os.getenv("GROQ_FALLBACK_MODELS", "openai/gpt-oss-20b")

model_candidates: list[str] = []
for candidate in [primary_model] + [
    m.strip() for m in fallback_raw.split(",") if m.strip()
]:
    if candidate and candidate not in model_candidates:
        model_candidates.append(candidate)


def _clean_messages_for_fallback(
    chat_messages: list[BaseMessage],
) -> list[BaseMessage]:
    cleaned: list[BaseMessage] = []
    for msg in chat_messages:
        if isinstance(msg, (SystemMessage, HumanMessage)):
            cleaned.append(msg)
        elif (
            isinstance(msg, AIMessage)
            and _normalize_message_content(msg.content)
            and not getattr(msg, "tool_calls", None)
        ):
            cleaned.append(msg)

    if not any(isinstance(m, HumanMessage) for m in cleaned):
        for msg in reversed(chat_messages):
            if isinstance(msg, HumanMessage):
                cleaned.append(msg)
                break
    return cleaned


def _invoke_with_fallback(chat_messages: list[BaseMessage]) -> tuple[object, str]:
    last_err: Exception | None = None
    for idx, model_name in enumerate(model_candidates):
        msgs_to_send = (
            chat_messages if idx == 0 else _clean_messages_for_fallback(chat_messages)
        )
        for retry in range(2):
            try:
                logger.info(
                    f"Calling Groq model '{model_name}' (attempt {idx + 1}, retry {retry})"
                )
                llm = ChatGroq(
                    model=model_name,
                    api_key=groq_api_key,
                    temperature=0,
                    max_retries=0,
                ).bind_tools(tools)
                response = llm.invoke(msgs_to_send)
                return response, model_name
            except Exception as e:
                last_err = e
                logger.warning(f"Model '{model_name}' failed: {e}")
                if "429" in str(e) and retry == 0:
                    time.sleep(2.0)
                    continue
                break

    raise RuntimeError(f"All models failed: {last_err}")


def _is_last_work_stats_query(prompt: str) -> bool:
    text = (prompt or "").lower().strip()
    has_past = bool(re.search(r"\b(last|pichle|pichla|previous|prev)\b", text))
    has_metric = bool(
        re.search(r"\b(token|tokens|time|kitna|kitne|work|task|model)\b", text)
    )
    return has_past and has_metric


def _format_last_run_stats() -> str:
    if not last_run_stats.get("prompt"):
        return "No previous task recorded yet."
    return (
        f"**Last Run Stats**\n"
        f"- **Prompt:** `{last_run_stats['prompt']}`\n"
        f"- **Model:** `{last_run_stats.get('model') or 'N/A'}`\n"
        f"- **Time:** `{last_run_stats['execution_time']}s`\n"
        f"- **Tokens:** `{last_run_stats['total_tokens']}` "
        f"(In: `{last_run_stats['prompt_tokens']}`, Out: `{last_run_stats['completion_tokens']}`)"
    )


class AgentState(TypedDict):
    messages: Sequence[BaseMessage]
    prompt_tokens: int
    completion_tokens: int
    active_model: str
    tool_steps: int


def agent_node(state: AgentState) -> AgentState:
    messages = list(state["messages"])
    if len(messages) > MAX_HISTORY_MESSAGES:
        messages = [messages[0]] + messages[-(MAX_HISTORY_MESSAGES - 1) :]

    chat_messages = [SystemMessage(content=SYSTEM_INSTRUCTION)] + messages

    try:
        response, used_model = _invoke_with_fallback(chat_messages)
    except Exception as e:
        err_str = str(e)
        logger.error(f"Agent execution failed across models: {e}")
        content = (
            "Rate limit (429) reached. Please wait briefly and retry with a smaller prompt."
            if "429" in err_str
            else f"Model error: {err_str}"
        )
        return {
            "messages": list(state["messages"]) + [AIMessage(content=content)],
            "prompt_tokens": state.get("prompt_tokens", 0),
            "completion_tokens": state.get("completion_tokens", 0),
            "active_model": state.get("active_model", primary_model),
            "tool_steps": state.get("tool_steps", 0),
        }

    p_tokens, c_tokens = _extract_token_usage(response)
    logger.info(f"Model '{used_model}' usage -> In: {p_tokens}, Out: {c_tokens}")

    return {
        "messages": list(state["messages"]) + [response],
        "prompt_tokens": state.get("prompt_tokens", 0) + p_tokens,
        "completion_tokens": state.get("completion_tokens", 0) + c_tokens,
        "active_model": used_model,
        "tool_steps": state.get("tool_steps", 0),
    }


def custom_tool_node(state: AgentState) -> AgentState:
    messages = list(state["messages"])
    last_message = messages[-1]
    steps_used = state.get("tool_steps", 0)

    tool_calls = getattr(last_message, "tool_calls", None) or []
    for idx, tc in enumerate(tool_calls):
        if steps_used >= MAX_TOOL_STEPS:
            messages.append(
                ToolMessage(
                    content="Tool step limit reached. Summarize current progress.",
                    tool_call_id=tc.get("id", f"call_limit_{idx}"),
                )
            )
            continue

        steps_used += 1
        tool_name = tc["name"]
        tool_args = tc.get("args", {})
        logger.info(f"Executing tool ({steps_used}/{MAX_TOOL_STEPS}): {tool_name}")

        target_tool = tool_map.get(tool_name)
        if target_tool is None:
            tool_result = f"Tool '{tool_name}' not found."
        else:
            try:
                tool_result = target_tool.invoke(tool_args)
            except Exception as e:
                tool_result = f"Tool execution error: {e}"

        messages.append(
            ToolMessage(
                content=_trim_tool_output(str(tool_result)),
                tool_call_id=tc.get("id", f"call_{steps_used}"),
            )
        )

    return {
        "messages": messages,
        "tool_steps": steps_used,
    }


def should_continue(state: AgentState) -> str:
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None)
    if tool_calls and state.get("tool_steps", 0) < MAX_TOOL_STEPS:
        return "tools"
    return "end"


workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", custom_tool_node)
workflow.set_entry_point("agent")
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "tools": "tools",
        "end": END,
    },
)
workflow.add_edge("tools", "agent")

memory_checkpointer = MemorySaver()
master_graph = workflow.compile(checkpointer=memory_checkpointer)


def execute_master_agent(user_prompt: str, thread_id: str = "default_session") -> str:
    global last_run_stats

    if _is_simple_greeting(user_prompt):
        return (
            "Hello! Ready for your DevOps or Git task.\n\n"
            "---\n"
            "**Model:** `fast-local` | **Time:** 0.0s | **Tokens:** 0 (In: 0, Out: 0)"
        )

    if _is_last_work_stats_query(user_prompt):
        return _format_last_run_stats()

    start_time = time.time()
    input_state = {
        "messages": [HumanMessage(content=user_prompt)],
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "active_model": primary_model,
        "tool_steps": 0,
    }
    config = {"configurable": {"thread_id": thread_id}}

    try:
        result = master_graph.invoke(input_state, config=config)
    except Exception as e:
        logger.error(f"Graph invocation failed: {e}")
        return f"Execution Error: {e}"

    execution_time = round(time.time() - start_time, 2)
    total_prompt_tokens = int(result.get("prompt_tokens", 0) or 0)
    total_completion_tokens = int(result.get("completion_tokens", 0) or 0)
    total_tokens = total_prompt_tokens + total_completion_tokens
    used_model = result.get("active_model", primary_model)

    last_run_stats = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": user_prompt,
        "model": used_model,
        "execution_time": execution_time,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_tokens,
    }
    save_last_run_stats(last_run_stats)

    messages_list = result.get("messages", [])
    last_msg = messages_list[-1] if messages_list else None
    final_response = _normalize_message_content(getattr(last_msg, "content", ""))

    if not final_response:
        recent_tool_msgs = [
            _normalize_message_content(getattr(m, "content", ""))
            for m in reversed(messages_list)
            if isinstance(m, ToolMessage)
        ]
        recent_tool_msgs = [t for t in recent_tool_msgs if t][:2]
        if recent_tool_msgs:
            final_response = (
                f"Executed {result.get('tool_steps', 0)} tool step(s).\n\n"
                + "\n---\n".join(reversed(recent_tool_msgs))
            )
        else:
            final_response = (
                f"Completed {result.get('tool_steps', 0)} tool step(s) with no text summary."
            )

    return (
        f"{final_response}\n\n"
        f"---\n"
        f"**Model:** `{used_model}` | "
        f"**Time:** {execution_time}s | "
        f"**Tokens:** {total_tokens} (In: {total_prompt_tokens}, Out: {total_completion_tokens})"
    )