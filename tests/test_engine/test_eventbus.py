"""Tests for EventBus, CollaborationEvent, and InMemoryEventBus."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import datetime
from uuid import UUID

import pytest

from hecate.engine.eventbus import (
    CollaborationEvent,
    CollaborationEventType,
    EventBus,
    InMemoryEventBus,
)


class TestCollaborationEventType:
    """Tests for the CollaborationEventType enum."""

    def test_agent_message(self) -> None:
        assert CollaborationEventType.AGENT_MESSAGE == "AGENT_MESSAGE"

    def test_agent_request(self) -> None:
        assert CollaborationEventType.AGENT_REQUEST == "AGENT_REQUEST"

    def test_agent_response(self) -> None:
        assert CollaborationEventType.AGENT_RESPONSE == "AGENT_RESPONSE"

    def test_task_assigned(self) -> None:
        assert CollaborationEventType.TASK_ASSIGNED == "TASK_ASSIGNED"

    def test_task_completed(self) -> None:
        assert CollaborationEventType.TASK_COMPLETED == "TASK_COMPLETED"

    def test_negotiation_proposal(self) -> None:
        assert CollaborationEventType.NEGOTIATION_PROPOSAL == "NEGOTIATION_PROPOSAL"

    def test_negotiation_accept(self) -> None:
        assert CollaborationEventType.NEGOTIATION_ACCEPT == "NEGOTIATION_ACCEPT"

    def test_negotiation_reject(self) -> None:
        assert CollaborationEventType.NEGOTIATION_REJECT == "NEGOTIATION_REJECT"

    def test_debate_argument(self) -> None:
        assert CollaborationEventType.DEBATE_ARGUMENT == "DEBATE_ARGUMENT"

    def test_debate_rebuttal(self) -> None:
        assert CollaborationEventType.DEBATE_REBUTTAL == "DEBATE_REBUTTAL"

    def test_debate_conclusion(self) -> None:
        assert CollaborationEventType.DEBATE_CONCLUSION == "DEBATE_CONCLUSION"


class TestCollaborationEvent:
    """Tests for the CollaborationEvent dataclass."""

    def test_creation_with_required_fields(self) -> None:
        event = CollaborationEvent(
            topic="negotiation",
            sender="agent_a",
            event_type=CollaborationEventType.AGENT_MESSAGE,
        )
        assert event.topic == "negotiation"
        assert event.sender == "agent_a"
        assert event.event_type == CollaborationEventType.AGENT_MESSAGE
        assert isinstance(event.id, UUID)
        assert isinstance(event.timestamp, datetime)
        assert event.payload == {}

    def test_creation_with_payload(self) -> None:
        event = CollaborationEvent(
            topic="agent_b",
            sender="agent_a",
            event_type=CollaborationEventType.NEGOTIATION_PROPOSAL,
            payload={"content": "I propose..."},
        )
        assert event.payload == {"content": "I propose..."}

    def test_auto_generated_id_unique(self) -> None:
        event1 = CollaborationEvent(
            topic="t",
            sender="a",
            event_type=CollaborationEventType.AGENT_MESSAGE,
        )
        event2 = CollaborationEvent(
            topic="t",
            sender="a",
            event_type=CollaborationEventType.AGENT_MESSAGE,
        )
        assert event1.id != event2.id

    def test_auto_generated_timestamp_utc(self) -> None:
        event = CollaborationEvent(
            topic="t",
            sender="a",
            event_type=CollaborationEventType.AGENT_MESSAGE,
        )
        assert event.timestamp.tzinfo is not None

    def test_immutability(self) -> None:
        event = CollaborationEvent(
            topic="t",
            sender="a",
            event_type=CollaborationEventType.AGENT_MESSAGE,
        )
        with pytest.raises(FrozenInstanceError):
            event.topic = "other"  # type: ignore[misc]


class TestEventBusABC:
    """Tests for EventBus abstract class."""

    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            EventBus()  # type: ignore[abstract]


class TestInMemoryEventBus:
    """Tests for InMemoryEventBus implementation."""

    async def test_publish_and_subscribe(self) -> None:
        bus = InMemoryEventBus()
        received: list[CollaborationEvent] = []

        async def handler(event: CollaborationEvent) -> None:
            received.append(event)

        await bus.subscribe("agent_a", handler)
        event = CollaborationEvent(
            topic="agent_a",
            sender="system",
            event_type=CollaborationEventType.AGENT_MESSAGE,
            payload={"content": "hello"},
        )
        await bus.publish("agent_a", event)
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0].payload["content"] == "hello"
        await bus.close()

    async def test_multiple_subscribers(self) -> None:
        bus = InMemoryEventBus()
        received_a: list[CollaborationEvent] = []
        received_b: list[CollaborationEvent] = []

        async def handler_a(event: CollaborationEvent) -> None:
            received_a.append(event)

        async def handler_b(event: CollaborationEvent) -> None:
            received_b.append(event)

        await bus.subscribe("topic_x", handler_a)
        await bus.subscribe("topic_x", handler_b)
        event = CollaborationEvent(
            topic="topic_x",
            sender="s",
            event_type=CollaborationEventType.AGENT_MESSAGE,
        )
        await bus.publish("topic_x", event)
        await asyncio.sleep(0.05)
        assert len(received_a) == 1
        assert len(received_b) == 1
        await bus.close()

    async def test_topic_isolation(self) -> None:
        bus = InMemoryEventBus()
        received: list[CollaborationEvent] = []

        async def handler(event: CollaborationEvent) -> None:
            received.append(event)

        await bus.subscribe("agent_a", handler)
        event_b = CollaborationEvent(
            topic="agent_b",
            sender="s",
            event_type=CollaborationEventType.AGENT_MESSAGE,
        )
        await bus.publish("agent_b", event_b)
        await asyncio.sleep(0.05)
        assert len(received) == 0
        await bus.close()

    async def test_unsubscribe(self) -> None:
        bus = InMemoryEventBus()
        received: list[CollaborationEvent] = []

        async def handler(event: CollaborationEvent) -> None:
            received.append(event)

        await bus.subscribe("topic", handler)
        await bus.unsubscribe("topic", handler)
        event = CollaborationEvent(
            topic="topic",
            sender="s",
            event_type=CollaborationEventType.AGENT_MESSAGE,
        )
        await bus.publish("topic", event)
        await asyncio.sleep(0.05)
        assert len(received) == 0
        await bus.close()

    async def test_close_clears_subscribers(self) -> None:
        bus = InMemoryEventBus()
        received: list[CollaborationEvent] = []

        async def handler(event: CollaborationEvent) -> None:
            received.append(event)

        await bus.subscribe("topic", handler)
        await bus.close()
        event = CollaborationEvent(
            topic="topic",
            sender="s",
            event_type=CollaborationEventType.AGENT_MESSAGE,
        )
        await bus.publish("topic", event)
        await asyncio.sleep(0.05)
        assert len(received) == 0


class TestPregelRuntimeEventBusIntegration:
    """Tests for PregelRuntime passing event_bus via execution_context."""

    async def test_event_bus_in_context(self) -> None:
        from hecate.engine.channel import ChannelDef, ChannelType
        from hecate.engine.checkpoint import InMemoryCheckpointStore
        from hecate.engine.pregel import PregelRuntime
        from hecate.engine.types import CompiledGraph, Edge, NodeConfig, NodeType
        from hecate.engine.worker import Worker, WorkerResult

        captured_ctx: dict = {}

        class CaptureWorker(Worker):
            async def execute(
                self,
                node_id: str,
                node_config: dict,
                channel_snapshot: dict,
                execution_context: dict | None = None,
            ) -> WorkerResult:
                if execution_context:
                    captured_ctx.update(execution_context)
                return WorkerResult(node_id=node_id, channel_updates={"messages": ["hi"]})

        graph = CompiledGraph(
            nodes={
                "test_node": NodeConfig(id="test_node", type=NodeType.CONVERSATION, config={}),
            },
            edges=[Edge(source="test_node", target="__end__")],
            channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
            entry_point="test_node",
            name="test",
        )

        bus = InMemoryEventBus()
        runtime = PregelRuntime(
            graph=graph,
            worker=CaptureWorker(),
            checkpoint_store=InMemoryCheckpointStore(),
            event_bus=bus,
        )

        import uuid as uuid_mod

        async for _ in runtime.execute(
            initial_input={"messages": [{"role": "user", "content": "hi"}]},
            session_id=uuid_mod.uuid4(),
        ):
            pass

        assert "event_bus" in captured_ctx
        assert captured_ctx["event_bus"] is bus

    async def test_no_event_bus_omitted_from_context(self) -> None:
        from hecate.engine.channel import ChannelDef, ChannelType
        from hecate.engine.checkpoint import InMemoryCheckpointStore
        from hecate.engine.pregel import PregelRuntime
        from hecate.engine.types import CompiledGraph, Edge, NodeConfig, NodeType
        from hecate.engine.worker import Worker, WorkerResult

        captured_ctx: dict = {}

        class CaptureWorker(Worker):
            async def execute(
                self,
                node_id: str,
                node_config: dict,
                channel_snapshot: dict,
                execution_context: dict | None = None,
            ) -> WorkerResult:
                if execution_context:
                    captured_ctx.update(execution_context)
                return WorkerResult(node_id=node_id, channel_updates={"messages": ["hi"]})

        graph = CompiledGraph(
            nodes={
                "test_node": NodeConfig(id="test_node", type=NodeType.CONVERSATION, config={}),
            },
            edges=[Edge(source="test_node", target="__end__")],
            channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
            entry_point="test_node",
            name="test",
        )

        runtime = PregelRuntime(
            graph=graph,
            worker=CaptureWorker(),
            checkpoint_store=InMemoryCheckpointStore(),
        )

        import uuid as uuid_mod

        async for _ in runtime.execute(
            initial_input={"messages": [{"role": "user", "content": "hi"}]},
            session_id=uuid_mod.uuid4(),
        ):
            pass

        assert "event_bus" not in captured_ctx
