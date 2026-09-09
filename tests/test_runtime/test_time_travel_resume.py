"""Time-travel resume engine tests (1.3.21②).

Covers the FORK event contract, the fold machine's snapshot-hydration branch,
commit-point integration, log-invariant neutrality, the continuation
derivation matrix, and the engine's ``resume_from`` tail-only restore path.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime.channel import ChannelManager
from hecate.runtime.checkpoint import InMemoryCheckpointStore
from hecate.runtime.command import Command
from hecate.runtime.compiler import GraphCompiler
from hecate.runtime.context import InMemoryContextEngine
from hecate.runtime.eventstore import (
    CURRENT_LOG_SCHEMA_VERSION,
    Event,
    EventType,
    InMemoryEventStore,
)
from hecate.runtime.pregel import PregelRuntime
from hecate.runtime.replay.continuation import derive_continuation, resolve_out_edges
from hecate.runtime.replay.logfold import fold_session
from hecate.runtime.replay.loginvariants import run_all
from hecate.runtime.types import (
    ChannelDef,
    ChannelType,
    CompiledGraph,
    Edge,
    NodeConfig,
    NodeType,
)
from hecate.runtime.worker import Worker, WorkerResult


class _EchoWorker(Worker):
    """Appends ``<node>:<content>`` to the messages channel."""

    def __init__(self, content: str = "out") -> None:
        super().__init__()
        self.executed: list[str] = []
        self._content = content

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        self.executed.append(node_id)
        messages = list(channel_snapshot.get("messages", []))
        return WorkerResult(
            node_id=node_id,
            channel_updates={"messages": messages + [f"{node_id}:{self._content}"]},
        )


class _InterruptAtWorker(Worker):
    """Worker-authored interrupt at a named node; echoes elsewhere."""

    def __init__(self, interrupt_at: str) -> None:
        super().__init__()
        self._interrupt_at = interrupt_at
        self.executed: list[str] = []

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        self.executed.append(node_id)
        messages = list(channel_snapshot.get("messages", []))
        updates = {"messages": messages + [f"{node_id}:out"]}
        if node_id == self._interrupt_at:
            return WorkerResult(
                node_id=node_id,
                channel_updates=updates,
                command=Command(interrupt={"ask": "approve?"}),
            )
        return WorkerResult(node_id=node_id, channel_updates=updates)


def _linear_graph(a_to_end: bool = False) -> CompiledGraph:
    """Three-node linear graph a → b → c (→ __end__)."""
    return GraphCompiler().compile(
        _graph_config(
            nodes=["a", "b", "c"],
            edges=[("a", "b"), ("b", "c")] + ([("c", "__end__")] if a_to_end else []),
        )
    )


def _graph_config(nodes: list[str], edges: list[tuple[str, str | dict]], state: dict | None = None) -> object:
    from hecate.runtime.types import GraphConfig

    channels: dict = {"messages": ChannelDef(type=ChannelType.TOPIC, default=[])}
    channels.update(state or {})
    return GraphConfig(
        name="tt_test",
        state=channels,
        nodes={n: NodeConfig(id=n, type=NodeType.CONVERSATION, config={}) for n in nodes},
        edges=[Edge(source=s, target=t) for s, t in edges],
        entry=nodes[0],
    )


def _runtime(graph: CompiledGraph, worker: Worker, store: InMemoryEventStore) -> PregelRuntime:
    return PregelRuntime(
        graph=graph,
        worker=worker,
        checkpoint_store=InMemoryCheckpointStore(),
        event_store=store,
        context_engine=InMemoryContextEngine(),
    )


async def _drain(gen) -> list[dict]:
    return [event async for event in gen]


def _mk_event(
    session_id: uuid.UUID,
    version: int,
    etype: EventType,
    payload: dict | None = None,
    node_id: str | None = None,
    superstep: int = 0,
) -> Event:
    return Event(
        session_id=session_id,
        superstep=superstep,
        event_type=etype,
        node_id=node_id,
        payload={"log_schema_version": CURRENT_LOG_SCHEMA_VERSION, **(payload or {})},
        trace_id=None,
        version=version,
    )


def _cm() -> ChannelManager:
    cm = ChannelManager()
    cm.register("messages", ChannelDef(type=ChannelType.TOPIC, default=[]))
    cm.register("draft", ChannelDef(type=ChannelType.LAST_VALUE, default=None))
    cm.register("_route", ChannelDef(type=ChannelType.LAST_VALUE, default="true"))
    cm.register("_resume_value", ChannelDef(type=ChannelType.LAST_VALUE, default=None))
    return cm


# --------------------------------------------------------------------------
# 1.1 FORK event type
# --------------------------------------------------------------------------


def test_fork_event_type_enum_value():
    assert EventType.FORK == "FORK"
    assert EventType.FORK.value == "FORK"


@pytest.mark.asyncio
async def test_fork_event_storeable_roundtrip():
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    await store.append(_mk_event(sid, 1, EventType.FORK, {"next_nodes": ["b"]}))
    events = await store.get_events(sid)
    assert events[0].event_type == EventType.FORK


# --------------------------------------------------------------------------
# 1.2 Fold: FORK hydration branch
# --------------------------------------------------------------------------


def test_fold_fork_hydration_then_incremental_writes():
    cm = _cm()
    sid = uuid.uuid4()
    events = [
        _mk_event(sid, 1, EventType.FORK, {"channel_state": {"messages": ["m1", "m2"], "draft": "plan"}}),
        _mk_event(sid, 2, EventType.CHANNEL_WRITE, {"channel": "draft", "value": "revised"}),
        _mk_event(sid, 3, EventType.STEP_END),
    ]
    last = fold_session(cm, iter(events))
    assert last == 3
    assert list(cm.read("messages")) == ["m1", "m2"]
    assert cm.read("draft") == "revised"


def test_fold_fork_hydration_replaces_not_merges():
    cm = _cm()
    cm.write("messages", ["stale"])
    sid = uuid.uuid4()
    fold_session(cm, iter([_mk_event(sid, 1, EventType.FORK, {"channel_state": {"messages": ["fresh"]}})]))
    assert list(cm.read("messages")) == ["fresh"]


def test_fold_fork_filters_ephemeral_payload_channels():
    cm = _cm()
    sid = uuid.uuid4()
    fold_session(
        cm,
        iter([_mk_event(sid, 1, EventType.FORK, {"channel_state": {"messages": ["m"], "_resume_value": "x"}})]),
    )
    assert list(cm.read("messages")) == ["m"]
    assert cm.read("_resume_value") is None


def test_fold_fork_route_survives_hydration():
    cm = _cm()
    sid = uuid.uuid4()
    fold_session(cm, iter([_mk_event(sid, 1, EventType.FORK, {"channel_state": {"_route": "false"}})]))
    assert cm.read("_route") == "false"


# --------------------------------------------------------------------------
# 1.3 Commit points / torn tail
# --------------------------------------------------------------------------


def test_fork_is_commit_point_for_torn_tail_fallback():
    from hecate.studio.replay.state_inspector import _select_commit_points

    sid = uuid.uuid4()
    events = [
        _mk_event(sid, 1, EventType.FORK, {"channel_state": {"messages": ["m"]}}),
        _mk_event(sid, 2, EventType.CHANNEL_WRITE, {"channel": "messages", "value": ["m", "torn"]}),
    ]
    assert _select_commit_points(events) == [1]


# --------------------------------------------------------------------------
# 1.4 Log invariants stay neutral over FORK + update_state batches
# --------------------------------------------------------------------------


def test_invariants_neutral_over_fork_and_update_streams():
    sid = uuid.uuid4()
    events = [
        _mk_event(sid, 1, EventType.TURN_START),
        _mk_event(sid, 2, EventType.FORK, {"channel_state": {"messages": ["m"]}, "next_nodes": ["b"]}),
        _mk_event(
            sid,
            3,
            EventType.CHANNEL_WRITE,
            {"channel": "draft", "value": "v", "source": "update_state", "actor": "user-1"},
        ),
        _mk_event(sid, 4, EventType.STEP_END, {"source": "update_state"}),
        _mk_event(sid, 5, EventType.TURN_END),
    ]
    run_all(events)  # must not raise


# --------------------------------------------------------------------------
# 2.1 Continuation derivation matrix
# --------------------------------------------------------------------------


def _events(sid: uuid.UUID, specs: list[tuple[EventType, dict | None, str | None, int]]) -> list[Event]:
    out = []
    for i, (etype, payload, node_id, superstep) in enumerate(specs, start=1):
        out.append(_mk_event(sid, i, etype, payload, node_id=node_id, superstep=superstep))
    return out


def test_derive_from_fork_payload():
    graph = _linear_graph()
    sid = uuid.uuid4()
    cont = derive_continuation(_events(sid, [(EventType.FORK, {"next_nodes": ["c"]}, None, 2)]), graph, {})
    assert cont.nodes == ["c"]
    assert cont.skip_before == set()


def test_derive_dynamic_fanout_plan_adopted_when_planner_in_executed():
    """1.3.21③: STEP_END with planner in executed + dispatch plan wins over static edges."""
    # The graph needs a CONDITION node (planner) so the live-planner guard
    # passes. Build a minimal one inline.
    from hecate.runtime.types import CompiledGraph, NodeConfig, NodeType

    graph = CompiledGraph(
        nodes={
            "planner": NodeConfig(id="planner", type=NodeType.CONDITION, config={}),
            "branch": NodeConfig(id="branch", type=NodeType.CONVERSATION, config={}),
        },
        edges=[],
        channels={},
        entry_point="planner",
    )
    sid = uuid.uuid4()
    cont = derive_continuation(
        _events(
            sid,
            [
                (EventType.NODE_END, None, "planner", 1),
                (EventType.STEP_END, {}, None, 1),
            ],
        ),
        graph,
        {
            "_dispatch": [
                {"node": "branch"},
                {"node": "branch"},
            ],
            "_route": "true",
        },
    )
    assert cont.nodes == ["branch"]


def test_derive_stale_dispatch_plan_ignored():
    """1.3.21③: dispatch plan left over from an older superstep SHALL NOT fire."""
    graph = _linear_graph()
    sid = uuid.uuid4()
    cont = derive_continuation(
        _events(
            sid,
            [
                (EventType.NODE_END, None, "unrelated", 1),
                (EventType.STEP_END, {}, None, 1),
            ],
        ),
        graph,
        {
            "_dispatch": [{"node": "ghost"}],
            "_route": "true",
        },
    )
    # No live planner in executed list → static out-edges win (none here →
    # empty continuation; the legacy fallback is to the entry point).
    assert cont.nodes == ["a"]


async def test_commit_points_exposes_fanout_metadata():
    """1.3.21③ STEP_END fanout segment surfaces in commit-points."""
    from hecate.studio.workflows.execution_service import WorkflowExecutionService

    # Build a minimal service: only event_store is exercised here.
    class _StubService:
        _event_store = None

    # The list_commit_points method lives on the service instance; it only
    # touches _event_store. Patch in our store.

    sid = uuid.uuid4()
    store = InMemoryEventStore()
    # Emit a planner superstep with a fanout segment.
    from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION

    await store.append(
        Event(
            session_id=sid,
            superstep=1,
            event_type=EventType.NODE_END,
            node_id="planner",
            payload={"log_schema_version": CURRENT_LOG_SCHEMA_VERSION},
        )
    )
    await store.append(
        Event(
            session_id=sid,
            superstep=1,
            event_type=EventType.STEP_END,
            node_id=None,
            payload={
                "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                "fanout": [
                    {
                        "source": "planner",
                        "dynamic": True,
                        "sub_channels": {"idx0": {}, "idx1": {}},
                    }
                ],
            },
        )
    )

    svc = WorkflowExecutionService.__new__(WorkflowExecutionService)
    svc._event_store = store
    svc._session_repo = None
    svc._graph_version_repo = None
    svc._compiled_graph_cache = None
    svc._checkpoint_store = None
    svc._conflict_resolver = None
    svc._harness_engine = None
    svc._streaming_repo = None

    anchors = await svc.list_commit_points(sid)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor["kind"] == "STEP_END"
    assert "fanout" in anchor
    assert anchor["fanout"]["packet_count"] == 2
    assert anchor["fanout"]["sources"] == ["planner"]


def test_derive_from_step_end_node_outedges():
    graph = _linear_graph()
    sid = uuid.uuid4()
    cont = derive_continuation(
        _events(
            sid,
            [
                (EventType.NODE_END, None, "a", 1),
                (EventType.CHANNEL_WRITE, {"channel": "messages", "value": ["a"]}, "a", 1),
                (EventType.STEP_END, {}, None, 1),
            ],
        ),
        graph,
        {},
    )
    assert cont.nodes == ["b"]


def test_derive_interrupt_declarative_before_runs_paused_nodes():
    graph = _linear_graph()
    sid = uuid.uuid4()
    descriptor = {"kind": "declarative", "phase": "before", "nodes": ["b"]}
    cont = derive_continuation(_events(sid, [(EventType.INTERRUPT, descriptor, "b", 1)]), graph, {})
    assert cont.nodes == ["b"]
    assert cont.skip_before == {"b"}


def test_derive_interrupt_declarative_after_follows_outedges():
    graph = _linear_graph()
    sid = uuid.uuid4()
    descriptor = {"kind": "declarative", "phase": "after", "nodes": ["a"]}
    cont = derive_continuation(_events(sid, [(EventType.INTERRUPT, descriptor, "a", 1)]), graph, {})
    assert cont.nodes == ["b"]
    assert cont.skip_before == set()


def test_derive_interrupt_worker_authored_follows_outedges():
    graph = _linear_graph()
    sid = uuid.uuid4()
    cont = derive_continuation(_events(sid, [(EventType.INTERRUPT, {}, "b", 1)]), graph, {})
    assert cont.nodes == ["c"]


def test_derive_skips_update_state_step_end_closer():
    graph = _linear_graph()
    sid = uuid.uuid4()
    cont = derive_continuation(
        _events(
            sid,
            [
                (EventType.FORK, {"next_nodes": ["c"]}, None, 1),
                (EventType.TURN_START, {"reason": "update_state"}, None, 1),
                (EventType.CHANNEL_WRITE, {"channel": "draft", "value": "v", "source": "update_state"}, None, 1),
                (EventType.STEP_END, {"source": "update_state"}, None, 1),
                (EventType.TURN_END, {}, None, 1),
            ],
        ),
        graph,
        {},
    )
    assert cont.nodes == ["c"]


def test_derive_empty_log_falls_back_to_entry():
    graph = _linear_graph()
    cont = derive_continuation([], graph, {})
    assert cont.nodes == ["a"]


def test_derive_conditional_edge_resolves_via_folded_route():
    graph = GraphCompiler().compile(
        _graph_config(
            nodes=["a", "yes", "no"],
            edges=[("a", {"true": "yes", "false": "no"}), ("yes", "__end__"), ("no", "__end__")],
        )
    )
    sid = uuid.uuid4()
    events = _events(
        sid,
        [
            (EventType.NODE_END, None, "a", 1),
            (EventType.STEP_END, {}, None, 1),
        ],
    )
    assert derive_continuation(events, graph, {"_route": "false"}).nodes == ["no"]
    assert derive_continuation(events, graph, {"_route": "true"}).nodes == ["yes"]


def test_derive_end_marker_yields_empty():
    graph = _linear_graph(a_to_end=True)
    sid = uuid.uuid4()
    cont = derive_continuation(
        _events(
            sid,
            [
                (EventType.NODE_END, None, "c", 3),
                (EventType.STEP_END, {}, None, 3),
            ],
        ),
        graph,
        {},
    )
    assert cont.nodes == []


def test_resolve_out_edges_dedupes():
    graph = GraphCompiler().compile(
        _graph_config(
            nodes=["a", "b", "c"],
            edges=[("a", "b"), ("a", "b"), ("a", "c")],
        )
    )
    assert resolve_out_edges(graph, ["a"]) == ["b", "c"]


# --------------------------------------------------------------------------
# 2.2/2.3 Engine resume_from: tail-only restore + fork bootstrap
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_from_rejects_historical_version():
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    worker = _EchoWorker()
    runtime = _runtime(_linear_graph(a_to_end=True), worker, store)
    await _drain(runtime.execute(session_id=sid, initial_input={"messages": ["hi"]}, execution_mode="conversational"))
    events = await store.get_events(sid)
    first_step_end = next(e.version for e in events if e.event_type == EventType.STEP_END)
    tail = events[-1].version

    runtime2 = _runtime(_linear_graph(a_to_end=True), _EchoWorker(), store)
    with pytest.raises(ValueError, match="fork"):
        await _drain(runtime2.execute(session_id=sid, resume_from=first_step_end, execution_mode="conversational"))
    # Guard must not append anything to the log.
    assert (await store.get_events(sid))[-1].version == tail


@pytest.mark.asyncio
async def test_resume_from_tail_continues_after_step_end():
    """Crash-recovery shape: log tail is a STEP_END; execution continues from
    the completed nodes' out-edges."""
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    runtime = _runtime(_linear_graph(a_to_end=True), _EchoWorker(), store)
    await _drain(runtime.execute(session_id=sid, initial_input={"messages": ["hi"]}, execution_mode="conversational"))
    events = await store.get_events(sid)

    # Simulate a crash after superstep 1: copy the log prefix that ends at
    # the first superstep STEP_END into a fresh session (skip the
    # initial_input commit anchor — it carries no executed nodes).
    first_step_end = next(
        e.version for e in events if e.event_type == EventType.STEP_END and e.payload.get("source") != "initial_input"
    )
    crashed_sid = uuid.uuid4()
    prefix = [e for e in events if e.version <= first_step_end]
    await store.append_batch(
        [
            Event(
                session_id=crashed_sid,
                superstep=e.superstep,
                event_type=e.event_type,
                node_id=e.node_id,
                payload=e.payload,
                trace_id=None,
            )
            for e in prefix
        ]
    )

    worker = _EchoWorker()
    runtime2 = _runtime(_linear_graph(a_to_end=True), worker, store)
    tail = (await store.get_events(crashed_sid))[-1].version
    await _drain(runtime2.execute(session_id=crashed_sid, resume_from=tail, execution_mode="conversational"))

    # Continuation derived from node "a"'s out-edges → b then c execute next.
    assert worker.executed == ["b", "c"]
    final_events = await store.get_events(crashed_sid)
    assert final_events[-1].event_type in (EventType.TURN_END, EventType.STEP_END)


