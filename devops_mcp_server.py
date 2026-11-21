import os
import shlex
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from github import Github
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("VSCode-DevOps-Server")


def _get_git_executable() -> str | None:
    """Locate Git for Windows git.exe on the system."""
    candidates = [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
        os.path.expandvars(r"%LocalAppData%\Programs\Git\cmd\git.exe"),
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path

    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or "GitHubDesktop" in entry:
            continue
        exe = Path(entry) / "git.exe"
        if exe.is_file():
            return str(exe)

    return None


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "Never"
    return env


def _parse_command_args(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.split()


def _run_git(git_exe: str, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [git_exe] + args,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _redact_secret(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, "***")


@mcp.tool()
def read_workspace_file(file_path: str) -> str:
    """Read content of a file in the workspace."""
    try:
        path = Path(file_path)
        if not path.exists():
            return f"Error: File '{file_path}' not found."
        return path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {str(e)}"


@mcp.tool()
def write_workspace_file(file_path: str, content: str) -> str:
    """Write or overwrite a file in the workspace."""
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Successfully updated {file_path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"


@mcp.tool()
def search_workspace_files(query: str, search_dir: str = ".") -> str:
    """Search workspace files matching a name or pattern."""
    try:
        root_dir = Path(search_dir)
        matched_files = [
            str(path)
            for path in root_dir.rglob(f"*{query}*")
            if "__pycache__" not in path.parts and ".git" not in path.parts
        ]
        if not matched_files:
            return f"No files found matching query: '{query}'"
        return f"Found {len(matched_files)} matching files:\n" + "\n".join(matched_files[:20])
    except Exception as e:
        return f"Error searching files: {str(e)}"


@mcp.tool()
def run_command(command: str) -> str:
    """Execute a terminal or git command safely with quoted arguments preserved."""
    token = os.getenv("GITHUB_TOKEN")
    try:
        cmd = (command or "").strip()
        if not cmd:
            return "Error: Empty command provided."

        env = _git_env()
        if cmd == "git" or cmd.startswith("git "):
            git_exe = _get_git_executable()
            if not git_exe:
                return (
                    "Error: Git for Windows (`git.exe`) not found. "
                    "Install Git for Windows from https://git-scm.com/downloads."
                )
            parts = _parse_command_args(cmd)
            result = _run_git(git_exe, parts[1:], env)
        else:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )

        out = _redact_secret(result.stdout, token)
        err = _redact_secret(result.stderr, token)
        return f"STDOUT:\n{out}\nSTDERR:\n{err}"
    except subprocess.TimeoutExpired:
        return "Error: Command execution timed out after 60 seconds."
    except Exception as e:
        return f"Error executing command: {_redact_secret(str(e), token)}"


@mcp.tool()
def create_github_pull_request(
    repo_name: str,
    branch_name: str,
    commit_message: str,
    pr_title: str,
    pr_body: str = "",
) -> str:
    """Stage changes, commit on a branch, push to origin, and open a GitHub Pull Request."""
    token = os.getenv("GITHUB_TOKEN")
    try:
        if not token:
            return "Error: GITHUB_TOKEN not found in environment or .env."

        resolved_repo = (repo_name or os.getenv("REPO_NAME") or "").strip()
        if not resolved_repo:
            return "Error: Repository name or REPO_NAME is required."

        git_exe = _get_git_executable()
        if not git_exe:
            return (
                "Error: Git for Windows (`git.exe`) not found. "
                "Install Git for Windows from https://git-scm.com/downloads."
            )

        env = _git_env()

        current_branch = _run_git(git_exe, ["rev-parse", "--abbrev-ref", "HEAD"], env)
        if current_branch.stdout.strip() != branch_name:
            checkout_res = _run_git(git_exe, ["checkout", "-b", branch_name], env)
            if checkout_res.returncode != 0:
                _run_git(git_exe, ["checkout", branch_name], env)

        add_res = _run_git(git_exe, ["add", "."], env)
        if add_res.returncode != 0:
            return f"Git Add Error: {_redact_secret(add_res.stderr.strip(), token)}"

        commit_res = _run_git(git_exe, ["commit", "-m", commit_message], env)
        if commit_res.returncode != 0:
            combined_out = f"{commit_res.stdout}\n{commit_res.stderr}".lower()
            if "nothing to commit" not in combined_out:
                return f"Git Commit Error: {_redact_secret(commit_res.stderr.strip(), token)}"

        remote_res = _run_git(git_exe, ["remote", "get-url", "origin"], env)
        remote_url = remote_res.stdout.strip()

        push_target = "origin"
        if remote_url.startswith("https://github.com/"):
            push_target = remote_url.replace(
                "https://github.com/",
                f"https://x-access-token:{token}@github.com/",
                1,
            )

        push_res = _run_git(git_exe, ["push", "-u", push_target, f"{branch_name}:{branch_name}"], env)
        if push_res.returncode != 0:
            return f"Git Push Error: {_redact_secret(push_res.stderr.strip(), token)}"

        gh = Github(token)
        repo = gh.get_repo(resolved_repo)
        base_branch = repo.default_branch or "main"

        pr = repo.create_pull(
            title=pr_title,
            body=pr_body or "Automated Pull Request generated by AI DevOps Master Agent.",
            head=branch_name,
            base=base_branch,
        )
        return f"Push successful & PR Created: {pr.html_url}"

    except Exception as e:
        return f"Error creating GitHub PR: {_redact_secret(str(e), token)}"


if __name__ == "__main__":
    mcp.run()