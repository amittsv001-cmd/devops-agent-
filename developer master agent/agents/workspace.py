"""Workspace sandboxing, safe file writes and .md skill/instruction loading."""
from __future__ import annotations

import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from config.settings import PROJECT_ROOT, get_settings

IGNORE_DIRS = frozenset({
    ".git", "venv", ".venv", "node_modules", "__pycache__", ".claude",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".idea", ".vscode",
})
SKILLS_DIR = PROJECT_ROOT / ".claude" / "skills" / "security-review" / "deploy"


class WorkspaceError(ValueError):
    """Raised for invalid or unsafe workspace paths/operations."""


def resolve_in_workspace(path_str: str) -> Path:
    """Resolve a path and guarantee it stays inside WORKSPACE_ROOT (blocks ../ and symlink escapes)."""
    if not path_str or not path_str.strip():
        raise WorkspaceError("Empty file path.")
    root = get_settings().workspace_root
    candidate = Path(path_str.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise WorkspaceError(f"Path `{path_str}` is outside the workspace.")
    return resolved


def atomic_write(path: Path, content: str) -> None:
    """Write via temp file + os.replace so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def iter_workspace_files() -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(get_settings().workspace_root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


@lru_cache(maxsize=None)
def load_skill(filename: str) -> str:
    """Load one sub-agent skill .md file (empty string if missing)."""
    try:
        return (SKILLS_DIR / filename).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


@lru_cache(maxsize=1)
def load_router_context() -> str:
    """Router prompt + rules only -- NOT every skill file (that would waste tokens on every call)."""
    parts: list[str] = []
    router_prompt = PROJECT_ROOT / "config" / "router_prompt.md"
    if router_prompt.exists():
        parts.append(router_prompt.read_text(encoding="utf-8"))
    for pattern in (".claude/rules/*.md", ".claude/agent-instructions/*.md"):
        parts.extend(p.read_text(encoding="utf-8") for p in sorted(PROJECT_ROOT.glob(pattern)))
    return "\n\n".join(p.strip() for p in parts if p.strip())
