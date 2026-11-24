"""Safe git branch/commit/push + GitHub PR creation (no shell, no blind `git add .`)."""
from __future__ import annotations

import logging
import re
import subprocess
import time

from github import Auth, Github, GithubException

from config.settings import get_settings

log = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 120


class GitAgentError(RuntimeError):
    """Raised when a git or GitHub operation fails (message is safe to show users)."""


def _git(*args: str) -> str:
    """Run git with an argument list (never a shell string) inside the workspace."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=get_settings().workspace_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or "").strip()
        raise GitAgentError(f"`git {' '.join(args)}` failed: {detail}") from e
    except subprocess.TimeoutExpired as e:
        raise GitAgentError(f"`git {args[0]}` timed out after {GIT_TIMEOUT_SECONDS}s") from e
    except FileNotFoundError as e:
        raise GitAgentError("git executable not found on PATH") from e
    return result.stdout.strip()


def _safe_branch(hint: str) -> str:
    slug = re.sub(r"[^a-z0-9/-]+", "-", hint.lower()).strip("-/")
    slug = re.sub(r"-{2,}", "-", slug)[:50].rstrip("-/") or "auto-update"
    return f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}"


def commit_and_open_pr(
    branch_hint: str,
    commit_message: str,
    *,
    files: list[str] | None = None,
    pr_title: str | None = None,
    pr_body: str | None = None,
) -> str:
    """Create a branch, commit tracked changes (or `files`), push and open a PR.

    The user's original branch is always restored afterwards.
    """
    settings = get_settings()
    commit_message = commit_message.strip() or "chore: automated update"
    if not _git("status", "--porcelain"):
        return "ℹ️ Working tree is clean -- nothing to commit."

    original_branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if original_branch == "HEAD":
        raise GitAgentError("Repository is in detached-HEAD state; check out a branch first.")

    branch = _safe_branch(branch_hint)
    _git("check-ref-format", "--branch", branch)
    _git("switch", "-c", branch)
    committed = False
    try:
        # Only tracked files by default -- never sweep in .env / secrets / build output.
        if files:
            _git("add", "--", *files)
        else:
            _git("add", "--update")
        staged = _git("diff", "--cached", "--name-only")
        if not staged:
            raise GitAgentError("No tracked changes to commit. List new (untracked) files explicitly.")
        _git("commit", "-m", commit_message)
        committed = True
        sha = _git("rev-parse", "--short", "HEAD")

        if not (settings.github_token and settings.github_repo):
            return (
                f"✅ Committed `{sha}` on local branch `{branch}` "
                "(set GITHUB_TOKEN + GITHUB_REPO to push and open a PR)."
            )

        _git("push", "--set-upstream", "origin", branch)
        try:
            repo = Github(auth=Auth.Token(settings.github_token)).get_repo(settings.github_repo)
            pr = repo.create_pull(
                title=pr_title or commit_message,
                body=pr_body or "Automated PR created by the Slack DevOps agent.",
                head=branch,
                base=settings.github_base_branch or repo.default_branch,
            )
        except GithubException as e:
            raise GitAgentError(f"GitHub API error ({e.status}): {e.data}") from e

        file_list = ", ".join(f"`{f}`" for f in staged.splitlines())
        return f"🚀 PR opened: {pr.html_url}\nBranch `{branch}` · commit `{sha}` · files: {file_list}"
    finally:
        try:
            _git("switch", original_branch)
            if not committed:
                _git("branch", "-D", branch)  # clean up the empty branch we just created
        except GitAgentError:
            log.warning("Could not restore original branch %s", original_branch, exc_info=True)
