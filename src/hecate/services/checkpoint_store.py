from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.engine.checkpoint import CheckpointStore


class PostgresCheckpointStore(CheckpointStore):
    """PostgreSQL-backed checkpoint store using SQLAlchemy async sessions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._cache: dict[uuid.UUID, dict] = {}

    async def save(
        self,
        session_id: uuid.UUID,
        superstep: int,
        node_id: str | None,
        channel_state: dict,
        pending_writes: list | None = None,
        metadata: dict | None = None,
    ) -> uuid.UUID:
        from hecate.models.checkpoint import CheckpointModel

        async with self._session_factory() as session:
            cp = CheckpointModel(
                session_id=session_id,
                superstep=superstep,
                node_id=node_id,
                channel_state=channel_state,
                pending_writes=pending_writes or [],
                metadata_=metadata or {},
            )
            session.add(cp)
            await session.flush()
            await session.commit()
            record = {
                "id": cp.id,
                "session_id": session_id,
                "superstep": superstep,
                "node_id": node_id,
                "channel_state": channel_state,
                "pending_writes": pending_writes or [],
                "metadata": metadata or {},
            }
            self._cache[session_id] = record
            return cp.id

    async def load(self, session_id: uuid.UUID, checkpoint_id: uuid.UUID | None = None) -> dict | None:
        from sqlalchemy import select

        from hecate.models.checkpoint import CheckpointModel

        if checkpoint_id is None:
            cached = self._cache.get(session_id)
            if cached is not None:
                return cached
            async with self._session_factory() as session:
                stmt = (
                    select(CheckpointModel)
                    .where(CheckpointModel.session_id == session_id)
                    .order_by(CheckpointModel.superstep.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                cp = result.scalar_one_or_none()
                if cp is None:
                    return None
                record = self._model_to_dict(cp)
                self._cache[session_id] = record
                return record
        async with self._session_factory() as session:
            stmt = select(CheckpointModel).where(
                CheckpointModel.session_id == session_id,
                CheckpointModel.id == checkpoint_id,
            )
            result = await session.execute(stmt)
            cp = result.scalar_one_or_none()
            if cp is None:
                return None
            return self._model_to_dict(cp)

    async def list_checkpoints(self, session_id: uuid.UUID, limit: int = 10) -> list[dict]:
        from sqlalchemy import select

        from hecate.models.checkpoint import CheckpointModel

        async with self._session_factory() as session:
            stmt = (
                select(CheckpointModel)
                .where(CheckpointModel.session_id == session_id)
                .order_by(CheckpointModel.superstep.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            cps = result.scalars().all()
            return [self._model_to_dict(cp) for cp in cps]

    @staticmethod
    def _model_to_dict(cp: Any) -> dict:
        return {
            "id": cp.id,
            "session_id": cp.session_id,
            "superstep": cp.superstep,
            "node_id": cp.node_id,
            "channel_state": cp.channel_state,
            "pending_writes": cp.pending_writes,
            "metadata": cp.metadata_,
        }
