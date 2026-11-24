import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from devops_mcp_server import (
    calculate_math,
    create_github_pull_request,
    read_workspace_file,
    run_command,
    search_workspace_files,
    write_workspace_file,
)
from guardrails import is_safe_command, is_safe_workspace_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
MEMORY_FILE = BASE_DIR / "memory.md"
STATS_FILE = BASE_DIR / "stats_history.json"
CACHE_FILE = BASE_DIR / "file_cache.json"

MAX_TOOL_OUTPUT_CHARS = 1800
MAX_CACHED_CONTEXT_CHARS = 3500
MAX_TOOL_STEPS = 4
MAX_HISTORY_MESSAGES = 4
MAX_MEMORY_CHARS = 900
MAX_LESSONS_CHARS = 650
MAX_LESSONS_COUNT = 10
MAX_RECENT_TASK_LOGS = 5
MAX_SESSION_QA_ENTRIES = 50

LESSONS_HEADER = "## Learned Lessons"
LOGS_HEADER = "## Recent Task Logs"

GREETING_WORDS = {"hi", "hii", "hiii", "hello", "hey", "namaste", "yo"}
DEFAULT_LESSONS = [
    "Use Windows commands (`dir`, `cd`, relative paths); never use Linux-only commands (`ls`, `pwd`, `rm`).",
    "Never count alphabets, letters, words, or numbers mentally—call `count_text_metrics_tool` for exact counts.",
    "Use `calculate_math_tool` for expressions or number lists instead of guessing arithmetic.",
    "Read the file first before adding or editing lines, then write the full updated content.",
]


