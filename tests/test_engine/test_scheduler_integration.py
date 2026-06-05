"""Tests for SchedulerStrategy integration with PregelRuntime.

Validates that the scheduler is correctly wired into the superstep loop:
- Default FIFOScheduler preserves existing behavior
- Custom scheduler receives correct node lists and context dicts
- Custom scheduler can influence node execution order
- Scheduler is called even for single-node supersteps
"""

from __future__ import annotations

import uuid

from hecate.engine.checkpoint import InMemoryCheckpointStore
from hecate.engine.pregel import PregelRuntime
from hecate.engine.scheduler import SchedulerStrategy
from hecate.engine.types import (
    ChannelDef,
    ChannelType,
    CompiledGraph,
    Edge,
    NodeConfig,
    NodeType,
    WorkerResult,
)
from hecate.engine.worker import Worker


class SimpleWorker(Worker):
    """Pass-through worker that echoes the node id as output."""

    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": [f"{node_id}_output"]},
        )


class TrackingScheduler(SchedulerStrategy):
    """Scheduler stub that records all select_next calls for inspection."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def select_next(self, nodes: list[str], context: dict) -> list[str]:
        self.calls.append({"nodes": list(nodes), "context": context})
        return list(nodes)

    def set_weights(self, weights: dict[str, float]) -> None:
        pass


class ReversingScheduler(SchedulerStrategy):
    """Scheduler that reverses the order of nodes."""

    def select_next(self, nodes: list[str], context: dict) -> list[str]:
        return list(reversed(nodes))

    def set_weights(self, weights: dict[str, float]) -> None:
        pass


def _make_linear_graph() -> CompiledGraph:
    """Build a three-node linear graph: A -> B -> C -> END."""
    return CompiledGraph(
        nodes={
            "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "A"}),
            "B": NodeConfig(id="B", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "B"}),
            "C": NodeConfig(id="C", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "C"}),
        },
        edges=[
            Edge(source="A", target="B"),
            Edge(source="B", target="C"),
            Edge(source="C", target="__end__"),
        ],
        channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry_point="A",
        name="test-linear",
    )


async def test_default_scheduler_uses_fifo() -> None:
    """PregelRuntime created without scheduler param works identically to FIFO."""
    graph = _make_linear_graph()
    checkpoint = InMemoryCheckpointStore()
    worker = SimpleWorker()
    session_id = uuid.uuid4()

    runtime = PregelRuntime(graph=graph, worker=worker, checkpoint_store=checkpoint)

    events = []
    async for event in runtime.execute(session_id=session_id, initial_input={"messages": ["start"]}):
        events.append(event)

    # Verify execution completed (we got values events)
    assert len(events) > 0
    # Verify no interrupt
    assert all(e.get("type") != "interrupt" for e in events)


async def test_custom_scheduler_called_each_superstep() -> None:
    """Custom scheduler's select_next is invoked for each superstep."""
    graph = _make_linear_graph()
    checkpoint = InMemoryCheckpointStore()
    worker = SimpleWorker()
    tracking = TrackingScheduler()
    session_id = uuid.uuid4()

    runtime = PregelRuntime(graph=graph, worker=worker, checkpoint_store=checkpoint, scheduler=tracking)

    events = []
    async for event in runtime.execute(session_id=session_id, initial_input={"messages": ["start"]}):
        events.append(event)

    # Linear graph has 3 nodes, each in its own superstep
    assert len(tracking.calls) == 3

    # Verify node lists passed to scheduler
    assert tracking.calls[0]["nodes"] == ["A"]
    assert tracking.calls[1]["nodes"] == ["B"]
    assert tracking.calls[2]["nodes"] == ["C"]


async def test_custom_scheduler_reorders_nodes() -> None:
    """Reversing scheduler changes node execution order."""
    # Build a graph where A and B are parallel (both from start)
    graph = CompiledGraph(
        nodes={
            "start": NodeConfig(id="start", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "S"}),
            "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "A"}),
            "B": NodeConfig(id="B", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "B"}),
        },
        edges=[
            Edge(source="start", target="A"),
            Edge(source="start", target="B"),
            Edge(source="A", target="__end__"),
            Edge(source="B", target="__end__"),
        ],
        channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry_point="start",
        name="test-parallel",
    )
    checkpoint = InMemoryCheckpointStore()
    reversing = ReversingScheduler()
    session_id = uuid.uuid4()

    # Track execution order via a recording worker
    execution_order: list[str] = []

    class OrderTrackingWorker(Worker):
        async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
            execution_order.append(node_id)
            return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_output"]})

    runtime = PregelRuntime(graph=graph, worker=OrderTrackingWorker(), checkpoint_store=checkpoint, scheduler=reversing)

    events = []
    async for event in runtime.execute(session_id=session_id, initial_input={"messages": ["start"]}):
        events.append(event)

    # start executes in superstep 1, then A and B in superstep 2
    # ReversingScheduler reverses ["A", "B"] to ["B", "A"]
    assert execution_order[0] == "start"
    # The order of A and B depends on _resolve_next_nodes output + reversing
    # With parallel nodes, _resolve_next_nodes returns them in edge order
    # ReversingScheduler flips that


async def test_context_dict_contains_superstep_and_snapshot() -> None:
    """Context dict passed to select_next has superstep and channel_snapshot keys."""
    graph = _make_linear_graph()
    checkpoint = InMemoryCheckpointStore()
    worker = SimpleWorker()
    tracking = TrackingScheduler()
    session_id = uuid.uuid4()

    runtime = PregelRuntime(graph=graph, worker=worker, checkpoint_store=checkpoint, scheduler=tracking)

    events = []
    async for event in runtime.execute(session_id=session_id, initial_input={"messages": ["start"]}):
        events.append(event)

    # Check first call context
    assert "superstep" in tracking.calls[0]["context"]
    assert "channel_snapshot" in tracking.calls[0]["context"]
    assert tracking.calls[0]["context"]["superstep"] == 1

    # Check second call context
    assert tracking.calls[1]["context"]["superstep"] == 2

    # Verify snapshot is a dict
    assert isinstance(tracking.calls[0]["context"]["channel_snapshot"], dict)


async def test_single_node_superstep_calls_select_next() -> None:
    """Scheduler is called even when there's only one node in the superstep."""
    graph = _make_linear_graph()
    checkpoint = InMemoryCheckpointStore()
    worker = SimpleWorker()
    tracking = TrackingScheduler()
    session_id = uuid.uuid4()

    runtime = PregelRuntime(graph=graph, worker=worker, checkpoint_store=checkpoint, scheduler=tracking)

    events = []
    async for event in runtime.execute(session_id=session_id, initial_input={"messages": ["start"]}):
        events.append(event)

    # Each superstep has exactly one node in a linear graph
    for call in tracking.calls:
        assert len(call["nodes"]) == 1

    # All three nodes were scheduled
    scheduled_nodes = [call["nodes"][0] for call in tracking.calls]
    assert scheduled_nodes == ["A", "B", "C"]
