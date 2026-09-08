"""Tests for 1.3.21① declarative interrupts.

Covers the four delivery slices of the change:

- DSL: top-level ``interrupt_before`` / ``interrupt_after`` arrays parse,
  compile, and roundtrip through ``CompiledGraph.to_json()``.
- Compiler validation: interrupt lists reference declared nodes; task mode
  rejects non-empty lists.
- ``remaining_steps`` exposure in the worker execution context.
- Worker-authored interrupt WAL commit order: the interrupting superstep's
  writes are logged before the ``INTERRUPT`` commit point, and resume passes
  the projection-equivalence check.
- Declarative pause points: whole-superstep pause before dispatch /
  after writes are committed, phase-aware resume derived from the last
  ``INTERRUPT`` event in the log, TURN_END pairing, and coexistence with
  worker-authored interrupts in the same session log.

The tests use lightweight worker stubs (no LLM calls), per tests/AGENTS.md.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime.checkpoint import InMemoryCheckpointStore
from hecate.runtime.compiler import GraphCompiler
from hecate.runtime.errors import GraphValidationError
from hecate.runtime.eventstore import EventType, InMemoryEventStore
from hecate.runtime.pregel import PregelRuntime
from hecate.runtime.types import (
    ChannelDef,
    ChannelType,
    Command,
    Edge,
    GraphConfig,
    NodeConfig,
    NodeType,
    WorkerResult,
)
from hecate.runtime.worker import Worker
from hecate.studio.workflows.graph_dsl import parse_graph


class RecordingWorker(Worker):
    """Echo worker that records executed node IDs and their execution contexts."""

    def __init__(self, event_store=None):
        super().__init__(event_store=event_store)
        self.calls: list[str] = []
        self.contexts: dict[str, list[dict]] = {}

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        self.calls.append(node_id)
        self.contexts.setdefault(node_id, []).append(dict(execution_context or {}))
        return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_output"]})


class ResumeEchoWorker(RecordingWorker):
    """RecordingWorker that records the ``_resume_value`` each node observed.

    Lets tests assert that a resumed node actually saw the human input.
    """

    def __init__(self, event_store=None):
        super().__init__(event_store=event_store)
        self.seen_resume: dict[str, object] = {}

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        result = await super().execute(node_id, node_config, channel_snapshot, execution_context)
        resume_value = channel_snapshot.get("_resume_value")
        if resume_value is not None:
            self.seen_resume[node_id] = resume_value
        return result


class InterruptWithWritesWorker(RecordingWorker):
    """RecordingWorker that interrupts at a target node with loggable writes."""

    def __init__(self, interrupt_at: str, event_store=None):
        super().__init__(event_store=event_store)
        self._interrupt_at = interrupt_at

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        await super().execute(node_id, node_config, channel_snapshot, execution_context)
        if node_id == self._interrupt_at:
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [f"{node_id}_interrupted"]},
                command=Command(interrupt={"type": "approval"}),
            )
        return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_output"]})


class RouteWorker(RecordingWorker):
    """RecordingWorker that routes from the ``check`` node with a set value."""

    def __init__(self, route_value: str = "false", event_store=None):
        super().__init__(event_store=event_store)
        self._route_value = route_value

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        await super().execute(node_id, node_config, channel_snapshot, execution_context)
        if node_id == "check":
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [f"{node_id}_output"], "_route": self._route_value},
            )
        return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_output"]})


def _linear_config(interrupt_before: list[str] | None = None, interrupt_after: list[str] | None = None) -> GraphConfig:
    """Three-node linear graph A -> B -> C -> __end__ with optional interrupt lists."""
    return GraphConfig(
        name="declarative-linear",
        nodes={
            "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={}),
            "B": NodeConfig(id="B", type=NodeType.CONVERSATION, config={}),
            "C": NodeConfig(id="C", type=NodeType.CONVERSATION, config={}),
        },
        edges=[
            Edge(source="A", target="B"),
            Edge(source="B", target="C"),
            Edge(source="C", target="__end__"),
        ],
        state={
            "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
            "_resume_value": ChannelDef(type=ChannelType.LAST_VALUE),
        },
        entry="A",
        interrupt_before=interrupt_before or [],
        interrupt_after=interrupt_after or [],
    )


def _diamond_config() -> GraphConfig:
    """Diamond graph A -> {B, C} -> {D, E} -> __end__ (B->D, C->E).

    B and C run in the same superstep after A; D and E run in the same
    superstep on resume from an after-pause at B/C's step.
    """
    nodes = {}
    for node_id in ("A", "B", "C", "D", "E"):
        nodes[node_id] = NodeConfig(id=node_id, type=NodeType.CONVERSATION, config={})
    return GraphConfig(
        name="declarative-diamond",
        nodes=nodes,
        edges=[
            Edge(source="A", target="B"),
            Edge(source="A", target="C"),
            Edge(source="B", target="D"),
            Edge(source="C", target="E"),
            Edge(source="D", target="__end__"),
            Edge(source="E", target="__end__"),
        ],
        state={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        entry="A",
    )


def _conditional_config() -> GraphConfig:
    """Conditional graph start -> check -> {true: yes, false: no} -> __end__."""
    nodes = {}
    for node_id in ("start", "check", "yes", "no"):
        nodes[node_id] = NodeConfig(id=node_id, type=NodeType.CONVERSATION, config={})
    return GraphConfig(
        name="declarative-conditional",
        nodes=nodes,
        edges=[
            Edge(source="start", target="check"),
            Edge(source="check", target={"true": "yes", "false": "no"}),
            Edge(source="yes", target="__end__"),
            Edge(source="no", target="__end__"),
        ],
        state={
            "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
            "_route": ChannelDef(type=ChannelType.LAST_VALUE),
        },
        entry="start",
    )


def _dsl_document(interrupt_before: list[str] | None = None, interrupt_after: list[str] | None = None) -> dict:
    return {
        "version": "1.0",
        "name": "dsl-interrupts",
        "state": {"messages": {"type": "topic", "default": []}},
        "nodes": {
            "A": {"type": "conversation", "config": {}},
            "B": {"type": "conversation", "config": {}},
        },
        "edges": [
            {"source": "A", "target": "B"},
            {"source": "B", "target": "__end__"},
        ],
        "entry": "A",
        "interrupt_before": interrupt_before or [],
        "interrupt_after": interrupt_after or [],
    }


async def _collect(agen) -> list[dict]:
    return [event async for event in agen]


class TestDeclarativeDsl:
    def test_parse_propagates_interrupt_lists(self):
        config = parse_graph(_dsl_document(interrupt_before=["B"], interrupt_after=["A"]))
        assert config.interrupt_before == ["B"]
        assert config.interrupt_after == ["A"]

    def test_absent_lists_default_to_empty(self):
        doc = _dsl_document()
        del doc["interrupt_before"]
        del doc["interrupt_after"]
        config = parse_graph(doc)
        assert config.interrupt_before == []
        assert config.interrupt_after == []

    def test_compile_carries_lists_and_to_json_roundtrips(self):
        config = parse_graph(_dsl_document(interrupt_before=["A"], interrupt_after=["B"]))
        compiled = GraphCompiler().compile(config)
        assert compiled.interrupt_before == ["A"]
        assert compiled.interrupt_after == ["B"]

        reparsed = parse_graph(compiled.to_json())
        recompiled = GraphCompiler().compile(reparsed)
        assert recompiled.interrupt_before == ["A"]
        assert recompiled.interrupt_after == ["B"]


class TestCompilerValidation:
    def test_interrupt_list_referencing_missing_node_rejected(self):
        config = _linear_config(interrupt_before=["ghost"])
        with pytest.raises(GraphValidationError, match="ghost"):
            GraphCompiler().compile(config)

    def test_interrupt_after_referencing_missing_node_rejected(self):
        config = _linear_config(interrupt_after=["ghost"])
        with pytest.raises(GraphValidationError, match="interrupt_after"):
            GraphCompiler().compile(config)

    def test_task_mode_rejects_non_empty_interrupt_lists(self):
        config = _linear_config(interrupt_before=["B"])
        with pytest.raises(GraphValidationError, match="task mode"):
            GraphCompiler().compile(config, execution_mode="task")

    def test_task_mode_allows_empty_lists(self):
        compiled = GraphCompiler().compile(_linear_config(), execution_mode="task")
        assert compiled.interrupt_before == []
        assert compiled.interrupt_after == []

    def test_conversational_mode_allows_interrupt_lists(self):
        compiled = GraphCompiler().compile(_linear_config(interrupt_before=["B"], interrupt_after=["C"]))
        assert compiled.interrupt_before == ["B"]
        assert compiled.interrupt_after == ["C"]


class TestRemainingSteps:
    async def test_worker_sees_remaining_steps_per_superstep(self):
        compiled = GraphCompiler().compile(_linear_config())
        worker = RecordingWorker()
        runtime = PregelRuntime(compiled, worker, InMemoryCheckpointStore(), max_supersteps=10)
        await _collect(runtime.execute(uuid.uuid4()))

        # Supersteps 1..3 run A, B, C — remaining budget decrements from 9 to 7.
        assert worker.contexts["A"][0]["remaining_steps"] == 9
        assert worker.contexts["B"][0]["remaining_steps"] == 8
        assert worker.contexts["C"][0]["remaining_steps"] == 7

    async def test_remaining_steps_reflects_restored_counter_after_resume(self):
        compiled = GraphCompiler().compile(_linear_config(interrupt_before=["B"]))
        checkpoint_store = InMemoryCheckpointStore()
        worker = RecordingWorker()
        runtime = PregelRuntime(compiled, worker, checkpoint_store, max_supersteps=10)
        session_id = uuid.uuid4()
        events = await _collect(runtime.execute(session_id, initial_input={"messages": ["hi"]}))
        assert events[-1]["type"] == "interrupt"

        # A ran at superstep 1; pause before B at superstep 2 (counter=2 saved).
        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(compiled, worker2, checkpoint_store, max_supersteps=10)
        await _collect(runtime2.execute(session_id, resume_value="approved"))

        # Human wait consumed no budget: B runs at superstep 3, remaining 7.
        assert worker2.contexts["B"][0]["remaining_steps"] == 7


class TestWorkerInterruptWalCommit:
    async def test_interrupt_step_writes_logged_before_interrupt_commit_point(self):
        compiled = GraphCompiler().compile(_linear_config())
        worker = InterruptWithWritesWorker(interrupt_at="B")
        checkpoint_store = InMemoryCheckpointStore()
        event_store = InMemoryEventStore()
        runtime = PregelRuntime(compiled, worker, checkpoint_store, event_store=event_store)
        session_id = uuid.uuid4()
        events = await _collect(runtime.execute(session_id))
        assert events[-1]["type"] == "interrupt"

        log = await event_store.get_events(session_id)
        interrupt_events = [e for e in log if e.event_type == EventType.INTERRUPT]
        assert len(interrupt_events) == 1
        interrupt_event = interrupt_events[0]

        # The interrupting node's write is committed BEFORE the INTERRUPT event
        # (the commit point) — the cache may never run ahead of the log.
        b_writes = [
            e
            for e in log
            if e.event_type == EventType.CHANNEL_WRITE and e.node_id == "B" and e.payload.get("channel") == "messages"
        ]
        assert b_writes, "interrupting node's writes must reach the log"
        assert all(e.version < interrupt_event.version for e in b_writes)

        # New worker-authored INTERRUPT payloads self-describe (design D10).
        assert interrupt_event.payload.get("kind") == "worker"
        assert interrupt_event.payload.get("nodes") == ["B"]

    async def test_sibling_writes_commit_before_interrupt_pause(self):
        # Diamond: B and C run in the same superstep; B interrupts. C's writes
        # must still be committed before the pause (no dropped pending writes).
        compiled = GraphCompiler().compile(_diamond_config())
        worker = InterruptWithWritesWorker(interrupt_at="B")
        checkpoint_store = InMemoryCheckpointStore()
        event_store = InMemoryEventStore()
        runtime = PregelRuntime(compiled, worker, checkpoint_store, event_store=event_store)
        session_id = uuid.uuid4()
        events = await _collect(runtime.execute(session_id))
        assert events[-1]["type"] == "interrupt"

        log = await event_store.get_events(session_id)
        interrupt_event = next(e for e in log if e.event_type == EventType.INTERRUPT)
        c_writes = [e for e in log if e.event_type == EventType.CHANNEL_WRITE and e.node_id == "C"]
        assert c_writes, "sibling node's writes must not be dropped on interrupt"
        assert all(e.version < interrupt_event.version for e in c_writes)

    async def test_resume_after_worker_interrupt_passes_projection_equivalence(self):
        compiled = GraphCompiler().compile(_linear_config())
        worker = InterruptWithWritesWorker(interrupt_at="B")
        checkpoint_store = InMemoryCheckpointStore()
        event_store = InMemoryEventStore()
        session_id = uuid.uuid4()

        runtime = PregelRuntime(compiled, worker, checkpoint_store, event_store=event_store)
        await _collect(runtime.execute(session_id, initial_input={"messages": ["hi"]}))

        # Restore path runs the projection-equivalence assertion against a full
        # log fold; cache-ahead-of-log divergence would raise RuntimeError here.
        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(compiled, worker2, checkpoint_store, event_store=event_store)
        events = await _collect(runtime2.execute(session_id, resume_value="approved"))
        assert all(e["type"] != "interrupt" for e in events)
        # Worker interrupt at B: resume continues at B's successor C.
        assert worker2.calls == ["C"]


class TestDeclarativeBefore:
    async def test_pause_before_dispatch_with_no_node_executed(self):
        compiled = GraphCompiler().compile(_linear_config(interrupt_before=["B"]))
        worker = RecordingWorker()
        checkpoint_store = InMemoryCheckpointStore()
        event_store = InMemoryEventStore()
        runtime = PregelRuntime(compiled, worker, checkpoint_store, event_store=event_store)
        session_id = uuid.uuid4()

        events = await _collect(runtime.execute(session_id, initial_input={"messages": ["hi"]}))

        # A ran (superstep 1); B's superstep paused before dispatch.
        assert worker.calls == ["A"]
        assert runtime.is_interrupted

        interrupt_events = [e for e in events if e["type"] == "interrupt"]
        assert len(interrupt_events) == 1
        descriptor = interrupt_events[0]["value"]
        assert descriptor["kind"] == "declarative"
        assert descriptor["phase"] == "before"
        assert descriptor["nodes"] == ["B"]
        assert descriptor["superstep"] == 2
        assert descriptor["remaining_steps"] == 98  # default max_supersteps=100

        # Log carries the declarative INTERRUPT event with the descriptor payload.
        log = await event_store.get_events(session_id)
        interrupt_log_event = next(e for e in log if e.event_type == EventType.INTERRUPT)
        assert interrupt_log_event.node_id == "B"
        assert interrupt_log_event.payload["phase"] == "before"

        # TURN_END pairing keeps the T0.5 audit pair closed.
        turn_ends = [e for e in log if e.event_type == EventType.TURN_END]
        assert turn_ends and turn_ends[-1].payload.get("reason") == "interrupt"

    async def test_resume_executes_paused_node_and_reads_resume_value(self):
        compiled = GraphCompiler().compile(_linear_config(interrupt_before=["B"]))
        checkpoint_store = InMemoryCheckpointStore()
        worker = RecordingWorker()
        runtime = PregelRuntime(compiled, worker, checkpoint_store)
        session_id = uuid.uuid4()
        await _collect(runtime.execute(session_id))

        worker2 = ResumeEchoWorker()
        runtime2 = PregelRuntime(compiled, worker2, checkpoint_store)
        await _collect(runtime2.execute(session_id, resume_value="approved"))

        # B itself executes after resume, sees the human input, then C follows.
        assert worker2.calls == ["B", "C"]
        assert worker2.seen_resume.get("B") == "approved"

    async def test_entry_node_before_interrupt_anchors_initial_state(self):
        compiled = GraphCompiler().compile(_linear_config(interrupt_before=["A"]))
        worker = RecordingWorker()
        checkpoint_store = InMemoryCheckpointStore()
        runtime = PregelRuntime(compiled, worker, checkpoint_store)
        session_id = uuid.uuid4()

        events = await _collect(runtime.execute(session_id, initial_input={"messages": ["plan"]}))

        assert worker.calls == []
        assert events[-1]["type"] == "interrupt"
        checkpoint = await checkpoint_store.load(session_id)
        assert checkpoint is not None
        assert checkpoint["channel_state"].get("messages") == ["plan"]

    async def test_whole_superstep_pauses_when_one_node_matches(self):
        config = _diamond_config()
        config.interrupt_before = ["B"]
        compiled = GraphCompiler().compile(config)
        worker = RecordingWorker()
        runtime = PregelRuntime(compiled, worker, InMemoryCheckpointStore())
        session_id = uuid.uuid4()
        events = await _collect(runtime.execute(session_id))

        # Neither B nor C executed in the paused superstep.
        assert worker.calls == ["A"]
        assert events[-1]["value"]["nodes"] == ["B", "C"]

        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(compiled, worker2, runtime._checkpoint_store)
        await _collect(runtime2.execute(session_id, resume_value="go"))
        # Both scheduled nodes execute after resume.
        assert worker2.calls == ["B", "C", "D", "E"]


class TestDeclarativeAfter:
    async def test_pause_after_writes_committed(self):
        compiled = GraphCompiler().compile(_linear_config(interrupt_after=["B"]))
        worker = RecordingWorker()
        checkpoint_store = InMemoryCheckpointStore()
        event_store = InMemoryEventStore()
        runtime = PregelRuntime(compiled, worker, checkpoint_store, event_store=event_store)
        session_id = uuid.uuid4()

        events = await _collect(runtime.execute(session_id, initial_input={"messages": ["hi"]}))

        # B executed and its writes committed before the pause; C never ran.
        assert worker.calls == ["A", "B"]
        descriptor = events[-1]["value"]
        assert descriptor["kind"] == "declarative"
        assert descriptor["phase"] == "after"
        assert descriptor["nodes"] == ["B"]

        log = await event_store.get_events(session_id)
        interrupt_event = next(e for e in log if e.event_type == EventType.INTERRUPT)
        b_writes = [e for e in log if e.event_type == EventType.CHANNEL_WRITE and e.node_id == "B"]
        assert b_writes
        assert all(e.version < interrupt_event.version for e in b_writes)

    async def test_resume_follows_out_edges_of_all_executed_nodes(self):
        config = _diamond_config()
        config.interrupt_after = ["B"]
        compiled = GraphCompiler().compile(config)
        worker = RecordingWorker()
        checkpoint_store = InMemoryCheckpointStore()
        runtime = PregelRuntime(compiled, worker, checkpoint_store)
        session_id = uuid.uuid4()
        await _collect(runtime.execute(session_id))

        # B and C executed in the interrupting superstep; resume runs D and E.
        assert worker.calls == ["A", "B", "C"]
        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(compiled, worker2, checkpoint_store)
        await _collect(runtime2.execute(session_id, resume_value="go"))
        assert worker2.calls == ["D", "E"]

    async def test_resume_resolves_conditional_edges_from_restored_route(self):
        config = _conditional_config()
        config.interrupt_after = ["check"]
        compiled = GraphCompiler().compile(config)
        worker = RouteWorker(route_value="false")
        checkpoint_store = InMemoryCheckpointStore()
        event_store = InMemoryEventStore()
        runtime = PregelRuntime(compiled, worker, checkpoint_store, event_store=event_store)
        session_id = uuid.uuid4()
        await _collect(runtime.execute(session_id))

        assert worker.calls == ["start", "check"]

        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(compiled, worker2, checkpoint_store, event_store=event_store)
        await _collect(runtime2.execute(session_id, resume_value="go"))

        # The persisted ``_route=false`` steers resume into the ``no`` branch.
        assert worker2.calls == ["no"]

    async def test_fan_out_graph_interrupt_roundtrip(self):
        # FAN_OUT -> branches -> MERGE with interrupt_after on the fan-out node:
        # the pause fires after branch dispatch completes, resume runs MERGE.
        nodes = {
            "fanout": NodeConfig(
                id="fanout",
                type=NodeType.FAN_OUT,
                config={"branches": ["branch_a", "branch_b"]},
            ),
            "branch_a": NodeConfig(id="branch_a", type=NodeType.CONVERSATION, config={}),
            "branch_b": NodeConfig(id="branch_b", type=NodeType.CONVERSATION, config={}),
            "merge": NodeConfig(
                id="merge",
                type=NodeType.MERGE,
                config={"fan_out_source": "fanout", "output_channel": "merged"},
            ),
        }
        config = GraphConfig(
            name="fanout-interrupt",
            nodes=nodes,
            edges=[
                Edge(source="fanout", target="branch_a"),
                Edge(source="fanout", target="branch_b"),
                Edge(source="branch_a", target="merge"),
                Edge(source="branch_b", target="merge"),
                Edge(source="merge", target="__end__"),
            ],
            state={
                "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
                "merged": ChannelDef(type=ChannelType.LAST_VALUE),
            },
            entry="fanout",
            interrupt_after=["fanout"],
        )
        compiled = GraphCompiler().compile(config)
        worker = RecordingWorker()
        checkpoint_store = InMemoryCheckpointStore()
        runtime = PregelRuntime(compiled, worker, checkpoint_store)
        session_id = uuid.uuid4()

        events = await _collect(runtime.execute(session_id))
        assert events[-1]["type"] == "interrupt"
        assert events[-1]["value"]["phase"] == "after"
        # Descriptor nodes are result-producing nodes (branch ids), so resume
        # follows branch out-edges into MERGE — not the fan-out's own edges.
        assert set(events[-1]["value"]["nodes"]) == {"branch_a", "branch_b"}
        # Branches dispatched; merge did not run.
        assert set(worker.calls) == {"branch_a", "branch_b"}

        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(compiled, worker2, checkpoint_store)
        await _collect(runtime2.execute(session_id, resume_value="go"))
        # MERGE executes engine-side (no worker dispatch); assert its output.
        merged = runtime2._channel_manager.snapshot().get("merged")
        assert set(merged.keys()) == {"branch_a", "branch_b"}


class TestMixedInterruptKinds:
    async def test_worker_then_declarative_interrupts_resume_correctly(self):
        # One session log carries both interrupt kinds; each resume derives
        # its continuation from the LAST unclosed INTERRUPT event.
        compiled = GraphCompiler().compile(_linear_config(interrupt_after=["C"]))
        worker = InterruptWithWritesWorker(interrupt_at="B")
        checkpoint_store = InMemoryCheckpointStore()
        event_store = InMemoryEventStore()
        session_id = uuid.uuid4()

        runtime = PregelRuntime(compiled, worker, checkpoint_store, event_store=event_store)
        await _collect(runtime.execute(session_id, initial_input={"messages": ["hi"]}))
        assert worker.calls == ["A", "B"]

        # Resume 1: worker interrupt at B → C runs → declarative after-pause at C.
        worker2 = RecordingWorker()
        runtime2 = PregelRuntime(compiled, worker2, checkpoint_store, event_store=event_store)
        events = await _collect(runtime2.execute(session_id, resume_value="approved"))
        assert worker2.calls == ["C"]
        assert events[-1]["type"] == "interrupt"
        assert events[-1]["value"]["kind"] == "declarative"

        # Resume 2: declarative after-pause at C → C's out-edge is __end__.
        worker3 = RecordingWorker()
        runtime3 = PregelRuntime(compiled, worker3, checkpoint_store, event_store=event_store)
        events = await _collect(runtime3.execute(session_id, resume_value="done"))
        assert worker3.calls == []
        assert all(e["type"] != "interrupt" for e in events)

        # The log closes both INTERRUPTs with RESUME events.
        log = await event_store.get_events(session_id)
        assert [e.event_type for e in log].count(EventType.INTERRUPT) == 2
        assert [e.event_type for e in log].count(EventType.RESUME) == 2
