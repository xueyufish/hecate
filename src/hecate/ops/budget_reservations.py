"""Quota-backed budget ledger (step7, task 4.1).

Maps the governance ``BudgetLedger`` seam onto the platform quota storage:
reservations are rows in ``quota_reservations`` (one open reservation per
``invocation_key`` via a partial unique index), settlements record usage
against the applicable quotas idempotently, and unknown outcomes hold the
reservation until reconciliation. The remaining budget is the quota limit
minus settled usage minus open (unreleased, unsettled) reservations.

The workspace/entry switch lives at the composition layer: callers build one
ledger per (workspace, entry) scope they choose to meter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from hecate_governance.usage import (
    BudgetLedger,
    ReleaseStatus,
    ReservationOutcome,
    ReservationStatus,
    SettleOutcome,
    SettleStatus,
    UsageRecord,
    UsageStatus,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.models.quota import QuotaModel, QuotaReservationModel, QuotaUsageModel


class QuotaBudgetLedger(BudgetLedger):
    """Budget authority over the platform quota storage for one scope."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        resource_type: str = "cost",
        scope: str = "workspace",
        window_type: str = "monthly",
        session_factory: async_sessionmaker | None = None,
        unit_scale: float = 1.0,
    ) -> None:
        self._factory = session_factory
        self._unit_scale = unit_scale
        self._db = db
        self._workspace_id = workspace_id
        self._resource_type = resource_type
        self._scope = scope
        self._window_type = window_type

    def _transaction_ledger(self, db: AsyncSession) -> QuotaBudgetLedger:
        return QuotaBudgetLedger(
            db,
            workspace_id=self._workspace_id,
            resource_type=self._resource_type,
            scope=self._scope,
            window_type=self._window_type,
            unit_scale=self._unit_scale,
        )

    async def _lock_scope(self) -> None:
        # A write serializes SQLite as well as PostgreSQL; SELECT FOR UPDATE
        # alone is ignored by SQLite. Lock before reading usage or reservations.
        await self._db.execute(
            update(QuotaModel)
            .where(
                QuotaModel.resource_type == self._resource_type,
                QuotaModel.scope == self._scope,
                QuotaModel.scope_id == self._workspace_id,
                QuotaModel.window_type == self._window_type,
            )
            .values(limit_value=QuotaModel.limit_value)
        )

    async def _quota(self) -> QuotaModel | None:
        result = await self._db.execute(
            select(QuotaModel)
            .where(
                QuotaModel.resource_type == self._resource_type,
                QuotaModel.scope == self._scope,
                QuotaModel.scope_id == self._workspace_id,
                QuotaModel.window_type == self._window_type,
                QuotaModel.enabled.is_(True),
                QuotaModel.deleted.is_(False),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _usage(self, quota: QuotaModel) -> QuotaUsageModel:
        now = datetime.now(UTC)
        result = await self._db.execute(
            select(QuotaUsageModel).where(
                QuotaUsageModel.quota_id == quota.id,
                QuotaUsageModel.period_start <= now,
                QuotaUsageModel.period_end > now,
                QuotaUsageModel.deleted.is_(False),
            )
        )
        usage = result.scalar_one_or_none()
        if usage is not None:
            return usage
        period_end = _period_end(now, self._window_type)
        usage = QuotaUsageModel(
            quota_id=quota.id,
            period_start=now,
            period_end=period_end,
            used_value=0.0,
            workspace_id=self._workspace_id,
        )
        self._db.add(usage)
        await self._db.flush()
        return usage

    async def _open_reservations_total(self) -> float:
        result = await self._db.execute(
            select(QuotaReservationModel).where(
                QuotaReservationModel.resource_type == self._resource_type,
                QuotaReservationModel.scope == self._scope,
                QuotaReservationModel.scope_id == self._workspace_id,
                QuotaReservationModel.window_type == self._window_type,
                QuotaReservationModel.released.is_(False),
                QuotaReservationModel.settled.is_(False),
                QuotaReservationModel.deleted.is_(False),
            )
        )
        return sum(row.units for row in result.scalars())

    async def _reservation(self, invocation_key: str) -> QuotaReservationModel | None:
        result = await self._db.execute(
            select(QuotaReservationModel)
            .where(
                QuotaReservationModel.invocation_key == invocation_key,
                QuotaReservationModel.resource_type == self._resource_type,
                QuotaReservationModel.scope == self._scope,
                QuotaReservationModel.scope_id == self._workspace_id,
                QuotaReservationModel.deleted.is_(False),
            )
            .order_by(QuotaReservationModel.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    def _remaining_window(self, quota: QuotaModel, used: float, open_holds: float) -> float:
        return max(0.0, quota.limit_value - used - open_holds)

    async def reserve(self, invocation_id: str, units: int) -> ReservationOutcome:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await self._transaction_ledger(db).reserve(invocation_id, units)
        await self._lock_scope()
        if units < 0:
            raise ValueError("reservation units must be non-negative")
        units = units * self._unit_scale
        existing = await self._reservation(invocation_id)
        if existing is not None and not existing.released and not existing.settled:
            if existing.units == units:
                quota = await self._quota()
                used = (await self._usage(quota)).used_value if quota else 0.0
                remaining = self._remaining_window(quota, used, await self._open_reservations_total()) if quota else 0.0
                return ReservationOutcome(ReservationStatus.RESERVED, remaining)
            return ReservationOutcome(ReservationStatus.CONFLICT, 0.0)
        if existing is not None and existing.settled:
            return ReservationOutcome(ReservationStatus.CONFLICT, 0.0)
        quota = await self._quota()
        if quota is None:
            # No quota definition for this scope: unlimited headroom.
            self._db.add(
                QuotaReservationModel(
                    invocation_key=invocation_id,
                    resource_type=self._resource_type,
                    scope=self._scope,
                    scope_id=self._workspace_id,
                    window_type=self._window_type,
                    units=float(units),
                )
            )
            await self._db.flush()
            return ReservationOutcome(ReservationStatus.RESERVED, float("inf"))
        usage = await self._usage(quota)
        remaining = self._remaining_window(quota, usage.used_value, await self._open_reservations_total())
        if units > remaining:
            return ReservationOutcome(ReservationStatus.INSUFFICIENT, remaining)
        self._db.add(
            QuotaReservationModel(
                invocation_key=invocation_id,
                resource_type=self._resource_type,
                scope=self._scope,
                scope_id=self._workspace_id,
                window_type=self._window_type,
                units=float(units),
            )
        )
        await self._db.flush()
        return ReservationOutcome(ReservationStatus.RESERVED, remaining - units)

    async def release(self, invocation_id: str) -> ReleaseStatus:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await self._transaction_ledger(db).release(invocation_id)
        await self._lock_scope()
        reservation = await self._reservation(invocation_id)
        if reservation is None or reservation.released or reservation.settled:
            return ReleaseStatus.NO_RESERVATION
        if reservation.unknown_hold:
            return ReleaseStatus.HELD_FOR_RECONCILIATION
        reservation.released = True
        await self._db.flush()
        return ReleaseStatus.RELEASED

    async def hold_for_reconciliation(self, invocation_id: str) -> bool:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await self._transaction_ledger(db).hold_for_reconciliation(invocation_id)
        await self._lock_scope()
        reservation = await self._reservation(invocation_id)
        if reservation is None or reservation.released or reservation.settled:
            return False
        reservation.unknown_hold = True
        await self._db.flush()
        return True

    async def settle(self, record: UsageRecord) -> SettleOutcome:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await self._transaction_ledger(db).settle(record)
        await self._lock_scope()
        reservation = await self._reservation(record.invocation_id)
        if reservation is None:
            return SettleOutcome(SettleStatus.CONFLICT, None)
        if reservation.settled and record.status is UsageStatus.UNKNOWN:
            return SettleOutcome(SettleStatus.CONFLICT, None)
        if record.status is UsageStatus.UNKNOWN:
            reservation.unknown_hold = True
            reservation.usage_status = record.status.value
            reservation.detail = record.detail
            await self._db.flush()
            return SettleOutcome(SettleStatus.SETTLED, record)
        units = record.units * self._unit_scale
        previous_status = reservation.usage_status
        reservation.usage_status = record.status.value
        reservation.detail = record.detail
        if reservation is not None and reservation.settled:
            if reservation.settled_units == units and previous_status == record.status.value:
                return SettleOutcome(SettleStatus.IDEMPOTENT_CONFIRMED, record)
            # Late revision: merge into the same entry, refund/charge the delta.
            delta = units - (reservation.settled_units or 0.0)
            reservation.settled_units = float(units)
            await self._apply_usage_delta(delta)
            await self._db.flush()
            return SettleOutcome(SettleStatus.REVISED, record)
        quota = await self._quota()
        if quota is not None:
            usage = await self._usage(quota)
            usage.used_value += units
        if reservation is not None:
            reservation.settled = True
            reservation.settled_units = float(units)
            reservation.unknown_hold = False
        await self._db.flush()
        return SettleOutcome(SettleStatus.SETTLED, record)

    async def _apply_usage_delta(self, delta: float) -> None:
        quota = await self._quota()
        if quota is None:
            return
        usage = await self._usage(quota)
        usage.used_value = max(0.0, usage.used_value + delta)

    async def remaining(self) -> float:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await self._transaction_ledger(db).remaining()
        quota = await self._quota()
        if quota is None:
            return float("inf")
        usage = await self._usage(quota)
        return self._remaining_window(quota, usage.used_value, await self._open_reservations_total())

    async def usage_for(self, invocation_id: str) -> UsageRecord | None:
        if self._factory is not None:
            async with self._factory.begin() as db:
                return await self._transaction_ledger(db).usage_for(invocation_id)
        reservation = await self._reservation(invocation_id)
        if reservation is None or reservation.usage_status is None:
            return None
        return UsageRecord(
            invocation_id=reservation.invocation_key,
            units=round((reservation.settled_units or 0) / self._unit_scale),
            status=_status_from_record(reservation),
            detail=reservation.detail or "",
        )


def _status_from_record(reservation: QuotaReservationModel) -> UsageStatus:
    return UsageStatus(reservation.usage_status or "actual")


def _period_end(now: datetime, window_type: str) -> datetime:
    if window_type == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if window_type == "rolling_minute":
        return now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return (
        now.replace(month=now.month % 12 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        if now.month < 12
        else now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    )
