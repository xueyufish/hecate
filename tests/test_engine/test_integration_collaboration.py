"""Integration tests for multi-agent advanced collaboration features (2.3a-2.3d).

Tests the wiring between EventBus, negotiation/debate templates, TaskAllocator,
and AgentTool through PregelRuntime execution with stub workers.
"""

from __future__ import annotations

import uuid
from typing import Any

from hecate.engine.agent_tool import AgentDefinition, AgentTool
from hecate.engine.checkpoint import InMemoryCheckpointStore
from hecate.engine.compiler import GraphCompiler
from hecate.engine.eventbus import CollaborationEvent, CollaborationEventType, InMemoryEventBus
from hecate.engine.pregel import PregelRuntime
from hecate.engine.task_allocator import RoundRobinTaskAllocator, SemanticTaskAllocator
from hecate.engine.templates import build_debate_graph, build_negotiation_graph
from hecate.engine.types import (
    WorkerResult,
)
from hecate.engine.worker import Worker

# ---------------------------------------------------------------------------
# Stub workers for integration tests
# ---------------------------------------------------------------------------


class EventBusAwareWorker(Worker):
    """Worker that publishes a CollaborationEvent via the event_bus in execution_context."""

    def __init__(self, publish_at_node: str = "agent_a") -> None:
        super().__init__()
        self._publish_at = publish_at_node
        self.published_events: list[CollaborationEvent] = []

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        updates: dict[str, Any] = {"messages": [f"{node_id}_output"]}

        if node_id == self._publish_at and execution_context:
            bus = execution_context.get("event_bus")
            if bus is not None:
                event = CollaborationEvent(
                    topic="test_topic",
                    sender=node_id,
                    event_type=CollaborationEventType.AGENT_MESSAGE,
                    payload={"text": f"hello from {node_id}"},
                )
                self.published_events.append(event)
                await bus.publish("test_topic", event)

        return WorkerResult(node_id=node_id, channel_updates=updates)


class NegotiationWorker(Worker):
    """Worker that simulates the negotiation protocol.

    - proposer: writes to negotiation_channel
    - responder: writes to agreement_status ("accepted" after 1 round)
    - check_agreement: condition node, writes _route based on agreement_status
    """

    def __init__(self, accept_after_rounds: int = 1) -> None:
        super().__init__()
        self._accept_after = accept_after_rounds
        self._round = 0

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        updates: dict[str, Any] = {"messages": [f"{node_id}_output"]}

        if node_id == "proposer":
            updates["negotiation_channel"] = f"proposal_round_{self._round}"
        elif node_id == "responder":
            status = channel_snapshot.get("agreement_status", "")
            if status == "accepted" or self._round >= self._accept_after - 1:
                updates["agreement_status"] = "accepted"
            else:
                updates["agreement_status"] = "counter"
                self._round += 1
        elif node_id == "check_agreement":
            status = channel_snapshot.get("agreement_status", "")
            updates["_route"] = "true" if status == "accepted" else "false"

        return WorkerResult(node_id=node_id, channel_updates=updates)


class DebateWorker(Worker):
    """Worker that simulates the debate protocol.

    - debater_a / debater_b: write arguments to messages
    - check_rounds: condition node, checks debate_round < max_debate_rounds
    - judge: writes verdict to messages
    """

    def __init__(self, total_rounds: int = 2) -> None:
        super().__init__()
        self._total_rounds = total_rounds

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        updates: dict[str, Any] = {"messages": [f"{node_id}_argument"]}

        if node_id == "check_rounds":
            current_round = channel_snapshot.get("debate_round", 0)
            max_rounds = channel_snapshot.get("max_debate_rounds", self._total_rounds)
            if current_round < max_rounds:
                updates["_route"] = "true"
                updates["debate_round"] = current_round + 1
            else:
                updates["_route"] = "false"
        elif node_id == "judge":
            updates["messages"] = ["judge_verdict"]
        elif node_id in ("debater_a", "debater_b"):
            current_round = channel_snapshot.get("debate_round", 0)
            updates["debate_round"] = current_round

        return WorkerResult(node_id=node_id, channel_updates=updates)


