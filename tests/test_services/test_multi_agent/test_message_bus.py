"""Unit tests for AgentMessageBus."""

from __future__ import annotations

from hecate.services.multi_agent.message_bus import AgentMessageBus


class TestAgentMessageBus:
    """Tests for the AgentMessageBus class."""

    def test_register_agent(self) -> None:
        """Test registering an agent."""
        bus = AgentMessageBus()
        bus.register_agent("agent-1")
        assert "agent-1" in bus._agents

    def test_subscribe(self) -> None:
        """Test subscribing to a topic."""
        bus = AgentMessageBus()
        bus.subscribe("agent-1", "topic-1")

        subscribers = bus.get_subscribers("topic-1")
        assert "agent-1" in subscribers

    def test_unsubscribe(self) -> None:
        """Test unsubscribing from a topic."""
        bus = AgentMessageBus()
        bus.subscribe("agent-1", "topic-1")
        bus.unsubscribe("agent-1", "topic-1")

        subscribers = bus.get_subscribers("topic-1")
        assert "agent-1" not in subscribers

    def test_publish(self) -> None:
        """Test publishing a message."""
        bus = AgentMessageBus()
        bus.subscribe("agent-1", "topic-1")

        msg = bus.publish("topic-1", "agent-2", {"data": "hello"})

        assert msg.topic == "topic-1"
        assert msg.sender_id == "agent-2"
        assert msg.content == {"data": "hello"}

    def test_direct_message(self) -> None:
        """Test sending a direct message."""
        bus = AgentMessageBus()

        msg = bus.direct_message("agent-1", "agent-2", {"data": "direct"})

        assert msg.sender_id == "agent-1"
        assert msg.recipient_id == "agent-2"

    def test_broadcast(self) -> None:
        """Test broadcasting a message."""
        bus = AgentMessageBus()
        bus.register_agent("agent-1")
        bus.register_agent("agent-2")

        msg = bus.broadcast("agent-1", {"data": "broadcast"})

        assert msg.topic == "broadcast"
        assert msg.sender_id == "agent-1"

    def test_get_messages(self) -> None:
        """Test getting messages for an agent."""
        bus = AgentMessageBus()
        bus.subscribe("agent-1", "topic-1")
        bus.publish("topic-1", "agent-2", {"msg": 1})
        bus.publish("topic-1", "agent-2", {"msg": 2})

        messages = bus.get_messages("agent-1", topic="topic-1")

        assert len(messages) >= 2

    def test_get_subscribers(self) -> None:
        """Test getting subscribers for a topic."""
        bus = AgentMessageBus()
        bus.subscribe("agent-1", "topic-1")
        bus.subscribe("agent-2", "topic-1")
        bus.subscribe("agent-3", "topic-2")

        subs = bus.get_subscribers("topic-1")

        assert len(subs) == 2
        assert "agent-1" in subs
        assert "agent-2" in subs
