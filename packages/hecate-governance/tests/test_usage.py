"""Tests for budget reservation/settlement semantics (task 1.3)."""

import asyncio

import pytest
from hecate_governance.usage import (
    BudgetGate,
    MemoryBudgetLedger,
    ReleaseStatus,
    ReservationStatus,
    SettleStatus,
    UsageRecord,
    UsageStatus,
)


class TestReserve:
    async def test_concurrent_over_reservation_admits_within_budget(self) -> None:
        # Limit 100, two concurrent 80-unit calls: exactly one gets in.
        ledger = MemoryBudgetLedger(limit=100)
        outcomes = await asyncio.gather(
            ledger.reserve("tool-a", 80),
            ledger.reserve("tool-b", 80),
        )
        statuses = {outcome.status for outcome in outcomes}
        assert statuses == {ReservationStatus.RESERVED, ReservationStatus.INSUFFICIENT}
        assert await ledger.remaining() == 20

    async def test_duplicate_reserve_same_amount_idempotent(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        first = await ledger.reserve("tool-a", 30)
        second = await ledger.reserve("tool-a", 30)
        assert first.status is ReservationStatus.RESERVED
        assert second.status is ReservationStatus.RESERVED
        assert await ledger.remaining() == 70

    async def test_duplicate_reserve_different_amount_conflict(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 30)
        outcome = await ledger.reserve("tool-a", 50)
        assert outcome.status is ReservationStatus.CONFLICT
        assert await ledger.remaining() == 70

    async def test_insufficient_denies_and_keeps_headroom(self) -> None:
        ledger = MemoryBudgetLedger(limit=50)
        outcome = await ledger.reserve("tool-a", 60)
        assert outcome.status is ReservationStatus.INSUFFICIENT
        assert outcome.remaining == 50

    async def test_release_before_dispatch_restores_headroom(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 80)
        released = await ledger.release("tool-a")
        assert released is ReleaseStatus.RELEASED
        assert await ledger.remaining() == 100
        again = await ledger.reserve("tool-b", 80)
        assert again.status is ReservationStatus.RESERVED

    async def test_release_without_reservation(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        assert await ledger.release("tool-x") is ReleaseStatus.NO_RESERVATION


class TestSettle:
    async def test_settle_refunds_unused_reservation(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 50)
        outcome = await ledger.settle(UsageRecord("tool-a", 30, UsageStatus.ACTUAL))
        assert outcome.status is SettleStatus.SETTLED
        assert await ledger.remaining() == 70

    async def test_settle_charges_overrun(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 10)
        outcome = await ledger.settle(UsageRecord("tool-a", 40, UsageStatus.ACTUAL))
        assert outcome.status is SettleStatus.SETTLED
        assert await ledger.remaining() == 60

    async def test_duplicate_settle_idempotent(self) -> None:
        # The same invocation_id reported twice (provider retry) records once.
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 50)
        await ledger.settle(UsageRecord("tool-a", 30, UsageStatus.ACTUAL))
        again = await ledger.settle(UsageRecord("tool-a", 30, UsageStatus.ACTUAL))
        assert again.status is SettleStatus.IDEMPOTENT_CONFIRMED
        record = await ledger.usage_for("tool-a")
        assert record is not None and record.units == 30
        assert await ledger.remaining() == 70

    async def test_late_revision_merges_into_same_entry(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 50)
        await ledger.settle(UsageRecord("tool-a", 20, UsageStatus.ESTIMATED))
        revised = await ledger.settle(UsageRecord("tool-a", 35, UsageStatus.ACTUAL))
        assert revised.status is SettleStatus.REVISED
        record = await ledger.usage_for("tool-a")
        assert record is not None
        assert record.units == 35 and record.status is UsageStatus.ACTUAL
        assert await ledger.remaining() == 65

    async def test_unknown_usage_not_zero_and_holds_reservation(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 50)
        outcome = await ledger.settle(UsageRecord("tool-a", 0, UsageStatus.UNKNOWN))
        assert outcome.status is SettleStatus.SETTLED
        record = await ledger.usage_for("tool-a")
        assert record is not None and record.status is UsageStatus.UNKNOWN
        # After settlement the hold is moot, but before it the hold blocks release.
        assert await ledger.remaining() == 50  # unknown usage retains the reservation

    async def test_hold_blocks_release_until_reconciliation(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 50)
        await ledger.hold_for_reconciliation("tool-a")
        assert await ledger.release("tool-a") is ReleaseStatus.HELD_FOR_RECONCILIATION
        assert await ledger.remaining() == 50
        # Reconciliation settles with the actual figure and clears the hold.
        await ledger.settle(UsageRecord("tool-a", 20, UsageStatus.ACTUAL))
        assert await ledger.remaining() == 80
        assert await ledger.release("tool-a") is ReleaseStatus.ALREADY_SETTLED

    async def test_clean_cancel_releases(self) -> None:
        # Cancel with no unknown usage: reservation released, not held.
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 50)
        assert await ledger.release("tool-a") is ReleaseStatus.RELEASED


class TestGateFailClosed:
    async def test_reserve_storage_failure_denies(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        gate = BudgetGate(ledger)
        ledger.fail_next_access(RuntimeError("store unavailable"))
        outcome = await gate.ensure_budget("tool-a", 10)
        assert outcome.status is ReservationStatus.INSUFFICIENT
        # Nothing was committed by the failed call.
        assert await ledger.remaining() == 100

    async def test_settle_storage_failure_conflicts(self) -> None:
        ledger = MemoryBudgetLedger(limit=100)
        await ledger.reserve("tool-a", 50)
        gate = BudgetGate(ledger)
        ledger.fail_next_access(RuntimeError("store unavailable"))
        outcome = await gate.settle(UsageRecord("tool-a", 10, UsageStatus.ACTUAL))
        assert outcome.status is SettleStatus.CONFLICT
        assert await ledger.usage_for("tool-a") is None

    def test_negative_limit_rejected(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            MemoryBudgetLedger(limit=-1)
