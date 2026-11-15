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
may still arrive). :meth:`SqlEventLog.read_committed` additionally consults
the committed sequence counter in the same snapshot: a counter above the
highest observed sequence proves tail rows were committed and then lost, so
those tails are reported as explicit gaps as well.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, true, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from hecate_durable.contracts.events import (
    ActorKind,
    ActorRef,
    EventEnvelope,
    EventKind,
    EventSource,
    GapRange,
    validate_governance_event,
)
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage.models import EventRow, RunSequenceRow, SubmissionRow, TaskStateRow

CONTRACT_VERSION = "0.1"


class EventConflictError(Exception):
    """A replayed event id or stream position disagrees with recorded state."""


@dataclass(frozen=True)
class EventPage:
    """Cursor-resumed read result: envelopes (incl. gap markers) + cursor."""

    events: list[EventEnvelope]
    next_cursor: int
    has_more: bool = False


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
        read_session_factory: Callable[[], Session] | None = None,
        source: str,
        actor_id: str | None = None,
        clock: Callable[[], str],
    ) -> None:
        self._session_factory = session_factory
        # Cursor reads and lookups take no writer lock on SQLite-backed
        # stores (the store passes its autocommit read factory); callers
        # that share one factory keep the previous behavior.
        self._read_session_factory = read_session_factory or session_factory
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
        event_id: str | None = None,
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
            event_id=event_id,
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
        with self._read_session_factory() as session:
            return session.execute(select(EventRow).where(EventRow.event_id == event_id)).scalar_one_or_none()

    def _find_by_position(self, envelope: EventEnvelope) -> EventRow | None:
        with self._read_session_factory() as session:
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

        if cursor < 0 or limit < 1:
            raise ValueError("cursor must be non-negative and limit must be positive")
        with self._read_session_factory() as session:
            rows = session.scalars(_event_page_rows(self._source, run_ref, cursor, limit)).all()
            return _read_page(session, self._source, run_ref, rows, cursor=cursor, limit=limit, committed=None)

    def read_committed(self, run_ref: BackendRef, *, cursor: int = 0, limit: int = 100) -> EventPage:
        """Read the stream after ``cursor`` and report proven tail gaps.

        Identical to :meth:`read`, plus one fact: the stream's committed
        sequence counter (``RunSequenceRow``) is read in the SAME database
        snapshot as the event page. The counter is allocated and bumped in
        the same transaction that writes each event row, so a counter
        value above the highest observed sequence proves those tail
        events were committed and their rows were subsequently lost —
        they are reported as an explicit tail gap instead of an empty
        page. Streams without a counter row (unknown streams and legacy
        data written before the counter existed) keep the plain
        :meth:`read` behavior; a committed counter never fabricates a gap
        below the highest observed sequence, and a page that filled its
        limit with real events defers the tail to the next page.
        """

        if cursor < 0 or limit < 1:
            raise ValueError("cursor must be non-negative and limit must be positive")
        # The aggregate keeps one row even without a counter or any events.
        # A single statement gives the counter and page one MVCC snapshot.
        counter = (
            select(func.max(RunSequenceRow.last_sequence).label("last_sequence"))
            .where(
                RunSequenceRow.run_issuer == run_ref.issuer_domain,
                RunSequenceRow.run_id == run_ref.id,
                RunSequenceRow.source == self._source,
            )
            .subquery()
        )
        page_row = aliased(EventRow, _event_page_rows(self._source, run_ref, cursor, limit).subquery())
        statement = (
            select(page_row, counter.c.last_sequence)
            .select_from(counter)
            .outerjoin(page_row, true())
            .order_by(page_row.source_sequence)
        )
        with self._read_session_factory() as session:
            rows = session.execute(statement).all()
            return _read_page(
                session,
                self._source,
                run_ref,
                [row for row, _ in rows if row is not None],
                cursor=cursor,
                limit=limit,
                committed=rows[0][1],
            )

    def latest(self, run_ref: BackendRef, *, event_types: tuple[str, ...]) -> EventEnvelope | None:
        """Read the latest matching fact in this source's complete Run scope."""
        with self._read_session_factory() as session:
            row = session.execute(
                select(EventRow)
                .where(
                    EventRow.run_issuer == run_ref.issuer_domain,
                    EventRow.run_id == run_ref.id,
                    EventRow.source == self._source,
                    EventRow.envelope["payload"]["event_type"].as_string().in_(event_types),
                )
                .order_by(EventRow.source_sequence.desc())
                .limit(1)
            ).scalar_one_or_none()
            return EventEnvelope.from_dict(row.envelope) if row is not None else None


def _task_ref_of(envelope_dict: dict[str, Any]) -> BackendRef:
    return BackendRef.from_dict(envelope_dict["task_ref"])


def _gap_envelope(
    run_ref: BackendRef,
    task_ref: BackendRef,
    *,
    first_missing: int,
    last_missing: int,
    occurred_at: str,
) -> EventEnvelope:
    """One explicit gap marker for ``first_missing..last_missing`` (inclusive)."""

    return EventEnvelope(
        contract_version=CONTRACT_VERSION,
        kind=EventKind.GAP,
        event_id=f"gap:{run_ref.id}:{first_missing}:{last_missing}",
        task_ref=task_ref,
        run_ref=run_ref,
        source_sequence=last_missing,
        occurred_at=occurred_at,
        received_at=occurred_at,
        gap=GapRange(from_sequence=first_missing, to_sequence=last_missing),
    )


