"""Unit tests for the memory-storage-family change.

Pure unit tests: capability routing, flag-gated tool seeding,
namespace visibility, gate heuristics, and the work-context-graph
node-type heuristic. These tests do not import any ORM model (so they
work in the SQLite-backed CI lane without an ARRAY rendering error)
and they do not need a live database or LLM service.

Run with ``python -m pytest tests/test_memory_storage_family_units.py -q``.
"""

from __future__ import annotations

import pytest
from hecate_memory.memory.tools_backend import (
    _MEMORY_ADD_SCOPES,
    _MEMORY_SEARCH_TIER_DEFAULT,
    _MEMORY_TOOL_NAMES,
    MEMORY_ADD_SCOPE_ACTOR_SCOPED,
    MEMORY_ADD_SCOPE_WORKSPACE_SHARED,
    get_memory_tool_names,
    get_visible_memory_tool_names,
)
from hecate_memory.memory.work_context_graph import (
    WORK_CONTEXT_NODE_NODE_TYPE_PATTERN,
    WORK_CONTEXT_NODE_TYPE_CORRECTION,
    WORK_CONTEXT_NODE_TYPE_METHOD,
    WORK_CONTEXT_NODE_TYPE_OUTCOME,
    WORK_CONTEXT_NODE_TYPE_SOURCE,
    derive_node_type,
)

from hecate.core.composition.memory_provider import (
    CAP_CROSS_THREAD,
    CAP_TASK_MEMORY,
    TIER_2,
    TIER_4,
    TIER_5,
    EpisodeWriteResult,
    TierRoutingError,
    route_search_by_tier,
)

# ──────────────────────── capability routing ────────────────────────


class _StubProvider:
    """Minimal duck-typed provider for capability routing tests."""

    def __init__(self, caps: frozenset[str]) -> None:
        self._caps = caps

    def capabilities(self) -> frozenset[str]:
        return self._caps


def test_route_search_by_tier_tier_2_passes_without_task_memory_caps() -> None:
    p = _StubProvider(frozenset({"search_memories"}))
    assert route_search_by_tier(p, TIER_2) is None  # no exception


def test_route_search_by_tier_tier_4_requires_task_memory_cap() -> None:
    legacy = _StubProvider(frozenset({"search_memories"}))
    with pytest.raises(TierRoutingError) as exc_info:
        route_search_by_tier(legacy, TIER_4)
    assert exc_info.value.requested_tier == "tier_4"
    assert exc_info.value.required_capability == CAP_TASK_MEMORY


def test_route_search_by_tier_tier_5_requires_cross_thread_cap() -> None:
    legacy = _StubProvider(frozenset({"search_memories"}))
    with pytest.raises(TierRoutingError) as exc_info:
        route_search_by_tier(legacy, TIER_5)
    assert exc_info.value.required_capability == CAP_CROSS_THREAD


def test_route_search_by_tier_unknown_tier_raises() -> None:
    p = _StubProvider(frozenset({CAP_TASK_MEMORY}))
    with pytest.raises(TierRoutingError):
        route_search_by_tier(p, "tier_99")


# ──────────────────────── tool flag gating ────────────────────────


def test_memory_tool_names_includes_all_ten() -> None:
    """Eight legacy + reflection_search + work_context_query."""
    expected = {
        "memory_replace",
        "memory_insert",
        "memory_rethink",
        "memory_search",
        "memory_add",
        "memory_update",
        "memory_forget",
        "conversation_search",
        "reflection_search",
        "work_context_query",
    }
    assert set(_MEMORY_TOOL_NAMES) == expected
    assert get_memory_tool_names() == expected


def test_visible_tool_names_hides_reflection_tools_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REFLECTION_ENABLED", "false")
    visible = get_visible_memory_tool_names()
    assert "reflection_search" not in visible
    assert "work_context_query" not in visible
    assert len(visible) == 8


def test_visible_tool_names_includes_all_when_flag_on() -> None:
    """Pass the flag explicitly: pydantic-settings caches ``REFLECTION_ENABLED``
    at first access, so ``monkeypatch.setenv`` doesn't propagate."""
    visible = get_visible_memory_tool_names(reflection_enabled=True)
    assert "reflection_search" in visible
    assert "work_context_query" in visible
    assert len(visible) == 10


def test_memory_add_scopes_constants_complete() -> None:
    """Both scopes must be in the accepted set."""
    assert {
        MEMORY_ADD_SCOPE_ACTOR_SCOPED,
        MEMORY_ADD_SCOPE_WORKSPACE_SHARED,
    } == _MEMORY_ADD_SCOPES


def test_memory_search_default_tier_is_tier_2() -> None:
    """Default tier must be tier_2 to preserve byte-identical pre-change behavior."""
    assert _MEMORY_SEARCH_TIER_DEFAULT == TIER_2


# ──────────────────────── work context graph heuristics ────────────────────────


def test_derive_node_type_method_default() -> None:
    assert derive_node_type(title="Unknown", hints="") == WORK_CONTEXT_NODE_TYPE_METHOD


def test_derive_node_type_correction_priority() -> None:
    """correction outranks pattern / outcome / source / method on keyword match."""
    title = "Correction: use X instead of Y"
    assert derive_node_type(title=title, hints="") == WORK_CONTEXT_NODE_TYPE_CORRECTION
    title = "Always run smoke tests"
    assert derive_node_type(title=title, hints="") == WORK_CONTEXT_NODE_NODE_TYPE_PATTERN
    title = "Result: this worked"
    assert derive_node_type(title=title, hints="outcome") == WORK_CONTEXT_NODE_TYPE_OUTCOME
    title = "Source: based on doc"
    assert derive_node_type(title=title, hints="") == WORK_CONTEXT_NODE_TYPE_SOURCE


# ──────────────────────── reflection lifecycle (off paths only) ────────────────────────


def test_build_reflection_engine_off_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pure-logic test: when flag is off, no engine is built."""
    monkeypatch.setenv("REFLECTION_ENABLED", "false")
    # Lazy import to keep the SQLite collection path alive
    from hecate.core.composition.reflection import build_reflection_engine

    assert build_reflection_engine() is None


def test_start_reflection_off_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFLECTION_ENABLED", "false")
    from hecate.core.composition.reflection import (
        get_reflection_bundle,
        start_reflection,
    )

    start_reflection()
    assert get_reflection_bundle() is None


def test_stop_reflection_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFLECTION_ENABLED", "false")
    import asyncio

    from hecate.core.composition.reflection import (
        get_reflection_bundle,
        stop_reflection,
    )

    asyncio.run(stop_reflection())
    asyncio.run(stop_reflection())
    assert get_reflection_bundle() is None


# ──────────────────────── result type sanity ────────────────────────


def test_episode_write_result_default_noop_shape() -> None:
    """Providers return this exact shape when the flag is off."""
    r = EpisodeWriteResult(ok=False, error="none")
    assert r.ok is False
    assert r.error == "none"
    assert r.episode_id is None
    assert r.closed is False
    assert r.metadata == {}
