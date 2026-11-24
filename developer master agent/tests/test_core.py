"""Offline unit tests (no LLM / Slack / GitHub calls). Run: pytest -q"""
from __future__ import annotations
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from config.settings import get_settings


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _fake_result(model: str, input_tokens: int, output_tokens: int) -> LLMResult:
    msg = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        response_metadata={"model_name": model},
    )
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


# --- pricing / usage ---------------------------------------------------------
def test_price_for_uses_longest_prefix(workspace):
    s = get_settings()
    assert s.price_for("claude-3-5-sonnet-20241022") == s.pricing["claude-3-5-sonnet"]
    assert s.price_for("gpt-4o-mini-2024-07-18") == s.pricing["gpt-4o-mini"]
    assert s.price_for("unknown-model") is None


def test_usage_tracker_tokens_and_cost(workspace):
    from agents.usage import UsageTracker

    tracker = UsageTracker(default_model="claude-3-5-sonnet-latest")
    run_id = uuid4()
    tracker.on_chat_model_start({}, [], run_id=run_id)
    tracker.on_llm_end(_fake_result("claude-3-5-sonnet-20241022", 1000, 500), run_id=run_id)

    assert tracker.calls == 1
    assert tracker.total_tokens == 1500
    cost, unpriced = tracker.cost_usd()
    assert cost == pytest.approx(1000 / 1e6 * 3.0 + 500 / 1e6 * 15.0)
    assert unpriced == []
    stats = tracker.format_stats(1.234)
    assert "1,500" in stats and "1.23s" in stats and "$0.010500" in stats


def test_usage_tracker_without_llm_calls(workspace):
    from agents.usage import UsageTracker

    stats = UsageTracker(default_model="x").format_stats(0.05)
    assert "$0.00" in stats and "no LLM calls" in stats


def test_unpriced_model_is_reported(workspace):
    from agents.usage import UsageTracker

    tracker = UsageTracker(default_model="x")
    tracker.on_llm_end(_fake_result("my-local-model", 10, 10), run_id=uuid4())
    assert "my-local-model" in tracker.format_stats(0.1)


# --- routing -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("prompt", "intent"),
    [
        ("create a PR for my changes", "git_ops"),
        ("where is app.py", "file_search"),
        ("add a /version route to demo/app.py", "code_modifier"),
        ("please improve the prompt wording", "chat"),  # "pr" must not match "improve"/"prompt"
    ],
)
def test_keyword_route(workspace, prompt, intent):
    from agents.master_agent import keyword_route

    assert keyword_route(prompt).intent == intent


def test_keyword_route_extracts_target_file(workspace):
    from agents.master_agent import keyword_route

    assert keyword_route("add a /version route to demo/app.py").target_file == "demo/app.py"


# --- workspace sandbox / file search -----------------------------------------
def test_resolve_blocks_path_traversal(workspace):
    from agents.workspace import WorkspaceError, resolve_in_workspace

    with pytest.raises(WorkspaceError):
        resolve_in_workspace("../outside.txt")
    assert resolve_in_workspace("inside.txt") == workspace.resolve() / "inside.txt"


def test_search_files_ranks_exact_match_first(workspace):
    from agents.file_search_agent import search_files

    (workspace / "demo").mkdir()
    (workspace / "demo" / "app.py").write_text("x")
    (workspace / "application_notes.md").write_text("x")
    result = search_files(["app.py"])
    assert "demo/app.py" in result
    assert "Found 1" in result
