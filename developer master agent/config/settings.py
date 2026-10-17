"""Central configuration loaded from environment variables / .env."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# USD per 1M tokens. Prices change -- verify on your provider's pricing page or
# override with MODEL_PRICING_JSON='{"my-model": {"input": 1.0, "output": 2.0}}'.
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
}


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str) -> frozenset[str]:
    return frozenset(v.strip() for v in os.getenv(name, "").split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_model: str
    llm_max_tokens: int
    workspace_root: Path
    github_token: str | None
    github_repo: str | None
    github_base_branch: str | None
    dry_run: bool
    max_file_chars: int
    allowed_slack_users: frozenset[str]
    pricing: dict[str, dict[str, float]]

    def price_for(self, model: str) -> dict[str, float] | None:
        """Longest-prefix match, so 'claude-3-5-sonnet-20241022' -> 'claude-3-5-sonnet'."""
        model = model.lower()
        matches = [key for key in self.pricing if model.startswith(key.lower())]
        return self.pricing[max(matches, key=len)] if matches else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    pricing = dict(DEFAULT_PRICING)
    if raw := os.getenv("MODEL_PRICING_JSON"):
        pricing.update(json.loads(raw))
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "anthropic"),
        llm_model=os.getenv("LLM_MODEL", "claude-3-5-sonnet-latest"),
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8192")),
        workspace_root=Path(os.getenv("WORKSPACE_ROOT", os.getcwd())).resolve(),
        github_token=os.getenv("GITHUB_TOKEN") or None,
        github_repo=os.getenv("GITHUB_REPO") or None,
        github_base_branch=os.getenv("GITHUB_BASE_BRANCH") or None,
        dry_run=_bool("DRY_RUN"),
        max_file_chars=int(os.getenv("MAX_FILE_CHARS", "20000")),
        allowed_slack_users=_csv("ALLOWED_SLACK_USER_IDS"),
        pricing=pricing,
    )