class MockPort:
    """Minimal mock port for AgentTool integration tests."""

    def __init__(self, response: str = "sub-agent result") -> None:
        self._response = response
        self.last_agent_definition: Any = None
        self.last_agent_id: uuid.UUID | None = None
        self.call_count: int = 0

    async def agent_execute(
        self,
        agent_id: uuid.UUID,
        messages: list[dict],
        channel_snapshot: dict,
        context: dict | None = None,
        agent_definition: Any | None = None,
    ) -> dict:
        self.last_agent_id = agent_id
        self.last_agent_definition = agent_definition
        self.call_count += 1
        return {"response": self._response, "usage": {"tokens": 10}}


# ---------------------------------------------------------------------------
# 10.1 EventBus + PregelRuntime integration
# ---------------------------------------------------------------------------


async def test_eventbus_in_pregel_execution_context():
    """EventBus is passed in execution_context and workers can publish events."""
    from hecate.engine.types import ChannelDef, ChannelType, CompiledGraph, Edge, NodeConfig, NodeType

    graph = CompiledGraph(
        entry_point="agent_a",
        channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        nodes={
            "agent_a": NodeConfig(id="agent_a", type=NodeType.AGENT, config={}),
        },
        edges=[Edge(source="agent_a", target="__end__")],
    )

    event_bus = InMemoryEventBus()
    worker = EventBusAwareWorker(publish_at_node="agent_a")
    checkpoint = InMemoryCheckpointStore()
    runtime = PregelRuntime(
        graph=graph,
        worker=worker,
        checkpoint_store=checkpoint,
        event_bus=event_bus,
    )

    results = []
    async for event in runtime.execute(
        session_id=uuid.uuid4(),
        initial_input={"messages": [{"role": "user", "content": "test"}]},
    ):
        results.append(event)

    assert len(worker.published_events) == 1
    event = worker.published_events[0]
    assert event.event_type == CollaborationEventType.AGENT_MESSAGE
    assert event.sender == "agent_a"
    assert event.payload["text"] == "hello from agent_a"

    # Verify subscribers receive the event
    received: list[CollaborationEvent] = []

    async def handler(evt: CollaborationEvent) -> None:
        received.append(evt)

    await event_bus.subscribe("test_topic", handler)
    # Publish another event to verify subscriber works
    await event_bus.publish(
        "test_topic",
        CollaborationEvent(
            topic="test_topic",
            sender="agent_b",
            event_type=CollaborationEventType.AGENT_MESSAGE,
            payload={"text": "hello from agent_b"},
        ),
    )

    # Yield control so asyncio.create_task handlers can run
    import asyncio

    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].sender == "agent_b"
    await event_bus.close()


async def test_eventbus_not_in_context_when_not_configured():
    """When event_bus is not configured, execution_context has no event_bus key."""
    from hecate.engine.types import ChannelDef, ChannelType, CompiledGraph, Edge, NodeConfig, NodeType

    class ContextInspectWorker(Worker):
        def __init__(self) -> None:
            super().__init__()
            self.contexts: list[dict | None] = []

        async def execute(
            self,
            node_id: str,
            node_config: dict,
            channel_snapshot: dict,
            execution_context: dict | None = None,
        ) -> WorkerResult:
            self.contexts.append(execution_context)
            return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_out"]})

    graph = CompiledGraph(
        entry_point="node_a",
        channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        nodes={
            "node_a": NodeConfig(id="node_a", type=NodeType.AGENT, config={}),
        },
        edges=[Edge(source="node_a", target="__end__")],
    )

    worker = ContextInspectWorker()
    runtime = PregelRuntime(
        graph=graph,
        worker=worker,
        checkpoint_store=InMemoryCheckpointStore(),
    )

    async for _ in runtime.execute(session_id=uuid.uuid4(), initial_input={"messages": []}):
        pass

    assert len(worker.contexts) == 1
    ctx = worker.contexts[0]
    assert ctx is not None
    assert "event_bus" not in ctx


