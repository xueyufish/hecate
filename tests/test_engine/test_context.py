"""Tests for the ContextEngine abstract interface and InMemoryContextEngine.

Validates the pluggable context management contract:

- ContextEngine ABC cannot be instantiated directly.
- InMemoryContextEngine.select_messages keeps recent messages within budget.
- InMemoryContextEngine.compress removes oldest messages.
- InMemoryContextEngine.estimate_tokens uses character-based estimation.
"""

from __future__ import annotations

import pytest

from hecate.engine.context import ContextEngine, InMemoryContextEngine

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
