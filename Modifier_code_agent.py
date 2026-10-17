import os

def load_agent_skill() -> str:
    """Code Modifier Agent ke liye .md skill instructions load karta hai."""
    skill_path = ".claude/skills/security-review/deploy/modifier agent skills.md"
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def run_code_agent(file_path: str, user_instruction: str) -> str:
    """
    Code Agent: Specified file ko read karta hai aur instructions ke according update karta hai.
    """
    # Skill instructions load karna
    skill_instructions = load_agent_skill()
    if skill_instructions:
        print("[Code Modifier Agent] Loaded instructions from skill .md file successfully.")

    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"# File created for: {user_instruction}\n")
    
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(f"\n# Change applied by Code Agent: {user_instruction}")
    
    return f"File '{file_path}' successfully modified."