# ---------------------------------------------------------------------------
# 10.2 Negotiation graph execution
# ---------------------------------------------------------------------------


async def test_negotiation_graph_executes_to_agreement():
    """Negotiation graph runs proposer→responder→check loop until agreement."""
    config = build_negotiation_graph(
        proposer_model="test-model",
        responder_model="test-model",
    )
    compiled = GraphCompiler().compile(config)

    worker = NegotiationWorker(accept_after_rounds=1)
    runtime = PregelRuntime(
        graph=compiled,
        worker=worker,
        checkpoint_store=InMemoryCheckpointStore(),
    )

    results = []
    async for event in runtime.execute(
        session_id=uuid.uuid4(),
        initial_input={"messages": [{"role": "user", "content": "negotiate price"}]},
    ):
        results.append(event)

    assert len(results) > 0
    final_state = results[-1]
    assert final_state["type"] == "values"
    assert final_state["state"]["agreement_status"] == "accepted"
    assert len(final_state["state"]["messages"]) > 0


async def test_negotiation_graph_multi_round():
    """Negotiation graph runs multiple rounds before agreement."""
    config = build_negotiation_graph(
        proposer_model="test-model",
        responder_model="test-model",
        max_rounds=5,
    )
    compiled = GraphCompiler().compile(config)

    worker = NegotiationWorker(accept_after_rounds=3)
    runtime = PregelRuntime(
        graph=compiled,
        worker=worker,
        checkpoint_store=InMemoryCheckpointStore(),
    )

    results = []
    async for event in runtime.execute(
        session_id=uuid.uuid4(),
        initial_input={"messages": []},
    ):
        results.append(event)

    final_state = results[-1]
    assert final_state["state"]["agreement_status"] == "accepted"


# ---------------------------------------------------------------------------
# 10.3 Debate graph execution
# ---------------------------------------------------------------------------


async def test_debate_graph_executes_with_judge():
    """Debate graph runs alternating turns then judge delivers verdict."""
    config = build_debate_graph(
        debater_a_model="model-a",
        debater_b_model="model-b",
        judge_model="judge-model",
        rounds=2,
    )
    compiled = GraphCompiler().compile(config)

    worker = DebateWorker(total_rounds=2)
    runtime = PregelRuntime(
        graph=compiled,
        worker=worker,
        checkpoint_store=InMemoryCheckpointStore(),
    )

    results = []
    async for event in runtime.execute(
        session_id=uuid.uuid4(),
        initial_input={"messages": []},
    ):
        results.append(event)

    assert len(results) > 0
    final_state = results[-1]
    assert final_state["type"] == "values"
    messages = final_state["state"]["messages"]
    assert len(messages) > 0
    assert "judge_verdict" in messages


async def test_debate_graph_executes_without_judge():
    """Debate graph without judge terminates after rounds complete."""
    config = build_debate_graph(
        debater_a_model="model-a",
        debater_b_model="model-b",
        judge_model=None,
        rounds=1,
    )
    compiled = GraphCompiler().compile(config)

    worker = DebateWorker(total_rounds=1)
    runtime = PregelRuntime(
        graph=compiled,
        worker=worker,
        checkpoint_store=InMemoryCheckpointStore(),
    )

    results = []
    async for event in runtime.execute(
        session_id=uuid.uuid4(),
        initial_input={"messages": []},
    ):
        results.append(event)

    assert len(results) > 0
    final_state = results[-1]
    messages = final_state["state"]["messages"]
    assert len(messages) > 0
    assert "judge_verdict" not in messages


# ---------------------------------------------------------------------------
# 10.4 TaskAllocator integration with agent selection
# ---------------------------------------------------------------------------


