"""Checkpoint persistence for graph execution state.

Provides the abstract contract (CheckpointStore) and an in-memory
implementation (InMemoryCheckpointStore) for testing and single-process use.
Production implementations live in the services layer.

Per the 1.3.19 log-as-truth change the checkpoint record is demoted to
a materialized cache: it carries only ``channel_state`` + ``log_version``.
Other recovery metadata (superstep, interrupted_node, route) is derived
from the event log. The legacy ``pending_writes`` parameter is accepted
and ignored for backward compatibility.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod


class CheckpointStore(ABC):
    """Materialized cache seam for execution state.

    The cache is intentionally an optimization over the event log:
    implementations MUST treat it as discardable — recovery falls back to
    fold-from-log when the cache is missing or stale.
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
            pending_writes: Accepted for backward compatibility; ignored.
            metadata: Optional bookkeeping metadata.

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
        del pending_writes  # legacy parameter; ignored post-1.3.19
        cp_id = uuid.uuid4()
        record = {
            "id": cp_id,
            "session_id": session_id,
            "superstep": superstep,
            "node_id": node_id,
            "channel_state": channel_state,
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
