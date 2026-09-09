"""Service-level tests for 1.3.21② time-travel (update_state / fork).

Engine semantics (fold, derivation, resume_from) are covered in
``tests/test_runtime/test_time_travel_resume.py``; these tests pin the
orchestration: gating, audited batch shape, lineage, parent-log
immutability, and the fork execution kickoff.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from hecate.runtime.channel import ChannelManager
from hecate.runtime.compiler import GraphCompiler
from hecate.runtime.eventstore import (
    CURRENT_LOG_SCHEMA_VERSION,
    Event,
    EventType,
    InMemoryEventStore,
)
from hecate.runtime.types import ChannelDef, ChannelType, Edge, GraphConfig, NodeConfig, NodeType
from hecate.runtime.worker import Worker, WorkerResult
from hecate.studio.workflows.execution_service import (
    InvalidForkAnchorError,
    InvalidStateChannelError,
    TurnInFlightError,
    WorkflowExecutionService,
)


class _RecordingWorker(Worker):
    """Echo worker that records the dispatch order."""

    def __init__(self) -> None:
        super().__init__()
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
        return WorkerResult(node_id=node_id, channel_updates={"messages": messages + [node_id]})


def _linear_compiled() -> Any:
    cfg = GraphConfig(
        name="fork_target",
        state={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        nodes={n: NodeConfig(id=n, type=NodeType.CONVERSATION, config={}) for n in ("a", "b", "c")},
        edges=[
            Edge(source="a", target="b"),
            Edge(source="b", target="c"),
            Edge(source="c", target="__end__"),
        ],
        entry="a",
    )
    compiled = GraphCompiler().compile(cfg)
    for _nid, ncfg in compiled.nodes.items():
        ncfg.config["_node_type"] = ncfg.type.value
    return compiled


class _ParentRow:
    """Minimal parent-session stand-in for the service's row lookup."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.agent_id = uuid.uuid4()
        self.workspace_id = uuid.UUID(int=0)


class _StubDBWithParent:
    def __init__(self, parent: _ParentRow) -> None:
        self._parent = parent
        self.added: list[Any] = []

    async def execute(self, stmt: object) -> Any:
        class _Result:
            def scalar_one_or_none(inner_self) -> Any:  # noqa: N805
                return self._parent

        return _Result()

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        # Emulate SQLAlchemy applying Python-side column defaults on flush.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()  # type: ignore[attr-defined]

    async def refresh(self, obj: object) -> None:
        pass


def _worker_factory(worker: _RecordingWorker):
    """Bind a stub worker for ``_create_composite_worker`` (loop-safe closure)."""

    def factory(*args: object, **kwargs: object) -> _RecordingWorker:
        return worker

    return factory


def _service(store: InMemoryEventStore, parent: _ParentRow | None = None) -> WorkflowExecutionService:
    svc = WorkflowExecutionService(
        port=None,
        db=_StubDBWithParent(parent) if parent is not None else None,
        event_store=store,
    )
    return svc


def _ev(
    session_id: uuid.UUID, etype: EventType, payload: dict | None = None, node_id: str | None = None, superstep: int = 1
) -> Event:
    return Event(
        session_id=session_id,
        superstep=superstep,
        event_type=etype,
        node_id=node_id,
        payload={"log_schema_version": CURRENT_LOG_SCHEMA_VERSION, **(payload or {})},
    )


def _parent_log_events(sid: uuid.UUID) -> list[Event]:
    """One completed superstep (node a), a worker interrupt at b, closed turn."""
    return [
        _ev(sid, EventType.TURN_START),
        _ev(sid, EventType.NODE_END, node_id="a"),
        _ev(sid, EventType.CHANNEL_WRITE, {"channel": "messages", "value": ["hi", "a"]}, node_id="a"),
        _ev(sid, EventType.STEP_END),
        _ev(sid, EventType.INTERRUPT, {"kind": "worker", "nodes": ["b"], "interrupt_value_type": "dict"}, node_id="b"),
        _ev(sid, EventType.TURN_END),
    ]


