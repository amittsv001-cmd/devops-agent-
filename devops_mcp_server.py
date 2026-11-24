<<<<<<< HEAD
import ast
import operator as _op
import os
import shlex
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from github import Github
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("VSCode-DevOps-Server")

_SAFE_BINOPS: dict[type, object] = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
}

_SAFE_UNARYOPS: dict[type, object] = {
    ast.UAdd: _op.pos,
    ast.USub: _op.neg,
}


def _eval_ast_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        if isinstance(node.value, bool):
            raise ValueError("Booleans are not valid numbers.")
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_UNARYOPS:
        fn = _SAFE_UNARYOPS[type(node.op)]
        return float(fn(_eval_ast_node(node.operand)))  # type: ignore[operator]
    if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINOPS:
        fn = _SAFE_BINOPS[type(node.op)]
        left = _eval_ast_node(node.left)
        right = _eval_ast_node(node.right)
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ZeroDivisionError("Division by zero is not allowed.")
        return float(fn(left, right))  # type: ignore[operator]
    raise ValueError(f"Unsupported math construct: {type(node).__name__}")


def _fmt_calc(val: float) -> str:
    return str(int(val)) if float(val).is_integer() else str(round(val, 6))


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
=======
import os
import subprocess
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
from github import Github

# .env file se environment variables (GITHUB_TOKEN) load karne ke liye
load_dotenv()

# MCP Server Initialize
mcp = FastMCP("VSCode-DevOps-Server")


