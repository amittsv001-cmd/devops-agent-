"""MCP server exposing sandboxed workspace + git tools (no shell=True, no path traversal)."""
from __future__ import annotations

import os
import shlex
import subprocess

from mcp.server.fastmcp import FastMCP

from agents.devops_git_agent import GitAgentError, commit_and_open_pr
from agents.workspace import WorkspaceError, atomic_write, resolve_in_workspace
from config.settings import get_settings

mcp = FastMCP("VSCode-DevOps-Server")

# NOTE: an allow-list is a mitigation, not a sandbox -- tools like npm/pytest can still run
# project code. Keep this list minimal and only run the server on trusted workspaces.
ALLOWED_COMMANDS = frozenset(
    c.strip() for c in os.getenv("MCP_ALLOWED_COMMANDS", "git,pytest,npm,ls").split(",") if c.strip()
)
COMMAND_TIMEOUT = int(os.getenv("MCP_COMMAND_TIMEOUT", "120"))
MAX_OUTPUT_CHARS = 20_000


@mcp.tool()
def read_workspace_file(file_path: str) -> str:
    """Read a UTF-8 text file inside the workspace."""
    try:
        return resolve_in_workspace(file_path).read_text(encoding="utf-8")
    except (WorkspaceError, OSError, UnicodeDecodeError) as e:
        return f"Error reading file: {e}"


@mcp.tool()
def write_workspace_file(file_path: str, content: str) -> str:
    """Create or overwrite a text file inside the workspace (atomic write)."""
    try:
        path = resolve_in_workspace(file_path)
        atomic_write(path, content)
        return f"Successfully updated {path.relative_to(get_settings().workspace_root)}"
    except (WorkspaceError, OSError) as e:
        return f"Error writing file: {e}"


@mcp.tool()
def run_command(command: str) -> str:
    """Run an allow-listed command (no shell) in the workspace, e.g. 'git status' or 'pytest -q'."""
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"Invalid command: {e}"
    if not argv or argv[0] not in ALLOWED_COMMANDS:
        return f"Command not allowed. Allowed: {', '.join(sorted(ALLOWED_COMMANDS))}"
    try:
        result = subprocess.run(
            argv,
            cwd=get_settings().workspace_root,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {COMMAND_TIMEOUT}s"
    except FileNotFoundError:
        return f"Executable not found: {argv[0]}"
    output = f"exit code: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return output[-MAX_OUTPUT_CHARS:]


@mcp.tool()
def create_github_pull_request(
    branch_name: str,
    commit_message: str,
    pr_title: str = "",
    pr_body: str = "",
    files: list[str] | None = None,
) -> str:
    """Commit tracked changes (or the given files) to a new branch, push, and open a PR.

    The target repository comes from GITHUB_REPO in .env.
    """
    try:
        return commit_and_open_pr(
            branch_name,
            commit_message,
            files=files,
            pr_title=pr_title or None,
            pr_body=pr_body or None,
        )
    except GitAgentError as e:
        return f"Error creating GitHub PR: {e}"


if __name__ == "__main__":
    mcp.run()
