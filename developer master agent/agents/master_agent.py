import os
import glob
import time
from typing import TypedDict, Sequence
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END

# Sub-agents import
from Modifier_code_agent import run_code_agent
from agents.GIT_agent import run_devops_agent
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
        ".claude/skills/**/*.md"  # <--- Yeh line saari skills aur sub-agent .md files ko dynamically load karegi
    ]
    for path_pattern in search_paths:
        for file_path in glob.glob(path_pattern, recursive=True):
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    combined_instructions.append(f.read())
    return "\n\n".join(combined_instructions)

system_context = load_claude_instructions()

def router_node(state: AgentState) -> AgentState:
    messages = state["messages"]
    last_msg = messages[-1].content.lower() if messages else ""
    
    print(f"[Router] Loaded System Context Length: {len(system_context)} chars from .claude/ & config/ folders")
    
    # Priority Intent Routing Logic
    if any(k in last_msg for k in ["add", "modify", "edit", "update", "write", "change", "create"]):
        next_step = "code_modifier"
    elif any(k in last_msg for k in ["git", "commit", "push", "branch", "pr", "pull request"]):
        next_step = "git_ops"
    elif any(k in last_msg for k in ["where", "path", "find", "search", "locate"]):
        next_step = "file_search"
    else:
        next_step = "file_search"
        
    print(f"[Router Decision] Routed to sub-agent: --> {next_step}")
    return {"messages": messages, "next_step": next_step}

def file_search_node(state: AgentState) -> AgentState:
    last_msg = [m.content for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    result = search_file_or_path(last_msg)
    return {"messages": state["messages"] + [AIMessage(content=result)], "next_step": "end"}

def code_modifier_node(state: AgentState) -> AgentState:
    last_msg = [m.content for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    target_file = "demo.txt" if "demo" in last_msg else "demo.txt"
    code_status = run_code_agent(target_file, last_msg)
    response_msg = f"📝 **Code Modifier Agent Executed:**\n{code_status}"
    return {"messages": state["messages"] + [AIMessage(content=response_msg)], "next_step": "end"}

def git_ops_node(state: AgentState) -> AgentState:
    last_msg = [m.content for m in state["messages"] if isinstance(m, HumanMessage)][-1]
    branch_name = "feature/slack-auto-update"
    devops_status = run_devops_agent(branch_name, f"Update based on prompt: {last_msg}")
    response_msg = f"🚀 **DevOps Git Agent Executed:**\n{devops_status}"
    return {"messages": state["messages"] + [AIMessage(content=response_msg)], "next_step": "end"}

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
    print(f"\n[Master Agent - LangGraph] Executing Query: {user_prompt}")
    
    start_time = time.time()
    
    initial_state = {
        "messages": [HumanMessage(content=user_prompt)],
        "next_step": ""
    }
    result = master_graph.invoke(initial_state)
    
    end_time = time.time()
    execution_time = round(end_time - start_time, 2)
    
    final_response = result["messages"][-1].content
    prompt_lower = user_prompt.lower()
    
    # Check flags for stats and flowchart
    ask_for_stats = any(word in prompt_lower for word in ["time", "token", "kitna time", "how long", "speed"])
    ask_for_flow = any(word in prompt_lower for word in ["flow", "chart", "diagram", "kaise kaam", "how it works"])
    
    response_append = ""
    
    if ask_for_stats:
        response_append += f"\n\n📊 **Execution Stats:**\n⏱️ Time Taken: `{execution_time} seconds`\n🪙 Token Usage: `Tracked via system execution`"
        
    if ask_for_flow:
        flowchart_md = "\n\n🗺️ **Agent Workflow Architecture:**\n```mermaid\ngraph TD\n    A[Slack User Message] --> B[Master Router Node]\n    B -->|File/Path Query| C[File Search Agent]\n    B -->|Code Edit/Add| D[Code Modifier Agent]\n    B -->|Git/PR/Push| E[DevOps Git Agent]\n    C --> F[.md Skills Loader]\n    D --> F\n    E --> F\n    F --> G[Final Response to Slack]\n```"
        response_append += flowchart_md

    return final_response + response_append