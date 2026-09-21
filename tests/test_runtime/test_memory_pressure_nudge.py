"""Tests for the memory pressure nudge processor (memory-pressure-alert).

Covers the latched injection semantics, usage numbers in the directive,
KV-cache prefix preservation, the best-effort pressure mark (and its
degradation), and the policy registry wiring (registration + threshold
cross-validation).
"""

from __future__ import annotations

import uuid

import pytest

from hecate.core.config import settings
from hecate.runtime.context_policy import PROCESSOR_REGISTRY, validate_policy_spec
from hecate.runtime.context_processors import (
    ChainContext,
    ContextProcessorChain,
    HeuristicTokenEstimator,
    MemoryPressureNudgeProcessor,
    flatten_units,
    unitize,
)


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


_WS = str(uuid.uuid4())
_AGENT = str(uuid.uuid4())


def _ctx(budget: int = 100, **kw) -> ChainContext:
    return ChainContext(
        budget=budget,
        estimator=HeuristicTokenEstimator(),
        execution_context={"workspace_id": _WS, "agent_id": _AGENT},
        **kw,
    )


def _processor_with_marker(marker, threshold: float = 0.9) -> MemoryPressureNudgeProcessor:
    """Processor with the pressure mark replaced by a stub (no DB)."""
    processor = MemoryPressureNudgeProcessor(threshold=threshold)
    processor._mark_pressure = marker  # type: ignore[method-assign]
    return processor


async def _run(monkeypatch: pytest.MonkeyPatch, processor, units, ctx):
    monkeypatch.setattr(settings, "MEMORY_PRESSURE_NUDGE_ENABLED", True)
    ContextProcessorChain([processor], session_state=ctx.state)
    return await processor.process(units, ctx)


# ---------------------------------------------------------------------------
# Latched injection (spec: 首次跨越 / 不重复 / 回落重跨)
# ---------------------------------------------------------------------------


async def test_injects_once_per_crossing(monkeypatch: pytest.MonkeyPatch) -> None:
    marks: list[bool] = []

    async def marker(_ctx) -> bool:
        marks.append(True)
        return True

    processor = _processor_with_marker(marker)
    state: dict = {}
    units = unitize([_user("x" * 4000)])  # ~1000 tokens > 90% of 100

    out, result = await _run(monkeypatch, processor, units, _ctx(state=state))
    assert result.metadata["nudged"] is True
    assert result.metadata["marked"] is True
    flat = flatten_units(out)
    directive = next(m for m in flat if m["role"] == "user" and m["content"].startswith("[memory_pressure]"))
    assert "tokens" in directive["content"] and "persist" in directive["content"]

    # Same crossing: latched, no repeat, no re-mark.
    out2, result2 = await _run(monkeypatch, processor, units, _ctx(state=state))
    assert result2.metadata.get("latched") is True
    assert flatten_units(out2) == flatten_units(units)
    assert len(marks) == 1


async def test_latch_resets_after_falling_below(monkeypatch: pytest.MonkeyPatch) -> None:
    async def marker(_ctx) -> bool:
        return False

    processor = _processor_with_marker(marker)
    state: dict = {}
    big = unitize([_user("x" * 4000)])
    small = unitize([_user("tiny")])

    await _run(monkeypatch, processor, big, _ctx(state=state))
    out, result = await _run(monkeypatch, processor, small, _ctx(state=state))
    assert result.metadata["nudged"] is False  # below threshold, latch reset
    out2, result2 = await _run(monkeypatch, processor, big, _ctx(state=state))
    assert result2.metadata["nudged"] is True  # fresh crossing injects again


async def test_below_threshold_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def marker(_ctx) -> bool:
        raise AssertionError("marker must not fire below threshold")

    processor = _processor_with_marker(marker)
    out, result = await _run(monkeypatch, processor, unitize([_user("tiny")]), _ctx())
    assert result.metadata["nudged"] is False
    assert flatten_units(out) == flatten_units(unitize([_user("tiny")]))


# ---------------------------------------------------------------------------
# KV-cache discipline + degradation (spec: 缓存纪律 / 降级)
# ---------------------------------------------------------------------------


async def test_injection_appends_tail_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def marker(_ctx) -> bool:
        return False

    processor = _processor_with_marker(marker)
    units = unitize([{"role": "system", "content": "sys"}, _user("x" * 4000)])
    out, _ = await _run(monkeypatch, processor, units, _ctx())
    # The protected prefix (system head) is untouched; the hint is appended.
    assert flatten_units(out)[:2] == flatten_units(units)
    assert len(flatten_units(out)) == 3


async def test_marker_failure_degrades_to_hint_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def marker(_ctx) -> bool:
        raise RuntimeError("db down")

    processor = _processor_with_marker(marker)
    out, result = await _run(monkeypatch, processor, unitize([_user("x" * 4000)]), _ctx())
    assert result.metadata["nudged"] is True
    assert "marked" not in result.metadata  # degraded, hint still injected


async def test_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MEMORY_PRESSURE_NUDGE_ENABLED", False)
    processor = MemoryPressureNudgeProcessor()
    out, result = await processor.process(unitize([_user("x" * 4000)]), _ctx())
    assert result.metadata["reason"] == "disabled"
    assert flatten_units(out) == flatten_units(unitize([_user("x" * 4000)]))


# ---------------------------------------------------------------------------
# Policy registry wiring (spec: 阈值在 budget_warn 之上)
# ---------------------------------------------------------------------------


def test_registry_contains_nudge() -> None:
    assert "memory_pressure_nudge" in PROCESSOR_REGISTRY
    spec = validate_policy_spec(["budget_warn", "memory_pressure_nudge"])
    assert spec[1]["type"] == "memory_pressure_nudge"


def test_threshold_must_sit_above_warn() -> None:
    with pytest.raises(Exception, match="strictly above"):
        validate_policy_spec([{"type": "memory_pressure_nudge", "params": {"threshold": 0.5}}])
    validate_policy_spec([{"type": "memory_pressure_nudge", "params": {"threshold": 0.95}}])
