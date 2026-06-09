"""Append-only event persistence for graph execution state.

Provides the abstract contract (EventStore) and a test implementation:
- ``InMemoryEventStore`` — for testing and single-process use

EventStore records granular execution events (node start/end, tool calls,
channel writes, interrupts) as an append-only log. This complements
CheckpointStore's snapshot model with fine-grained audit trails and
incremental replay capability.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """Standard event categories for execution tracking."""

    NODE_START = "NODE_START"
    NODE_END = "NODE_END"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    CHANNEL_WRITE = "CHANNEL_WRITE"
    LLM_REQUEST = "LLM_REQUEST"
    LLM_RESPONSE = "LLM_RESPONSE"
    INTERRUPT = "INTERRUPT"
    RESUME = "RESUME"
    ERROR = "ERROR"
    PII_DETECTED = "PII_DETECTED"
    CUSTOM = "CUSTOM"


@dataclass(frozen=True)
class Event:
    """Immutable record of a single execution event.

    Each event captures a granular state change during graph execution.
    Events are append-only and versioned per session for incremental replay.

    Attributes:
        id: Unique identifier for this event (auto-generated).
        session_id: The execution session this event belongs to.
        superstep: The superstep counter at the time of the event.
        event_type: The category of event.
        node_id: The node that produced the event (None for session-level events).
        timestamp: When the event occurred (auto-generated UTC).
        payload: Arbitrary event-specific data.
        version: Monotonically increasing version number within the session.
    """

    session_id: uuid.UUID
    superstep: int
    event_type: EventType
    node_id: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = field(default_factory=dict)
    version: int = 0


class EventStore(ABC):
    """Abstract interface for append-only event persistence.

    An EventStore records granular execution events as an append-only log,
    complementing CheckpointStore's snapshot model. Events are versioned
    per session for incremental replay and audit trails.
    """

    @abstractmethod
    async def append(self, event: Event) -> uuid.UUID:
        """Persist an event and return its ID.

        Args:
            event: The event to persist.

        Returns:
            The UUID of the persisted event.
        """
        ...

    @abstractmethod
    async def get_events(
        self,
        session_id: uuid.UUID,
        from_version: int = 0,
    ) -> list[Event]:
        """Retrieve events for a session, optionally from a given version.

        Args:
            session_id: The session to query events for.
            from_version: Minimum version to include (inclusive).

        Returns:
            A list of events in version-ascending order.
        """
        ...

    @abstractmethod
    def replay(
        self,
        session_id: uuid.UUID,
        from_version: int = 0,
    ) -> AsyncGenerator[Event, None]:
        """Yield events for a session as an async stream.

        Args:
            session_id: The session to replay events for.
            from_version: Minimum version to include (inclusive).

        Yields:
            Events in version-ascending order.
        """
        ...

    @abstractmethod
    async def get_version(self, session_id: uuid.UUID) -> int:
        """Return the current version (highest) for a session.

        Args:
            session_id: The session to query.

        Returns:
            The highest version number, or 0 if no events exist.
        """
        ...


class InMemoryEventStore(EventStore):
    """In-memory event store intended for testing and single-process use.

    Stores events in a dict mapping session_id to a list of Event records.
    Version numbers are assigned sequentially per session starting from 1.
    """

    def __init__(self) -> None:
        self._store: dict[uuid.UUID, list[Event]] = {}

    async def append(self, event: Event) -> uuid.UUID:
        """Append an event with an auto-assigned version number.

        Args:
            event: The event to persist.

        Returns:
            The UUID of the persisted event.
        """
        session_events = self._store.setdefault(event.session_id, [])
        next_version = len(session_events) + 1
        versioned = Event(
            session_id=event.session_id,
            superstep=event.superstep,
            event_type=event.event_type,
            node_id=event.node_id,
            id=event.id,
            timestamp=event.timestamp,
            payload=event.payload,
            version=next_version,
        )
        session_events.append(versioned)
        return versioned.id

    async def get_events(
        self,
        session_id: uuid.UUID,
        from_version: int = 0,
    ) -> list[Event]:
        """Retrieve events for a session from a given version.

        Args:
            session_id: The session to query.
            from_version: Minimum version to include (inclusive).

        Returns:
            A list of events in version-ascending order.
        """
        session_events = self._store.get(session_id, [])
        return [e for e in session_events if e.version >= from_version]

    async def replay(
        self,
        session_id: uuid.UUID,
        from_version: int = 0,
    ) -> AsyncGenerator[Event, None]:
        """Yield events for a session as an async stream.

        Args:
            session_id: The session to replay.
            from_version: Minimum version to include (inclusive).

        Yields:
            Events in version-ascending order.
        """
        for event in await self.get_events(session_id, from_version):
            yield event

    async def get_version(self, session_id: uuid.UUID) -> int:
        """Return the current version for a session.

        Args:
            session_id: The session to query.

        Returns:
            The highest version number, or 0 if no events exist.
        """
        session_events = self._store.get(session_id, [])
        return session_events[-1].version if session_events else 0