# --------------------------------------------------------------------------
# update_state
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_state_appends_audited_batch():
    sid = uuid.uuid4()
    store = InMemoryEventStore()
    for event in _parent_log_events(sid):
        await store.append(event)

    svc = _service(store)
    svc._build_continuation_graph = _fake_graph  # type: ignore[method-assign]

    result = await svc.update_state(sid, {"messages": ["patched"]}, actor="user-1")

    events = await store.get_events(sid)
    tail = events[-4:]
    assert tail[0].event_type == EventType.TURN_START
    assert tail[0].payload["reason"] == "update_state"
    assert tail[0].payload["actor"] == "user-1"
    assert tail[1].event_type == EventType.CHANNEL_WRITE
    assert tail[1].payload["channel"] == "messages"
    assert tail[1].payload["source"] == "update_state"
    assert tail[1].payload["actor"] == "user-1"
    assert tail[2].event_type == EventType.STEP_END
    assert tail[2].payload["source"] == "update_state"
    assert tail[3].event_type == EventType.TURN_END
    # Fold response: TOPIC append semantics — prior messages + the patch.
    assert result["channel_state"]["messages"] == ["hi", "a", "patched"]
    assert result["log_version"] == events[-1].version


@pytest.mark.asyncio
async def test_update_state_refuses_in_flight_turn():
    sid = uuid.uuid4()
    store = InMemoryEventStore()
    await store.append(_ev(sid, EventType.TURN_START))
    await store.append(_ev(sid, EventType.NODE_END, node_id="a"))

    svc = _service(store)
    with pytest.raises(TurnInFlightError):
        await svc.update_state(sid, {"messages": ["x"]})
    assert len(await store.get_events(sid)) == 2  # nothing appended


@pytest.mark.asyncio
async def test_update_state_allows_crashed_turn_via_error_escape():
    sid = uuid.uuid4()
    store = InMemoryEventStore()
    await store.append(_ev(sid, EventType.TURN_START))
    await store.append(_ev(sid, EventType.ERROR, {"error_type": "RuntimeError"}))

    svc = _service(store)
    svc._build_continuation_graph = _fake_graph  # type: ignore[method-assign]
    result = await svc.update_state(sid, {"messages": ["x"]})
    assert result["channel_state"]["messages"] == ["x"]


@pytest.mark.asyncio
async def test_update_state_rejects_non_loggable_or_unknown_channel():
    sid = uuid.uuid4()
    store = InMemoryEventStore()
    svc = _service(store)
    svc._build_continuation_graph = _fake_graph  # type: ignore[method-assign]

    with pytest.raises(InvalidStateChannelError):
        await svc.update_state(sid, {"_resume_value": "x"})  # LogPolicy-excluded
    with pytest.raises(InvalidStateChannelError):
        await svc.update_state(sid, {"draft": "x"})  # not in graph state


@pytest.mark.asyncio
async def test_update_state_keeps_interrupt_open():
    """A mutation after an unclosed INTERRUPT must not close it (HITL re-plan)."""
    sid = uuid.uuid4()
    store = InMemoryEventStore()
    for event in _parent_log_events(sid):
        await store.append(event)

    svc = _service(store)
    svc._build_continuation_graph = _fake_graph  # type: ignore[method-assign]
    await svc.update_state(sid, {"messages": ["patch"]})

    events = await store.get_events(sid)
    interrupts = [e for e in events if e.event_type == EventType.INTERRUPT]
    resumes = [e for e in events if e.event_type == EventType.RESUME]
    assert len(interrupts) == 1 and not resumes


# --------------------------------------------------------------------------
# fork
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_from_interrupt_anchor_with_updates_runs_child():
    parent = _ParentRow()
    sid = parent.id
    store = InMemoryEventStore()
    for event in _parent_log_events(sid):
        await store.append(event)
    parent_events_before = [(e.version, e.event_type, dict(e.payload)) for e in await store.get_events(sid)]

    svc = _service(store, parent)
    worker = _RecordingWorker()
    svc._build_continuation_graph = _fake_graph  # type: ignore[method-assign]
    svc._create_composite_worker = _worker_factory(worker)

    result = await svc.fork_session(
        sid,
        at_version=5,
        updates={"messages": ["plan-v2"]},
        actor="user-1",
    )

    # Anchor: the INTERRUPT (worker-authored at b) → out-edges → [c].
    assert result["parent_session_id"] == str(sid)
    assert result["parent_log_version"] == 5
    assert result["effective_version"] == 5
    assert result["next_nodes"] == ["c"]
    assert result["executed"] is True
    assert "side_effects_note" in result and result["side_effects_note"]

    # Parent log untouched — byte-identical event sequence.
    parent_events_after = [(e.version, e.event_type, dict(e.payload)) for e in await store.get_events(sid)]
    assert parent_events_after == parent_events_before

    # Child log: FORK bootstrap → update batch → execution events.
    child_id = result["session_id"]
    child_events = await store.get_events(child_id)
    assert child_events[0].event_type == EventType.FORK
    fork_payload = child_events[0].payload
    assert fork_payload["parent_session_id"] == str(sid)
    assert fork_payload["parent_log_version"] == 5
    assert fork_payload["next_nodes"] == ["c"]
    assert "messages" in fork_payload["channel_state"]
    update_writes = [
        e for e in child_events if e.event_type == EventType.CHANNEL_WRITE and e.payload.get("source") == "update_state"
    ]
    assert len(update_writes) == 1
    assert worker.executed == ["c"]

    # Child log is self-contained: fold reproduces executed state.
    cm = ChannelManager()
    cm.register("messages", ChannelDef(type=ChannelType.TOPIC, default=[]))
    from hecate.runtime.replay.logfold import fold_session

    fold_session(cm, iter(child_events))
    assert "c" in list(cm.read("messages"))


