"""Agent State Store — persistence layer for AgentState.

Provides AgentStateStore ABC defining the persistence contract, and
InMemoryStateStore for single-process use and testing.
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

from hecate.services.state.state import AgentState


@dataclass
class SessionSummary:
    """Summary information about a persisted session.

    Attributes:
        session_id: The session identifier.
        updated_at: When the session state was last saved.
    """

    session_id: uuid.UUID
    updated_at: datetime


class AgentStateStore(ABC):
    """Abstract interface for persisting AgentState.

    Implementations store per-session AgentState keyed by (agent_id, session_id).
    The store is optional — WorkflowExecutionService functions without one.
    """

    @abstractmethod
    async def save(self, agent_id: uuid.UUID, session_id: uuid.UUID, state: AgentState) -> None:
        """Persist an AgentState.

        Args:
            agent_id: The agent this state belongs to.
            session_id: The session this state belongs to.
            state: The AgentState to persist.
        """
        ...

    @abstractmethod
    async def load(self, agent_id: uuid.UUID, session_id: uuid.UUID) -> AgentState | None:
        """Load an AgentState by key.

        Args:
            agent_id: The agent to load state for.
            session_id: The session to load state for.

        Returns:
            The persisted AgentState, or None if not found.
        """
        ...

    @abstractmethod
    async def delete(self, agent_id: uuid.UUID, session_id: uuid.UUID) -> None:
        """Delete a persisted AgentState.

        Args:
            agent_id: The agent to delete state for.
            session_id: The session to delete state for.
        """
        ...

    @abstractmethod
    async def list_sessions(self, agent_id: uuid.UUID) -> list[SessionSummary]:
        """List all sessions with persisted state for an agent.

        Args:
            agent_id: The agent to list sessions for.

        Returns:
            A list of SessionSummary for each persisted session.
        """
        ...


class InMemoryStateStore(AgentStateStore):
    """In-memory AgentStateStore for single-process use and testing.

    Uses a dict keyed by (agent_id, session_id) with asyncio.Lock per key
    for concurrent access safety. State is lost on process restart.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], AgentState] = {}
        self._timestamps: dict[tuple[uuid.UUID, uuid.UUID], datetime] = {}
        self._locks: dict[tuple[uuid.UUID, uuid.UUID], asyncio.Lock] = {}

    def _key(self, agent_id: uuid.UUID, session_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        return (agent_id, session_id)

    def _get_lock(self, key: tuple[uuid.UUID, uuid.UUID]) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def save(self, agent_id: uuid.UUID, session_id: uuid.UUID, state: AgentState) -> None:
        key = self._key(agent_id, session_id)
        lock = self._get_lock(key)
        async with lock:
            self._store[key] = state
            self._timestamps[key] = datetime.now(UTC)

    async def load(self, agent_id: uuid.UUID, session_id: uuid.UUID) -> AgentState | None:
        key = self._key(agent_id, session_id)
        return self._store.get(key)

    async def delete(self, agent_id: uuid.UUID, session_id: uuid.UUID) -> None:
        key = self._key(agent_id, session_id)
        lock = self._get_lock(key)
        async with lock:
            self._store.pop(key, None)
            self._timestamps.pop(key, None)

    async def list_sessions(self, agent_id: uuid.UUID) -> list[SessionSummary]:
        results: list[SessionSummary] = []
        for (aid, sid), ts in self._timestamps.items():
            if aid == agent_id:
                results.append(SessionSummary(session_id=sid, updated_at=ts))
        return results