def _compute_text_metrics(text: str) -> dict[str, object]:
    raw = str(text or "")
    words = re.findall(r"\b\w+\b", raw)
    numbers = re.findall(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?", raw)
    alphabets = [ch for ch in raw if ch.isalpha()]
    upper_alpha = sum(1 for ch in alphabets if ch.isupper())
    lower_alpha = sum(1 for ch in alphabets if ch.islower())
    freq = Counter(ch.lower() for ch in alphabets)
    return {
        "total_characters": len(raw),
        "total_alphabets": len(alphabets),
        "uppercase_alphabets": upper_alpha,
        "lowercase_alphabets": lower_alpha,
        "total_words": len(words),
        "total_number_tokens": len(numbers),
        "numbers": numbers,
        "letter_frequency_nonzero": {
            ch: freq[ch] for ch in sorted(freq.keys())
        },
        "missing_letters": [
            ch for ch in "abcdefghijklmnopqrstuvwxyz" if freq.get(ch, 0) == 0
        ],
    }


def _load_disk_cache() -> dict[str, dict[str, object]]:
    if not CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as e:
        logger.warning(f"Failed to load {CACHE_FILE}: {e}")
    return {}


def _save_disk_cache(cache_data: dict[str, dict[str, object]]) -> None:
    try:
        CACHE_FILE.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save {CACHE_FILE}: {e}")


_FILE_READ_CACHE: dict[str, dict[str, object]] = _load_disk_cache()
_SESSION_QA_CACHE: dict[str, dict[str, dict[str, object]]] = {}

SYSTEM_INSTRUCTION = (
    "You are a concise, reliable AI DevOps Master Agent running on Windows (CMD/PowerShell). "
    "Understand the user's intent carefully: distinguish between reading/counting/analyzing a file versus adding/editing/writing lines in a file. "
    "Never count alphabets, letters, words, or numbers manually—always call `count_text_metrics_tool` so the answer is deterministic and exact. "
    "Use `calculate_math_tool` for arithmetic or number lists. "
    "Never use Linux-only commands like `pwd`, `ls`, or `rm`; use Windows-compatible commands or relative paths. "
    "Execute tools directly when requested without unnecessary questions. "
    "Never suggest or run commands that download external installers or scripts. "
    "Always provide a clear, concise text summary."
)


def _parse_memory_sections() -> tuple[list[str], list[str]]:
    if not MEMORY_FILE.exists():
        return list(DEFAULT_LESSONS), []

    try:
        lines = MEMORY_FILE.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        logger.warning(f"Failed to read {MEMORY_FILE}: {e}")
        return list(DEFAULT_LESSONS), []

    lessons: list[str] = []
    logs: list[str] = []
    section = "lessons"

    for raw_ln in lines:
        ln = raw_ln.strip()
        if not ln:
            continue
        if ln.startswith(LESSONS_HEADER):
            section = "lessons"
            continue
        if ln.startswith(LOGS_HEADER):
            section = "logs"
            continue
        if ln.startswith("- "):
            item = ln[2:].strip()
            if section == "lessons":
                lessons.append(item)
            else:
                logs.append(f"- {item}")

    if not lessons:
        lessons = list(DEFAULT_LESSONS)
    return lessons, logs


def _write_memory_sections(lessons: list[str], logs: list[str]) -> None:
    deduped_lessons: list[str] = []
    seen: set[str] = set()
    for r in lessons:
        clean = re.sub(r"\s+", " ", str(r or "")).strip()
        if not clean:
            continue
        norm_key = re.sub(r"[^a-z0-9]+", " ", clean.lower()).strip()
        if norm_key and norm_key not in seen:
            seen.add(norm_key)
            deduped_lessons.append(clean[:140])

    trimmed_lessons = deduped_lessons[-MAX_LESSONS_COUNT:]
    trimmed_logs = [
        ln if ln.startswith("- ") else f"- {ln.strip()}"
        for ln in logs[-MAX_RECENT_TASK_LOGS:]
        if str(ln or "").strip()
    ]

    parts = [
        LESSONS_HEADER,
        "\n".join(f"- {r}" for r in trimmed_lessons),
        "",
        LOGS_HEADER,
    ]
    if trimmed_logs:
        parts.append("\n".join(trimmed_logs))

    try:
        MEMORY_FILE.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to write {MEMORY_FILE}: {e}")


def _record_lesson(rule_text: str) -> None:
    clean = re.sub(r"\s+", " ", str(rule_text or "")).strip()
    if not clean:
        return
    lessons, logs = _parse_memory_sections()
    lessons.append(clean[:140])
    _write_memory_sections(lessons, logs)


def _learn_from_user_prompt(prompt: str) -> None:
    text = (prompt or "").strip()
    lower = text.lower()
    if not text or len(text) > 220:
        return
    has_teaching_cue = bool(
        re.search(
            r"\b(yaad rakhna|yaad rakho|aage se|galat|mat karo|do not|dont|don't|always|never|rule|seekho|sikho)\b",
            lower,
        )
    )
    if has_teaching_cue:
        summary_rule = re.sub(
            r"^(yaad rakhna|yaad rakho|rule:)\s*", "", text, flags=re.IGNORECASE
        ).strip()
        _record_lesson(f"User rule: {summary_rule}")


def _learn_from_tool_outcome(tool_name: str, tool_result: str) -> None:
    res = str(tool_result or "").strip()
    if not res:
        return
    lower = res.lower()
    if (
        "guardrail blocked" in lower
        or res.lower().startswith("error")
        or "failed" in lower
    ):
        short_reason = re.sub(r"\s+", " ", res)[:100]
        _record_lesson(f"Avoid trigger in `{tool_name}`: {short_reason}")


def _load_compact_lessons() -> str:
    lessons, logs = _parse_memory_sections()
    if not MEMORY_FILE.exists():
        _write_memory_sections(lessons, logs)
    if not lessons:
        return ""
    block = "\n".join(f"- {r}" for r in lessons[-MAX_LESSONS_COUNT:])
    if len(block) > MAX_LESSONS_CHARS:
        block = block[-MAX_LESSONS_CHARS:]
    return f"\n\n--- LEARNED LESSONS (memory.md) ---\n{block}"


def _is_history_or_summary_query(prompt: str) -> bool:
    lower = (prompt or "").strip().lower()
    return bool(
        re.search(
            r"\b(history|pichla|pichle|previous|prev|summary|summarize|yaad|kya kiya)\b",
            lower,
        )
    )


def _load_recent_task_logs(max_chars: int = MAX_MEMORY_CHARS) -> str:
    _, logs = _parse_memory_sections()
    if not logs:
        return ""
    content = "\n".join(logs[-MAX_RECENT_TASK_LOGS:]).strip()
    if len(content) > max_chars:
        content = content[-max_chars:]
    return f"\n\n--- RECENT TASK LOGS (memory.md) ---\n{content}"


def _build_system_instruction(user_prompt: str = "") -> str:
    base = f"{SYSTEM_INSTRUCTION}{_load_compact_lessons()}"
    if _is_history_or_summary_query(user_prompt):
        return f"{base}{_load_recent_task_logs()}"
    return base


def _resolve_workspace_file(file_path: str) -> Path:
    p = Path(file_path)
    if not p.is_absolute():
        p = (BASE_DIR / p).resolve()
    else:
        p = p.resolve()
    return p


def _clear_session_qa_cache(thread_id: str | None = None) -> None:
    if thread_id:
        _SESSION_QA_CACHE.pop(thread_id, None)
    else:
        _SESSION_QA_CACHE.clear()


def _invalidate_file_cache(file_path: str) -> None:
    try:
        key = str(_resolve_workspace_file(file_path))
        if key in _FILE_READ_CACHE:
            _FILE_READ_CACHE.pop(key, None)
            _save_disk_cache(_FILE_READ_CACHE)
    except Exception:
        pass
    _clear_session_qa_cache()


def _get_cached_entry_if_fresh(file_path: str) -> dict[str, object] | None:
    resolved = _resolve_workspace_file(file_path)
    cache_key = str(resolved)
    if not resolved.exists() or not resolved.is_file():
        return None
    current_mtime = resolved.stat().st_mtime
    cached = _FILE_READ_CACHE.get(cache_key)
    if cached is not None and cached.get("mtime") == current_mtime:
        return cached
    return None


def _read_file_with_cache(file_path: str) -> tuple[str, bool]:
    resolved = _resolve_workspace_file(file_path)
    cache_key = str(resolved)

    current_mtime: float | None = None
    if resolved.exists() and resolved.is_file():
        current_mtime = resolved.stat().st_mtime

    cached = _get_cached_entry_if_fresh(file_path)
    if cached is not None:
        logger.info(f"Cache HIT for '{file_path}' (mtime={current_mtime})")
        return str(cached.get("content", "")), True

    logger.info(f"Cache MISS for '{file_path}' -> reading from disk")
    content = read_workspace_file(file_path)
    if current_mtime is not None and not str(content).startswith("Error"):
        metrics = _compute_text_metrics(str(content))
        _FILE_READ_CACHE[cache_key] = {
            "mtime": current_mtime,
            "content": content,
            "counts": metrics,
        }
        _save_disk_cache(_FILE_READ_CACHE)
    return str(content), False


def _extract_referenced_file(prompt: str) -> str | None:
    match = re.search(
        r"\b([a-zA-Z0-9_\-./\\]+\.(?:txt|md|py|json|log|csv))\b", prompt or ""
    )
    if not match:
        return None
    candidate = match.group(1)
    ok, _ = is_safe_workspace_path(candidate)
    return candidate if ok else None


def _is_mutating_or_action_prompt(prompt: str) -> bool:
    lower = (prompt or "").strip().lower()
    if re.match(
        r"^(add|add line|append|write|save|edit|update|insert|create|delete|remove)\b",
        lower,
    ):
        return True
    return bool(
        re.search(
            r"\b(add line|append|write|save|edit|update|push|commit|pr|pull request|run|command|cmd|powershell|git|delete|remove)\b",
            lower,
        )
    )


def _detect_file_metric_intent(prompt: str) -> str | None:
    lower = (prompt or "").strip().lower()
    if _is_mutating_or_action_prompt(lower):
        return None
    has_count_verb = bool(
        re.search(
            r"\b(count|how many|number of|total|kitne|kitna|batao|tell)\b",
            lower,
        )
    )
    if not has_count_verb:
        return None
    if re.search(r"\b(alphabets?|letters?|akshar)\b", lower):
        return "alpha_count"
    if re.search(r"\b(words?|shabd)\b", lower):
        return "word_count"
    if re.search(r"\b(numbers?|digits?|ank)\b", lower):
        return "number_count"
    return None


def _try_cached_file_metric_answer(prompt: str, thread_id: str) -> str | None:
    metric_type = _detect_file_metric_intent(prompt)
    if not metric_type:
        return None

    raw_file = _extract_referenced_file(prompt)
    if not raw_file:
        return None

    cached = _get_cached_entry_if_fresh(raw_file)
    if not cached:
        return None

    content = str(cached.get("content", ""))
    metrics = _compute_text_metrics(content)

    if metric_type == "word_count":
        body = f"`{raw_file}` contains **{metrics['total_words']} words** in total."
    elif metric_type == "alpha_count":
        freq: dict[str, int] = metrics.get("letter_frequency_nonzero") or {}  # type: ignore[assignment]
        freq_str = ", ".join(f"{k}:{v}" for k, v in freq.items()) or "None"
        missing_str = ", ".join(metrics.get("missing_letters") or []) or "None"  # type: ignore[arg-type]
        body = (
            f"`{raw_file}` contains **{metrics['total_alphabets']} alphabets** "
            f"(Uppercase: {metrics['uppercase_alphabets']}, Lowercase: {metrics['lowercase_alphabets']}).\n"
            f"- **Letter frequency (non-zero):** {freq_str}\n"
            f"- **Missing letters (0):** {missing_str}"
        )
    elif metric_type == "number_count":
        nums_list = metrics.get("numbers") or []
        nums_disp = ", ".join(str(x) for x in nums_list) if nums_list else "None"  # type: ignore[union-attr]
        body = (
            f"`{raw_file}` contains **{metrics['total_number_tokens']} number(s)** "
            f"(Values: {nums_disp})."
        )
    else:
        return None

    reply = (
        f"{body}\n\n"
        f"---\n"
        f"**Model:** `python-deterministic` | **Time:** 0.0s | **Tokens:** 0 (In: 0, Out: 0)"
    )
    _store_session_qa_answer(prompt, reply, thread_id)
    return reply


def _canonical_question_key(prompt: str) -> str:
    lower = (prompt or "").strip().lower()
    raw_file = _extract_referenced_file(lower) or ""
    metric_type = _detect_file_metric_intent(lower)
    if raw_file and metric_type:
        return f"file:{raw_file}:{metric_type}"
    if raw_file and re.search(r"\b(about|summary|summarize|read)\b", lower):
        return f"file:{raw_file}:summary"
    cleaned = re.sub(r"\s+", " ", lower)
    cleaned = re.sub(r"[?!.,]+$", "", cleaned).strip()
    return f"raw:{cleaned}"


def _snapshot_file_mtimes(prompt: str) -> dict[str, float]:
    mtimes: dict[str, float] = {}
    raw_file = _extract_referenced_file(prompt)
    if raw_file:
        resolved = _resolve_workspace_file(raw_file)
        if resolved.exists() and resolved.is_file():
            mtimes[str(resolved)] = resolved.stat().st_mtime
    return mtimes


def _try_session_repeat_answer(prompt: str, thread_id: str) -> str | None:
    if _is_mutating_or_action_prompt(prompt):
        return None

    key = _canonical_question_key(prompt)
    if not key:
        return None

    session_map = _SESSION_QA_CACHE.get(thread_id)
    if not session_map:
        return None

    entry = session_map.get(key)
    if not entry:
        return None

    saved_mtimes = entry.get("file_mtimes") or {}
    if isinstance(saved_mtimes, dict):
        for fpath, old_mtime in saved_mtimes.items():
            p = Path(fpath)
            if not p.exists() or not p.is_file() or p.stat().st_mtime != old_mtime:
                logger.info(f"Session Q&A invalidated due to file change: {fpath}")
                session_map.pop(key, None)
                return None

    cached_answer = str(entry.get("answer", "")).strip()
    if not cached_answer:
        return None

    clean_body = re.sub(
        r"\n+---\n\*\*Model:\*\*.*$", "", cached_answer, flags=re.DOTALL
    ).strip()
    logger.info(f"Session Q&A HIT for thread='{thread_id}' -> '{key}'")
    return (
        f"{clean_body}\n\n"
        f"---\n"
        f"**Model:** `session-history-cache` | **Time:** 0.0s | **Tokens:** 0 (In: 0, Out: 0)"
    )


def _store_session_qa_answer(prompt: str, answer: str, thread_id: str) -> None:
    if _is_mutating_or_action_prompt(prompt):
        _clear_session_qa_cache(thread_id)
        return
    if not answer or answer.startswith("Execution Error:") or "Model error:" in answer:
        return

    key = _canonical_question_key(prompt)
    if not key:
        return

    session_map = _SESSION_QA_CACHE.setdefault(thread_id, {})
    session_map[key] = {
        "answer": answer,
        "file_mtimes": _snapshot_file_mtimes(prompt),
    }
    while len(session_map) > MAX_SESSION_QA_ENTRIES:
        oldest_key = next(iter(session_map))
        session_map.pop(oldest_key, None)


def _build_prompt_with_cached_file(prompt: str) -> tuple[str, bool]:
    text = (prompt or "").strip()
    raw_file = _extract_referenced_file(text)
    if not raw_file:
        return text, False

    cached = _get_cached_entry_if_fresh(raw_file)
    if not cached:
        return text, False

    content = str(cached.get("content", ""))
    if len(content) > MAX_CACHED_CONTEXT_CHARS:
        content = content[:MAX_CACHED_CONTEXT_CHARS] + "\n... [truncated cached content] ..."

    logger.info(f"Injecting cached content of '{raw_file}' directly into LLM prompt")
    enriched = (
        f"{text}\n\n"
        f"[Cached content of `{raw_file}` (mtime unchanged)]:\n"
        f"```text\n{content}\n```"
    )
    return enriched, True


def _append_memory_log(summary_line: str) -> None:
    lessons, logs = _parse_memory_sections()
    stamp = datetime.now().strftime("%Y-%m-%d")
    clean_summary = re.sub(r"\s+", " ", summary_line).strip()[:140]
    entry = f"- [{stamp}] {clean_summary}"

    if logs and logs[-1] == entry:
        return

    logs.append(entry)
    _write_memory_sections(lessons, logs[-MAX_RECENT_TASK_LOGS:])


def _sanitize_windows_command(command: str) -> str:
    cmd = (command or "").strip()
    if not cmd:
        return cmd
    if re.fullmatch(r"pwd", cmd, flags=re.IGNORECASE):
        return "cd"
    if re.fullmatch(r"ls(?:\s+-la|\s+-al|\s+-l|\s+-a)?", cmd, flags=re.IGNORECASE):
        return "dir"
    if re.match(r"^ls\s+(.+)$", cmd, flags=re.IGNORECASE):
        target = re.sub(r"^ls(?:\s+-[a-zA-Z]+)*\s+", "", cmd, flags=re.IGNORECASE).strip()
        return f"dir {target}" if target else "dir"
    return cmd


def _is_simple_greeting(prompt: str) -> bool:
    cleaned = re.sub(r"[^a-zA-Z\s]", "", prompt or "").strip().lower()
    return cleaned in GREETING_WORDS


def _trim_tool_output(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    raw = str(text or "")
    if len(raw) <= limit:
        return raw
    half = limit // 2
    omitted = len(raw) - limit
    return f"{raw[:half]}\n... [trimmed {omitted} chars] ...\n{raw[-half:]}"


def _extract_token_usage(response: object) -> tuple[int, int]:
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return 0, 0
    if isinstance(meta, dict):
        return int(meta.get("input_tokens", 0) or 0), int(
            meta.get("output_tokens", 0) or 0
        )
    return int(getattr(meta, "input_tokens", 0) or 0), int(
        getattr(meta, "output_tokens", 0) or 0
    )


def _normalize_message_content(content: object) -> str:
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts).strip()
    if content is None:
        return ""
    return str(content).strip()


def load_last_run_stats() -> dict:
    default_stats = {
        "timestamp": None,
        "prompt": None,
        "model": None,
        "execution_time": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    if not STATS_FILE.exists():
        return default_stats
    try:
        data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("last_run"), dict):
            return {**default_stats, **data["last_run"]}
    except Exception as e:
        logger.error(f"Failed to load {STATS_FILE}: {e}")
    return default_stats


def save_last_run_stats(stats: dict) -> None:
    history: list[dict] = []
    if STATS_FILE.exists():
        try:
            existing = json.loads(STATS_FILE.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("history"), list):
                history = existing["history"]
        except Exception:
            history = []

    history.append(stats)
    payload = {
        "last_run": stats,
        "history": history[-50:],
    }
    try:
        STATS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to save {STATS_FILE}: {e}")


last_run_stats = load_last_run_stats()


@tool
def read_workspace_file_tool(file_path: str) -> str:
    """Read content of a file in the workspace (uses mtime-aware persistent cache)."""
    ok, info = is_safe_workspace_path(file_path)
    if not ok:
        return f"Guardrail Blocked: {info}"
    content, from_cache = _read_file_with_cache(file_path)
    prefix = "[CACHED FILE CONTENT]\n" if from_cache else "[FRESH FILE READ]\n"
    return _trim_tool_output(f"{prefix}{content}")


@tool
def write_workspace_file_tool(file_path: str, content: str) -> str:
    """Write or overwrite a file in the workspace and invalidate its read cache."""
    ok, info = is_safe_workspace_path(file_path)
    if not ok:
        return f"Guardrail Blocked: {info}"
    result = write_workspace_file(file_path, content)
    _invalidate_file_cache(file_path)
    return _trim_tool_output(result)


@tool
def search_workspace_files_tool(query: str, search_dir: str = ".") -> str:
    """Search workspace files by name or pattern."""
    ok, info = is_safe_workspace_path(search_dir)
    if not ok:
        return f"Guardrail Blocked: {info}"
    return _trim_tool_output(search_workspace_files(query, search_dir))


@tool
def run_command_tool(command: str) -> str:
    """Execute a safe Windows shell or git command."""
    normalized_cmd = _sanitize_windows_command(command)
    ok, info = is_safe_command(normalized_cmd)
    if not ok:
        return f"Guardrail Blocked: {info}"
    return _trim_tool_output(run_command(normalized_cmd))


@tool
def create_github_pull_request_tool(
    repo_name: str,
    branch_name: str,
    commit_message: str,
    pr_title: str,
    pr_body: str = "",
) -> str:
    """Commit branch changes and open a GitHub Pull Request."""
    resolved_repo = (repo_name or os.getenv("REPO_NAME") or "").strip()
    result = create_github_pull_request(
        resolved_repo, branch_name, commit_message, pr_title, pr_body
    )
    return _trim_tool_output(result)


@tool
def calculate_math_tool(
    expression: str = "",
    numbers: list[float] | None = None,
    operation: str = "eval",
) -> str:
    """Safely calculate math expressions (+, -, *, /, //, %, **) or stats (sum/avg/min/max) on numbers."""
    return calculate_math(expression=expression, numbers=numbers, operation=operation)


@tool
def count_text_metrics_tool(file_path: str = "", text: str = "") -> str:
    """Deterministically count alphabets, words, characters, numbers, and letter frequencies for a file or text."""
    source_text = text
    if file_path:
        ok, info = is_safe_workspace_path(file_path)
        if not ok:
            return f"Guardrail Blocked: {info}"
        content, _ = _read_file_with_cache(file_path)
        if str(content).startswith("Error"):
            return str(content)
        source_text = str(content)

    metrics = _compute_text_metrics(source_text)
    return json.dumps(metrics, indent=2)


tools = [
    read_workspace_file_tool,
    write_workspace_file_tool,
    search_workspace_files_tool,
    run_command_tool,
    create_github_pull_request_tool,
    calculate_math_tool,
    count_text_metrics_tool,
]
tool_map = {t.name: t for t in tools}

groq_api_key = os.getenv("GROQ_API_KEY")
if not groq_api_key:
    raise ValueError("Missing GROQ_API_KEY in environment.")

primary_model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b")
fallback_raw = os.getenv("GROQ_FALLBACK_MODELS", "openai/gpt-oss-20b")

model_candidates: list[str] = []
for candidate in [primary_model] + [
    m.strip() for m in fallback_raw.split(",") if m.strip()
]:
    if candidate and candidate not in model_candidates:
        model_candidates.append(candidate)


def _clean_messages_for_fallback(
    chat_messages: list[BaseMessage],
) -> list[BaseMessage]:
    cleaned: list[BaseMessage] = []
    for msg in chat_messages:
        if isinstance(msg, (SystemMessage, HumanMessage)):
            cleaned.append(msg)
        elif (
            isinstance(msg, AIMessage)
            and _normalize_message_content(msg.content)
            and not getattr(msg, "tool_calls", None)
        ):
            cleaned.append(msg)

    if not any(isinstance(m, HumanMessage) for m in cleaned):
        for msg in reversed(chat_messages):
            if isinstance(msg, HumanMessage):
                cleaned.append(msg)
                break
    return cleaned


def _invoke_with_fallback(chat_messages: list[BaseMessage]) -> tuple[object, str]:
    last_err: Exception | None = None
    for idx, model_name in enumerate(model_candidates):
        msgs_to_send = (
            chat_messages if idx == 0 else _clean_messages_for_fallback(chat_messages)
        )
        for retry in range(2):
            try:
                logger.info(
                    f"Calling Groq model '{model_name}' (attempt {idx + 1}, retry {retry})"
                )
                llm = ChatGroq(
                    model=model_name,
                    api_key=groq_api_key,
                    temperature=0,
                    max_retries=0,
                ).bind_tools(tools)
                response = llm.invoke(msgs_to_send)
                return response, model_name
            except Exception as e:
                last_err = e
                logger.warning(f"Model '{model_name}' failed: {e}")
                if "429" in str(e) and retry == 0:
                    time.sleep(2.0)
                    continue
                break

    raise RuntimeError(f"All models failed: {last_err}")


def _is_last_work_stats_query(prompt: str) -> bool:
    text = (prompt or "").lower().strip()
    has_past = bool(re.search(r"\b(last|pichle|pichla|previous|prev)\b", text))
    has_metric = bool(
        re.search(r"\b(token|tokens|time|kitna|kitne|work|task|model)\b", text)
    )
    return has_past and has_metric


def _format_last_run_stats() -> str:
    if not last_run_stats.get("prompt"):
        return "No previous task recorded yet."
    return (
        f"**Last Run Stats**\n"
        f"- **Prompt:** `{last_run_stats['prompt']}`\n"
        f"- **Model:** `{last_run_stats.get('model') or 'N/A'}`\n"
        f"- **Time:** `{last_run_stats['execution_time']}s`\n"
        f"- **Tokens:** `{last_run_stats['total_tokens']}` "
        f"(In: `{last_run_stats['prompt_tokens']}`, Out: `{last_run_stats['completion_tokens']}`)"
    )


class AgentState(TypedDict):
    messages: Sequence[BaseMessage]
    prompt_tokens: int
    completion_tokens: int
    active_model: str
    tool_steps: int
    raw_prompt: str


def agent_node(state: AgentState) -> AgentState:
    messages = list(state["messages"])
    if len(messages) > MAX_HISTORY_MESSAGES:
        messages = [messages[0]] + messages[-(MAX_HISTORY_MESSAGES - 1) :]

    sys_text = _build_system_instruction(state.get("raw_prompt", ""))
    chat_messages = [SystemMessage(content=sys_text)] + messages

    try:
        response, used_model = _invoke_with_fallback(chat_messages)
    except Exception as e:
        err_str = str(e)
        logger.error(f"Agent execution failed across models: {e}")
        content = (
            "Rate limit (429) reached. Please wait briefly and retry with a smaller prompt."
            if "429" in err_str
            else f"Model error: {err_str}"
        )
        return {
            "messages": list(state["messages"]) + [AIMessage(content=content)],
            "prompt_tokens": state.get("prompt_tokens", 0),
            "completion_tokens": state.get("completion_tokens", 0),
            "active_model": state.get("active_model", primary_model),
            "tool_steps": state.get("tool_steps", 0),
            "raw_prompt": state.get("raw_prompt", ""),
        }

    p_tokens, c_tokens = _extract_token_usage(response)
    logger.info(f"Model '{used_model}' usage -> In: {p_tokens}, Out: {c_tokens}")

    return {
        "messages": list(state["messages"]) + [response],
        "prompt_tokens": state.get("prompt_tokens", 0) + p_tokens,
        "completion_tokens": state.get("completion_tokens", 0) + c_tokens,
        "active_model": used_model,
        "tool_steps": state.get("tool_steps", 0),
        "raw_prompt": state.get("raw_prompt", ""),
    }


def custom_tool_node(state: AgentState) -> AgentState:
    messages = list(state["messages"])
    last_message = messages[-1]
    steps_used = state.get("tool_steps", 0)

    tool_calls = getattr(last_message, "tool_calls", None) or []
    for idx, tc in enumerate(tool_calls):
        if steps_used >= MAX_TOOL_STEPS:
            messages.append(
                ToolMessage(
                    content="Tool step limit reached. Summarize current progress.",
                    tool_call_id=tc.get("id", f"call_limit_{idx}"),
                )
            )
            continue

        steps_used += 1
        tool_name = tc["name"]
        tool_args = tc.get("args", {})
        logger.info(f"Executing tool ({steps_used}/{MAX_TOOL_STEPS}): {tool_name}")

        target_tool = tool_map.get(tool_name)
        if target_tool is None:
            tool_result = f"Tool '{tool_name}' not found."
        else:
            try:
                tool_result = target_tool.invoke(tool_args)
            except Exception as e:
                tool_result = f"Tool execution error: {e}"

        _learn_from_tool_outcome(tool_name, str(tool_result))
        messages.append(
            ToolMessage(
                content=_trim_tool_output(str(tool_result)),
                tool_call_id=tc.get("id", f"call_{steps_used}"),
            )
        )

    return {
        "messages": messages,
        "tool_steps": steps_used,
        "raw_prompt": state.get("raw_prompt", ""),
    }


def should_continue(state: AgentState) -> str:
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None)
    if tool_calls and state.get("tool_steps", 0) < MAX_TOOL_STEPS:
        return "tools"
    return "end"


