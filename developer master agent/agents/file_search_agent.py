"""Find files in the workspace by name / path keywords, ranked by relevance."""
from __future__ import annotations

import re

from agents.workspace import iter_workspace_files
from config.settings import get_settings

STOP_WORDS = frozenset({
    "where", "is", "the", "path", "of", "file", "files", "find", "search", "what",
    "to", "in", "a", "an", "for", "me", "show", "locate", "my", "please", "which",
})


def extract_terms(query: str) -> list[str]:
    """Pull candidate file-name keywords out of a natural-language query."""
    words = re.findall(r"[\w.\-]+", query.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 1]


def _score(terms: list[str], name: str, stem: str, rel: str) -> int:
    score = 0
    for term in terms:
        if term in (name, stem):
            score += 3  # exact file name / stem
        elif term in name:
            score += 2  # partial file name
        elif term in rel:
            score += 1  # matches a parent directory
    return score


def search_files(terms: list[str], max_results: int = 25) -> str:
    root = get_settings().workspace_root
    terms = [t.lower() for t in terms if t and t.strip()]
    if not terms:
        return "🔍 Tell me what to look for, e.g. `where is app.py`."

    scored: list[tuple[int, str]] = []
    for path in iter_workspace_files():
        rel = path.relative_to(root).as_posix()
        score = _score(terms, path.name.lower(), path.stem.lower(), rel.lower())
        if score:
            scored.append((score, rel))

    if not scored:
        wanted = ", ".join(f"`{t}`" for t in terms)
        return f"❌ No files matching {wanted} in `{root}`."

    scored.sort(key=lambda item: (-item[0], item[1]))
    lines = [f"• `{root / rel}`" for _, rel in scored[:max_results]]
    more = f"\n…and {len(scored) - max_results} more" if len(scored) > max_results else ""
    return f"🔍 *Found {len(scored)} matching file(s):*\n" + "\n".join(lines) + more
