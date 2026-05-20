from __future__ import annotations

import uuid
from abc import ABC, abstractmethod


class CheckpointStore(ABC):
    """Abstract interface for persisting and retrieving execution checkpoints."""

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
        """Save a checkpoint and return its ID."""
        ...

    @abstractmethod
    async def load(self, session_id: uuid.UUID, checkpoint_id: uuid.UUID | None = None) -> dict | None:
        """Load a checkpoint by ID, or return the latest for the session if ID is None."""
        ...

    @abstractmethod
    async def list_checkpoints(self, session_id: uuid.UUID, limit: int = 10) -> list[dict]:
        """List checkpoints for a session, ordered by superstep descending."""
        ...


class InMemoryCheckpointStore(CheckpointStore):
    """In-memory checkpoint store for testing purposes."""

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
