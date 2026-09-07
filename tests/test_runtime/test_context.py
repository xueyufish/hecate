"""Tests for the ContextEngine abstract interface and its implementations.

Validates the pluggable context management contract:

- ContextEngine ABC cannot be instantiated directly.
- InMemoryContextEngine.select_messages keeps recent messages within budget.
- InMemoryContextEngine.compress removes oldest messages.
- InMemoryContextEngine.estimate_tokens uses character-based estimation.
- PriorityContextEngine (4.12) selects by importance, always keeping system
  messages and the newest user message, preserving conversation order.
"""

from __future__ import annotations

import pytest

from hecate.runtime.context import ContextEngine, InMemoryContextEngine, PriorityContextEngine

# --- ContextEngine ABC ---


def test_context_engine_is_abstract():
    """ContextEngine SHALL NOT be instantiable directly."""
    with pytest.raises(TypeError):
        ContextEngine()  # type: ignore[abstract]


# --- InMemoryContextEngine ---


@pytest.fixture
def engine() -> InMemoryContextEngine:
    return InMemoryContextEngine(max_messages=5, chars_per_token=4)


def _make_messages(count: int) -> list[dict]:
    """Helper to create test messages."""
    return [{"role": "user", "content": f"Message {i}"} for i in range(count)]


# --- select_messages ---


def test_select_messages_returns_recent(engine: InMemoryContextEngine):
    """select_messages SHALL return the most recent messages that fit budget."""
    messages = _make_messages(10)
    result = engine.select_messages(messages, budget=100)
    assert len(result) <= 10
    assert result == messages[-len(result) :]


def test_select_messages_respects_budget(engine: InMemoryContextEngine):
    """select_messages SHALL not significantly exceed token budget."""
    messages = _make_messages(100)
    budget = 10
    result = engine.select_messages(messages, budget=budget)
    estimated = engine.estimate_tokens(result)
    assert estimated <= budget + 5  # Allow small margin for estimation variance


def test_select_messages_empty_history(engine: InMemoryContextEngine):
    """select_messages SHALL return empty list for empty history."""
    result = engine.select_messages([], budget=100)
    assert result == []


def test_select_messages_zero_budget(engine: InMemoryContextEngine):
    """select_messages SHALL return empty list for zero budget."""
    messages = _make_messages(5)
    result = engine.select_messages(messages, budget=0)
    assert result == []


def test_select_messages_preserves_order(engine: InMemoryContextEngine):
    """select_messages SHALL preserve message order (oldest to newest)."""
    messages = _make_messages(5)
    result = engine.select_messages(messages, budget=1000)
    assert result == messages


# --- compress ---


def test_compress_removes_oldest(engine: InMemoryContextEngine):
    """compress SHALL remove oldest messages when count exceeds threshold."""
    messages = _make_messages(10)
    result = engine.compress(messages)
    assert len(result) == 5
    assert result == messages[-5:]


def test_compress_within_threshold(engine: InMemoryContextEngine):
    """compress SHALL return all messages when within threshold."""
    messages = _make_messages(3)
    result = engine.compress(messages)
    assert result == messages


def test_compress_at_threshold(engine: InMemoryContextEngine):
    """compress SHALL return all messages when at threshold."""
    messages = _make_messages(5)
    result = engine.compress(messages)
    assert result == messages


def test_compress_returns_new_list(engine: InMemoryContextEngine):
    """compress SHALL return a new list, not the original."""
    messages = _make_messages(10)
    result = engine.compress(messages)
    assert result is not messages


# --- estimate_tokens ---


def test_estimate_tokens_returns_positive(engine: InMemoryContextEngine):
    """estimate_tokens SHALL return positive estimate for non-empty messages."""
    messages = _make_messages(5)
    result = engine.estimate_tokens(messages)
    assert result > 0


def test_estimate_tokens_empty_list(engine: InMemoryContextEngine):
    """estimate_tokens SHALL return 0 for empty list."""
    result = engine.estimate_tokens([])
    assert result == 0


def test_estimate_tokens_scales_with_content(engine: InMemoryContextEngine):
    """estimate_tokens SHALL scale with message content length."""
    short_messages = [{"role": "user", "content": "Hi"}]
    long_messages = [{"role": "user", "content": "Hello " * 100}]
    short_estimate = engine.estimate_tokens(short_messages)
    long_estimate = engine.estimate_tokens(long_messages)
    assert long_estimate > short_estimate


def test_estimate_tokens_handles_none_content(engine: InMemoryContextEngine):
    """estimate_tokens SHALL handle messages with None content."""
    messages = [{"role": "assistant", "content": None}]
    result = engine.estimate_tokens(messages)
    assert result >= 0


# --- PriorityContextEngine (4.12 Message Prioritization) ---


@pytest.fixture
def priority_engine() -> PriorityContextEngine:
    return PriorityContextEngine()


def test_priority_select_keeps_system_and_newest_user(priority_engine: PriorityContextEngine):
    """System messages and the newest user message survive tight budgets."""
    messages = [
        {"role": "system", "content": "x" * 400},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "latest question"},
    ]
    result = priority_engine.select_messages(messages, budget=250)
    assert messages[0] in result
    assert messages[3] in result


def test_priority_select_fits_budget(priority_engine: PriorityContextEngine):
    messages = [{"role": "user", "content": "x" * 400 + f" {i}"} for i in range(20)]
    result = priority_engine.select_messages(messages, budget=200)
    assert priority_engine.estimate_tokens(result) <= 200 + 5


def test_priority_select_prefers_recent_over_old(priority_engine: PriorityContextEngine):
    """With a tight budget, recent messages win over stale ones."""
    messages = [
        {"role": "user", "content": "ancient" + " " * 200},
        {"role": "user", "content": "recent"},
    ]
    result = priority_engine.select_messages(messages, budget=50)
    assert messages[1] in result


def test_priority_select_preserves_order(priority_engine: PriorityContextEngine):
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    result = priority_engine.select_messages(messages, budget=10_000)
    assert result == messages


def test_priority_select_empty_and_zero_budget(priority_engine: PriorityContextEngine):
    assert priority_engine.select_messages([], budget=100) == []
    assert priority_engine.select_messages([{"role": "user", "content": "x"}], budget=0) == []


def test_priority_compress_keeps_half(priority_engine: PriorityContextEngine):
    messages = _make_messages(10)
    result = priority_engine.compress(messages)
    assert len(result) == 5
    assert result == [m for m in messages if m in result]


def test_priority_compress_never_empties(priority_engine: PriorityContextEngine):
    messages = _make_messages(2)
    assert len(priority_engine.compress(messages)) == 2


def test_priority_penalizes_failed_tool_results(priority_engine: PriorityContextEngine):
    """Failed tool results score lower than successful ones of the same age."""
    ok = {"role": "tool", "content": "all good"}
    bad = {"role": "tool", "content": "Error: connection refused"}
    engine = PriorityContextEngine()
    ok_score = engine._score(5, ok, 10)
    bad_score = engine._score(5, bad, 10)
    assert bad_score < ok_score


def test_priority_select_non_dict_entries_ignored(priority_engine: PriorityContextEngine):
    result = priority_engine.select_messages(["junk", {"role": "user", "content": "real"}], budget=100)
    assert result == [{"role": "user", "content": "real"}]