@pytest.mark.asyncio
async def test_fork_snaps_down_to_nearest_commit_point():
    parent = _ParentRow()
    sid = parent.id
    store = InMemoryEventStore()
    for event in _parent_log_events(sid):
        await store.append(event)

    svc = _service(store, parent)
    svc._build_continuation_graph = _fake_graph  # type: ignore[method-assign]
    worker = _RecordingWorker()
    svc._create_composite_worker = _worker_factory(worker)

    # v3 is a mid-superstep CHANNEL_WRITE; the nearest commit at or below
    # is none (STEP_END sits at v4) → rejected. v4 snaps exactly.
    with pytest.raises(InvalidForkAnchorError):
        await svc.fork_session(sid, at_version=3)

    result = await svc.fork_session(sid, at_version=4)
    assert result["effective_version"] == 4
    # STEP_END anchor → executed nodes since previous commit = [a] → out-edges → [b].
    assert result["next_nodes"] == ["b"]
    assert worker.executed[:1] == ["b"]


@pytest.mark.asyncio
async def test_fork_rejects_bad_anchors():
    parent = _ParentRow()
    sid = parent.id
    store = InMemoryEventStore()
    for event in _parent_log_events(sid):
        await store.append(event)

    svc = _service(store, parent)
    svc._build_continuation_graph = _fake_graph  # type: ignore[method-assign]

    with pytest.raises(InvalidForkAnchorError):  # beyond tail
        await svc.fork_session(sid, at_version=99)
    with pytest.raises(InvalidForkAnchorError):  # below first commit point
        await svc.fork_session(sid, at_version=2)

    empty_parent = _ParentRow()
    empty_store = InMemoryEventStore()
    svc2 = _service(empty_store, empty_parent)
    svc2._build_continuation_graph = _fake_graph  # type: ignore[method-assign]
    with pytest.raises(InvalidForkAnchorError):  # empty log
        await svc2.fork_session(empty_parent.id, at_version=1)

    no_row_svc = _service(InMemoryEventStore())  # no parent row resolvable
    with pytest.raises(InvalidForkAnchorError):
        await no_row_svc.fork_session(uuid.uuid4(), at_version=1)


@pytest.mark.asyncio
async def test_fork_same_anchor_twice_yields_independent_children():
    parent = _ParentRow()
    sid = parent.id
    store = InMemoryEventStore()
    for event in _parent_log_events(sid):
        await store.append(event)

    children = []
    for i in range(2):
        svc = _service(store, parent)
        svc._build_continuation_graph = _fake_graph  # type: ignore[method-assign]
        worker = _RecordingWorker()
        svc._create_composite_worker = _worker_factory(worker)
        result = await svc.fork_session(sid, at_version=5, updates={"messages": [f"variant-{i}"]})
        children.append(result["session_id"])

    assert children[0] != children[1]
    logs = [await store.get_events(cid) for cid in children]
    assert logs[0][0].event_type == EventType.FORK and logs[1][0].event_type == EventType.FORK
    # Parent untouched: same event count, still ends on the closed interrupt turn.
    parent_after = await store.get_events(sid)
    assert len(parent_after) == 6 and parent_after[-1].event_type == EventType.TURN_END


async def _fake_graph(session: Any, model: str | None = None, workflow_id: uuid.UUID | None = None) -> tuple[Any, str]:
    return _linear_compiled(), "conversational"
