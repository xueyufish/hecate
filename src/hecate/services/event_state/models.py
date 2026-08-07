"""SQLAlchemy ORM model for event log persistence.

The ``EventModel`` table stores ``Event`` records produced by the engine-layer
``EventStore`` ABC (see ``src/hecate/engine/eventstore.py``) for the production
PostgreSQL backend.

Composite primary key ``(session_id, version)`` enforces per-session monotonic
versioning at the database level. The ``id`` UUID is a business identifier for
cross-session referencing (trace correlation) and is not part of the primary
key. The ``org_id`` and ``user_id`` columns are operational columns (nullable)
populated via a ``tenant_context_provider`` when the store is wired through
FastAPI DI; they enable GDPR deletes and per-tenant retention queries but are
NOT part of the EventStore ABC contract (which remains single-key ``session_id``).

The ``payload`` column uses SQLAlchemy ``JSON`` ( PostgreSQL ``JSONB``) so
``payload->>'tool' = 'search'`` queries work without redefining schema. The
``created_at`` column is reserved for future retention/TTL strategies (this
change does not implement cleanup logic — see design.md decision 3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.core.database import Base


class EventModel(Base):
    """ORM model for the append-only event log — one row per persisted Event.

    Rows are addressed by ``(session_id, version)`` matching the engine-layer
    ``EventStore`` ABC contract. ``PostgresEventStore`` is the only writer;
    reads come from ``get_events`` / ``replay`` / ``get_version``.

    The ``payload`` JSONB column carries the ``Event.payload`` dict verbatim.
    ``org_id`` / ``user_id`` are operational columns populated by the wired
    store's ``tenant_context_provider``; they are nullable so test paths that
    bypass DI can still append events. ``created_at`` is reserved for future
    retention sweeps (no cleanup logic ships in this change).
    """

    __tablename__ = "events"

    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    superstep: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    org_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_events_session_version", "session_id", "version"),
        Index("idx_events_org_user_created", "org_id", "user_id", "created_at"),
    )
