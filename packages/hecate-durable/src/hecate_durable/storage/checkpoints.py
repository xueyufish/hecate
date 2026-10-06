"""SQL-backed execution checkpoint store (step6d).

Implements the runtime's ``CheckpointStore`` ABC over the host's (or the
platform's) own database. The table is a **discardable cache**: recovery
prefers the warm checkpoint and falls back to log replay / full replay when
rows are missing or unreadable, so no schema evolution or consistency
guarantees are attempted here — a bad row is simply invisible.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from hecate_durable.storage.models import Base


class CheckpointRow(Base):
    """One persisted execution checkpoint (discardable cache row)."""

    __tablename__ = "durable_checkpoint"

    # Composite PK: one session's checkpoints are append-only rows; the
    # uuid checkpoint_id keeps them distinct at identical timestamps.
    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    superstep: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    node_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)


class SqlCheckpointStore:
    """CheckpointStore ABC implementation over SQLAlchemy 2.0 (sync engine).

    The runtime's ABC is async; the sync engine is wrapped there by the
    caller via ``asyncio.to_thread`` (the same pattern as ``SqlDurableStore``)
    — or directly when the caller already runs in a worker thread.
    """

    def __init__(self, session_factory) -> None:
        # A configured sessionmaker (e.g. ``SqlDurableStore.session_factory``).
        self._session_factory = session_factory

    def save_sync(
        self,
        session_id: uuid.UUID,
        superstep: int,
        node_id: str | None,
        channel_state: dict,
        pending_writes: list | None = None,
        metadata: dict | None = None,
    ) -> uuid.UUID:
        del pending_writes  # legacy parameter; ignored (same as InMemory)
        checkpoint_id = uuid.uuid4()
        with self._session_factory() as session, session.begin():
            session.add(
                CheckpointRow(
                    session_id=str(session_id),
                    superstep=int(superstep),
                    node_id=node_id,
                    channel_state=json.loads(json.dumps(channel_state, ensure_ascii=False, default=str)),
                    metadata_json=json.loads(json.dumps(metadata or {}, ensure_ascii=False, default=str)),
                    checkpoint_id=str(checkpoint_id),
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
        return checkpoint_id

    def load_sync(self, session_id: uuid.UUID, checkpoint_id: uuid.UUID | None = None) -> dict | None:
        with self._session_factory() as session:
            if checkpoint_id is None:
                row = session.execute(
                    select(CheckpointRow)
                    .where(CheckpointRow.session_id == str(session_id))
                    .order_by(CheckpointRow.created_at.desc(), CheckpointRow.checkpoint_id.desc())
                    .limit(1)
                ).scalar_one_or_none()
            else:
                row = session.execute(
                    select(CheckpointRow).where(CheckpointRow.checkpoint_id == str(checkpoint_id))
                ).scalar_one_or_none()
            if row is None:
                return None
            return self._record_of(row)

    def list_sync(self, session_id: uuid.UUID, limit: int = 10) -> list[dict]:
        with self._session_factory() as session:
            rows = (
                session.execute(
                    select(CheckpointRow)
                    .where(CheckpointRow.session_id == str(session_id))
                    .order_by(CheckpointRow.created_at.desc(), CheckpointRow.checkpoint_id.desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            return [self._record_of(row) for row in rows]

    def delete_session_sync(self, session_id: uuid.UUID) -> int:
        """Drop every checkpoint of one session (the wake clean-slate path)."""

        with self._session_factory() as session, session.begin():
            from sqlalchemy import delete

            result = session.execute(delete(CheckpointRow).where(CheckpointRow.session_id == str(session_id)))
            return int(result.rowcount or 0)

    def has_checkpoint_sync(self, session_id: uuid.UUID) -> bool:
        with self._session_factory() as session:
            row = session.execute(
                select(CheckpointRow.checkpoint_id).where(CheckpointRow.session_id == str(session_id)).limit(1)
            ).scalar_one_or_none()
            return row is not None

    @staticmethod
    def _record_of(row: CheckpointRow) -> dict:
        return {
            "id": uuid.UUID(row.checkpoint_id),
            "session_id": uuid.UUID(row.session_id),
            "superstep": int(row.superstep),
            "node_id": row.node_id,
            "channel_state": row.channel_state or {},
            "metadata": row.metadata_json or {},
        }


class AsyncSqlCheckpointStore(SqlCheckpointStore):
    """Async facade matching the runtime's CheckpointStore ABC signature."""

    async def save(self, session_id, superstep, node_id, channel_state, pending_writes=None, metadata=None):
        import asyncio

        return await asyncio.to_thread(
            self.save_sync, session_id, superstep, node_id, channel_state, pending_writes, metadata
        )

    async def load(self, session_id, checkpoint_id=None):
        import asyncio

        return await asyncio.to_thread(self.load_sync, session_id, checkpoint_id)

    async def list_checkpoints(self, session_id, limit: int = 10):
        import asyncio

        return await asyncio.to_thread(self.list_sync, session_id, limit)

    async def has_checkpoint(self, session_id: uuid.UUID) -> bool:
        import asyncio

        return await asyncio.to_thread(self.has_checkpoint_sync, session_id)

    async def delete_session(self, session_id: uuid.UUID) -> int:
        import asyncio

        return await asyncio.to_thread(self.delete_session_sync, session_id)
