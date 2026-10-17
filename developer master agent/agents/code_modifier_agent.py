"""LLM-powered single-file editor with diff preview, dry-run and atomic writes."""
from __future__ import annotations

import difflib
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from agents.llm import get_llm, message_text, was_truncated
from agents.workspace import WorkspaceError, atomic_write, load_skill, resolve_in_workspace
from config.settings import get_settings

SYSTEM_PROMPT = """You are a careful senior software engineer editing a single file.
Apply the user's instruction and return the COMPLETE updated file content.
Rules: output ONLY the file content (no markdown fences, no explanations);
preserve existing style and unrelated code; make the minimal change required."""

_FENCE_RE = re.compile(r"^```[\w+-]*\n(.*?)\n?```\s*$", re.S)
_MAX_DIFF_PREVIEW = 2500


def _strip_fences(text: str) -> str:
    """Models sometimes wrap output in ``` fences despite instructions -- remove them."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match else text


def _build_diff(original: str, updated: str, rel: str) -> str:
    diff = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), updated.splitlines(keepends=True), f"a/{rel}", f"b/{rel}"
    ))
    if len(diff) > _MAX_DIFF_PREVIEW:
        diff = diff[:_MAX_DIFF_PREVIEW] + "\n... (diff truncated)"
    return diff.replace("```", "'''")  # never break the Slack code block


def modify_file(target_file: str, instruction: str, config: RunnableConfig | None = None) -> str:
    """Apply a natural-language instruction to one workspace file using the LLM."""
    settings = get_settings()
    path = resolve_in_workspace(target_file)
    if path.is_dir():
        raise WorkspaceError(f"`{target_file}` is a directory, not a file.")
    rel = path.relative_to(settings.workspace_root).as_posix()
    existed = path.exists()
    original = path.read_text(encoding="utf-8") if existed else ""
    if len(original) > settings.max_file_chars:
        raise WorkspaceError(f"`{rel}` is too large ({len(original):,} chars) to edit safely.")

    system = SYSTEM_PROMPT
    if skill := load_skill("modifier agent skills.md"):
        system += f"\n\nAdditional guidelines:\n{skill}"

    ai = get_llm().invoke(
        [
            SystemMessage(content=system),
            HumanMessage(content=(
                f"File: {rel}\n<current_file>\n{original}\n</current_file>\n\n"
                f"Instruction: {instruction}"
            )),
        ],
        config=config,
    )
    if was_truncated(ai):
        raise WorkspaceError("Model output was truncated; increase LLM_MAX_TOKENS or edit a smaller file.")

    updated = _strip_fences(message_text(ai))
    if not updated.strip():
        raise WorkspaceError("Model returned empty content; refusing to blank the file.")
    if not updated.endswith("\n"):
        updated += "\n"
    if updated == original:
        return f"ℹ️ No changes were needed in `{rel}`."

    preview = _build_diff(original, updated, rel)
    if settings.dry_run:
        return f"🧪 *DRY RUN* -- proposed change to `{rel}` (not written):\n```\n{preview}\n```"

    atomic_write(path, updated)
    action = "Updated" if existed else "Created"
    return f"📝 {action} `{rel}`:\n```\n{preview}\n```"
