import os
import re

def load_agent_skill() -> str:
    skill_path = ".claude/skills/security-review/deploy/file search agent.md"
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def search_file_or_path(query: str) -> str:
    skill_instructions = load_agent_skill()
    if skill_instructions:
        print("[File Search Agent] Loaded instructions from skill .md file successfully.")

    root_dir = os.getcwd()
    ignore_dirs = {".git", "venv", ".venv", "node_modules", "__pycache__", ".claude", ".vscode"}
    
    clean_query = query.lower()
    
    # Agar user ne specific filename maangi hai (jaise demo.txt ya demo file)
    target_filename = None
    if "demo" in clean_query:
        target_filename = "demo.txt"

    matched_path = None

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for file in files:
            # Agar specific target filename mil jaye toh turant wahi single path return karo
            if target_filename and file.lower() == target_filename.lower():
                matched_path = os.path.abspath(os.path.join(root, file))
                break
        if matched_path:
            break

    # Fallback agar specific target na ho toh general keyword match karein
    if not matched_path:
        words = re.findall(r'\b\w+(?:\.\w+)?\b', clean_query)
        stop_words = {"where", "is", "the", "path", "of", "file", "find", "search", "what", "add", "new", "feature", "log", "to", "in", "a", "for"}
        search_keywords = [w for w in words if w not in stop_words]
        
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for file in files:
                if any(kw in file.lower() for kw in search_keywords):
                    matched_path = os.path.abspath(os.path.join(root, file))
                    break
            if matched_path:
                break

    if matched_path:
        return f"🔍 **Found File Path:**\n`{matched_path}`"
    else:
        return f"❌ No matching file found in the workspace."