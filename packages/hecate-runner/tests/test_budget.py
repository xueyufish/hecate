"""Local budget ledger and model-call refusal (task 4.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest_runner import write_profile
from hecate_durable.storage.store import SqlDurableStore
from hecate_governance.usage import (
    BudgetGate,
    ReleaseStatus,
    ReservationStatus,
    SettleStatus,
    UsageRecord,
    UsageStatus,
)
from hecate_runner.budget import BudgetExhaustedError, DurableBudgetLedger
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceStore
from hecate_runner.profile import load_profile


@pytest.fixture()
def ledger(tmp_path: Path) -> DurableBudgetLedger:
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'budget.db'}")
    store.create_schema()
    return DurableBudgetLedger(store.session_factory, limit=100.0)


class TestDurableBudgetLedger:
    async def test_reserve_release_restores_headroom(self, ledger: DurableBudgetLedger) -> None:
        reserved = await ledger.reserve("tool-a", 80)
        assert reserved.status is ReservationStatus.RESERVED
        assert await ledger.remaining() == pytest.approx(20.0)
        assert await ledger.release("tool-a") is ReleaseStatus.RELEASED
        assert await ledger.remaining() == pytest.approx(100.0)

    async def test_insufficient_denies_over_limit(self, ledger: DurableBudgetLedger) -> None:
        denied = await ledger.reserve("tool-big", 120)
        assert denied.status is ReservationStatus.INSUFFICIENT

    async def test_open_reservation_holds_headroom(self, ledger: DurableBudgetLedger) -> None:
        await ledger.reserve("tool-a", 80)
        second = await ledger.reserve("tool-b", 30)
        assert second.status is ReservationStatus.INSUFFICIENT
        assert second.remaining == pytest.approx(20.0)

    async def test_settle_idempotent_and_revisable(self, ledger: DurableBudgetLedger) -> None:
        await ledger.reserve("tool-a", 50)
        settled = await ledger.settle(UsageRecord("tool-a", 30, UsageStatus.ACTUAL))
        assert settled.status is SettleStatus.SETTLED
        again = await ledger.settle(UsageRecord("tool-a", 30, UsageStatus.ACTUAL))
        assert again.status is SettleStatus.IDEMPOTENT_CONFIRMED
        revised = await ledger.settle(UsageRecord("tool-a", 35, UsageStatus.ACTUAL))
        assert revised.status is SettleStatus.REVISED
        assert await ledger.remaining() == pytest.approx(65.0)

    async def test_unknown_hold_blocks_release(self, ledger: DurableBudgetLedger) -> None:
        await ledger.reserve("tool-a", 50)
        await ledger.hold_for_reconciliation("tool-a")
        assert await ledger.release("tool-a") is ReleaseStatus.HELD_FOR_RECONCILIATION
        assert await ledger.remaining() == pytest.approx(50.0)


class TestModelCallRefusal:
    def _engine(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: float) -> ExecutionEngine:
        monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")
        profile = load_profile(
            write_profile(
                tmp_path,
                durable={"database_url": f"sqlite:///{tmp_path / 'e.db'}", "workspace": "ws"},
            )
        )
        store = SqlDurableStore(f"sqlite:///{tmp_path / 'e.db'}")
        store.create_schema()
        ledger = DurableBudgetLedger(store.session_factory, limit=limit)
        evidence = EvidenceStore(profile.config.evidence_dir)

        async def dispatch(*args):  # noqa: ANN002, ANN003
            return {"status": "ok", "result": {}}

        return ExecutionEngine(profile, evidence, dispatch, budget_gate=BudgetGate(ledger))

    async def test_exhausted_budget_refuses_model_call(self, tmp_path, monkeypatch) -> None:
        engine = self._engine(tmp_path, monkeypatch, limit=0.5)
        from hecate_runner.engine import RunState

        state = RunState(run_id="r-1", status="running", principal="p", domains=())
        refusal = await engine._reserve_model_budget(state)
        assert refusal is not None and refusal.error is not None
        assert isinstance(refusal.error, BudgetExhaustedError)
        denials = [e for e in engine._evidence.query(kind="budget")]
        assert denials and denials[-1].outcome == "denied"

    async def test_available_budget_allows_model_call(self, tmp_path, monkeypatch) -> None:
        engine = self._engine(tmp_path, monkeypatch, limit=100.0)
        from hecate_runner.engine import RunState

        state = RunState(run_id="r-2", status="running", principal="p", domains=())
        assert await engine._reserve_model_budget(state) is None
