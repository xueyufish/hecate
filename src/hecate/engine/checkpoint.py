"""Checkpoint persistence for graph execution state.

Provides the abstract contract (CheckpointStore) and a default in-memory
implementation (InMemoryCheckpointStore) for saving and restoring execution
state at superstep boundaries. Checkpoints enable interrupt/resume semantics
and fault-tolerant replay.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod


class CheckpointStore(ABC):
    """Abstract interface for persisting and retrieving execution checkpoints.

    A checkpoint captures the full channel state, the current superstep counter,
    the node that was executing, and optional pending writes. Implementations may
    store checkpoints in memory, a database, or a distributed store.
    """

    @abstractmethod
    async def save(
        self,
        session_id: uuid.UUID,
        superstep: int,
        node_id: str | None,
        channel_state: dict,
        pending_writes: list | None = None,
        metadata: dict | None = None,
    ) -> uuid.UUID:
        """Persist a checkpoint for the given session.

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
        ...

    @abstractmethod
    async def load(self, session_id: uuid.UUID, checkpoint_id: uuid.UUID | None = None) -> dict | None:
        """Load a checkpoint by ID, or return the latest for the session.

        Args:
            session_id: The execution session to load from.
            checkpoint_id: Specific checkpoint to load. If None, returns the
                most recent checkpoint for the session.

        Returns:
            The checkpoint record dict, or None if not found.
        """
        ...

    @abstractmethod
    async def list_checkpoints(self, session_id: uuid.UUID, limit: int = 10) -> list[dict]:
        """List checkpoints for a session, ordered by superstep descending.

        Args:
            session_id: The execution session to query.
            limit: Maximum number of checkpoints to return.

        Returns:
            A list of checkpoint record dicts, newest first.
        """
        ...


class InMemoryCheckpointStore(CheckpointStore):
    """In-memory checkpoint store intended for testing and single-process use.

    Uses dual storage:
    - ``_store`` maps each session_id to a chronological list of all checkpoint
      records (full history for ``list_checkpoints`` and ID-based ``load``).
    - ``_cache`` maps each session_id to its most recent checkpoint record
      (O(1) lookup for the common case of loading the latest checkpoint).
    """

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, list[dict]] = {}
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
        cp_id = uuid.uuid4()
        record = {
            "id": cp_id,
            "session_id": session_id,
            "superstep": superstep,
            "node_id": node_id,
            "channel_state": channel_state,
            "pending_writes": pending_writes or [],
            "metadata": metadata or {},
        }
        if session_id not in self._store:
            self._store[session_id] = []
        self._store[session_id].append(record)
        self._cache[session_id] = record
        return cp_id

    async def load(self, session_id: uuid.UUID, checkpoint_id: uuid.UUID | None = None) -> dict | None:
        if checkpoint_id is None:
            return self._cache.get(session_id)
        checkpoints = self._store.get(session_id, [])
        for cp in checkpoints:
            if cp["id"] == checkpoint_id:
                return cp
        return None

    async def list_checkpoints(self, session_id: uuid.UUID, limit: int = 10) -> list[dict]:
        cps = self._store.get(session_id, [])
        return cps[-limit:]
