import subprocess
import os
import time
from github import Github

def load_agent_skill() -> str:
    """Git DevOps Agent ke liye .md skill instructions load karta hai."""
    skill_path = ".claude/skills/security-review/deploy/git agent skills.md"
    if os.path.exists(skill_path):
        with open(skill_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""

def run_devops_agent(branch_name: str, commit_msg: str) -> str:
    """
    DevOps Agent: Local Git branch banakar commit करता hai aur GitHub PR raise karta hai.
    """
    # Skill instructions load karna
    skill_instructions = load_agent_skill()
    if skill_instructions:
        print("[Git DevOps Agent] Loaded instructions from skill .md file successfully.")

    # Unique branch name banana taaki same branch error na aaye
    unique_branch = f"{branch_name}-{int(time.time())}"

    try:
        # Local Git Commands
        subprocess.run(["git", "checkout", "-b", unique_branch], check=True)
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        
        # GitHub Integration
        github_token = os.getenv("GITHUB_TOKEN")
        repo_name = os.getenv("GITHUB_REPO") # e.g. "user/repo"
        
        if github_token and repo_name:
            subprocess.run(["git", "push", "origin", unique_branch], check=True)
            g = Github(github_token)
            repo = g.get_repo(repo_name)
            
            # Note: base="main" ya "master" check kar lena apne repo ke hisab se
            pr = repo.create_pull(
                title=f"Slack Auto PR: {commit_msg}",
                body="Automated PR created via DevOps AI Agent.",
                head=unique_branch,
                base="main"
            )
            return f"PR Created Successfully: {pr.html_url}"
            
        return f"Branch '{unique_branch}' committed locally."
        
    except Exception as e:
        return f"Git operation status: Executed with local changes ({str(e)})"