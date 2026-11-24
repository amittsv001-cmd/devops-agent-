import os
import glob
import time
import logging
from typing import TypedDict, Sequence
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END

# Logging setup for production-grade telemetry (Yahan logger define kiya hai)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Sub-agents import with safe error handling checks
try:
    from Modifier_code_agent import run_code_agent
except ImportError:
    run_code_agent = None
    logger.warning("Modifier_code_agent could not be imported.")

try:
    from agents.GIT_agent import run_devops_agent
except ImportError:
    run_devops_agent = None
    logger.warning("GIT_agent could not be imported.")

from agents.file_search_agent import search_file_or_path

load_dotenv()

class AgentState(TypedDict):
    messages: Sequence[BaseMessage]
    next_step: str

def load_claude_instructions() -> str:
    combined_instructions = []
    search_paths = [
        "config/router_prompt.md",
        ".claude/rules/*.md",
        ".claude/agent-instructions/*.md",
        ".claude/skills/**/*.md"
    ]
    for path_pattern in search_paths:
        for file_path in glob.glob(path_pattern, recursive=True):
            if os.path.exists(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        combined_instructions.append(f.read())
                except Exception as e:
                    logger.error(f"Could not read instruction file {file_path}: {e}")
    return "\n\n".join(combined_instructions)

system_context = load_claude_instructions()

def router_node(state: AgentState) -> AgentState:
    try:
        messages = state["messages"]
        last_msg = messages[-1].content.lower() if messages else ""
        
        # Priority Intent Routing Logic (Git Ops given higher priority for push/commit/pr queries)
        if any(k in last_msg for k in ["git", "commit", "push", "branch", "pr", "pull request"]):
            next_step = "git_ops"
        elif any(k in last_msg for k in ["add", "modify", "edit", "update", "write", "change", "create"]):
            next_step = "code_modifier"
        elif any(k in last_msg for k in ["where", "path", "find", "search", "locate"]):
            next_step = "file_search"
        else:
            next_step = "file_search"
            
        logger.info(f"Router classified intent as: {next_step}")
        return {"messages": messages, "next_step": next_step}
    except Exception as e:
        logger.error(f"Router Error: {e}")
        return {"messages": state["messages"], "next_step": "file_search"}

def file_search_node(state: AgentState) -> AgentState:
    try:
        last_msg = [m.content for m in state["messages"] if isinstance(m, HumanMessage)][-1]
        result = search_file_or_path(last_msg)
        return {"messages": state["messages"] + [AIMessage(content=result)], "next_step": "end"}
    except Exception as e:
        logger.error(f"Error in file search: {e}")
        return {"messages": state["messages"] + [AIMessage(content=f"❌ Error in file search: {str(e)}")], "next_step": "end"}

def code_modifier_node(state: AgentState) -> AgentState:
    try:
        if not run_code_agent:
            return {"messages": state["messages"] + [AIMessage(content="❌ Code Modifier Agent is not available.")], "next_step": "end"}
        
        last_msg = [m.content for m in state["messages"] if isinstance(m, HumanMessage)][-1]
        target_file = "demo.txt" if "demo" in last_msg else "demo.txt"
        code_status = run_code_agent(target_file, last_msg)
        response_msg = f"📝 **Code Modifier Agent Executed:**\n{code_status}"
        return {"messages": state["messages"] + [AIMessage(content=response_msg)], "next_step": "end"}
    except Exception as e:
        logger.error(f"Error during code modification: {e}")
        return {"messages": state["messages"] + [AIMessage(content=f"❌ Error during code modification: {str(e)}")], "next_step": "end"}

def git_ops_node(state: AgentState) -> AgentState:
    try:
        if not run_devops_agent:
            return {"messages": state["messages"] + [AIMessage(content="❌ DevOps Git Agent is not available.")], "next_step": "end"}
            
        last_msg = [m.content for m in state["messages"] if isinstance(m, HumanMessage)][-1]
        branch_name = "feature/slack-auto-update"
        devops_status = run_devops_agent(branch_name, f"Update based on prompt: {last_msg}")
        response_msg = f"🚀 **DevOps Git Agent Executed:**\n{devops_status}"
        return {"messages": state["messages"] + [AIMessage(content=response_msg)], "next_step": "end"}
    except Exception as e:
        logger.error(f"Error during Git operations: {e}")
        return {"messages": state["messages"] + [AIMessage(content=f"❌ Error during Git operations: {str(e)}")], "next_step": "end"}

def route_decision(state: AgentState) -> str:
    return state["next_step"]

workflow = StateGraph(AgentState)

workflow.add_node("router", router_node)
workflow.add_node("file_search", file_search_node)
workflow.add_node("code_modifier", code_modifier_node)
workflow.add_node("git_ops", git_ops_node)

workflow.set_entry_point("router")

workflow.add_conditional_edges(
    "router",
    route_decision,
    {
        "file_search": "file_search",
        "code_modifier": "code_modifier",
        "git_ops": "git_ops"
    }
)

workflow.add_edge("file_search", END)
workflow.add_edge("code_modifier", END)
workflow.add_edge("git_ops", END)

master_graph = workflow.compile()

def execute_master_agent(user_prompt: str) -> str:
    logger.info(f"Executing Query: {user_prompt}")
    
    start_time = time.time()
    prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
    
    initial_state = {
        "messages": [HumanMessage(content=user_prompt)],
        "next_step": ""
    }
    
    try:
        result = master_graph.invoke(initial_state)
        
        if result and "messages" in result:
            for msg in result["messages"]:
                if hasattr(msg, "response_metadata") and msg.response_metadata:
                    usage = msg.response_metadata.get("usage", {})
                    prompt_tokens += usage.get("input_tokens", 0)
                    completion_tokens += usage.get("output_tokens", 0)
                elif hasattr(msg, "usage_metadata") and msg.usage_metadata:
                    usage = msg.usage_metadata
                    prompt_tokens += usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0)
                    completion_tokens += usage.get("output_tokens", 0) or usage.get("completion_tokens", 0)
            
            total_tokens = prompt_tokens + completion_tokens
            
    except Exception as e:
        logger.error(f"Execution Error: {e}")
        result = master_graph.invoke(initial_state)
    
    end_time = time.time()
    execution_time = round(end_time - start_time, 2)
    
    final_response = result["messages"][-1].content if result and "messages" in result else "❌ No response generated."
    prompt_lower = user_prompt.lower()
    
    ask_for_stats = any(word in prompt_lower for word in ["time", "token", "kitna time", "how long", "speed"])
    ask_for_flow = any(word in prompt_lower for word in ["flow", "chart", "diagram", "kaise kaam", "how it works"])
    
    response_append = ""
    
    if ask_for_stats:
        response_append += f"\n\n📊 **Execution Stats:**\n⏱️ Time Taken: `{execution_time} seconds`\n🪙 Token Usage: `Total: {total_tokens} (Prompt: {prompt_tokens}, Completion: {completion_tokens})`"
        
    if ask_for_flow:
        flowchart_md = "\n\n🗺️ **Agent Workflow Architecture:**\n```mermaid\ngraph TD\n    A[Slack User Message] --> B[Master Router Node]\n    B -->|File/Path Query| C[File Search Agent]\n    B -->|Code Edit/Add| D[Code Modifier Agent]\n    B -->|Git/PR/Push| E[DevOps Git Agent]\n    C --> F[.md Skills Loader]\n    D --> F\n    E --> F\n    F --> G[Final Response to Slack]\n```"
        response_append += flowchart_md

    return final_response + response_append