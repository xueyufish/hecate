"""Tests for the memory integration processors (agent-memory-tools).

Covers ``MemoryPrefetchProcessor`` (flag gating, budget skip, provider
failure degradation, tail injection) and
``RetrievalEscalationHintProcessor`` (marker consumption, debounce) plus the
default-chain composition.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from hecate.core.composition import memory_provider as mp_mod
from hecate.core.config import settings
from hecate.runtime.context_processors import (
    _ESCALATION_LAST_HINT,
    ChainContext,
    ContextUnit,
    MemoryPrefetchProcessor,
    RetrievalEscalationHintProcessor,
    default_chain_processors,
    flatten_units,
    unitize,
)

_WS = str(uuid.uuid4())
_AGENT = str(uuid.uuid4())


def _ctx(**overrides: Any) -> ChainContext:
    from hecate.runtime.context_processors import HeuristicTokenEstimator

    defaults: dict[str, Any] = {
        "budget": 100_000,
        "estimator": HeuristicTokenEstimator(),
        "state": {},
        "execution_context": {"workspace_id": _WS, "agent_id": _AGENT, "session_id": "s1"},
        "session_id": "s1",
    }
    defaults.update(overrides)
    return ChainContext(**defaults)


def _units() -> list[ContextUnit]:
    return unitize(
        [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What did we decide about the deployment target?"},
        ]
    )


@dataclass
class _Entry:
    content: str
    source: str = "knowledge_memory"
    score: float = 0.9
    metadata: dict = field(default_factory=dict)


class _StubProvider:
    def __init__(self, entries: list[_Entry] | None = None, fail: bool = False) -> None:
        self.entries = entries or []
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def capabilities(self) -> frozenset[str]:
        return frozenset({"search", "prefetch"})

    async def prefetch(self, **kwargs: Any) -> list[_Entry]:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("provider down")
        return self.entries


@pytest.fixture(autouse=True)
def _reset_cooldown() -> Any:
    _ESCALATION_LAST_HINT.clear()
    yield
    _ESCALATION_LAST_HINT.clear()


class TestMemoryPrefetch:
    async def test_disabled_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "MEMORY_PREFETCH_ENABLED", False)
        units = _units()
        out, result = await MemoryPrefetchProcessor().process(units, _ctx())
        assert out == units
        assert result.metadata["injected"] is False
        assert result.metadata["reason"] == "disabled"

    async def test_over_budget_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "MEMORY_PREFETCH_ENABLED", True)
        units = _units()
        out, result = await MemoryPrefetchProcessor().process(units, _ctx(budget=1))
        assert out == units
        assert result.metadata["reason"] == "over_budget"

    async def test_missing_scope_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "MEMORY_PREFETCH_ENABLED", True)
        units = _units()
        out, result = await MemoryPrefetchProcessor().process(units, _ctx(execution_context={"session_id": "s1"}))
        assert result.metadata["reason"] == "no_scope"

    async def test_provider_error_degrades(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "MEMORY_PREFETCH_ENABLED", True)
        monkeypatch.setattr(mp_mod, "resolve_memory_provider", lambda: _StubProvider(fail=True))
        units = _units()
        out, result = await MemoryPrefetchProcessor().process(units, _ctx())
        assert out == units
        assert result.metadata["reason"] == "provider_error"

    async def test_injects_at_tail_with_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "MEMORY_PREFETCH_ENABLED", True)
        stub = _StubProvider([_Entry("Deployment target is staging"), _Entry("User prefers tags")])
        monkeypatch.setattr(mp_mod, "resolve_memory_provider", lambda: stub)
        processor = MemoryPrefetchProcessor()
        units = _units()
        out, result = await processor.process(units, _ctx())

        assert result.metadata["injected"] is True
        assert result.metadata["entries"] == 2
        flat = flatten_units(out)
        assert len(flat) == 3
        injected = flat[-1]
        assert injected["role"] == "user"
        assert injected["content"].startswith("[memory_context]")
        assert "staging" in injected["content"]
        # Provider received the recent exchange as the query.
        assert stub.calls and "deployment target" in stub.calls[0]["query_text"].lower()

    async def test_empty_entries_skips_injection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "MEMORY_PREFETCH_ENABLED", True)
        monkeypatch.setattr(mp_mod, "resolve_memory_provider", lambda: _StubProvider([]))
        baseline = _units()
        out, result = await MemoryPrefetchProcessor().process(baseline, _ctx())
        assert flatten_units(out) == flatten_units(baseline)
        assert result.metadata["reason"] == "empty"


class TestEscalationHint:
    async def test_no_marker_is_noop(self) -> None:
        baseline = _units()
        out, result = await RetrievalEscalationHintProcessor().process(baseline, _ctx())
        assert flatten_units(out) == flatten_units(baseline)
        assert result.metadata["hinted"] is False

    async def test_marker_injects_single_hint_and_consumes(self) -> None:
        ctx = _ctx()
        ctx.execution_context["memory_retrieval_low_signal"] = True
        processor = RetrievalEscalationHintProcessor()
        units = _units()

        out, result = await processor.process(units, ctx)
        assert result.metadata["hinted"] is True
        flat = flatten_units(out)
        assert flat[-1]["content"].startswith("[memory_hint]")
        # Marker consumed — a second pass in the same turn does not re-hint.
        out2, result2 = await processor.process(out, ctx)
        assert result2.metadata["hinted"] is False
        assert flat == flatten_units(out2)[: len(flat)]

    async def test_cooldown_suppresses_repeat_hints(self) -> None:
        ctx = _ctx()
        ctx.execution_context["memory_retrieval_low_signal"] = True
        processor = RetrievalEscalationHintProcessor()
        await processor.process(_units(), ctx)

        ctx.execution_context["memory_retrieval_low_signal"] = True
        _, second = await processor.process(_units(), ctx)
        assert second.metadata["hinted"] is False
        assert second.metadata["cooldown"] is True


class TestDefaultChain:
    def test_memory_processors_appended(self) -> None:
        names = [p.name for p in default_chain_processors()]
        assert names[-2:] == ["memory_prefetch", "retrieval_escalation_hint"]
        # The core lossiness ladder is untouched and ordered first.
        assert names[:6] == [
            "tool_result_truncation",
            "kv_cache_aware",
            "round_window",
            "offload",
            "compression",
            "terminate",
        ]

    async def test_flag_off_chain_end_to_end_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With flags off the chain output equals the legacy six-step chain."""
        from hecate.runtime.context_processors import ContextProcessorChain

        monkeypatch.setattr(settings, "MEMORY_PREFETCH_ENABLED", False)
        legacy = ContextProcessorChain(
            [
                p
                for p in default_chain_processors()
                if p.name.startswith(("tool_result", "kv", "round", "offload", "compression", "terminate"))
            ]
        )
        full = ContextProcessorChain(default_chain_processors())
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        report_legacy = await legacy.apply(messages, {}, None, "n1")
        report_full = await full.apply(messages, {}, None, "n1")
        assert report_full.messages == report_legacy.messages
        assert report_full.tokens_after == report_legacy.tokens_after
