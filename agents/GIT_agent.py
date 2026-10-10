import os
import time
from github import Github, GithubException

def load_agent_skill() -> str:
    """Git DevOps Agent ke liye .md skill instructions load karta hai."""
    skill_path = ".claude/skills/security-review/deploy/git agent skills.md"
    if os.path.exists(skill_path):
        try:
            with open(skill_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            print(f"[Git Agent] Skill load error: {e}")
    return ""

def run_devops_agent(branch_name: str, commit_msg: str) -> str:
    """
    DevOps Agent: Workspace ki saari relevant files ko dynamically scan karke 
    PyGithub ke through GitHub par push karta hai aur PR raise karta hai.
    """
    skill_instructions = load_agent_skill()
    if skill_instructions:
        print("[Git DevOps Agent] Loaded instructions from skill .md file successfully.")

    unique_branch = f"{branch_name}-{int(time.time())}"

    try:
        github_token = os.getenv("GITHUB_TOKEN")
        repo_name = os.getenv("GITHUB_REPO") or os.getenv("REPO_NAME")
        
        if not github_token or not repo_name:
            return "❌ GitHub Token or Repository name is missing in configuration."

        g = Github(github_token)
        repo = g.get_repo(repo_name.strip())

        # Base branch detect karna
        base_branch_name = "main"
        try:
            base_branch = repo.get_branch(base_branch_name)
        except:
            base_branch_name = "master"
            base_branch = repo.get_branch(base_branch_name)

        # Nayi branch create karna
        repo.create_git_ref(ref=f"refs/heads/{unique_branch}", sha=base_branch.commit.sha)

        # Workspace ki saari python, text, aur config files ko automatically find karna
        ignored_dirs = {".git", "__pycache__", ".venv", "venv", ".idea", ".vscode", "downloads"}
        uploaded_files_count = 0

        for root, dirs, files in os.walk("."):
            # Ignore unnecessary system folders
            dirs[:] = [d for d in dirs if d not in ignored_dirs]
            
            for file in files:
                if file.endswith((".py", ".md", ".txt", ".env.example", ".json")):
                    local_path = os.path.join(root, file)
                    # Normalize path for GitHub (replace backslashes for Windows compatibility)
                    repo_file_path = local_path.replace("./", "").replace("\\", "/")
                    if repo_file_path.startswith("/"):
                        repo_file_path = repo_file_path[1:]

                    try:
                        with open(local_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                    except Exception as read_err:
                        print(f"Skipping file {local_path} due to read error: {read_err}")
                        continue

                    try:
                        # Check agar file pehle se hai toh update karo, nahi toh create karo
                        contents = repo.get_contents(repo_file_path, ref=unique_branch)
                        repo.update_file(
                            path=repo_file_path,
                            message=f"Update {repo_file_path}: {commit_msg}",
                            content=content,
                            sha=contents.sha,
                            branch=unique_branch
                        )
                        uploaded_files_count += 1
                    except:
                        repo.create_file(
                            path=repo_file_path,
                            message=f"Create {repo_file_path}: {commit_msg}",
                            content=content,
                            branch=unique_branch
                        )
                        uploaded_files_count += 1

        if uploaded_files_count == 0:
            return "⚠️ No valid source files found to push."

        # Pull Request create karna
        pr = repo.create_pull(
            title=f"Slack Auto PR: {commit_msg}",
            body=f"🤖 Automated PR created securely via PyGithub DevOps AI Agent. Total files updated: {uploaded_files_count}",
            head=unique_branch,
            base=base_branch_name
        )
        return f"✅ PR Created Successfully! Total files synced: {uploaded_files_count}\n🔗 Link: {pr.html_url}"
            
    except GithubException as g_err:
        error_msg = g_err.data.get('message', str(g_err)) if hasattr(g_err, 'data') else str(g_err)
        return f"❌ GitHub API Error: {error_msg}"
    except Exception as e:
        return f"❌ Unexpected Error: {str(e)}"