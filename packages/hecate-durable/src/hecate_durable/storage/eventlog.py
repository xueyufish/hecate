"""Event log with sequence allocation, dedup, and cursor reads with gaps.

The log is the transactional outbox: state writes append their envelope row in
the same database transaction via :meth:`SqlEventLog.emit` on the caller's
session. Standalone ingestion (:meth:`SqlEventLog.append`) applies the dedup
rules on its own session:

- the same ``event_id`` replayed with identical content is idempotent (no
  second row, no second sequence allocation);
- the same ``event_id`` with different content, or a different event claiming
  an occupied ``(run, source, source_sequence)`` position, raises
  :class:`EventConflictError`;
- out-of-order sequences are accepted — order is defined by
  ``source_sequence`` at read time, never by arrival.

Reads (:meth:`SqlEventLog.read`) resume from an integer cursor and synthesize
explicit gap envelopes for sequence holes inside the observed range; the tail
beyond the highest observed sequence is never reported as a gap (later events
may still arrive).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hecate_durable.contracts.events import (
    ActorKind,
    ActorRef,
    EventEnvelope,
    EventKind,
    EventSource,
    GapRange,
    validate_governance_event,
)
from hecate_durable.contracts.references import BackendRef
from hecate_durable.storage.models import EventRow, RunSequenceRow

CONTRACT_VERSION = "0.1"


class EventConflictError(Exception):
    """A replayed event id or stream position disagrees with recorded state."""


@dataclass(frozen=True)
class EventPage:
    """Cursor-resumed read result: envelopes (incl. gap markers) + cursor."""

    events: list[EventEnvelope]
    next_cursor: int


def build_envelope(
    *,
    task_ref: BackendRef,
    run_ref: BackendRef,
    source: EventSource | str,
    source_sequence: int,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: str,
    received_at: str | None = None,
    actor_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    event_id: str | None = None,
) -> EventEnvelope:
    """Build a governance-profile envelope (actor and source always set)."""

    actor = ActorRef(kind=ActorKind.SERVICE, id=actor_id or str(source))
    return EventEnvelope(
        contract_version=CONTRACT_VERSION,
        kind=EventKind.EVENT,
        event_id=event_id or _new_event_id(),
        task_ref=task_ref,
        run_ref=run_ref,
        source_sequence=source_sequence,
        occurred_at=occurred_at,
        received_at=received_at or occurred_at,
        payload_schema_ref=f"urn:hecate:durable:event:{event_type}",
        payload={"event_type": event_type, **payload},
        actor=actor,
        source=EventSource(source) if isinstance(source, str) else source,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def _new_event_id() -> str:
    import uuid

    return f"evt-{uuid.uuid4()}"


class SqlEventLog:
    """Append/read access to the durable event log.

    ``emit`` participates in the caller's transaction (same-commit outbox);
    ``append``/``read`` own their short-lived session. Sequence allocation is
    a single conditional ``UPDATE`` under the caller's transaction so two
    writers never observe the same position.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        source: str,
        actor_id: str | None = None,
        clock: Callable[[], str],
    ) -> None:
        self._session_factory = session_factory
        self._source = source
        self._actor_id = actor_id
        self._clock = clock

    # -- same-transaction emission (outbox) --------------------------------

    def emit(
        self,
        session: Session,
        *,
        task_ref: BackendRef,
        run_ref: BackendRef,
        event_type: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> EventEnvelope:
        """Allocate a sequence and insert the envelope on ``session``.

        The governance profile (actor + source) is validated before the row is
        written so a malformed envelope aborts the caller's transaction rather
        than landing a non-attributable event.
        """

        sequence = self._allocate_sequence(session, run_ref)
        now = self._clock()
        envelope = build_envelope(
            task_ref=task_ref,
            run_ref=run_ref,
            source=self._source,
            source_sequence=sequence,
            event_type=event_type,
            payload=payload,
            occurred_at=now,
            actor_id=self._actor_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        validate_governance_event(envelope)
        session.add(
            EventRow(
                run_issuer=run_ref.issuer_domain,
                run_id=run_ref.id,
                source=self._source,
                source_sequence=sequence,
                event_id=envelope.event_id,
                envelope=envelope.to_dict(),
                received_at=now,
            )
        )
        return envelope

    def _allocate_sequence(self, session: Session, run_ref: BackendRef) -> int:
        updated = session.execute(
            update(RunSequenceRow)
            .where(
                RunSequenceRow.run_issuer == run_ref.issuer_domain,
                RunSequenceRow.run_id == run_ref.id,
                RunSequenceRow.source == self._source,
            )
            .values(last_sequence=RunSequenceRow.last_sequence + 1)
        )
        if updated.rowcount == 0:
            # First emission for this stream: insert the counter under a
            # savepoint so a concurrent creator loses cleanly instead of
            # aborting the caller's transaction (PostgreSQL semantics).
            try:
                with session.begin_nested():
                    session.add(
                        RunSequenceRow(
                            run_issuer=run_ref.issuer_domain,
                            run_id=run_ref.id,
                            source=self._source,
                            last_sequence=1,
                        )
                    )
                return 1
            except IntegrityError:
                updated = session.execute(
                    update(RunSequenceRow)
                    .where(
                        RunSequenceRow.run_issuer == run_ref.issuer_domain,
                        RunSequenceRow.run_id == run_ref.id,
                        RunSequenceRow.source == self._source,
                    )
                    .values(last_sequence=RunSequenceRow.last_sequence + 1)
                )
                if updated.rowcount == 0:  # pragma: no cover - lost twice
                    raise
        return session.execute(
            select(RunSequenceRow.last_sequence).where(
                RunSequenceRow.run_issuer == run_ref.issuer_domain,
                RunSequenceRow.run_id == run_ref.id,
                RunSequenceRow.source == self._source,
            )
        ).scalar_one()

    # -- standalone ingestion with dedup ------------------------------------

    def append(self, envelope: EventEnvelope) -> int:
        """Ingest one external envelope; idempotent per ``event_id``.

        Returns the stored ``source_sequence``. Raises
        :class:`EventConflictError` when the same event id arrives with
        different content or when a different event claims an occupied stream
        position. The envelope's own source is used for the stream position;
        it is written verbatim (no re-attribution of actor/source).
        """

        validate_governance_event(envelope)
        existing = self._find_by_event_id(envelope.event_id)
        if existing is not None:
            if _same_content(existing.envelope, envelope):
                return existing.source_sequence
            raise EventConflictError(f"event id {envelope.event_id!r} already recorded with different content")
        for attempt in range(3):
            try:
                with self._session_factory() as session, session.begin():
                    session.add(
                        EventRow(
                            run_issuer=envelope.run_ref.issuer_domain,
                            run_id=envelope.run_ref.id,
                            source=str(envelope.source) if envelope.source else "unknown",
                            source_sequence=envelope.source_sequence,
                            event_id=envelope.event_id,
                            envelope=envelope.to_dict(),
                            received_at=envelope.received_at,
                        )
                    )
                    session.flush()
                return envelope.source_sequence
            except IntegrityError:
                existing = self._find_by_event_id(envelope.event_id)
                if existing is not None:
                    if _same_content(existing.envelope, envelope):
                        return existing.source_sequence
                    raise EventConflictError(
                        f"event id {envelope.event_id!r} already recorded with different content"
                    ) from None
                position = self._find_by_position(envelope)
                if position is not None:
                    raise EventConflictError(
                        f"stream position ({envelope.run_ref.id}, {envelope.source_sequence}) "
                        f"is already occupied by event {position.event_id!r}"
                    ) from None
                if attempt == 2:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    def _find_by_event_id(self, event_id: str) -> EventRow | None:
        with self._session_factory() as session:
            return session.execute(select(EventRow).where(EventRow.event_id == event_id)).scalar_one_or_none()

    def _find_by_position(self, envelope: EventEnvelope) -> EventRow | None:
        with self._session_factory() as session:
            return session.execute(
                select(EventRow).where(
                    EventRow.run_issuer == envelope.run_ref.issuer_domain,
                    EventRow.run_id == envelope.run_ref.id,
                    EventRow.source == str(envelope.source),
                    EventRow.source_sequence == envelope.source_sequence,
                )
            ).scalar_one_or_none()

    # -- cursor reads with gap markers ---------------------------------------

    def read(self, run_ref: BackendRef, *, cursor: int = 0, limit: int = 100) -> EventPage:
        """Read the run's stream after ``cursor`` in sequence order.

        Missing sequence numbers inside the observed range are returned as
        explicit gap envelopes. The returned ``next_cursor`` is the highest
        sequence observed (unchanged when the page is empty) so callers tail
        safely; gaps beyond the observed maximum are never fabricated.
        """

        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(EventRow)
                    .where(
                        EventRow.run_issuer == run_ref.issuer_domain,
                        EventRow.run_id == run_ref.id,
                        EventRow.source == self._source,
                        EventRow.source_sequence > cursor,
                    )
                    .order_by(EventRow.source_sequence)
                )
                .scalars()
                .all()
            )
        if not rows:
            return EventPage(events=[], next_cursor=cursor)
        events: list[EventEnvelope] = []
        expected = cursor + 1
        last_sequence = cursor
        for row in rows:
            if len(events) >= limit:
                break
            if row.source_sequence > expected:
                events.append(
                    EventEnvelope(
                        contract_version=CONTRACT_VERSION,
                        kind=EventKind.GAP,
                        event_id=f"gap:{run_ref.id}:{expected}:{row.source_sequence - 1}",
                        task_ref=_task_ref_of(row.envelope),
                        run_ref=run_ref,
                        source_sequence=row.source_sequence - 1,
                        occurred_at=row.received_at,
                        received_at=row.received_at,
                        gap=GapRange(from_sequence=expected, to_sequence=row.source_sequence - 1),
                    )
                )
            events.append(EventEnvelope.from_dict(row.envelope))
            expected = row.source_sequence + 1
            last_sequence = row.source_sequence
        return EventPage(events=events, next_cursor=last_sequence)


def _task_ref_of(envelope_dict: dict[str, Any]) -> BackendRef:
    return BackendRef.from_dict(envelope_dict["task_ref"])


def _same_content(recorded: dict[str, Any], envelope: EventEnvelope) -> bool:
    incoming = envelope.to_dict()
    return {k: v for k, v in recorded.items() if k != "received_at"} == {
        k: v for k, v in incoming.items() if k != "received_at"
    }


__all__ = [
    "CONTRACT_VERSION",
    "EventConflictError",
    "EventPage",
    "SqlEventLog",
    "build_envelope",
]