# --- TOOL 1: Read Workspace File ---
@mcp.tool()
def read_workspace_file(file_path: str) -> str:
    """VS Code workspace se kisi file ka content padhta hai."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
>>>>>>> origin/main
    except Exception as e:
        return f"Error reading file: {str(e)}"


<<<<<<< HEAD
@mcp.tool()
def write_workspace_file(file_path: str, content: str) -> str:
    """Write or overwrite a file in the workspace."""
    try:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
=======
# --- TOOL 2: Write / Update Workspace File ---
@mcp.tool()
def write_workspace_file(file_path: str, content: str) -> str:
    """VS Code workspace ki kisi file ko edit ya naya content likhta hai."""
    try:
        dir_name = os.path.dirname(file_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
            
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
>>>>>>> origin/main
        return f"Successfully updated {file_path}"
    except Exception as e:
        return f"Error writing file: {str(e)}"


<<<<<<< HEAD
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


def _stage_commit_and_push(
    git_exe: str,
    env: dict[str, str],
    branch_name: str,
    commit_message: str,
    push_target: str,
    token: str,
) -> str | None:
    add_res = _run_git(git_exe, ["add", "."], env)
    if add_res.returncode != 0:
        return f"Git Add Error: {_redact_secret(add_res.stderr.strip(), token)}"

    commit_res = _run_git(git_exe, ["commit", "-m", commit_message], env)
    if commit_res.returncode != 0:
        combined_out = f"{commit_res.stdout}\n{commit_res.stderr}".lower()
        if "nothing to commit" not in combined_out:
            return f"Git Commit Error: {_redact_secret(commit_res.stderr.strip(), token)}"

    push_res = _run_git(git_exe, ["push", "-u", push_target, f"{branch_name}:{branch_name}"], env)
    if push_res.returncode != 0:
        return f"Git Push Error: {_redact_secret(push_res.stderr.strip(), token)}"

    return None


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
        gh = Github(token)
        repo = gh.get_repo(resolved_repo)
        base_branch = repo.default_branch or "main"

        remote_res = _run_git(git_exe, ["remote", "get-url", "origin"], env)
        remote_url = remote_res.stdout.strip()
        push_target = "origin"
        if remote_url.startswith("https://github.com/"):
            push_target = remote_url.replace(
                "https://github.com/",
                f"https://x-access-token:{token}@github.com/",
                1,
            )

        _run_git(git_exe, ["fetch", "origin"], env)

        current_branch = _run_git(git_exe, ["rev-parse", "--abbrev-ref", "HEAD"], env).stdout.strip()
        if current_branch != branch_name:
            exists_local = _run_git(git_exe, ["show-ref", "--verify", f"refs/heads/{branch_name}"], env).returncode == 0
            if exists_local:
                _run_git(git_exe, ["checkout", branch_name], env)
            else:
                base_ref_ok = _run_git(git_exe, ["show-ref", "--verify", f"refs/remotes/origin/{base_branch}"], env).returncode == 0
                if base_ref_ok:
                    _run_git(git_exe, ["checkout", "-b", branch_name, f"origin/{base_branch}"], env)
                else:
                    _run_git(git_exe, ["checkout", "-b", branch_name], env)

        err = _stage_commit_and_push(git_exe, env, branch_name, commit_message, push_target, token)
        if err:
            return err

        try:
            pr = repo.create_pull(
                title=pr_title,
                body=pr_body or "Automated Pull Request generated by AI DevOps Master Agent.",
                head=branch_name,
                base=base_branch,
            )
            return f"Push successful & PR Created: {pr.html_url}"
        except Exception as pr_err:
            err_msg = str(pr_err)
            if "no history in common" not in err_msg.lower():
                raise

            # Unrelated history recovery: try merging origin/<base_branch> first
            merge_res = _run_git(
                git_exe,
                ["merge", f"origin/{base_branch}", "--allow-unrelated-histories", "-m", f"Merge origin/{base_branch}"],
                env,
            )
            if merge_res.returncode == 0:
                push_after_merge = _run_git(git_exe, ["push", "-u", push_target, f"{branch_name}:{branch_name}"], env)
                if push_after_merge.returncode == 0:
                    pr = repo.create_pull(
                        title=pr_title,
                        body=pr_body or "Automated Pull Request generated by AI DevOps Master Agent.",
                        head=branch_name,
                        base=base_branch,
                    )
                    return f"Merged origin/{base_branch}, pushed & PR Created: {pr.html_url}"

            # If merge had conflicts, abort merge and create a fresh branch from origin/<base_branch>
            _run_git(git_exe, ["merge", "--abort"], env)
            clean_branch = f"{branch_name}-synced"
            _run_git(git_exe, ["checkout", "-B", clean_branch, f"origin/{base_branch}"], env)
            err_clean = _stage_commit_and_push(git_exe, env, clean_branch, commit_message, push_target, token)
            if err_clean:
                return err_clean

            pr = repo.create_pull(
                title=pr_title,
                body=pr_body or "Automated Pull Request generated by AI DevOps Master Agent.",
                head=clean_branch,
                base=base_branch,
            )
            return f"Created synced branch `{clean_branch}`, pushed & PR Created: {pr.html_url}"

    except Exception as e:
        return f"Error creating GitHub PR: {_redact_secret(str(e), token)}"


@mcp.tool()
def calculate_math(
    expression: str = "",
    numbers: list[float] | None = None,
    operation: str = "eval",
) -> str:
    """Safely evaluate a math expression or compute stats (sum/avg/min/max) on a list of numbers."""
    try:
        if numbers:
            clean_nums = [float(n) for n in numbers]
            total = sum(clean_nums)
            avg = total / len(clean_nums)
            mn = min(clean_nums)
            mx = max(clean_nums)
            op = (operation or "all").strip().lower()
            if op == "sum":
                return f"Sum: {_fmt_calc(total)}"
            if op in ("avg", "average", "mean"):
                return f"Average: {_fmt_calc(avg)}"
            if op in ("min", "minimum"):
                return f"Min: {_fmt_calc(mn)}"
            if op in ("max", "maximum"):
                return f"Max: {_fmt_calc(mx)}"
            return (
                f"Count: {len(clean_nums)} | Sum: {_fmt_calc(total)} | "
                f"Avg: {_fmt_calc(avg)} | Min: {_fmt_calc(mn)} | Max: {_fmt_calc(mx)}"
            )

        expr = (expression or "").strip()
        if not expr:
            return "Error: Provide either a math `expression` or a `numbers` list."

        parsed = ast.parse(expr, mode="eval")
        result = _eval_ast_node(parsed.body)
        return f"{expr} = {_fmt_calc(result)}"
    except Exception as e:
        return f"Math Error: {e}"
=======
# --- TOOL 3: Run Terminal Command (Tests & Git) ---
@mcp.tool()
def run_command(command: str) -> str:
    """Terminal command execute karta hai (e.g. git status, npm test, etc.)."""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    except Exception as e:
        return f"Error executing command: {str(e)}"


# --- TOOL 4: GitHub PR & Push Automation ---
@mcp.tool()
def create_github_pull_request(repo_name: str, branch_name: str, commit_message: str, pr_title: str, pr_body: str = "") -> str:
    """
    Local git changes ko branch par commit karta hai, origin par push karta hai,
    aur GitHub par Pull Request (PR) open karta hai.
    """
    try:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            return "Error: .env file ya environment mein GITHUB_TOKEN nahi mila."

        # 1. Local Git commands execute karna
        subprocess.run(f"git checkout -b {branch_name}", shell=True, check=True)
        subprocess.run("git add .", shell=True, check=True)
        subprocess.run(f'git commit -m "{commit_message}"', shell=True, check=True)
        subprocess.run(f"git push origin {branch_name}", shell=True, check=True)

        # 2. PyGithub ke zariye GitHub PR kholna
        g = Github(token)
        repo = g.get_repo(repo_name)
        
        pr = repo.create_pull(
            title=pr_title,
            body=pr_body if pr_body else "Automated Pull Request generated by Slack AI DevOps Agent.",
            head=branch_name,
            base="main"
        )
        return f"✅ Push successful & PR Created: {pr.html_url}"

    except Exception as e:
        return f"Error creating GitHub PR: {str(e)}"
>>>>>>> origin/main


if __name__ == "__main__":
    mcp.run()