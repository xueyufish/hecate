"""Tests for TaskAllocator ABC, SemanticTaskAllocator, and RoundRobinTaskAllocator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hecate.engine.task_allocator import (
    RoundRobinTaskAllocator,
    SemanticTaskAllocator,
    TaskAllocator,
)


def _make_candidate(name: str, persona: str = "") -> SimpleNamespace:
    return SimpleNamespace(name=name, persona=persona)


async def _async_gen_tokens(tokens: list[str]):
    for t in tokens:
        yield t


class TestTaskAllocatorABC:
    """Tests for TaskAllocator abstract class."""

    async def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            TaskAllocator()  # type: ignore[abstract]


class TestSemanticTaskAllocator:
    """Tests for SemanticTaskAllocator."""

    async def test_returns_best_match(self) -> None:
        finance = _make_candidate("finance_agent", "Finance specialist")
        legal = _make_candidate("legal_agent", "Legal expert")
        tech = _make_candidate("tech_agent", "Technology analyst")

        port = SimpleNamespace(
            llm_invoke=lambda messages, config: _async_gen_tokens(["finance_agent"]),
        )
        allocator = SemanticTaskAllocator(port=port)
        result = await allocator.allocate("Review financial report", [finance, legal, tech])
        assert result is finance

    async def test_returns_none_for_empty_candidates(self) -> None:
        port = SimpleNamespace(
            llm_invoke=lambda messages, config: _async_gen_tokens(["anything"]),
        )
        allocator = SemanticTaskAllocator(port=port)
        result = await allocator.allocate("Some task", [])
        assert result is None

    async def test_returns_none_on_llm_failure(self) -> None:
        def failing_invoke(messages: object, config: object):
            raise RuntimeError("LLM unavailable")

        port = SimpleNamespace(llm_invoke=failing_invoke)
        allocator = SemanticTaskAllocator(port=port)
        result = await allocator.allocate(
            "task",
            [_make_candidate("a", "desc")],
        )
        assert result is None

    async def test_create_if_not_found_raises(self) -> None:
        port = SimpleNamespace(
            llm_invoke=lambda messages, config: _async_gen_tokens(["a"]),
        )
        allocator = SemanticTaskAllocator(port=port)
        with pytest.raises(NotImplementedError, match="P3"):
            await allocator.allocate(
                "task",
                [_make_candidate("a", "desc")],
                create_if_not_found=True,
            )

    async def test_fallback_to_first_on_no_name_match(self) -> None:
        a = _make_candidate("agent_a", "desc_a")
        port = SimpleNamespace(
            llm_invoke=lambda messages, config: _async_gen_tokens(["unknown_response"]),
        )
        allocator = SemanticTaskAllocator(port=port)
        result = await allocator.allocate("task", [a])
        assert result is a


class TestRoundRobinTaskAllocator:
    """Tests for RoundRobinTaskAllocator."""

    async def test_cycles_through_candidates(self) -> None:
        a = _make_candidate("a", "desc_a")
        b = _make_candidate("b", "desc_b")
        c = _make_candidate("c", "desc_c")
        allocator = RoundRobinTaskAllocator()

        assert await allocator.allocate("task", [a, b, c]) is a
        assert await allocator.allocate("task", [a, b, c]) is b
        assert await allocator.allocate("task", [a, b, c]) is c
        assert await allocator.allocate("task", [a, b, c]) is a

    async def test_returns_none_for_empty(self) -> None:
        allocator = RoundRobinTaskAllocator()
        result = await allocator.allocate("task", [])
        assert result is None

    async def test_create_if_not_found_raises(self) -> None:
        allocator = RoundRobinTaskAllocator()
        with pytest.raises(NotImplementedError, match="P3"):
            await allocator.allocate(
                "task",
                [_make_candidate("a", "desc")],
                create_if_not_found=True,
            )
