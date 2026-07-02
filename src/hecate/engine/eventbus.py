"""Real-time publish/subscribe event bus for inter-agent communication.

Provides the abstract contract (EventBus) and a session-scoped implementation:
- ``InMemoryEventBus`` -- for single-session, single-process agent coordination

Unlike EventStore (append-only audit log), EventBus supports real-time pub/sub
messaging between agents during graph execution. Agents subscribe to topics
and receive CollaborationEvent messages published by other agents.
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class CollaborationEventType(StrEnum):
    """Standard event categories for multi-agent collaboration."""

    AGENT_MESSAGE = "AGENT_MESSAGE"
    AGENT_REQUEST = "AGENT_REQUEST"
    AGENT_RESPONSE = "AGENT_RESPONSE"
    TASK_ASSIGNED = "TASK_ASSIGNED"
    TASK_COMPLETED = "TASK_COMPLETED"
    NEGOTIATION_PROPOSAL = "NEGOTIATION_PROPOSAL"
    NEGOTIATION_ACCEPT = "NEGOTIATION_ACCEPT"
    NEGOTIATION_REJECT = "NEGOTIATION_REJECT"
    DEBATE_ARGUMENT = "DEBATE_ARGUMENT"
    DEBATE_REBUTTAL = "DEBATE_REBUTTAL"
    DEBATE_CONCLUSION = "DEBATE_CONCLUSION"


@dataclass(frozen=True)
class CollaborationEvent:
    """Immutable record of a single inter-agent collaboration event.

    Attributes:
        id: Unique identifier for this event (auto-generated).
        topic: The pub/sub topic this event was published to.
        sender: The node ID of the agent that published this event.
        event_type: The category of collaboration event.
        payload: Arbitrary event-specific data.
        timestamp: When the event occurred (auto-generated UTC).
    """

    topic: str
    sender: str
    event_type: CollaborationEventType
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


# Type alias for async event handlers
EventHandler = Callable[[CollaborationEvent], Awaitable[None]]


class EventBus(ABC):
    """Abstract interface for real-time pub/sub event bus.

    An EventBus enables agents to communicate via publish/subscribe
    during graph execution. Topics are arbitrary strings (e.g., agent
    node IDs, negotiation channels). Events are delivered asynchronously
    to all subscribed handlers.
    """

    @abstractmethod
    async def publish(self, topic: str, event: CollaborationEvent) -> None:
        """Publish an event to all subscribers of a topic.

        Args:
            topic: The topic to publish to.
            event: The collaboration event to deliver.
        """
        ...

    @abstractmethod
    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Subscribe a handler to receive events on a topic.

        Args:
            topic: The topic to subscribe to.
            handler: An async callable invoked for each event on this topic.
        """
        ...

    @abstractmethod
    async def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        """Remove a handler from a topic's subscriber list.

        Args:
            topic: The topic to unsubscribe from.
            handler: The handler to remove.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Flush pending events and release resources."""
        ...


class InMemoryEventBus(EventBus):
    """In-memory event bus for session-scoped agent coordination.

    Uses a dict of handler lists per topic. Events are dispatched
    immediately to all subscribers via asyncio.create_task.
    Suitable for single-process, single-session use.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._closed = False

    async def publish(self, topic: str, event: CollaborationEvent) -> None:
        """Dispatch event to all subscribers of the topic.

        Args:
            topic: The topic to publish to.
            event: The collaboration event to deliver.
        """
        handlers = self._subscribers.get(topic, [])
        for handler in handlers:
            asyncio.create_task(handler(event))

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Add a handler to a topic's subscriber list.

        Args:
            topic: The topic to subscribe to.
            handler: An async callable invoked for each event on this topic.
        """
        self._subscribers.setdefault(topic, []).append(handler)

    async def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        """Remove a handler from a topic's subscriber list.

        Args:
            topic: The topic to unsubscribe from.
            handler: The handler to remove.
        """
        handlers = self._subscribers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def close(self) -> None:
        """Mark bus as closed and clear subscribers."""
        self._closed = True
        self._subscribers.clear()