def _stream_task_identity(
    session: Session, source: str, run_ref: BackendRef, cursor: int
) -> tuple[BackendRef, str] | None:
    """Resolve the Task identity of a stream whose tail rows are all gone.

    Any surviving row of the stream names the Task that emitted it; the
    fallbacks are the current task state link and the original submission.
    """

    surviving = session.execute(
        select(EventRow.envelope, EventRow.received_at)
        .where(
            EventRow.run_issuer == run_ref.issuer_domain,
            EventRow.run_id == run_ref.id,
            EventRow.source == source,
            EventRow.source_sequence <= cursor,
        )
        .order_by(EventRow.source_sequence.desc())
        .limit(1)
    ).first()
    if surviving is not None:
        return _task_ref_of(surviving.envelope), surviving.received_at
    current = session.execute(
        select(TaskStateRow.task_issuer, TaskStateRow.task_id, TaskStateRow.recorded_at).where(
            TaskStateRow.run_issuer == run_ref.issuer_domain,
            TaskStateRow.run_id == run_ref.id,
        )
    ).one_or_none()
    if current is not None:
        return BackendRef(RefKind.TASK, current.task_issuer, current.task_id), current.recorded_at
    binding = session.execute(
        select(SubmissionRow.task_issuer, SubmissionRow.task_id, SubmissionRow.created_at).where(
            SubmissionRow.run_issuer == run_ref.issuer_domain,
            SubmissionRow.run_id == run_ref.id,
        )
    ).first()
    if binding is not None:
        task_issuer, task_id, created_at = binding
        return BackendRef(RefKind.TASK, task_issuer, task_id), created_at
    return None


def _event_page_rows(source: str, run_ref: BackendRef, cursor: int, limit: int):
    return (
        select(EventRow)
        .where(
            EventRow.run_issuer == run_ref.issuer_domain,
            EventRow.run_id == run_ref.id,
            EventRow.source == source,
            EventRow.source_sequence > cursor,
        )
        .order_by(EventRow.source_sequence)
        .limit(limit + 1)
    )


def _read_page(
    session: Session,
    source: str,
    run_ref: BackendRef,
    rows: Sequence[EventRow],
    *,
    cursor: int,
    limit: int,
    committed: int | None,
) -> EventPage:
    """Build one cursor page inside ``session``'s snapshot.

    ``committed`` is the stream's committed sequence counter read from the
    same snapshot (``None`` for plain reads and counter-less streams).
    Sequence holes inside the observed rows become explicit gap envelopes.
    A committed counter above the highest observed sequence proves the tail
    rows were committed and then lost, so the tail becomes an explicit gap
    too — but only when the page has room for it: a page that filled its
    limit with real events defers the tail to the next page through
    ``has_more``. An all-rows-lost stream reports the tail gap only while
    a durable binding survives to identify the Task.
    """

    if not rows:
        if committed is not None and committed > cursor:
            identity = _stream_task_identity(session, source, run_ref, cursor)
            if identity is not None:
                task_ref, occurred_at = identity
                return EventPage(
                    events=[
                        _gap_envelope(
                            run_ref,
                            task_ref,
                            first_missing=cursor + 1,
                            last_missing=committed,
                            occurred_at=occurred_at,
                        )
                    ],
                    next_cursor=committed,
                )
        return EventPage(events=[], next_cursor=cursor)
    events: list[EventEnvelope] = []
    expected = cursor + 1
    last_sequence = cursor
    for row in rows:
        if len(events) >= limit:
            break
        if row.source_sequence > expected:
            events.append(
                _gap_envelope(
                    run_ref,
                    _task_ref_of(row.envelope),
                    first_missing=expected,
                    last_missing=row.source_sequence - 1,
                    occurred_at=row.received_at,
                )
            )
            last_sequence = row.source_sequence - 1
            if len(events) >= limit:
                break
        events.append(EventEnvelope.from_dict(row.envelope))
        expected = row.source_sequence + 1
        last_sequence = row.source_sequence
    tail_exists = committed is not None and committed > last_sequence
    if tail_exists and len(events) < limit:
        events.append(
            _gap_envelope(
                run_ref,
                _task_ref_of(rows[-1].envelope),
                first_missing=last_sequence + 1,
                last_missing=committed,
                occurred_at=rows[-1].received_at,
            )
        )
        last_sequence = committed
        return EventPage(events=events, next_cursor=last_sequence, has_more=False)
    return EventPage(
        events=events,
        next_cursor=last_sequence,
        has_more=any(row.source_sequence > last_sequence for row in rows) or tail_exists,
    )


def _same_content(recorded: dict[str, Any], envelope: EventEnvelope) -> bool:
    incoming = envelope.to_dict()
    timestamp_fields = {"occurred_at", "received_at"}
    return {k: v for k, v in recorded.items() if k not in timestamp_fields} == {
        k: v for k, v in incoming.items() if k not in timestamp_fields
    }


__all__ = [
    "CONTRACT_VERSION",
    "EventConflictError",
    "EventPage",
    "SqlEventLog",
    "build_envelope",
]
