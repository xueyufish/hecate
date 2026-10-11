"""QuotaBudgetLedger: reservation/settlement semantics on quota storage (4.1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from hecate_governance.usage import (
    BudgetGate,
    ReleaseStatus,
    ReservationStatus,
    SettleStatus,
    UsageRecord,
    UsageStatus,
)
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.quota import QuotaModel, QuotaResourceType, QuotaScope, QuotaWindowType
from hecate.ops.budget_reservations import QuotaBudgetLedger

WS = uuid.UUID(int=42)
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


async def _quota(db: AsyncSession, limit: float = 100.0) -> QuotaModel:
    quota = QuotaModel(
        name="test-quota",
        resource_type=QuotaResourceType.COST,
        scope=QuotaScope.WORKSPACE,
        scope_id=WS,
        limit_value=limit,
        window_type=QuotaWindowType.MONTHLY,
        workspace_id=WS,
    )
    db.add(quota)
    await db.flush()
    return quota


def _ledger(db: AsyncSession) -> QuotaBudgetLedger:
    return QuotaBudgetLedger(db, workspace_id=WS)


class TestReserve:
    async def test_reserve_insufficient_and_release_restores(self, db_session: AsyncSession) -> None:
        await _quota(db_session, limit=50.0)
        ledger = _ledger(db_session)
        denied = await ledger.reserve("tool-big", 60)
        assert denied.status is ReservationStatus.INSUFFICIENT
        reserved = await ledger.reserve("tool-big", 40)
        assert reserved.status is ReservationStatus.RESERVED
        assert await ledger.remaining() == pytest.approx(10.0)
        released = await ledger.release("tool-big")
        assert released is ReleaseStatus.RELEASED
        assert await ledger.remaining() == pytest.approx(50.0)

    async def test_open_reservation_counts_against_headroom(self, db_session: AsyncSession) -> None:
        await _quota(db_session, limit=100.0)
        ledger = _ledger(db_session)
        await ledger.reserve("tool-a", 80)
        second = await ledger.reserve("tool-b", 30)
        # Concurrent-style over-reservation: only budget-worth fits.
        assert second.status is ReservationStatus.INSUFFICIENT
        assert second.remaining == pytest.approx(20.0)

    async def test_duplicate_reserve_same_amount_idempotent(self, db_session: AsyncSession) -> None:
        await _quota(db_session)
        ledger = _ledger(db_session)
        await ledger.reserve("tool-a", 30)
        again = await ledger.reserve("tool-a", 30)
        assert again.status is ReservationStatus.RESERVED
        assert await ledger.remaining() == pytest.approx(70.0)

    async def test_no_quota_definition_is_unlimited(self, db_session: AsyncSession) -> None:
        ledger = _ledger(db_session)
        outcome = await ledger.reserve("tool-a", 10_000)
        assert outcome.status is ReservationStatus.RESERVED
        assert outcome.remaining == float("inf")


class TestSettle:
    async def test_settle_refunds_unused_reservation(self, db_session: AsyncSession) -> None:
        await _quota(db_session)
        ledger = _ledger(db_session)
        await ledger.reserve("tool-a", 50)
        outcome = await ledger.settle(UsageRecord("tool-a", 30, UsageStatus.ACTUAL))
        assert outcome.status is SettleStatus.SETTLED
        assert await ledger.remaining() == pytest.approx(70.0)
        record = await ledger.usage_for("tool-a")
        assert record is not None and record.units == 30

    async def test_duplicate_settle_idempotent(self, db_session: AsyncSession) -> None:
        await _quota(db_session)
        ledger = _ledger(db_session)
        await ledger.reserve("tool-a", 50)
        await ledger.settle(UsageRecord("tool-a", 30, UsageStatus.ACTUAL))
        again = await ledger.settle(UsageRecord("tool-a", 30, UsageStatus.ACTUAL))
        assert again.status is SettleStatus.IDEMPOTENT_CONFIRMED
        assert await ledger.remaining() == pytest.approx(70.0)

    async def test_late_revision_merges(self, db_session: AsyncSession) -> None:
        await _quota(db_session)
        ledger = _ledger(db_session)
        await ledger.reserve("tool-a", 50)
        await ledger.settle(UsageRecord("tool-a", 20, UsageStatus.ESTIMATED))
        revised = await ledger.settle(UsageRecord("tool-a", 35, UsageStatus.ACTUAL))
        assert revised.status is SettleStatus.REVISED
        assert await ledger.remaining() == pytest.approx(65.0)
        record = await ledger.usage_for("tool-a")
        assert record is not None and record.units == 35

    async def test_unknown_usage_hold_blocks_release(self, db_session: AsyncSession) -> None:
        await _quota(db_session)
        ledger = _ledger(db_session)
        await ledger.reserve("tool-a", 50)
        assert await ledger.hold_for_reconciliation("tool-a") is True
        assert await ledger.release("tool-a") is ReleaseStatus.HELD_FOR_RECONCILIATION
        assert await ledger.remaining() == pytest.approx(50.0)
        await ledger.settle(UsageRecord("tool-a", 20, UsageStatus.ACTUAL))
        assert await ledger.remaining() == pytest.approx(80.0)

    async def test_clean_cancel_releases(self, db_session: AsyncSession) -> None:
        await _quota(db_session)
        ledger = _ledger(db_session)
        await ledger.reserve("tool-a", 50)
        assert await ledger.release("tool-a") is ReleaseStatus.RELEASED


class TestGate:
    async def test_gate_fail_closed_on_storage_error(self, db_session: AsyncSession) -> None:
        await _quota(db_session)
        ledger = _ledger(db_session)
        gate = BudgetGate(ledger)
        # Force a failure: close the underlying session's connection path.
        await db_session.rollback()
        outcome = await gate.ensure_budget("tool-x", 5)
        # A rolled-back session still works for new queries in this fixture;
        # simulate failure instead by breaking the quota lookup.
        ledger._db = None  # type: ignore[assignment]
        broken = BudgetGate(QuotaBudgetLedger(None, workspace_id=WS))  # type: ignore[arg-type]
        outcome = await broken.ensure_budget("tool-x", 5)
        assert outcome.status is ReservationStatus.INSUFFICIENT


class TestTimeWindow:
    async def test_period_end_helpers(self) -> None:
        from hecate.ops.budget_reservations import _period_end

        daily = _period_end(NOW, "daily")
        assert daily.hour == 0
        assert daily.day == NOW.day + 1
        monthly = _period_end(datetime(2026, 1, 15, tzinfo=UTC), "monthly")
        assert (monthly.year, monthly.month, monthly.day) == (2026, 2, 1)
