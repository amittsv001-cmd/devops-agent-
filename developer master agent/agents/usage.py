"""Per-request token usage, cost and latency tracking via a LangChain callback."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from config.settings import get_settings


@dataclass
class ModelUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


def _extract_usage(response: LLMResult) -> tuple[int, int, str | None]:
    """Return (input_tokens, output_tokens, model_name) from an LLM result."""
    in_tok = out_tok = 0
    model: str | None = None
    for generation_list in response.generations:
        for gen in generation_list:
            msg = getattr(gen, "message", None)
            if msg is None:
                continue
            usage = getattr(msg, "usage_metadata", None) or {}
            in_tok += usage.get("input_tokens", 0)
            out_tok += usage.get("output_tokens", 0)
            meta = getattr(msg, "response_metadata", None) or {}
            model = model or meta.get("model_name") or meta.get("model")

    llm_output = response.llm_output or {}
    if not (in_tok or out_tok):  # older integrations report usage in llm_output
        legacy = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if isinstance(legacy, dict):
            in_tok = legacy.get("prompt_tokens") or legacy.get("input_tokens") or 0
            out_tok = legacy.get("completion_tokens") or legacy.get("output_tokens") or 0
    return int(in_tok), int(out_tok), model or llm_output.get("model_name")


class UsageTracker(BaseCallbackHandler):
    """Collects token usage + LLM latency for ONE request. Create a new instance per query."""

    def __init__(self, default_model: str) -> None:
        super().__init__()
        self.default_model = default_model
        self.by_model: dict[str, ModelUsage] = {}
        self._starts: dict[UUID, float] = {}
        self._lock = threading.Lock()

    # --- callback hooks -------------------------------------------------
    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._starts[run_id] = time.perf_counter()

    def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._starts[run_id] = time.perf_counter()

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._starts.pop(run_id, None)
        elapsed = time.perf_counter() - started if started else 0.0
        in_tok, out_tok, model = _extract_usage(response)
        with self._lock:
            usage = self.by_model.setdefault(model or self.default_model, ModelUsage())
            usage.calls += 1
            usage.input_tokens += in_tok
            usage.output_tokens += out_tok
            usage.seconds += elapsed

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._starts.pop(run_id, None)

    # --- aggregates -----------------------------------------------------
    @property
    def calls(self) -> int:
        return sum(u.calls for u in self.by_model.values())

    @property
    def input_tokens(self) -> int:
        return sum(u.input_tokens for u in self.by_model.values())

    @property
    def output_tokens(self) -> int:
        return sum(u.output_tokens for u in self.by_model.values())

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def llm_seconds(self) -> float:
        return sum(u.seconds for u in self.by_model.values())

    def cost_usd(self) -> tuple[float, list[str]]:
        """Return (estimated cost in USD, models with no configured price)."""
        settings = get_settings()
        total, unpriced = 0.0, []
        for model, u in self.by_model.items():
            price = settings.price_for(model)
            if price is None:
                unpriced.append(model)
                continue
            total += u.input_tokens / 1e6 * price["input"] + u.output_tokens / 1e6 * price["output"]
        return total, unpriced

    def format_stats(self, wall_seconds: float) -> str:
        lines = ["📊 *Query stats*"]
        time_line = f"⏱️ Time taken: `{wall_seconds:.2f}s`"
        if self.calls:
            other = max(wall_seconds - self.llm_seconds, 0.0)
            time_line += f" (LLM: `{self.llm_seconds:.2f}s`, tools/other: `{other:.2f}s`)"
        lines.append(time_line)

        if not self.calls:
            lines += ["🔢 Tokens: `0` (no LLM calls needed)", "💰 Cost: `$0.00`"]
            return "\n".join(lines)

        plural = "s" if self.calls != 1 else ""
        lines.append(
            f"🔢 Tokens: `{self.input_tokens:,}` in + `{self.output_tokens:,}` out "
            f"= `{self.total_tokens:,}` total ({self.calls} LLM call{plural})"
        )
        cost, unpriced = self.cost_usd()
        lines.append(f"💰 Est. cost: `${cost:.6f}`")
        if len(self.by_model) > 1:
            for model, u in self.by_model.items():
                lines.append(f"   • `{model}`: {u.calls} call(s), {u.input_tokens:,} in / {u.output_tokens:,} out")
        if unpriced:
            names = ", ".join(f"`{m}`" for m in unpriced)
            lines.append(f"⚠️ No pricing configured for: {names} (set MODEL_PRICING_JSON)")
        return "\n".join(lines)