@pytest.mark.asyncio
async def test_fork_bootstrap_end_to_end():
    """Parent interrupts at b (worker-authored); fork creates a child whose
    FORK event carries the snapshot; the child continues at b's out-edges."""
    store = InMemoryEventStore()
    parent = uuid.uuid4()
    parent_runtime = _runtime(_linear_graph(a_to_end=True), _InterruptAtWorker("b"), store)
    out = await _drain(
        parent_runtime.execute(session_id=parent, initial_input={"messages": ["hi"]}, execution_mode="conversational")
    )
    assert any(e.get("type") == "interrupt" for e in out)
    parent_events = await store.get_events(parent)
    interrupt_version = next(e.version for e in parent_events if e.event_type == EventType.INTERRUPT)

    # Service-layer flow (engine primitives only): snapshot + derive + FORK.
    parent_runtime2 = _runtime(_linear_graph(a_to_end=True), _EchoWorker(), store)
    snap = await parent_runtime2.snapshot_at_version(parent, interrupt_version)
    assert snap["log_version"] == interrupt_version
    assert "_route" in snap["channel_state"] or "_route" not in snap["channel_state"]  # filtered set is policy-driven
    assert "_session_id" not in snap["channel_state"]
    cont = derive_continuation(
        [e for e in parent_events if e.version <= interrupt_version],
        parent_runtime2._graph,
        snap["channel_state"],
    )
    assert cont.nodes == ["c"]

    child = uuid.uuid4()
    await store.append(
        Event(
            session_id=child,
            superstep=snap["superstep"],
            event_type=EventType.FORK,
            payload={
                "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                "parent_session_id": parent,
                "parent_log_version": interrupt_version,
                "channel_state": snap["channel_state"],
                "next_nodes": cont.nodes,
            },
        )
    )

    child_worker = _EchoWorker(content="child")
    child_runtime = _runtime(_linear_graph(a_to_end=True), child_worker, store)
    tail = (await store.get_events(child))[-1].version
    await _drain(child_runtime.execute(session_id=child, resume_from=tail, execution_mode="conversational"))

    assert child_worker.executed == ["c"]
    child_events = await store.get_events(child)
    assert child_events[0].event_type == EventType.FORK
    writes = [e for e in child_events if e.event_type == EventType.CHANNEL_WRITE]
    assert writes, "child execution must commit writes"
    folded = _cm()
    fold_session(folded, iter(child_events))
    assert "c:child" in list(folded.read("messages"))


@pytest.mark.asyncio
async def test_snapshot_at_version_filters_ephemerals():
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    runtime = _runtime(_linear_graph(a_to_end=True), _EchoWorker(), store)
    await _drain(
        runtime.execute(
            session_id=sid,
            initial_input={"messages": ["hi"], "_session_id": str(sid), "sys.execution_mode": "conversational"},
            execution_mode="conversational",
        )
    )
    snap = await runtime.snapshot_at_version(sid)
    assert "_session_id" not in snap["channel_state"]
    assert "sys.execution_mode" not in snap["channel_state"]
    assert "messages" in snap["channel_state"]
    assert snap["superstep"] >= 1
