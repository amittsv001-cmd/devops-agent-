import re
from pathlib import Path

WORKSPACE_ROOT = Path(".").resolve()

BLOCKED_COMMAND_PATTERNS = [
    # Destructive file / disk / OS commands
    r"\brm\s+-rf\b",
    r"\brmdir\s+/s\b",
    r"\bdel\s+.*(/f|/s|/q)\b",
    r"\bformat\s+[a-zA-Z]:",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    # Destructive git commands
    r"\bgit\s+push\s+.*--force\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-fd\b",
    # Silent remote download + auto-execute patterns
    r"\binvoke-webrequest\b",
    r"\biwr\b",
    r"\bstart-process\b",
    r"\bmsiexec\b",
    r"\bdownloadfile\b",
    r"\bcurl\b.*\|\s*(sh|bash|pwsh|powershell)",
]


def is_safe_workspace_path(file_path: str) -> tuple[bool, str]:
    try:
        target = (WORKSPACE_ROOT / file_path).resolve()
        if target == WORKSPACE_ROOT or WORKSPACE_ROOT in target.parents:
            return True, str(target)
        return False, f"Blocked path outside workspace: {file_path}"
    except Exception as e:
        return False, f"Invalid path '{file_path}': {e}"


def is_safe_command(command: str) -> tuple[bool, str]:
    cmd = (command or "").strip()
    if not cmd:
        return False, "Empty command is not allowed."

    for pattern in BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, cmd, flags=re.IGNORECASE):
            return False, f"Blocked dangerous/unapproved command pattern: `{pattern}`"

    return True, "OK"