"""Local budget ledger for the standalone host (step7, task 4.2).

Maps the governance ``BudgetLedger`` seam onto the runner's durable storage:
reservations are rows in ``durable_budget_reservations`` (one open reservation
per ``invocation_key`` via a partial unique index), settlement records actual
usage idempotently, and unknown outcomes hold the reservation until
reconciliation. The remaining budget is the profile-configured limit minus
settled usage minus open reservations.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hecate_durable.storage.models import BudgetReservationRow, BudgetScopeRow
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
from sqlalchemy.exc import IntegrityError


class BudgetExhaustedError(RuntimeError):
    """A metered call was refused because the local budget is exhausted."""


class DurableBudgetLedger(BudgetLedger):
    """Budget authority over the runner's local durable storage."""

    def __init__(self, session_factory, *, limit: float) -> None:
        if limit < 0:
            raise ValueError("budget limit must be non-negative")
        self._session_factory = session_factory
        self._limit = limit
        with self._session_factory() as session:
            if session.get(BudgetScopeRow, "local") is None:
                try:
                    with session.begin_nested():
                        session.add(BudgetScopeRow(scope="local", version=0))
                        session.flush()
                    session.commit()
                except IntegrityError:
                    session.rollback()

    def _now(self) -> str:
        return datetime.now(tz=UTC).isoformat()

    def _sync_reserve(self, invocation_id: str, units: float) -> ReservationOutcome:
        with self._session_factory() as session:
            session.execute(
                update(BudgetScopeRow).where(BudgetScopeRow.scope == "local").values(version=BudgetScopeRow.version + 1)
            )
            row = session.execute(
                select(BudgetReservationRow)
                .where(BudgetReservationRow.invocation_key == invocation_id)
                .order_by(BudgetReservationRow.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is not None and row.settled:
                return ReservationOutcome(ReservationStatus.CONFLICT, self._sync_remaining(session))
            if row is not None and not row.released and not row.settled:
                if row.units == units:
                    return ReservationOutcome(ReservationStatus.RESERVED, self._sync_remaining(session))
                return ReservationOutcome(ReservationStatus.CONFLICT, self._sync_remaining(session))
            remaining = self._sync_remaining(session)
            if units > remaining:
                return ReservationOutcome(ReservationStatus.INSUFFICIENT, remaining)
            session.add(
                BudgetReservationRow(
                    invocation_key=invocation_id,
                    units=units,
                    recorded_at=self._now(),
                )
            )
            session.commit()
            return ReservationOutcome(ReservationStatus.RESERVED, remaining - units)

    def _sync_remaining(self, session) -> float:  # noqa: ANN001
        rows = session.execute(select(BudgetReservationRow)).scalars().all()
        settled = sum(r.settled_units or 0.0 for r in rows if r.settled)
        open_holds = sum(r.units for r in rows if not r.released and not r.settled)
        return max(0.0, self._limit - settled - open_holds)

    async def reserve(self, invocation_id: str, units: int) -> ReservationOutcome:
        if units < 0:
            raise ValueError("reservation units must be non-negative")
        return self._sync_reserve(invocation_id, float(units))

    async def release(self, invocation_id: str) -> ReleaseStatus:
        with self._session_factory() as session:
            session.execute(
                update(BudgetScopeRow).where(BudgetScopeRow.scope == "local").values(version=BudgetScopeRow.version + 1)
            )
            row = session.execute(
                select(BudgetReservationRow)
                .where(BudgetReservationRow.invocation_key == invocation_id)
                .order_by(BudgetReservationRow.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None or row.released or row.settled:
                return ReleaseStatus.NO_RESERVATION
            if row.unknown_hold:
                return ReleaseStatus.HELD_FOR_RECONCILIATION
            row.released = True
            session.commit()
            return ReleaseStatus.RELEASED

    async def hold_for_reconciliation(self, invocation_id: str) -> bool:
        with self._session_factory() as session:
            session.execute(
                update(BudgetScopeRow).where(BudgetScopeRow.scope == "local").values(version=BudgetScopeRow.version + 1)
            )
            row = session.execute(
                select(BudgetReservationRow)
                .where(BudgetReservationRow.invocation_key == invocation_id)
                .order_by(BudgetReservationRow.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None or row.released or row.settled:
                return False
            row.unknown_hold = True
            session.commit()
            return True

    def _sync_settle(self, record: UsageRecord) -> SettleOutcome:
        with self._session_factory() as session:
            session.execute(
                update(BudgetScopeRow).where(BudgetScopeRow.scope == "local").values(version=BudgetScopeRow.version + 1)
            )
            row = session.execute(
                select(BudgetReservationRow)
                .where(BudgetReservationRow.invocation_key == record.invocation_id)
                .order_by(BudgetReservationRow.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = BudgetReservationRow(invocation_key=record.invocation_id, units=0.0, recorded_at=self._now())
                session.add(row)
            if row.settled and record.status is UsageStatus.UNKNOWN:
                return SettleOutcome(SettleStatus.CONFLICT, None)
            previous_status = row.usage_status
            row.usage_status = record.status.value
            row.detail = record.detail
            if record.status is UsageStatus.UNKNOWN:
                row.unknown_hold = True
                session.commit()
                return SettleOutcome(SettleStatus.SETTLED, record)
            if row is not None and row.settled:
                if row.settled_units == record.units and previous_status == record.status.value:
                    session.commit()
                    return SettleOutcome(SettleStatus.IDEMPOTENT_CONFIRMED, record)
                row.settled_units = float(record.units)
                session.commit()
                return SettleOutcome(SettleStatus.REVISED, record)
            if row is not None:
                row.settled = True
                row.settled_units = float(record.units)
                row.unknown_hold = False
                session.commit()
                return SettleOutcome(SettleStatus.SETTLED, record)
            # Settle without reservation (e.g. reconciliation after restart):
            # still records the usage entry against the limit.
            session.add(
                BudgetReservationRow(
                    invocation_key=record.invocation_id,
                    units=0.0,
                    settled=True,
                    settled_units=float(record.units),
                    recorded_at=self._now(),
                )
            )
            session.commit()
            return SettleOutcome(SettleStatus.SETTLED, record)

    async def settle(self, record: UsageRecord) -> SettleOutcome:
        return self._sync_settle(record)

    async def remaining(self) -> float:
        with self._session_factory() as session:
            return self._sync_remaining(session)

    async def usage_for(self, invocation_id: str) -> UsageRecord | None:
        with self._session_factory() as session:
            session.execute(
                update(BudgetScopeRow).where(BudgetScopeRow.scope == "local").values(version=BudgetScopeRow.version + 1)
            )
            row = session.execute(
                select(BudgetReservationRow)
                .where(BudgetReservationRow.invocation_key == invocation_id)
                .order_by(BudgetReservationRow.id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if row is None or row.usage_status is None:
                return None
            return UsageRecord(
                invocation_id=row.invocation_key,
                units=int(row.settled_units or 0),
                status=UsageStatus(row.usage_status),
                detail=row.detail or "",
            )
