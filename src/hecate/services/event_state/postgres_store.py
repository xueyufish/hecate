"""PostgreSQL-backed EventStore — durable append-only event log.

Provides :class:`PostgresEventStore` implementing the engine-layer ``EventStore``
ABC via SQLAlchemy ORM writes to the ``events`` table (defined in
:mod:`hecate.services.event_state.models`).

PostgreSQL is the **durable source of truth** for execution events. Every
successful ``append`` MUST result in a durably committed row; any SQLAlchemy
exception during append or read is propagated to the caller.

Key design:

- Uses the project's existing ``async_session_factory`` from
  ``hecate.core.database`` so the store inherits the same connection pool,
  async driver, and PG dialect handling as the rest of the codebase.
- ``append`` serializes concurrent writes for the same ``session_id`` via
  ``SELECT COALESCE(MAX(version), 0) + 1 ... FOR UPDATE``. The PG row lock
  forms a natural queue without requiring an external lock service.
- ``(session_id, version)`` composite primary key plus ``ON CONFLICT DO
  NOTHING`` is the last-line defense against version collisions; the rare
  collision re-raises as ``EventVersionConflictError`` after one retry.
- ``org_id`` / ``user_id`` are operational columns populated via an optional
  ``tenant_context_provider`` closure; they enable GDPR deletes and
  per-tenant retention queries but are NOT part of the ABC contract.

See ``design.md`` decision 2 for the rationale behind ``MAX+1 FOR UPDATE``
vs alternatives (advisory locks, Redis distributed locks, separate counter
table).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from hecate.engine.eventstore import Event, EventStore, EventVersionConflictError
from hecate.services.event_state.models import EventModel

logger = logging.getLogger(__name__)

TenantContextProvider = Callable[[], tuple[uuid.UUID, uuid.UUID] | None]


@contextmanager
def _null_span() -> Iterator[None]:
    yield None


def _get_tracer() -> Any:
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover — opentelemetry is a hard dependency
        return None
    return trace.get_tracer(__name__)


class PostgresEventStore(EventStore):
    """``EventStore`` implementation backed by the ``events`` PG table.

    Uses ``SELECT ... FOR UPDATE`` to serialize per-session appends and
    propagates SQLAlchemy exceptions so callers can decide retry behavior.
    The ``acquire_event_lock`` ABC method is inherited as the default no-op
    (PG row locking inside ``append`` is sufficient — no external lock
    needed).
    """

    def __init__(
        self,
        async_session_factory: async_sessionmaker,
        tenant_context_provider: TenantContextProvider | None = None,
        max_append_retries: int = 1,
    ) -> None:
        self._async_session_factory = async_session_factory
        self._tenant_context_provider = tenant_context_provider
        self._max_append_retries = max_append_retries

    async def append(self, event: Event) -> uuid.UUID:
        """Persist ``event`` with a per-session monotonic ``version`` assigned server-side.

        Concurrency contract: within one transaction, ``SELECT ... FOR UPDATE``
        on existing rows for the same ``session_id`` forms a queue. The
        ``INSERT ... ON CONFLICT DO NOTHING`` is the last-line defense; if it
        still hits a conflict (extremely rare race), the whole append is
        retried up to ``max_append_retries`` times, then raises
        :class:`EventVersionConflictError`.

        Returns:
            The UUID of the persisted event (same as ``event.id``).
        """
        org_id: uuid.UUID | None = None
        user_id: uuid.UUID | None = None
        if self._tenant_context_provider is not None:
            tenant = self._tenant_context_provider()
            if tenant is not None:
                org_id, user_id = tenant

        payload_dict: dict[str, Any] = dict(event.payload) if event.payload else {}

        tracer = _get_tracer()
        cm = tracer.start_as_current_span("event_store.append") if tracer is not None else _null_span()
        with cm as span:
            last_exc: Exception | None = None
            for _attempt in range(self._max_append_retries + 1):
                try:
                    event_id = await self._append_one(event, org_id, user_id, payload_dict)
                    if span is not None:
                        span.set_attributes(
                            {
                                "event.session_id": str(event.session_id),
                                "event.event_type": str(event.event_type),
                                "event.backend": "postgres",
                            }
                        )
                    logger.info(
                        "event_store_append",
                        extra={
                            "event.session_id": str(event.session_id),
                            "event.event_type": str(event.event_type),
                            "event.backend": "postgres",
                        },
                    )
                    return event_id
                except EventVersionConflictError as exc:
                    last_exc = exc
                    continue
            if last_exc is None:  # pragma: no cover — loop runs at least once
                raise RuntimeError("unreachable: last_exc must be set after loop")
            if span is not None:
                span.record_exception(last_exc)
            raise last_exc

    async def _append_one(
        self,
        event: Event,
        org_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        payload_dict: dict[str, Any],
    ) -> uuid.UUID:
        async with self._async_session_factory() as session:
            lock_stmt = (
                select(func.max(EventModel.version)).where(EventModel.session_id == event.session_id).with_for_update()
            )
            result = await session.execute(lock_stmt)
            current_max = result.scalar_one()
            next_version = (current_max or 0) + 1

            insert_stmt = (
                pg_insert(EventModel)
                .values(
                    session_id=event.session_id,
                    version=next_version,
                    id=event.id,
                    superstep=event.superstep,
                    event_type=str(event.event_type),
                    node_id=event.node_id,
                    trace_id=event.trace_id,
                    payload=payload_dict,
                    org_id=org_id,
                    user_id=user_id,
                )
                .on_conflict_do_nothing(index_elements=["session_id", "version"])
            )
            insert_result = await session.execute(insert_stmt)
            await session.commit()

            if insert_result.rowcount == 0:
                raise EventVersionConflictError(event.session_id, next_version)
            return event.id

    async def append_batch(self, events: list[Event]) -> list[uuid.UUID]:
        """Persist a list of events in a single transaction with batch-internal order preserved.

        Acquires the per-session ``FOR UPDATE`` lock once, assigns sequential
        versions in input order, and inserts all rows in one INSERT statement
        (PG-specific multi-row VALUES). Any version collision raises
        :class:`EventVersionConflictError` after the configured retries.

        Args:
            events: Ordered list of events to persist.

        Returns:
            UUIDs of the persisted events in input order.
        """
        if not events:
            return []
        org_id: uuid.UUID | None = None
        user_id: uuid.UUID | None = None
        if self._tenant_context_provider is not None:
            tenant = self._tenant_context_provider()
            if tenant is not None:
                org_id, user_id = tenant

        rows: list[dict[str, Any]] = []
        for event in events:
            rows.append(
                {
                    "session_id": event.session_id,
                    "id": event.id,
                    "superstep": event.superstep,
                    "event_type": str(event.event_type),
                    "node_id": event.node_id,
                    "trace_id": event.trace_id,
                    "payload": dict(event.payload) if event.payload else {},
                    "org_id": org_id,
                    "user_id": user_id,
                }
            )

        tracer = _get_tracer()
        cm = tracer.start_as_current_span("event_store.append_batch") if tracer is not None else _null_span()
        with cm as span:
            last_exc: Exception | None = None
            for _attempt in range(self._max_append_retries + 1):
                try:
                    async with self._async_session_factory() as session:
                        lock_stmt = (
                            select(func.max(EventModel.version))
                            .where(EventModel.session_id == events[0].session_id)
                            .with_for_update()
                        )
                        result = await session.execute(lock_stmt)
                        current_max = result.scalar_one() or 0
                        for offset, row in enumerate(rows, start=1):
                            row["version"] = current_max + offset

                        insert_stmt = (
                            pg_insert(EventModel)
                            .values(rows)
                            .on_conflict_do_nothing(index_elements=["session_id", "version"])
                        )
                        insert_result = await session.execute(insert_stmt)
                        await session.commit()

                        if insert_result.rowcount != len(rows):
                            raise EventVersionConflictError(events[0].session_id, current_max + 1)

                        if span is not None:
                            span.set_attributes(
                                {
                                    "event.session_id": str(events[0].session_id),
                                    "event.batch_size": len(rows),
                                    "event.backend": "postgres",
                                }
                            )
                        return [row["id"] for row in rows]
                except EventVersionConflictError as exc:
                    last_exc = exc
                    continue
            if last_exc is None:
                raise RuntimeError("unreachable: last_exc must be set after loop")
            if span is not None:
                span.record_exception(last_exc)
            raise last_exc

    async def get_events(
        self,
        session_id: uuid.UUID,
        from_version: int = 0,
    ) -> list[Event]:
        async with self._async_session_factory() as session:
            stmt = (
                select(EventModel)
                .where(
                    EventModel.session_id == session_id,
                    EventModel.version >= from_version,
                )
                .order_by(EventModel.version.asc())
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [_row_to_event(row) for row in rows]

    async def replay(
        self,
        session_id: uuid.UUID,
        from_version: int = 0,
    ) -> AsyncGenerator[Event, None]:
        events = await self.get_events(session_id, from_version)
        for event in events:
            yield event

    async def get_version(self, session_id: uuid.UUID) -> int:
        async with self._async_session_factory() as session:
            stmt = select(func.max(EventModel.version)).where(EventModel.session_id == session_id)
            result = await session.execute(stmt)
            current_max = result.scalar_one()
        return current_max or 0


def _row_to_event(row: EventModel) -> Event:
    """Reconstruct an engine-layer ``Event`` from an ORM row.

    ``event_type`` is stored as a string and re-converted to ``EventType``;
    unknown string values fall back to ``EventType.CUSTOM`` so future event
    types do not break reads of historical rows.
    """
    from hecate.engine.eventstore import EventType

    try:
        event_type = EventType(row.event_type)
    except ValueError:
        event_type = EventType.CUSTOM

    return Event(
        session_id=row.session_id,
        superstep=row.superstep,
        event_type=event_type,
        node_id=row.node_id,
        id=row.id,
        payload=dict(row.payload) if row.payload else {},
        trace_id=row.trace_id,
        version=row.version,
    )
