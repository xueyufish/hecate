"""Claim/receipt ledger for ledger-less dispatch paths (step7 local slice).

Paths without a durable action ledger of their own (chat execution, gateway
direct calls) use this ledger as the authoritative claim/receipt owner: the
policy judgment, the ``invocation_id`` claim and the execution receipt live in
one record. Ledger-backed paths (runner durable tasks, platform persistent
task execution) keep their ledger as the exclusive claim owner and never
double-claim here.

Semantics per ``invocation_id`` (deterministic per logical call):

- first claim → ``CLAIMED`` (exactly one executor proceeds);
- concurrent or retry duplicate while unresolved → ``DUPLICATE_IN_FLIGHT``
  (the caller must not re-execute; the prior outcome is not established);
- duplicate after a completed outcome → ``COMPLETED_REPLAY`` with the
  recorded result (idempotent replay, no second side effect);
- same id with a different identity → ``CONFLICT`` (never executed);
- a terminal failure clears the open claim for retry (the receipt is
  established); an unresolved record never does.

The reference implementation is in-memory; the SQL-backed store arrives with
the platform approval-authority migration and persists claims across restarts.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ClaimStatus(StrEnum):
    """Outcome of a claim attempt for one invocation id."""

    CLAIMED = "claimed"
    DUPLICATE_IN_FLIGHT = "duplicate_in_flight"
    COMPLETED_REPLAY = "completed_replay"
    CONFLICT = "conflict"


class OutcomeUnknownError(Exception):
    """The prior outcome of this invocation is not established.

    Flagged with ``outcome_unknown`` so the runtime's receipt classification
    records an unknown outcome (reconciliation) instead of a clean failure.
    """

    outcome_unknown = True


@dataclass
class ClaimRecord:
    """Authoritative claim/receipt record for one invocation."""

    invocation_id: str
    tool_name: str
    arguments_digest: str
    principal_fingerprint: str
    side_effect_class: str
    completed: bool = False
    succeeded: bool | None = None
    result: Any = None
    result_digest: str | None = None


@dataclass(frozen=True)
class ClaimOutcome:
    status: ClaimStatus
    record: ClaimRecord | None = None
    detail: str = ""


def identity_matches(record: ClaimRecord, tool_name: str, arguments_digest: str, principal_fingerprint: str) -> bool:
    """Whether a request matches the record's persisted identity."""
    return (
        record.tool_name == tool_name
        and record.arguments_digest == arguments_digest
        and record.principal_fingerprint == principal_fingerprint
    )


class ActionClaimLedger(ABC):
    """Authority for claims and receipts on ledger-less dispatch paths."""

    @abstractmethod
    async def claim(
        self,
        invocation_id: str,
        *,
        tool_name: str,
        arguments_digest: str,
        principal_fingerprint: str,
        side_effect_class: str,
    ) -> ClaimOutcome:
        """Atomically claim the invocation for execution."""

    @abstractmethod
    async def complete(
        self,
        invocation_id: str,
        *,
        succeeded: bool,
        result: Any = None,
        result_digest: str | None = None,
    ) -> None:
        """Attach the terminal receipt; a store failure leaves it unresolved."""

    @abstractmethod
    async def resolve(self, invocation_id: str) -> ClaimRecord | None:
        """Return the record for reconciliation queries."""


class MemoryActionClaimLedger(ActionClaimLedger):
    """Reference in-memory ledger; operations serialize on one lock."""

    def __init__(self) -> None:
        self._records: dict[str, ClaimRecord] = {}
        self._lock = asyncio.Lock()
        self._fail_next_complete: Exception | None = None

    def fail_next_complete(self, error: Exception) -> None:
        """Inject a receipt-write failure for the next ``complete`` call."""
        self._fail_next_complete = error

    async def claim(
        self,
        invocation_id: str,
        *,
        tool_name: str,
        arguments_digest: str,
        principal_fingerprint: str,
        side_effect_class: str,
    ) -> ClaimOutcome:
        async with self._lock:
            record = self._records.get(invocation_id)
            if record is not None:
                if not identity_matches(record, tool_name, arguments_digest, principal_fingerprint):
                    return ClaimOutcome(
                        ClaimStatus.CONFLICT,
                        record,
                        "invocation id reused with different arguments or principal",
                    )
                if record.completed:
                    if record.succeeded:
                        return ClaimOutcome(ClaimStatus.COMPLETED_REPLAY, record)
                    # Terminal failure: the receipt is established; a retry of
                    # the same logical call re-claims as a new attempt.
                    fresh = ClaimRecord(
                        invocation_id=invocation_id,
                        tool_name=tool_name,
                        arguments_digest=arguments_digest,
                        principal_fingerprint=principal_fingerprint,
                        side_effect_class=side_effect_class,
                    )
                    self._records[invocation_id] = fresh
                    return ClaimOutcome(ClaimStatus.CLAIMED, fresh)
                return ClaimOutcome(
                    ClaimStatus.DUPLICATE_IN_FLIGHT,
                    record,
                    "prior attempt has no established outcome; reconciliation required",
                )
            fresh = ClaimRecord(
                invocation_id=invocation_id,
                tool_name=tool_name,
                arguments_digest=arguments_digest,
                principal_fingerprint=principal_fingerprint,
                side_effect_class=side_effect_class,
            )
            self._records[invocation_id] = fresh
            return ClaimOutcome(ClaimStatus.CLAIMED, fresh)

    async def complete(
        self,
        invocation_id: str,
        *,
        succeeded: bool,
        result: Any = None,
        result_digest: str | None = None,
    ) -> None:
        async with self._lock:
            if self._fail_next_complete is not None:
                error, self._fail_next_complete = self._fail_next_complete, None
                raise error
            record = self._records.get(invocation_id)
            if record is None:
                raise KeyError(f"unknown invocation {invocation_id!r}")
            record.completed = True
            record.succeeded = succeeded
            record.result = result
            record.result_digest = result_digest

    async def resolve(self, invocation_id: str) -> ClaimRecord | None:
        async with self._lock:
            return self._records.get(invocation_id)
