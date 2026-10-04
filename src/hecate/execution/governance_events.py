"""Platform event persistence and cursor reads (step6 platform track).

Two event families share the ``platform_events`` table and one per-run
sequence space:

- **governance events** — task lifecycle transitions, command receipts,
  reconciliation marks. Emitted through :meth:`PlatformEventService.emit`
  which enforces the governance profile (``actor`` and ``source`` both
  required) before anything is persisted.
- **run stream events** — engine stream events mapped to envelopes by
  ``RunEventMapper`` for task-control-dispatched executions. The mapper's
  internal sequence stays internal; persistence re-sequences through this
  service so one run's stored stream is monotonic and gap-free.

Reads are cursor-paginated per run reference: the cursor is the last
``source_sequence`` consumed, so a disconnected client resumes exactly
where it stopped. Workspace scoping makes a foreign run indistinguishable
from a missing one.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.events import (
    ActorRef,
    EventEnvelope,
    EventKind,
    EventSource,
    validate_governance_event,
)
from hecate.contracts.execution.references import BackendRef, RefKind, require_kind
from hecate.execution.backend import EventPage
from hecate.models.platform_event import PlatformEventModel

CONTRACT_VERSION = "0.1"

# Payload schema refs for the governance events this change emits. Each ref
# names the payload shape so consumers can distinguish event families
# without parsing payloads.
TASK_SUBMITTED = "hecate.platform.task_submitted/0"
TASK_STATE_CHANGED = "hecate.platform.task_state_changed/0"
COMMAND_RECORDED = "hecate.platform.command_recorded/0"
COMMAND_TRANSITIONED = "hecate.platform.command_transitioned/0"
TASK_RECONCILIATION = "hecate.platform.task_reconciliation/0"
RUN_TERMINAL = "hecate.platform.run_terminal/0"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _split_ref(ref: BackendRef) -> tuple[str, str]:
    return ref.issuer_domain, ref.id


class PlatformEventService:
    """Persist and page platform events on one async session."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def next_sequence(self, run_ref: BackendRef, workspace_id: uuid.UUID | None = None) -> int:
        """Allocate the next per-run sequence (max + 1; 0 for a fresh run)."""

        require_kind(run_ref, RefKind.RUN)
        issuer, ref_id = _split_ref(run_ref)
        current = (
            await self._db.execute(
                select(func.max(PlatformEventModel.source_sequence)).where(
                    PlatformEventModel.run_issuer_domain == issuer,
                    PlatformEventModel.run_ref_id == ref_id,
                )
            )
        ).scalar_one()
        # max() == 0 is a valid allocated sequence, not "no rows".
        return (current if current is not None else -1) + 1

    async def emit(
        self,
        *,
        task_ref: BackendRef,
        run_ref: BackendRef,
        payload_schema_ref: str,
        payload: dict[str, Any],
        actor: ActorRef,
        source: EventSource = EventSource.PLATFORM,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> EventEnvelope:
        """Build, validate (governance profile), and persist one event."""

        envelope = EventEnvelope(
            contract_version=CONTRACT_VERSION,
            kind=EventKind.EVENT,
            event_id=str(uuid.uuid4()),
            task_ref=task_ref,
            run_ref=run_ref,
            source_sequence=await self.next_sequence(run_ref, workspace_id),
            occurred_at=_utc_now(),
            received_at=_utc_now(),
            payload_schema_ref=payload_schema_ref,
            payload=dict(payload),
            correlation_id=correlation_id,
            causation_id=causation_id,
            actor=actor,
            source=source,
        )
        validate_governance_event(envelope)
        await self.append(envelope, workspace_id=workspace_id)
        return envelope

    async def append(self, envelope: EventEnvelope, *, workspace_id: uuid.UUID | None = None) -> EventEnvelope:
        """Persist one envelope verbatim (run stream events; pre-sequenced)."""

        row = self._to_row(envelope, workspace_id)
        self._db.add(row)
        await self._db.flush()
        return envelope

    async def append_resequenced(
        self, envelope: EventEnvelope, *, workspace_id: uuid.UUID | None = None
    ) -> EventEnvelope:
        """Persist one envelope under the run's next stored sequence.

        Stream events arrive with the mapper's internal sequence; the stored
        stream must stay monotonic across both event families, so the mapper
        sequence is replaced by the store's allocation (the original value
        survives inside the envelope serialization's ``extra`` only if the
        caller put it there).
        """

        sequence = await self.next_sequence(envelope.run_ref, workspace_id)
        stored = replace(envelope, source_sequence=sequence, received_at=_utc_now())
        await self.append(stored, workspace_id=workspace_id)
        return stored

    async def find_by_event_id(self, event_id: str) -> EventEnvelope | None:
        """One stored envelope by its globally-unique event id (relay dedup)."""

        row = (
            await self._db.execute(
                select(PlatformEventModel).where(
                    PlatformEventModel.event_id == event_id,
                    PlatformEventModel.deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        return EventEnvelope.from_dict(dict(row.envelope)) if row is not None else None

    async def read_run_events(
        self,
        run_ref: BackendRef,
        *,
        workspace_id: uuid.UUID,
        cursor: str | None = None,
        limit: int = 100,
    ) -> EventPage:
        """Page one run's stored events after ``cursor`` (workspace-scoped).

        Missing and foreign-workspace runs are indistinguishable: both
        yield an empty page.
        """

        require_kind(run_ref, RefKind.RUN)
        issuer, ref_id = _split_ref(run_ref)
        after = int(cursor) if cursor is not None else -1
        rows = (
            (
                await self._db.execute(
                    select(PlatformEventModel)
                    .where(
                        PlatformEventModel.workspace_id == workspace_id,
                        PlatformEventModel.run_issuer_domain == issuer,
                        PlatformEventModel.run_ref_id == ref_id,
                        PlatformEventModel.source_sequence > after,
                        PlatformEventModel.deleted.is_(False),
                    )
                    .order_by(PlatformEventModel.source_sequence)
                    .limit(limit + 1)
                )
            )
            .scalars()
            .all()
        )
        has_more = len(rows) > limit
        window = rows[:limit]
        events = tuple(EventEnvelope.from_dict(dict(row.envelope)) for row in window)
        next_cursor = str(window[-1].source_sequence) if window else cursor
        return EventPage(events=events, next_cursor=next_cursor, has_more=has_more)

    async def latest_run_envelope(self, run_ref: BackendRef, *, workspace_id: uuid.UUID) -> EventEnvelope | None:
        """The newest stored envelope for one run (SSE terminal detection)."""

        require_kind(run_ref, RefKind.RUN)
        issuer, ref_id = _split_ref(run_ref)
        row = (
            await self._db.execute(
                select(PlatformEventModel)
                .where(
                    PlatformEventModel.workspace_id == workspace_id,
                    PlatformEventModel.run_issuer_domain == issuer,
                    PlatformEventModel.run_ref_id == ref_id,
                    PlatformEventModel.deleted.is_(False),
                )
                .order_by(PlatformEventModel.source_sequence.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return EventEnvelope.from_dict(dict(row.envelope)) if row is not None else None

    async def run_terminal(self, run_ref: BackendRef, *, workspace_id: uuid.UUID) -> bool:
        """Whether the run's stream already carries a terminal event."""

        latest = await self.latest_run_envelope(run_ref, workspace_id=workspace_id)
        return latest is not None and latest.payload_schema_ref == RUN_TERMINAL

    @staticmethod
    def _to_row(envelope: EventEnvelope, workspace_id: uuid.UUID | None) -> PlatformEventModel:
        task_issuer, task_id = _split_ref(envelope.task_ref)
        run_issuer, run_id = _split_ref(envelope.run_ref)
        return PlatformEventModel(
            event_id=envelope.event_id,
            contract_version=envelope.contract_version,
            kind=envelope.kind.value,
            task_issuer_domain=task_issuer,
            task_ref_id=task_id,
            run_issuer_domain=run_issuer,
            run_ref_id=run_id,
            source_sequence=envelope.source_sequence,
            occurred_at=envelope.occurred_at,
            received_at=envelope.received_at,
            payload_schema_ref=envelope.payload_schema_ref,
            payload=dict(envelope.payload),
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            actor_kind=envelope.actor.kind.value if envelope.actor is not None else None,
            actor_id=envelope.actor.id if envelope.actor is not None else None,
            source=envelope.source.value if envelope.source is not None else None,
            envelope=envelope.to_dict(),
            workspace_id=workspace_id,
        )
