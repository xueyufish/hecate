"""Agent message bus for event-driven multi-agent communication.

Provides publish-subscribe messaging, direct messaging, and broadcast
capabilities for agent coordination.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """A message in the agent message bus."""

    id: str
    topic: str
    sender_id: str
    content: dict[str, Any]
    timestamp: datetime
    recipient_id: str | None = None


@dataclass
class Subscription:
    """A topic subscription."""

    agent_id: str
    topic: str
    callback: Any = None


class AgentMessageBus:
    """Event-driven message bus for agent communication.

    Supports:
    - Pub/sub messaging via topics
    - Direct point-to-point messaging
    - Broadcast to all agents
    """

    def __init__(self) -> None:
        """Initialize the message bus."""
        self._subscriptions: dict[str, list[Subscription]] = {}
        self._message_history: list[Message] = []
        self._agents: set[str] = set()

    def register_agent(self, agent_id: str) -> None:
        """Register an agent with the message bus.

        Args:
            agent_id: The agent identifier.
        """
        self._agents.add(agent_id)
        logger.debug(f"Registered agent {agent_id}")

    def subscribe(self, agent_id: str, topic: str) -> None:
        """Subscribe an agent to a topic.

        Args:
            agent_id: The agent identifier.
            topic: The topic to subscribe to.
        """
        if topic not in self._subscriptions:
            self._subscriptions[topic] = []

        sub = Subscription(agent_id=agent_id, topic=topic)
        self._subscriptions[topic].append(sub)
        self._agents.add(agent_id)

        logger.debug(f"Agent {agent_id} subscribed to topic '{topic}'")

    def unsubscribe(self, agent_id: str, topic: str) -> None:
        """Unsubscribe an agent from a topic.

        Args:
            agent_id: The agent identifier.
            topic: The topic to unsubscribe from.
        """
        if topic in self._subscriptions:
            self._subscriptions[topic] = [s for s in self._subscriptions[topic] if s.agent_id != agent_id]

    def publish(
        self,
        topic: str,
        sender_id: str,
        content: dict[str, Any],
    ) -> Message:
        """Publish a message to a topic.

        Args:
            topic: The topic to publish to.
            sender_id: The sender agent identifier.
            content: Message content.

        Returns:
            The published Message.
        """
        msg = Message(
            id=str(uuid4()),
            topic=topic,
            sender_id=sender_id,
            content=content,
            timestamp=datetime.now(UTC),
        )
        self._message_history.append(msg)

        subscribers = self._subscriptions.get(topic, [])
        logger.info(f"Published to topic '{topic}' from {sender_id} ({len(subscribers)} subscribers)")

        return msg

    def direct_message(
        self,
        from_agent: str,
        to_agent: str,
        content: dict[str, Any],
    ) -> Message:
        """Send a direct message between agents.

        Args:
            from_agent: Sender agent identifier.
            to_agent: Recipient agent identifier.
            content: Message content.

        Returns:
            The sent Message.
        """
        msg = Message(
            id=str(uuid4()),
            topic=f"direct:{to_agent}",
            sender_id=from_agent,
            content=content,
            timestamp=datetime.now(UTC),
            recipient_id=to_agent,
        )
        self._message_history.append(msg)

        logger.debug(f"Direct message from {from_agent} to {to_agent}")
        return msg

    def broadcast(
        self,
        from_agent: str,
        content: dict[str, Any],
    ) -> Message:
        """Broadcast a message to all agents.

        Args:
            from_agent: Sender agent identifier.
            content: Message content.

        Returns:
            The broadcast Message.
        """
        msg = Message(
            id=str(uuid4()),
            topic="broadcast",
            sender_id=from_agent,
            content=content,
            timestamp=datetime.now(UTC),
        )
        self._message_history.append(msg)

        logger.info(f"Broadcast from {from_agent} to {len(self._agents)} agents")
        return msg

    def get_messages(
        self,
        agent_id: str,
        topic: str | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Get messages for an agent.

        Args:
            agent_id: The agent identifier.
            topic: Optional topic filter.
            limit: Maximum messages to return.

        Returns:
            List of messages.
        """
        messages = []

        for msg in reversed(self._message_history):
            # Direct messages
            if msg.recipient_id == agent_id:
                messages.append(msg)
                continue

            # Topic messages (if subscribed)
            if topic and msg.topic == topic:
                subs = self._subscriptions.get(topic, [])
                if any(s.agent_id == agent_id for s in subs):
                    messages.append(msg)
                    continue

            # Broadcast messages
            if msg.topic == "broadcast" and msg.sender_id != agent_id:
                messages.append(msg)

            if len(messages) >= limit:
                break

        return messages

    def get_subscribers(self, topic: str) -> list[str]:
        """Get subscribers for a topic.

        Args:
            topic: The topic name.

        Returns:
            List of agent IDs subscribed to the topic.
        """
        subs = self._subscriptions.get(topic, [])
        return [s.agent_id for s in subs]
