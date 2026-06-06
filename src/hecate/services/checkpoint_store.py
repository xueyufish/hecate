"""PostgreSQL-backed checkpoint store for production use.

Provides ``PostgresCheckpointStore`` — a concrete implementation of
:class:`hecate.engine.checkpoint.CheckpointStore` that persists checkpoints
to PostgreSQL via SQLAlchemy async sessions. Supports cross-node recovery,
time-travel debugging, and an LRU memory cache for the hot path.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.engine.checkpoint import CheckpointStore

logger = logging.getLogger(__name__)


class PostgresCheckpointStore(CheckpointStore):
    """PostgreSQL-backed checkpoint store using SQLAlchemy async sessions.

    Supports cross-node recovery and time-travel debugging.
    Uses an LRU cache for recent checkpoints to accelerate the hot path
    (e.g., resuming an interrupted session).

    Args:
        session_factory: Async session factory for creating database sessions.
            Each method call creates and disposes its own session.
        cache_size: Maximum number of sessions to keep in the LRU cache.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        cache_size: int = 128,
    ) -> None:
        """Initialize with a session factory.

        Args:
            session_factory: Async session factory for database operations.
            cache_size: Maximum number of sessions to cache in memory.
        """
        self._session_factory = session_factory
        self._cache: OrderedDict[uuid.UUID, dict] = OrderedDict()
        self._cache_size = cache_size

    def _update_cache(self, session_id: uuid.UUID, record: dict) -> None:
        """Update LRU cache with new checkpoint.

        Args:
            session_id: Session ID to cache under.
            record: Checkpoint record dict.
        """
        if session_id in self._cache:
            self._cache.move_to_end(session_id)
        self._cache[session_id] = record
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    async def save(
        self,
        session_id: uuid.UUID,
        superstep: int,
        node_id: str | None,
        channel_state: dict,
        pending_writes: list | None = None,
        metadata: dict | None = None,
    ) -> uuid.UUID:
        """Persist a checkpoint to PostgreSQL.

        Args:
            session_id: The execution session this checkpoint belongs to.
            superstep: The superstep counter at the time of checkpoint.
            node_id: The node that was executing (None if multiple nodes ran).
            channel_state: A full snapshot of all channel values.
            pending_writes: Writes that were queued but not yet applied.
            metadata: Arbitrary metadata (e.g., interrupt information).

        Returns:
            A unique identifier for the saved checkpoint.
        """
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

            record = self._checkpoint_to_dict(cp)
            self._update_cache(session_id, record)

            logger.debug(f"Saved checkpoint {cp.id} for session {session_id} at superstep {superstep}")
            return cp.id

    async def load(self, session_id: uuid.UUID, checkpoint_id: uuid.UUID | None = None) -> dict | None:
        """Load a checkpoint from PostgreSQL or cache.

        Args:
            session_id: The execution session to load from.
            checkpoint_id: Specific checkpoint to load. If None, returns the
                most recent checkpoint for the session.

        Returns:
            The checkpoint record dict, or None if not found.
        """
        from sqlalchemy import select

        from hecate.models.checkpoint import CheckpointModel

        if checkpoint_id is None:
            # Try cache first for latest checkpoint
            cached = self._cache.get(session_id)
            if cached is not None:
                logger.debug(f"Cache hit for session {session_id}")
                return cached

            # Query database for latest
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

                record = self._checkpoint_to_dict(cp)
                self._update_cache(session_id, record)
                return record

        # Load specific checkpoint by ID
        async with self._session_factory() as session:
            stmt = select(CheckpointModel).where(
                CheckpointModel.session_id == session_id,
                CheckpointModel.id == checkpoint_id,
            )
            result = await session.execute(stmt)
            cp = result.scalar_one_or_none()

            if cp is None:
                return None

            return self._checkpoint_to_dict(cp)

    async def list_checkpoints(self, session_id: uuid.UUID, limit: int = 10) -> list[dict]:
        """List checkpoints for a session, ordered by superstep descending.

        Args:
            session_id: The execution session to query.
            limit: Maximum number of checkpoints to return.

        Returns:
            A list of checkpoint record dicts, newest first.
        """
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
            return [self._checkpoint_to_dict(cp) for cp in cps]

    @staticmethod
    def _checkpoint_to_dict(cp: Any) -> dict:
        """Convert a CheckpointModel ORM instance to a plain dict.

        Args:
            cp: The ORM model instance.

        Returns:
            Dict with checkpoint data.
        """
        return {
            "id": cp.id,
            "session_id": cp.session_id,
            "superstep": cp.superstep,
            "node_id": cp.node_id,
            "channel_state": cp.channel_state,
            "pending_writes": cp.pending_writes,
            "metadata": cp.metadata_,
        }
