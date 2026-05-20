from __future__ import annotations

import uuid

import pytest

from hecate.engine.channel import ChannelManager
from hecate.engine.checkpoint import InMemoryCheckpointStore
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
    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": [f"{node_id}_output"]},
        )


class InterruptWorker(Worker):
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
    def __init__(self, route_value: str = "true"):
        self._route_value = route_value

    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        updates: dict = {"messages": [f"{node_id}_output"]}
        if node_id == "check":
            updates["_route"] = self._route_value
        return WorkerResult(node_id=node_id, channel_updates=updates)


class GotoWorker(Worker):
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


def _make_linear_graph() -> CompiledGraph:
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
    @pytest.mark.asyncio
    async def test_linear_three_nodes(self):
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
    @pytest.mark.asyncio
    async def test_interrupt_stops_execution(self):
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
        graph = _make_linear_graph()
        worker = InterruptWorker(interrupt_at="B")
        store = InMemoryCheckpointStore()
        runtime = PregelRuntime(graph, worker, store)

        session_id = uuid.uuid4()
        results = []
        async for event in runtime.execute(session_id, initial_input={"messages": ["init"]}):
            results.append(event)

        assert runtime.is_interrupted
        assert len(results) == 2

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
    @pytest.mark.asyncio
    async def test_conditional_true_branch(self):
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
    @pytest.mark.asyncio
    async def test_goto_skips_intermediate_node(self):
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
    def test_last_value_overwrites(self):
        mgr = ChannelManager()
        mgr.register("val", ChannelDef(type=ChannelType.LAST_VALUE))
        mgr.write("val", "a")
        mgr.write("val", "b")
        assert mgr.read("val") == "b"

    def test_topic_appends(self):
        mgr = ChannelManager()
        mgr.register("msgs", ChannelDef(type=ChannelType.TOPIC, default=[]))
        mgr.write("msgs", "a")
        mgr.write("msgs", "b")
        assert mgr.read("msgs") == ["a", "b"]

    def test_accumulator_adds(self):
        mgr = ChannelManager()
        mgr.register("count", ChannelDef(type=ChannelType.ACCUMULATOR, initial=0, reduce_fn="add"))
        mgr.write("count", 1)
        mgr.write("count", 2)
        mgr.write("count", 3)
        assert mgr.read("count") == 6

    def test_restore_rebuilds_state(self):
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
