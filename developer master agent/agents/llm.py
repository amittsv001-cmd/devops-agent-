"""Single place to construct the chat model (provider-agnostic) + message helpers."""
from __future__ import annotations

from functools import lru_cache

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from config.settings import get_settings


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """Build the chat model once; LLM_PROVIDER / LLM_MODEL pick the backend."""
    s = get_settings()
    return init_chat_model(
        s.llm_model,
        model_provider=s.llm_provider,
        temperature=0,
        max_tokens=s.llm_max_tokens,
    )


def message_text(msg: BaseMessage) -> str:
    """Flatten message content (str or list of content blocks) into plain text."""
    content = msg.content
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)


def was_truncated(msg: BaseMessage) -> bool:
    """True if the provider stopped because it hit max_tokens."""
    meta = getattr(msg, "response_metadata", None) or {}
    return meta.get("stop_reason") == "max_tokens" or meta.get("finish_reason") == "length"