async def test_round_robin_allocator_selects_agents():
    """RoundRobinTaskAllocator cycles through agents in order."""
    from unittest.mock import MagicMock

    allocator = RoundRobinTaskAllocator()

    candidates = []
    for i in range(3):
        agent = MagicMock()
        agent.id = uuid.uuid4()
        agent.name = f"agent_{i}"
        candidates.append(agent)

    first = await allocator.allocate("task 1", candidates)
    second = await allocator.allocate("task 2", candidates)
    third = await allocator.allocate("task 3", candidates)
    fourth = await allocator.allocate("task 4", candidates)

    assert first is not None
    assert second is not None
    assert third is not None
    assert fourth is not None
    assert first.id == candidates[0].id
    assert second.id == candidates[1].id
    assert third.id == candidates[2].id
    assert fourth.id == candidates[0].id  # wraps around


async def test_semantic_allocator_calls_llm():
    """SemanticTaskAllocator calls port.llm_invoke to rank candidates."""
    from unittest.mock import MagicMock

    async def mock_invoke(messages, config):
        yield 'Based on the task "write code", Agent_0 is the best match.'

    port = MagicMock()
    port.llm_invoke = mock_invoke

    allocator = SemanticTaskAllocator(port=port)

    candidates = []
    for i in range(3):
        agent = MagicMock()
        agent.id = uuid.uuid4()
        agent.name = f"Agent_{i}"
        agent.description = f"Agent {i} description"
        candidates.append(agent)

    result = await allocator.allocate("write code", candidates)

    assert result is not None
    assert result.id == candidates[0].id


# ---------------------------------------------------------------------------
# 10.5 AgentTool invocation through mock port
# ---------------------------------------------------------------------------


async def test_agent_tool_invokes_port_with_definition():
    """AgentTool.execute() calls port.agent_execute with agent_definition."""
    agent_id = uuid.uuid4()
    definition = AgentDefinition(
        agent_id=agent_id,
        description="A specialist tool",
        tools=["search", "read"],
        disallowed_tools=["agent_execute"],
        context_mode="inherited",
        max_turns=5,
        timeout_seconds=30.0,
    )

    tool = AgentTool(definition=definition, agent_name="specialist")
    port = MockPort(response="specialist response")

    result = await tool.execute(
        args={"task": "analyze this data"},
        port=port,
        channel_snapshot={"messages": [{"role": "user", "content": "context"}]},
    )

    assert result["response"] == "specialist response"
    assert port.call_count == 1
    assert port.last_agent_id == agent_id
    assert port.last_agent_definition is definition


async def test_agent_tool_isolated_context():
    """AgentTool with context_mode='isolated' sends only task message."""
    agent_id = uuid.uuid4()
    definition = AgentDefinition(
        agent_id=agent_id,
        description="Isolated tool",
        context_mode="isolated",
    )

    captured_messages: list[dict] = []

    class CapturingPort:
        async def agent_execute(
            self,
            agent_id: uuid.UUID,
            messages: list[dict],
            channel_snapshot: dict,
            context: dict | None = None,
            agent_definition: Any | None = None,
        ) -> dict:
            captured_messages.extend(messages)
            return {"response": "done"}

    tool = AgentTool(definition=definition)
    await tool.execute(
        args={"task": "do something"},
        port=CapturingPort(),
        channel_snapshot={"messages": [{"role": "user", "content": "parent context"}]},
    )

    assert len(captured_messages) == 1
    assert captured_messages[0]["content"] == "do something"
    assert captured_messages[0]["role"] == "user"


async def test_agent_tool_timeout():
    """AgentTool enforces timeout_seconds."""
    import asyncio

    agent_id = uuid.uuid4()
    definition = AgentDefinition(
        agent_id=agent_id,
        description="Timeout tool",
        timeout_seconds=0.01,
    )

    class SlowPort:
        async def agent_execute(
            self,
            agent_id: uuid.UUID,
            messages: list[dict],
            channel_snapshot: dict,
            context: dict | None = None,
            agent_definition: Any | None = None,
        ) -> dict:
            await asyncio.sleep(10)
            return {"response": "never"}  # pragma: no cover

    tool = AgentTool(definition=definition)
    result = await tool.execute(args={"task": "slow task"}, port=SlowPort())

    assert "error" in result
    assert result["timed_out"] is True