workflow = StateGraph(AgentState)
workflow.add_node("agent", agent_node)
workflow.add_node("tools", custom_tool_node)
workflow.set_entry_point("agent")
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "tools": "tools",
        "end": END,
    },
)
workflow.add_edge("tools", "agent")

memory_checkpointer = MemorySaver()
master_graph = workflow.compile(checkpointer=memory_checkpointer)


def execute_master_agent(user_prompt: str, thread_id: str = "default_session") -> str:
    global last_run_stats

    _learn_from_user_prompt(user_prompt)

    if _is_simple_greeting(user_prompt):
        return (
            "Hello! Ready for your DevOps or Git task.\n\n"
            "---\n"
            "**Model:** `fast-local` | **Time:** 0.0s | **Tokens:** 0 (In: 0, Out: 0)"
        )

    if _is_last_work_stats_query(user_prompt):
        return _format_last_run_stats()

    repeat_reply = _try_session_repeat_answer(user_prompt, thread_id)
    if repeat_reply is not None:
        return repeat_reply

    metric_reply = _try_cached_file_metric_answer(user_prompt, thread_id)
    if metric_reply is not None:
        return metric_reply

    prepared_prompt, _ = _build_prompt_with_cached_file(user_prompt)

    start_time = time.time()
    input_state = {
        "messages": [HumanMessage(content=prepared_prompt)],
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "active_model": primary_model,
        "tool_steps": 0,
        "raw_prompt": user_prompt,
    }
    config = {"configurable": {"thread_id": thread_id}}

    try:
        result = master_graph.invoke(input_state, config=config)
    except Exception as e:
        logger.error(f"Graph invocation failed: {e}")
        return f"Execution Error: {e}"

    execution_time = round(time.time() - start_time, 2)
    total_prompt_tokens = int(result.get("prompt_tokens", 0) or 0)
    total_completion_tokens = int(result.get("completion_tokens", 0) or 0)
    total_tokens = total_prompt_tokens + total_completion_tokens
    used_model = result.get("active_model", primary_model)

    last_run_stats = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "prompt": user_prompt,
        "model": used_model,
        "execution_time": execution_time,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_tokens,
    }
    save_last_run_stats(last_run_stats)

    messages_list = result.get("messages", [])
    last_msg = messages_list[-1] if messages_list else None
    final_response = _normalize_message_content(getattr(last_msg, "content", ""))

    if not final_response:
        recent_tool_msgs = [
            _normalize_message_content(getattr(m, "content", ""))
            for m in reversed(messages_list)
            if isinstance(m, ToolMessage)
        ]
        recent_tool_msgs = [t for t in recent_tool_msgs if t][:2]
        if recent_tool_msgs:
            final_response = (
                f"Executed {result.get('tool_steps', 0)} tool step(s).\n\n"
                + "\n---\n".join(reversed(recent_tool_msgs))
            )
        else:
            final_response = (
                f"Completed {result.get('tool_steps', 0)} tool step(s) with no text summary."
            )

    _append_memory_log(f"Prompt: {user_prompt[:60]} -> {final_response[:75]}")

    formatted_output = (
        f"{final_response}\n\n"
        f"---\n"
        f"**Model:** `{used_model}` | "
        f"**Time:** {execution_time}s | "
        f"**Tokens:** {total_tokens} (In: {total_prompt_tokens}, Out: {total_completion_tokens})"
    )
    _store_session_qa_answer(user_prompt, formatted_output, thread_id)
    return formatted_output