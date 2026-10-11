"""Budget reservation and settlement keyed by ``invocation_id``.

Reservation is a synchronous atomic check: the dispatch path waits for the
reservation commit before continuing, and concurrent reservations serialize so
only budget-worthy calls are admitted. Settlement is idempotent per
``invocation_id`` — duplicate reports confirm, late revisions merge into the
same entry. Unknown usage is recorded explicitly as unknown (never zero) and
keeps its reservation until reconciliation or the timeout policy resolves it.

Release rules: a reservation whose dispatch failed before sending is released;
a cancelled run with no unknown usage releases; while an outcome is unknown the
reservation is held.

The :class:`BudgetLedger` seam is the authority; the SQL-backed stores
(platform quota storage, runner-local durable storage) arrive with their
integration tasks — this module ships the reference in-memory ledger and the
fail-closed gate shared by all dispatch paths.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class UsageStatus(StrEnum):
    """Quality of a settled usage figure."""

    ACTUAL = "actual"
    ESTIMATED = "estimated"
    UNVERIFIED = "unverified"
    UNKNOWN = "unknown"


class ReservationStatus(StrEnum):
    """Outcome of a reservation attempt."""

    RESERVED = "reserved"
    INSUFFICIENT = "insufficient"
    CONFLICT = "conflict"


class SettleStatus(StrEnum):
    """Outcome of a settlement attempt."""

    SETTLED = "settled"
    REVISED = "revised"
    IDEMPOTENT_CONFIRMED = "idempotent_confirmed"
    CONFLICT = "conflict"


class ReleaseStatus(StrEnum):
    """Outcome of a release attempt."""

    RELEASED = "released"
    HELD_FOR_RECONCILIATION = "held_for_reconciliation"
    ALREADY_SETTLED = "already_settled"
    NO_RESERVATION = "no_reservation"


@dataclass(frozen=True)
class UsageRecord:
    """One settled usage entry; ``units`` is an adapter-defined meter."""

    invocation_id: str
    units: int
    status: UsageStatus
    detail: str = ""


@dataclass(frozen=True)
class Reservation:
    invocation_id: str
    units: int
    reserved_at: datetime


@dataclass(frozen=True)
class ReservationOutcome:
    status: ReservationStatus
    remaining: float = 0.0


@dataclass(frozen=True)
class SettleOutcome:
    status: SettleStatus
    record: UsageRecord | None = None


@dataclass
class _InvocationAccount:
    reservation: Reservation | None = None
    usage: UsageRecord | None = None
    revisions: int = 0
    charged: bool = False
    unknown_hold: bool = False


class BudgetLedger(ABC):
    """Authority for reservations and settlements within one budget scope.

    Instances are constructed per scope (per task/principal on the runner,
    per workspace/quota definition on the platform) with the scope's limit.
    Implementations must serialize concurrent reservations against the shared
    remaining budget and make settlement idempotent per ``invocation_id``.
    """

    @abstractmethod
    async def reserve(self, invocation_id: str, units: int) -> ReservationOutcome:
        """Atomically hold budget for one call before dispatch continues."""

    @abstractmethod
    async def release(self, invocation_id: str) -> ReleaseStatus:
        """Release a reservation (pre-dispatch failure / clean cancel)."""

    @abstractmethod
    async def hold_for_reconciliation(self, invocation_id: str) -> bool:
        """Mark the reservation as held: outcome unknown, never released."""

    @abstractmethod
    async def settle(self, record: UsageRecord) -> SettleOutcome:
        """Settle actual usage; duplicates confirm, revisions merge."""

    @abstractmethod
    async def remaining(self) -> float:
        """Current uncommitted budget headroom."""

    @abstractmethod
    async def usage_for(self, invocation_id: str) -> UsageRecord | None:
        """Return the settled usage entry if any."""


class MemoryBudgetLedger(BudgetLedger):
    """Reference in-memory ledger; concurrent ops serialize on one lock."""

    def __init__(self, limit: int) -> None:
        if limit < 0:
            raise ValueError("budget limit must be non-negative")
        self._limit = limit
        self._remaining = limit
        self._accounts: dict[str, _InvocationAccount] = {}
        self._lock = asyncio.Lock()
        self._fail_mode: Exception | None = None

    def fail_next_access(self, error: Exception) -> None:
        """Inject a storage failure for the next access (fail-closed tests)."""
        self._fail_mode = error

    def _maybe_fail(self) -> None:
        if self._fail_mode is not None:
            error, self._fail_mode = self._fail_mode, None
            raise error

    async def reserve(self, invocation_id: str, units: int) -> ReservationOutcome:
        if units < 0:
            raise ValueError("reservation units must be non-negative")
        async with self._lock:
            self._maybe_fail()
            account = self._accounts.setdefault(invocation_id, _InvocationAccount())
            if account.reservation is not None:
                if account.reservation.units == units:
                    return ReservationOutcome(ReservationStatus.RESERVED, self._remaining)
                return ReservationOutcome(ReservationStatus.CONFLICT, self._remaining)
            if account.usage is not None:
                return ReservationOutcome(ReservationStatus.CONFLICT, self._remaining)
            if units > self._remaining:
                return ReservationOutcome(ReservationStatus.INSUFFICIENT, self._remaining)
            self._remaining -= units
            account.reservation = Reservation(
                invocation_id=invocation_id,
                units=units,
                reserved_at=datetime.now(UTC),
            )
            return ReservationOutcome(ReservationStatus.RESERVED, self._remaining)

    async def release(self, invocation_id: str) -> ReleaseStatus:
        async with self._lock:
            self._maybe_fail()
            account = self._accounts.get(invocation_id)
            if account is None:
                return ReleaseStatus.NO_RESERVATION
            if account.usage is not None:
                if account.usage.status is UsageStatus.UNKNOWN:
                    return ReleaseStatus.HELD_FOR_RECONCILIATION
                return ReleaseStatus.ALREADY_SETTLED
            if account.reservation is None:
                return ReleaseStatus.NO_RESERVATION
            if account.unknown_hold:
                # Unknown outcome: the reservation is held until reconciliation
                # or the timeout policy resolves it — never silently released.
                return ReleaseStatus.HELD_FOR_RECONCILIATION
            self._remaining += account.reservation.units
            account.reservation = None
            return ReleaseStatus.RELEASED

    async def hold_for_reconciliation(self, invocation_id: str) -> bool:
        async with self._lock:
            self._maybe_fail()
            account = self._accounts.get(invocation_id)
            if account is None or account.reservation is None:
                return False
            account.unknown_hold = True
            return True

    async def settle(self, record: UsageRecord) -> SettleOutcome:
        async with self._lock:
            self._maybe_fail()
            account = self._accounts.setdefault(record.invocation_id, _InvocationAccount())
            if account.usage is not None:
                if account.usage.units == record.units and account.usage.status is record.status:
                    return SettleOutcome(SettleStatus.IDEMPOTENT_CONFIRMED, account.usage)
                # Late revision: merge into the same entry, keep it traceable.
                if account.usage.status is UsageStatus.UNKNOWN and record.status is not UsageStatus.UNKNOWN:
                    # Reconciliation resolves the unknown: settle against the
                    # still-held reservation, then clear the hold.
                    if account.reservation is not None:
                        refund = max(account.reservation.units - record.units, 0)
                        over = max(record.units - account.reservation.units, 0)
                        self._remaining += refund
                        self._remaining -= min(over, self._remaining)
                        account.reservation = None
                    account.unknown_hold = False
                    account.charged = True
                elif record.status is UsageStatus.UNKNOWN:
                    # A late uncertain report must not erase an established charge.
                    account.unknown_hold = True
                    return SettleOutcome(SettleStatus.CONFLICT, account.usage)
                elif account.charged:
                    delta = record.units - account.usage.units
                    if delta < 0:
                        self._remaining += -delta
                    elif delta > 0:
                        self._remaining -= min(delta, self._remaining)
                account.usage = record
                account.revisions += 1
                return SettleOutcome(SettleStatus.REVISED, record)
            account.usage = record
            account.revisions += 1
            if record.status is UsageStatus.UNKNOWN:
                account.unknown_hold = True
                return SettleOutcome(SettleStatus.SETTLED, record)
            if account.reservation is not None:
                # Reconcile the reservation against actual usage: refund the
                # unused part, charge an over-run against remaining headroom.
                refund = max(account.reservation.units - record.units, 0)
                over = max(record.units - account.reservation.units, 0)
                self._remaining += refund
                self._remaining -= min(over, self._remaining)
                account.reservation = None
                account.charged = True
            account.unknown_hold = False
            return SettleOutcome(SettleStatus.SETTLED, record)

    async def remaining(self) -> int:
        async with self._lock:
            return self._remaining

    async def usage_for(self, invocation_id: str) -> UsageRecord | None:
        async with self._lock:
            account = self._accounts.get(invocation_id)
            return account.usage if account else None


class BudgetGate:
    """Fail-closed facade over the ledger for dispatch paths.

    A storage failure on reservation denies the call (never dispatches without
    a committed reservation); a failure on settlement surfaces as an explicit
    conflict so the caller records an unknown account instead of losing usage.
    """

    def __init__(self, ledger: BudgetLedger) -> None:
        self._ledger = ledger

    async def ensure_budget(self, invocation_id: str, units: int) -> ReservationOutcome:
        """Reserve before dispatch continues; failures deny, never pass."""
        try:
            return await self._ledger.reserve(invocation_id, units)
        except Exception:  # noqa: BLE001 - fail-closed by contract
            return ReservationOutcome(ReservationStatus.INSUFFICIENT, -1)

    async def settle(self, record: UsageRecord) -> SettleOutcome:
        try:
            return await self._ledger.settle(record)
        except Exception:  # noqa: BLE001 - fail-closed by contract
            return SettleOutcome(SettleStatus.CONFLICT, None)
