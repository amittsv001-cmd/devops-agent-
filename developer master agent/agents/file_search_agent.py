import os
import re

def load_agent_skill() -> str:
    """File Search Agent ke liye .md skill instructions load karta hai."""
    skill_path = ".claude/skills/security-review/deploy/file search agent.md"
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def search_file_or_path(query: str) -> str:
    """
    User query se filenames/keywords extract karta hai aur workspace traverse karke matching files ka absolute path dhoondhta hai.
    """
    skill_instructions = load_agent_skill()
    if skill_instructions:
        print("[File Search Agent] Loaded instructions from skill .md file successfully.")

    root_dir = os.getcwd()
    ignore_dirs = {".git", "venv", ".venv", "node_modules", "__pycache__", ".claude"}
    
    clean_query = query.lower()
    words = re.findall(r'\b\w+(?:\.\w+)?\b', clean_query)
    
    stop_words = {"where", "is", "the", "path", "of", "file", "find", "search", "what", "add", "new", "feature", "log", "to", "in", "a", "for"}
    search_keywords = [w for w in words if w not in stop_words]
    
    matched_paths = set()

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for file in files:
            file_lower = file.lower()
            if any(keyword in file_lower for keyword in search_keywords):
                full_path = os.path.join(root, file)
                # Absolute path generate karna taaki poora path dikhe
                abs_path = os.path.abspath(full_path)
                matched_paths.add(abs_path)

    # Fallback search agar keywords match na hon
    if not matched_paths:
        for root, dirs, files in os.walk(root_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for file in files:
                for word in words:
                    if len(word) > 2 and word in file.lower():
                        full_path = os.path.join(root, file)
                        abs_path = os.path.abspath(full_path)
                        matched_paths.add(abs_path)

    if matched_paths:
        paths_list = "\n".join([f"• `{p}`" for p in sorted(matched_paths)])
        return f"🔍 **Found matching file paths:**\n{paths_list}"
    else:
        return f"❌ No files matching keywords `{search_keywords}` were found in the workspace."