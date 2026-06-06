"""Tests for the Pregel runtime execution engine.

Validates the core superstep-driven execution loop that drives graph-based
agent workflows.  Covered behaviours:

- Linear multi-node execution and checkpoint persistence.
- Interrupt / resume semantics (pause at a node, restore state, continue).
- Conditional branching via the ``_route`` channel.
- ``Command.goto`` jumps that skip intermediate nodes.
- Cycle detection via ``max_supersteps`` guard.
- ``ChannelManager`` semantics for all channel types (LAST_VALUE, TOPIC,
  ACCUMULATOR).

The tests use lightweight **worker stubs** instead of real LLM calls, keeping
them fast and deterministic.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.engine.channel import ChannelManager
from hecate.engine.checkpoint import InMemoryCheckpointStore
from hecate.engine.eviction import SizeBasedEviction
from hecate.engine.pregel import PregelRuntime
from hecate.engine.types import (
    ChannelDef,
    ChannelType,
    Command,
    CompiledGraph,
    Edge,
    NodeConfig,
    NodeType,
    WorkerResult,
)
from hecate.engine.worker import Worker


class SimpleWorker(Worker):
    """Pass-through worker that echoes the node id as output.

    Used as the baseline worker in tests that do not need interrupts,
    routing, or goto commands.
    """

    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": [f"{node_id}_output"]},
        )


class InterruptWorker(Worker):
    """Worker that emits an interrupt ``Command`` when reaching a target node.

    All other nodes behave like ``SimpleWorker``.  The ``interrupt_at``
    parameter controls which node triggers the interrupt, defaulting to ``B``.
    """

    def __init__(self, interrupt_at: str = "B"):
        self._interrupt_at = interrupt_at

    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        if node_id == self._interrupt_at:
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": ["interrupted"]},
                command=Command(interrupt={"type": "approval", "message": "Please confirm"}),
            )
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": [f"{node_id}_output"]},
        )


class RoutingWorker(Worker):
    """Worker that writes a route value on the ``_route`` channel at a condition node.

    This simulates a conditional node deciding which branch to take.  The
    ``route_value`` parameter (``"true"`` or ``"false"``) determines the edge
    the engine will follow out of the condition node (``"check"``).
    """

    def __init__(self, route_value: str = "true"):
        self._route_value = route_value

    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        updates: dict = {"messages": [f"{node_id}_output"]}
        if node_id == "check":
            updates["_route"] = self._route_value
        return WorkerResult(node_id=node_id, channel_updates=updates)


class GotoWorker(Worker):
    """Worker that issues a ``Command.goto("C")`` when executing node A.

    Used to verify that the runtime can skip intermediate nodes (B) and jump
    directly to a downstream node (C).
    """

    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        if node_id == "A":
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": ["from_A"]},
                command=Command(goto="C"),
            )
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": [f"{node_id}_output"]},
        )


class InterruptRoutingWorker(Worker):
    """Worker that interrupts at a condition node while setting a specific route.

    Combines the behaviours of ``InterruptWorker`` and ``RoutingWorker``:
    when the engine reaches the ``"check"`` node it writes ``_route`` and
    raises an interrupt simultaneously.  On resume the engine must honour the
    route that was set before the interrupt.
    """

    def __init__(self, route_value: str = "false"):
        self._route_value = route_value

    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        if node_id == "check":
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": ["checking"], "_route": self._route_value},
                command=Command(interrupt={"type": "approval", "message": "Confirm route?"}),
            )
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": [f"{node_id}_output"]},
        )


def _make_linear_graph() -> CompiledGraph:
    """Build a three-node linear graph: A -> B -> C -> END.

    Each node is a ``CONVERSATION`` type.  The single ``messages`` TOPIC
    channel collects output from every superstep.
    """
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


def _make_conditional_graph() -> CompiledGraph:
    """Build a graph with a conditional branch: start -> check -> branch_a | branch_b.

    The ``check`` node is a ``CONDITION`` type whose outgoing edge maps
    ``{"true": "branch_a", "false": "branch_b"}``.  Both branches lead to
    ``__end__``.
    """
    return CompiledGraph(
        nodes={
            "start": NodeConfig(
                id="start", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "Start"}
            ),
            "check": NodeConfig(id="check", type=NodeType.CONDITION, config={"expression": "route"}),
            "branch_a": NodeConfig(
                id="branch_a", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "A"}
            ),
            "branch_b": NodeConfig(
                id="branch_b", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "B"}
            ),
        },
        edges=[
            Edge(source="start", target="check"),
            Edge(source="check", target={"true": "branch_a", "false": "branch_b"}),
            Edge(source="branch_a", target="__end__"),
            Edge(source="branch_b", target="__end__"),
        ],
        channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry_point="start",
        name="test-conditional",
    )


def _make_goto_graph() -> CompiledGraph:
    """Build a linear graph (A -> B -> C) used to test Command.goto skips.

    The topology is identical to ``_make_linear_graph``; the difference is
    that tests pair it with ``GotoWorker`` which jumps from A directly to C.
    """
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
        name="test-goto",
    )


class TestLinearExecution:
    """Verify that the engine processes a straight-line graph from entry to END."""

    @pytest.mark.asyncio
    async def test_linear_three_nodes(self):
        """All three nodes execute in order and emit a final values event."""
        graph = _make_linear_graph()
        worker = SimpleWorker()
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        results = []
        async for event in runtime.execute(session_id, initial_input={"messages": ["init"]}):
            results.append(event)

        assert len(results) == 3
        assert results[-1]["type"] == "values"

    @pytest.mark.asyncio
    async def test_checkpoints_saved(self):
        """A checkpoint is persisted after each superstep so the session can be resumed."""
        graph = _make_linear_graph()
        worker = SimpleWorker()
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        async for _ in runtime.execute(session_id):
            pass

        cps = await store.list_checkpoints(session_id)
        assert len(cps) == 3


class TestInterruptResume:
    """Validate interrupt-then-resume semantics: execution pauses at a node,
    preserves channel state in a checkpoint, and continues correctly after the
    caller supplies a resume value."""

    @pytest.mark.asyncio
    async def test_interrupt_stops_execution(self):
        """When a worker raises an interrupt the runtime stops and reports the interrupt event."""
        graph = _make_linear_graph()
        worker = InterruptWorker(interrupt_at="B")
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        results = []
        async for event in runtime.execute(session_id):
            results.append(event)

        assert runtime.is_interrupted
        assert results[-1]["type"] == "interrupt"

    @pytest.mark.asyncio
    async def test_resume_from_interrupt(self):
        """Resuming with a fresh runtime instance replays from the checkpoint and runs the remaining nodes."""
        graph = _make_linear_graph()
        worker = InterruptWorker(interrupt_at="B")
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        results = []
        async for event in runtime.execute(session_id, initial_input={"messages": ["init"]}):
            results.append(event)

        assert runtime.is_interrupted
        # Only A and B executed before the interrupt; C is still pending.
        assert len(results) == 2

        # Resume with a new runtime that uses a non-interrupting worker.
        runtime2 = PregelRuntime(graph, SimpleWorker(), store)
        resume_results = []
        async for event in runtime2.execute(session_id, resume_value="approved"):
            resume_results.append(event)

        assert not runtime2.is_interrupted
        assert len(resume_results) == 1
        assert resume_results[-1]["type"] == "values"
        final_state = resume_results[-1]["state"]
        assert "C_output" in final_state.get("messages", [])

    @pytest.mark.asyncio
    async def test_interrupt_preserves_channel_state(self):
        """Channel state written before the interrupt is captured in the checkpoint."""
        graph = _make_linear_graph()
        worker = InterruptWorker(interrupt_at="B")
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        async for _ in runtime.execute(session_id, initial_input={"messages": ["init"]}):
            pass

        checkpoint = await store.load(session_id)
        assert checkpoint is not None
        assert "A_output" in checkpoint["channel_state"].get("messages", [])


class TestConditionalExecution:
    """Verify that the engine follows the correct branch when a condition node
    writes a route value to the ``_route`` channel."""

    @pytest.mark.asyncio
    async def test_conditional_true_branch(self):
        """When ``_route`` is ``"true"`` the engine executes branch_a and skips branch_b."""
        graph = _make_conditional_graph()
        worker = RoutingWorker(route_value="true")
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        results = []
        async for event in runtime.execute(session_id):
            results.append(event)

        assert len(results) == 3
        final_messages = results[-1]["state"]["messages"]
        assert "branch_a_output" in final_messages
        assert "branch_b_output" not in final_messages

    @pytest.mark.asyncio
    async def test_conditional_false_branch(self):
        """When ``_route`` is ``"false"`` the engine executes branch_b and skips branch_a."""
        graph = _make_conditional_graph()
        worker = RoutingWorker(route_value="false")
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        results = []
        async for event in runtime.execute(session_id):
            results.append(event)

        assert len(results) == 3
        final_messages = results[-1]["state"]["messages"]
        assert "branch_b_output" in final_messages
        assert "branch_a_output" not in final_messages


class TestCommandGoto:
    """Validate that ``Command.goto`` causes the engine to jump to an arbitrary
    downstream node, skipping any intermediate nodes."""

    @pytest.mark.asyncio
    async def test_goto_skips_intermediate_node(self):
        """Node A issues goto(C); node B should never execute."""
        graph = _make_goto_graph()
        worker = GotoWorker()
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        results = []
        async for event in runtime.execute(session_id):
            results.append(event)

        final_messages = results[-1]["state"]["messages"]
        assert "from_A" in final_messages
        assert "C_output" in final_messages
        assert "B_output" not in final_messages


class TestChannelManager:
    """Unit tests for ``ChannelManager`` write / read semantics across all
    channel types, and for snapshot / restore round-tripping."""

    def test_last_value_overwrites(self):
        """LAST_VALUE channel keeps only the most recent write."""
        mgr = ChannelManager()
        mgr.register("val", ChannelDef(type=ChannelType.LAST_VALUE))
        mgr.write("val", "a")
        mgr.write("val", "b")
        assert mgr.read("val") == "b"

    def test_topic_appends(self):
        """TOPIC channel accumulates values into a list."""
        mgr = ChannelManager()
        mgr.register("msgs", ChannelDef(type=ChannelType.TOPIC, default=[]))
        mgr.write("msgs", "a")
        mgr.write("msgs", "b")
        assert mgr.read("msgs") == ["a", "b"]

    def test_accumulator_adds(self):
        """ACCUMULATOR channel reduces values using the configured function."""
        mgr = ChannelManager()
        mgr.register("count", ChannelDef(type=ChannelType.ACCUMULATOR, initial=0, reduce_fn="add"))
        mgr.write("count", 1)
        mgr.write("count", 2)
        mgr.write("count", 3)
        assert mgr.read("count") == 6

    def test_restore_rebuilds_state(self):
        """A snapshot taken from one manager can fully restore another."""
        mgr = ChannelManager()
        mgr.register("msgs", ChannelDef(type=ChannelType.TOPIC, default=[]))
        mgr.register("val", ChannelDef(type=ChannelType.LAST_VALUE))
        mgr.write("msgs", "a")
        mgr.write("val", 42)
        snapshot = mgr.snapshot()

        mgr2 = ChannelManager()
        mgr2.register("msgs", ChannelDef(type=ChannelType.TOPIC, default=[]))
        mgr2.register("val", ChannelDef(type=ChannelType.LAST_VALUE))
        mgr2.restore(snapshot)
        assert mgr2.read("msgs") == ["a"]
        assert mgr2.read("val") == 42


def _make_interrupt_conditional_graph() -> CompiledGraph:
    """Build a conditional graph used for interrupt-then-resume routing tests.

    Same topology as ``_make_conditional_graph`` but paired with
    ``InterruptRoutingWorker`` so the engine interrupts at the ``check`` node
    while simultaneously recording the chosen route.
    """
    return CompiledGraph(
        nodes={
            "start": NodeConfig(
                id="start", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "Start"}
            ),
            "check": NodeConfig(id="check", type=NodeType.CONDITION, config={"expression": "route"}),
            "branch_a": NodeConfig(
                id="branch_a", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "A"}
            ),
            "branch_b": NodeConfig(
                id="branch_b", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "B"}
            ),
        },
        edges=[
            Edge(source="start", target="check"),
            Edge(source="check", target={"true": "branch_a", "false": "branch_b"}),
            Edge(source="branch_a", target="__end__"),
            Edge(source="branch_b", target="__end__"),
        ],
        channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry_point="start",
        name="test-interrupt-conditional",
    )


class TestInterruptResumeRouting:
    """Verify that the route value recorded before an interrupt is honoured
    after resume, so execution follows the correct branch."""

    @pytest.mark.asyncio
    async def test_resume_follows_false_branch(self):
        """Interrupt at ``check`` with route=false; after resume, branch_b runs and branch_a does not."""
        graph = _make_interrupt_conditional_graph()
        worker = InterruptRoutingWorker(route_value="false")
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        async for _ in runtime.execute(session_id, initial_input={"messages": ["init"]}):
            pass

        assert runtime.is_interrupted

        runtime2 = PregelRuntime(graph, SimpleWorker(), store)
        resume_results = []
        async for event in runtime2.execute(session_id, resume_value="approved"):
            resume_results.append(event)

        final_messages = resume_results[-1]["state"]["messages"]
        assert "branch_b_output" in final_messages
        assert "branch_a_output" not in final_messages

    @pytest.mark.asyncio
    async def test_resume_follows_true_branch(self):
        """Interrupt at ``check`` with route=true; after resume, branch_a runs and branch_b does not."""
        graph = _make_interrupt_conditional_graph()
        worker = InterruptRoutingWorker(route_value="true")
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        async for _ in runtime.execute(session_id, initial_input={"messages": ["init"]}):
            pass

        assert runtime.is_interrupted

        runtime2 = PregelRuntime(graph, SimpleWorker(), store)
        resume_results = []
        async for event in runtime2.execute(session_id, resume_value="approved"):
            resume_results.append(event)

        final_messages = resume_results[-1]["state"]["messages"]
        assert "branch_a_output" in final_messages
        assert "branch_b_output" not in final_messages


class TestMaxSupersteps:
    """Verify that the runtime raises an error when a graph cycles beyond the
    configured superstep limit, preventing infinite loops."""

    @pytest.mark.asyncio
    async def test_cyclic_graph_raises(self):
        """An A <-> B cycle should exhaust max_supersteps and raise RuntimeError."""
        graph = CompiledGraph(
            nodes={
                "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "A"}),
                "B": NodeConfig(id="B", type=NodeType.CONVERSATION, config={"model": "test", "system_prompt": "B"}),
            },
            edges=[
                Edge(source="A", target="B"),
                Edge(source="B", target="A"),
            ],
            channels={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
            entry_point="A",
            name="test-cyclic",
        )
        worker = SimpleWorker()
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store, max_supersteps=10)

        session_id = uuid.uuid4()
        with pytest.raises(RuntimeError, match="max supersteps"):
            async for _ in runtime.execute(session_id):
                pass

    @pytest.mark.asyncio
    async def test_default_max_supersteps(self):
        """When not explicitly set the runtime defaults to 100 supersteps."""
        graph = _make_linear_graph()
        worker = SimpleWorker()
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        assert runtime._max_supersteps == 100


class TopFiveWorker(Worker):
    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": [f"{node_id}_{i}" for i in range(5)]},
        )


class TestEvictionIntegration:
    @pytest.mark.asyncio
    async def test_runtime_applies_eviction_policy(self):
        graph = _make_linear_graph()
        worker = TopFiveWorker()
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store, eviction_policy=SizeBasedEviction(max_size=3))

        session_id = uuid.uuid4()
        results = []
        async for event in runtime.execute(session_id):
            results.append(event)

        final_state = results[-1]["state"]
        assert len(final_state["messages"]) == 3
        assert final_state["messages"][-3:] == final_state["messages"]
