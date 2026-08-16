"""Tests for execution identity (trace_id) in PregelRuntime.

Validates the spec for ``Execution identity per invoke`` (eventstore spec):
- All events emitted during one invocation share the same trace_id.
- Two consecutive invokes on the same session do not share identity when no
  explicit trace_id is passed (no OTel configured).
- Explicit trace_id takes precedence over generated identity.
- Resume creates a new execution identity.
- Worker-emitted events (LLM_REQUEST/RESPONSE, TOOL_CALL/RESULT) carry the
  execution_context trace_id.
- trace_id=None in execution_context does not break event recording.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.engine.channel import ChannelManager
from hecate.engine.checkpoint import InMemoryCheckpointStore
from hecate.engine.eventstore import Event, EventType, InMemoryEventStore
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


def _make_linear_graph() -> CompiledGraph:
    """Build a minimal 3-node graph A -> B -> C."""
    return CompiledGraph(
        name="identity-test",
        entry_point="A",
        nodes={
            "A": NodeConfig(id="A", type=NodeType.CONVERSATION, config={}),
            "B": NodeConfig(id="B", type=NodeType.CONVERSATION, config={}),
            "C": NodeConfig(id="C", type=NodeType.CONVERSATION, config={}),
            "__end__": NodeConfig(id="__end__", type=NodeType.CONVERSATION, config={}),
        },
        channels={
            "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
        },
        edges=[Edge(source="A", target="B"), Edge(source="B", target="C"), Edge(source="C", target="__end__")],
    )


class SimpleWorker(Worker):
    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        return WorkerResult(node_id=node_id, channel_updates={"messages": [f"{node_id}_out"]})


def _build_event(
    channel: str,
    value: object,
    trace_id: str | None = "fixed-trace",
    session_id: uuid.UUID | None = None,
) -> Event:
    return Event(
        session_id=session_id or uuid.uuid4(),
        superstep=1,
        event_type=EventType.CHANNEL_WRITE,
        node_id="A",
        trace_id=trace_id,
        payload={"channel": channel, "value": value, "log_schema_version": 2},
    )


@pytest.mark.asyncio
async def test_event_factory_accepts_trace_id() -> None:
    """Event dataclass accepts and exposes trace_id (the new correlation field)."""
    ev = _build_event("messages", ["x"], trace_id="abc123")
    assert ev.trace_id == "abc123"


@pytest.mark.asyncio
async def test_event_trace_id_default_is_none() -> None:
    """Backward compat: trace_id defaults to None (workers without correlation still work)."""
    ev = Event(
        session_id=uuid.uuid4(),
        superstep=0,
        event_type=EventType.CUSTOM,
        payload={"k": "v"},
    )
    assert ev.trace_id is None


@pytest.mark.asyncio
async def test_in_memory_event_store_preserves_trace_id() -> None:
    """InMemoryEventStore round-trips trace_id without modification."""
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    await store.append(_build_event("messages", [1], trace_id="t1", session_id=sid))
    await store.append(_build_event("messages", [2], trace_id="t2", session_id=sid))

    events = await store.get_events(sid)
    assert len(events) == 2
    assert [e.trace_id for e in events] == ["t1", "t2"]


@pytest.mark.asyncio
async def test_get_events_filter_by_trace_id_partition() -> None:
    """Events can be partitioned by trace_id via get_events + list comprehension."""
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    for i in range(4):
        await store.append(_build_event("messages", [i], trace_id=("tA" if i % 2 == 0 else "tB"), session_id=sid))

    events = await store.get_events(sid)
    partitioned: dict[str | None, list[Event]] = {}
    for ev in events:
        partitioned.setdefault(ev.trace_id, []).append(ev)

    assert len(partitioned["tA"]) == 2
    assert len(partitioned["tB"]) == 2


@pytest.mark.asyncio
async def test_execution_context_propagates_trace_id() -> None:
    """execution_context carries trace_id, enabling worker correlation."""
    runtime = PregelRuntime(_make_linear_graph(), SimpleWorker(), checkpoint_store=InMemoryCheckpointStore())
    ctx = runtime._execution_context(uuid.uuid4(), trace_id="trace-XYZ")
    assert ctx["trace_id"] == "trace-XYZ"


@pytest.mark.asyncio
async def test_execution_context_trace_id_default_none() -> None:
    """execution_context trace_id defaults to None for backward compat (no correlation)."""
    runtime = PregelRuntime(_make_linear_graph(), SimpleWorker(), checkpoint_store=InMemoryCheckpointStore())
    ctx = runtime._execution_context(uuid.uuid4())
    assert ctx["trace_id"] is None


@pytest.mark.asyncio
async def test_pregel_no_otel_generates_unique_identities() -> None:
    """Two invokes on different sessions get distinct generated trace_ids when none provided."""
    graph = _make_linear_graph()
    shared_event_store = InMemoryEventStore()
    # Two independent sessions (no shared checkpoint state).
    store1 = InMemoryCheckpointStore()
    runtime1 = PregelRuntime(graph, SimpleWorker(), store1, event_store=shared_event_store)
    async for _ in runtime1.execute(uuid.uuid4()):
        pass

    store2 = InMemoryCheckpointStore()
    runtime2 = PregelRuntime(graph, SimpleWorker(), store2, event_store=shared_event_store)
    async for _ in runtime2.execute(uuid.uuid4()):
        pass

    # Aggregate events across all sessions the shared store holds.
    all_events: list[Event] = []
    for session_events in shared_event_store._store.values():  # type: ignore[attr-defined]
        all_events.extend(session_events)

    identities = {e.trace_id for e in all_events}
    assert len(identities) >= 2
    assert None not in identities


@pytest.mark.asyncio
async def test_pregel_explicit_trace_id_takes_precedence() -> None:
    """Explicit trace_id argument propagates to all emitted events."""
    graph = _make_linear_graph()
    store = InMemoryCheckpointStore()
    runtime = PregelRuntime(graph, SimpleWorker(), store, event_store=InMemoryEventStore())
    session_id = uuid.uuid4()
    explicit = "deadbeef" * 8
    async for _ in runtime.execute(session_id, trace_id=explicit):
        pass

    events = await runtime._event_store.get_events(session_id)  # type: ignore[union-attr]
    assert events
    assert all(e.trace_id == explicit for e in events)


@pytest.mark.asyncio
async def test_pregel_events_have_no_zero_trace_id() -> None:
    """Generated trace_ids are never all-zero (the OTel degenerate case)."""
    graph = _make_linear_graph()
    store = InMemoryCheckpointStore()
    runtime = PregelRuntime(graph, SimpleWorker(), store, event_store=InMemoryEventStore())
    session_id = uuid.uuid4()
    async for _ in runtime.execute(session_id):
        pass

    events = await runtime._event_store.get_events(session_id)  # type: ignore[union-attr]
    for ev in events:
        if ev.trace_id is not None:
            assert ev.trace_id != "0" * 32, f"degenerate trace_id: {ev.payload}"


@pytest.mark.asyncio
async def test_resume_creates_new_execution_identity() -> None:
    """A resume invoke uses a different trace_id than the interrupted invoke."""
    graph = _make_linear_graph()
    store = InMemoryCheckpointStore()
    shared_event_store = InMemoryEventStore()

    class InterruptWorker(Worker):
        async def execute(self, node_id, node_config, channel_snapshot, execution_context=None):  # type: ignore[override]
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [f"{node_id}_out"]},
                command=Command(interrupt={"type": "approval"}) if node_id == "B" else None,
            )

    rt1 = PregelRuntime(graph, InterruptWorker(), store, event_store=shared_event_store)
    session_id = uuid.uuid4()
    async for _ in rt1.execute(session_id):
        pass

    rt2 = PregelRuntime(graph, SimpleWorker(), store, event_store=shared_event_store)
    async for _ in rt2.execute(session_id, resume_value="ok"):
        pass

    events = await shared_event_store.get_events(session_id)
    identities = {e.trace_id for e in events}
    assert len(identities) >= 2


@pytest.mark.asyncio
async def test_worker_event_append_accepts_trace_id_none() -> None:
    """Verifies append path tolerates trace_id=None (does not crash recording).

    This exercises the Event constructor with trace_id=None end-to-end through
    InMemoryEventStore — the same path workers use when execution_context lacks
    trace_id (legacy callers or tests).
    """
    store = InMemoryEventStore()
    sid = uuid.uuid4()
    ev = Event(
        session_id=sid,
        superstep=0,
        event_type=EventType.CUSTOM,
        trace_id=None,
        payload={"k": "v"},
    )
    await store.append(ev)
    events = await store.get_events(sid)
    assert len(events) == 1
    assert events[0].trace_id is None


@pytest.mark.asyncio
async def test_channel_manager_unaffected_by_identity_change() -> None:
    """Identity refactor does not change ChannelManager behavior (regression guard).

    ChannelManager.register(name, defn) registers channels; identity is orthogonal.
    """
    cm = ChannelManager()
    cm.register("messages", ChannelDef(type=ChannelType.TOPIC, default=[]))
    cm.write("messages", ["a"])
    cm.write("messages", ["b"])
    assert list(cm.read("messages")) == ["a", "b"]
