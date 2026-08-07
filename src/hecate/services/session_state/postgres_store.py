"""PostgreSQL-backed SessionStateStore — durable persistence for distributed state.

Provides :class:`PostgresSessionStateStore` implementing the engine-layer
``SessionStateStore`` ABC via SQLAlchemy ORM writes to the ``session_states``
table (defined in :mod:`hecate.services.session_state.models`).

PostgreSQL is the **source of truth** in the tiered store design. Every
successful ``save`` MUST result in a durable row; any SQLAlchemy exception
during save or load is propagated to the caller — Redis-backed
``TieredSessionStateStore`` relies on this strict-failure contract to know
when to retry or surface the error.

Key design:

- Uses the project's existing ``async_session_factory`` from
  ``hecate.core.database`` so the store inherits the same connection pool,
  async driver, and PG dialect handling as the rest of the codebase.
- ``save`` uses ``INSERT ... ON CONFLICT DO UPDATE`` (PostgreSQL upsert) to
  make repeated saves for the same ``(org_id, user_id, session_id)`` idempotent.
- ``superstep`` is extracted from ``state.metadata['superstep']`` and stored
  in a dedicated column so ``list_recent`` ordering does not need to descend
  into JSONB.
- ``updated_at`` is server-defaulted to ``func.now()`` and refreshed by the
  upsert — drives ``list_recent`` ordering and TTL filtering.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.sql import desc

from hecate.engine.session_state import SessionState, SessionStateStore, SessionSummary
from hecate.services.session_state.models import SessionStateModel


class PostgresSessionStateStore(SessionStateStore):
    """``SessionStateStore`` implementation backed by the ``session_states`` PG table.

    Uses ``INSERT ... ON CONFLICT DO UPDATE`` for upserts; propagates
    SQLAlchemy exceptions to the caller so the tiered store can decide
    whether to retry or surface the failure.
    """

    def __init__(
        self,
        async_session_factory: async_sessionmaker,
        table_name: str | None = None,
    ) -> None:
        self._async_session_factory = async_session_factory
        self._table_name = table_name

    async def save(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, state: SessionState) -> None:
        json_state = state.model_dump(mode="json")
        superstep_raw = state.metadata.get("superstep") if isinstance(state.metadata, dict) else None
        superstep = superstep_raw if isinstance(superstep_raw, int) else None
        now = datetime.now(UTC)

        async with self._async_session_factory() as session:
            # Acquire row-level lock on existing entry (if any) to serialize
            # concurrent saves on the same (org, user, session) key. The
            # subsequent upsert is still required for the first-time insert
            # case where no row exists yet. The combination gives us
            # last-write-wins semantics safely serialized at the PG tier.
            lock_stmt = (
                select(SessionStateModel)
                .where(
                    SessionStateModel.org_id == org_id,
                    SessionStateModel.user_id == user_id,
                    SessionStateModel.session_id == session_id,
                )
                .with_for_update()
            )
            await session.execute(lock_stmt)

            upsert = (
                pg_insert(SessionStateModel)
                .values(
                    org_id=org_id,
                    user_id=user_id,
                    session_id=session_id,
                    state=json_state,
                    superstep=superstep,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["org_id", "user_id", "session_id"],
                    set_={"state": json_state, "superstep": superstep, "updated_at": now},
                )
            )
            await session.execute(upsert)
            await session.commit()

    async def load(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionState | None:
        async with self._async_session_factory() as session:
            stmt = select(SessionStateModel).where(
                SessionStateModel.org_id == org_id,
                SessionStateModel.user_id == user_id,
                SessionStateModel.session_id == session_id,
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
        if row is None:
            return None
        return SessionState.model_validate_json(row.state if isinstance(row.state, str) else _dump_json(row.state))

    async def list_recent(self, org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10) -> list[SessionSummary]:
        async with self._async_session_factory() as session:
            stmt = (
                select(SessionStateModel)
                .where(SessionStateModel.org_id == org_id, SessionStateModel.user_id == user_id)
                .order_by(desc(SessionStateModel.updated_at))
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [
            SessionSummary(
                session_id=row.session_id,
                org_id=row.org_id,
                user_id=row.user_id,
                updated_at=row.updated_at,
                superstep=row.superstep,
            )
            for row in rows
        ]


def _dump_json(obj: object) -> str:
    import json

    return json.dumps(obj, default=str)
