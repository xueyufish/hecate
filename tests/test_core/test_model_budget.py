"""Model-call budget boundary (task 4.4): four settlement points."""

from __future__ import annotations

import pytest
from hecate_governance.usage import MemoryBudgetLedger, UsageStatus

from hecate.core.composition.governance import (
    BudgetRefusedError,
    GovernanceMode,
    GovernanceService,
    PlatformGovernance,
)
from hecate.core.composition.runtime_port_adapter import _ProductionRuntimePort
from tests.test_core.test_governance_facts import _AllowProfile


def _governance(limit: float = 1000.0) -> tuple[PlatformGovernance, MemoryBudgetLedger]:
    ledger = MemoryBudgetLedger(limit=int(limit))
    governance = PlatformGovernance(
        GovernanceService(_AllowProfile()),
        GovernanceMode.ENFORCED,
        model_budget=__import__("hecate_governance.usage", fromlist=["BudgetGate"]).BudgetGate(ledger),
    )
    return governance, ledger


class TestModelBudgetLifecycle:
    async def test_settled_actual_usage(self) -> None:
        governance, ledger = _governance()
        invocation = await governance.begin_model_call(estimated_units=10)
        assert invocation is not None and invocation.startswith("model-")
        await governance.settle_model_call(invocation, units=42, status="actual")
        record = await ledger.usage_for(invocation)
        assert record is not None and record.units == 42
        assert record.status is UsageStatus.ACTUAL

    async def test_estimated_when_provider_usage_missing(self) -> None:
        governance, ledger = _governance()
        invocation = await governance.begin_model_call()
        await governance.settle_model_call(invocation, units=7, status="estimated")
        record = await ledger.usage_for(invocation)
        assert record is not None and record.units == 7

    async def test_exception_leaves_explicit_unknown_account(self) -> None:
        governance, ledger = _governance()
        invocation = await governance.begin_model_call()
        # Stream dies mid-call: the unknown account is recorded, never zero.
        await governance.hold_model_call_unknown(invocation)
        record = await ledger.usage_for(invocation)
        assert record is not None and record.status is UsageStatus.UNKNOWN
        # The hold blocks release until reconciliation.
        from hecate_governance.usage import ReleaseStatus

        assert await ledger.release(invocation) is ReleaseStatus.HELD_FOR_RECONCILIATION

    async def test_exhausted_budget_refuses_new_call(self) -> None:
        governance, _ = _governance(limit=5.0)
        await governance.begin_model_call(estimated_units=5)
        with pytest.raises(BudgetRefusedError, match="refused"):
            await governance.begin_model_call(estimated_units=5)

    async def test_unmetered_posture_returns_none(self) -> None:
        governance = PlatformGovernance(GovernanceService(_AllowProfile()), GovernanceMode.ENFORCED)
        assert await governance.begin_model_call() is None


class TestPortBoundary:
    def _port(self, governance: PlatformGovernance, llm) -> _ProductionRuntimePort:
        class _Registry:
            async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003
                return "ok"

        return _ProductionRuntimePort(db=None, llm_service=llm, tool_registry=_Registry(), governance=governance)

    async def test_normal_stream_settles(self) -> None:
        governance, ledger = _governance()

        class _LLM:
            async def chat_stream(self, **kwargs):
                yield {"content": "hello"}
                yield {"content": None, "tool_calls": None}

        port = self._port(governance, _LLM())
        chunks = [
            c async for c in port.llm_invoke([{"role": "user", "content": "hi"}], {"model": "m", "max_tokens": 10})
        ]
        assert chunks == ["hello"]
        settled = [await ledger.usage_for(k) for k in _all_keys(ledger)]
        assert any(r is not None and r.units > 0 for r in settled)

    async def test_mid_stream_exception_holds_unknown(self) -> None:
        governance, ledger = _governance()

        class _BrokenLLM:
            async def chat_stream(self, **kwargs):
                yield {"content": "partial"}
                raise ConnectionError("stream died")

        port = self._port(governance, _BrokenLLM())
        with pytest.raises(ConnectionError):
            _ = [
                c async for c in port.llm_invoke([{"role": "user", "content": "hi"}], {"model": "m", "max_tokens": 10})
            ]
        records = [await ledger.usage_for(k) for k in _all_keys(ledger)]
        assert any(r is not None and r.status is UsageStatus.UNKNOWN for r in records)

    async def test_cancellation_keeps_the_entire_reservation(self) -> None:
        import asyncio

        governance, ledger = _governance()

        class StubCancelledLLM:
            async def chat_stream(self, **kwargs):
                yield {"content": "partial"}
                raise asyncio.CancelledError

        port = self._port(governance, StubCancelledLLM())
        with pytest.raises(asyncio.CancelledError):
            _ = [chunk async for chunk in port.llm_invoke([{"content": "hi"}], {"max_tokens": 10})]
        assert await ledger.remaining() == 989
        for key in _all_keys(ledger):
            assert (await ledger.usage_for(key)).status is UsageStatus.UNKNOWN

    async def test_structured_stream_settles_before_terminal_yield(self) -> None:
        governance, ledger = _governance()

        class StubLLM:
            async def chat_stream(self, **kwargs):
                yield {"content": "hello"}

        stream = self._port(governance, StubLLM()).llm_invoke_structured([{"content": "hi"}], {"max_tokens": 10})
        assert (await anext(stream))["content"] == "hello"
        assert (await anext(stream))["content"] is None
        assert await ledger.remaining() == 997
        await stream.aclose()
        assert await ledger.remaining() == 997

    async def test_direct_chat_uses_same_budget_gate(self) -> None:
        from types import SimpleNamespace

        from hecate.core.composition.governance import GovernedModelService

        governance, ledger = _governance()

        class StubLLM:
            async def chat(self, **kwargs):
                return SimpleNamespace(content="hi", usage={"total_tokens": 3})

        await GovernedModelService(StubLLM(), governance).chat(messages=[{"content": "hi"}], max_tokens=10)
        assert await ledger.remaining() == 997


def _all_keys(ledger: MemoryBudgetLedger) -> list[str]:
    return list(ledger._accounts.keys())  # noqa: SLF001
