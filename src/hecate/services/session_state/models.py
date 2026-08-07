"""SQLAlchemy ORM model for distributed session state persistence.

The ``SessionStateModel`` table stores ``SessionState`` snapshots produced by
the engine-layer ``SessionStateStore`` ABC (see ``src/hecate/engine/session_state.py``)
for the production-tiered backing store that combines a Redis hot-path cache with
PostgreSQL durable persistence.

This model uses the core :class:`hecate.core.database.Base` directly (rather than
:data:`hecate.models.base.BaseModel`) because ``session_states`` is an
upserted key-value snapshot table — not a soft-delete-aware entity model.
The composite primary key ``(org_id, user_id, session_id)`` enforces
multi-tenant isolation at the database level.

Schema columns:

- ``state`` — JSONB storing the serialized ``SessionState`` JSON from
  ``SessionState.model_dump_json()``. JSONB enables future PostgreSQL-side
  queries on session metadata without redefining the schema.
- ``updated_at`` — TIMESTAMPTZ refreshed on every upsert, used for
  ``list_recent`` ordering and TTL filtering.
- ``superstep`` — optional integer extracted from ``state.metadata['superstep']``
  for cheap ORDER BY without parsing JSONB at query time.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.core.database import Base


class SessionStateModel(Base):
    """ORM model for distributed session state — upserted JSONB snapshots.

    Rows are addressed by the triple ``(org_id, user_id, session_id)`` matching
    the ``SessionStateStore`` ABC contract. Two implementations read/write this
    table: ``PostgresSessionStateStore`` (single-backend) and
    ``TieredSessionStateStore`` (Redis cache + this table).

    The ``state`` JSONB column carries the full ``SessionState`` payload
    serialized via :meth:`SessionState.model_dump_json`; deserialization uses
    :meth:`SessionState.model_validate_json`. The ``updated_at`` column drives
    both ``list_recent`` ordering and idle-TTL filtering. The redundant
    ``superstep`` column is extracted from ``state.metadata`` at write time so
    ORDER BY queries do not need to descend into JSONB.
    """

    __tablename__ = "session_states"

    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)

    state: Mapped[dict] = mapped_column(JSON, nullable=False)
    superstep: Mapped[int | None] = mapped_column(Integer, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index(
            "idx_session_states_org_user_updated",
            "org_id",
            "user_id",
            "updated_at",
        ),
    )
