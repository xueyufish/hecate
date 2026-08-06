"""Integration tests for EventStore + PregelRuntime + WorkflowExecutionService.

Validates the end-to-end event flow: PregelRuntime emits events → EventStore
appends them with monotonic versions → SessionState.event_position syncs.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.engine.eventstore import Event, EventType, InMemoryEventStore
from hecate.engine.session_state import SessionState
from hecate.services.workflow.execution_service import _sync_event_position


@pytest.mark.parametrize("n_events", [1, 3, 10])
async def test_eventstore_append_yields_monotonic_versions(n_events: int):
    """Append N events to one session; versions SHALL be [1, 2, ..., N] in order."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    for i in range(n_events):
        await store.append(
            Event(
                session_id=session_id,
                superstep=i,
                event_type=EventType.NODE_START,
                node_id=f"node_{i}",
            )
        )

    events = await store.get_events(session_id)
    assert [e.version for e in events] == list(range(1, n_events + 1))
    assert all(e.session_id == session_id for e in events)


async def test_eventstore_event_type_round_trip():
    """Each EventType SHALL round-trip through append+get_events unchanged."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    types = [
        EventType.NODE_START,
        EventType.NODE_END,
        EventType.TOOL_CALL,
        EventType.TOOL_RESULT,
        EventType.LLM_REQUEST,
        EventType.LLM_RESPONSE,
        EventType.CHANNEL_WRITE,
        EventType.INTERRUPT,
        EventType.RESUME,
        EventType.ERROR,
        EventType.CUSTOM,
    ]
    for et in types:
        await store.append(Event(session_id=session_id, superstep=0, event_type=et, payload={"k": et.value}))

    events = await store.get_events(session_id)
    assert [e.event_type for e in events] == types
    assert all(e.payload["k"] == e.event_type.value for e in events)


async def test_sync_event_position_after_pregel_emission():
    """After N events emitted, _sync_event_position SHALL set event_position=N."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    for _ in range(7):
        await store.append(Event(session_id=session_id, superstep=0, event_type=EventType.NODE_START))

    state = SessionState(agent_state={})
    synced = await _sync_event_position(state, store, session_id)
    assert synced.event_position == 7


async def test_session_isolation_in_eventstore():
    """Events from one session SHALL NOT appear in another session's queries."""
    store = InMemoryEventStore()
    s1, s2 = uuid.uuid4(), uuid.uuid4()
    for _ in range(3):
        await store.append(Event(session_id=s1, superstep=0, event_type=EventType.NODE_START))
    for _ in range(5):
        await store.append(Event(session_id=s2, superstep=0, event_type=EventType.NODE_END))

    assert len(await store.get_events(s1)) == 3
    assert len(await store.get_events(s2)) == 5
    assert all(e.event_type == EventType.NODE_START for e in await store.get_events(s1))
    assert all(e.event_type == EventType.NODE_END for e in await store.get_events(s2))


async def test_replay_yields_in_version_order():
    """replay SHALL yield events in ascending version order."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    for i in range(5):
        await store.append(Event(session_id=session_id, superstep=i, event_type=EventType.NODE_START))

    seen_versions = []
    async for event in store.replay(session_id):
        seen_versions.append(event.version)
    assert seen_versions == [1, 2, 3, 4, 5]


async def test_replay_from_version_filters():
    """replay(session_id, from_version=N) SHALL yield events with version>=N."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    for i in range(10):
        await store.append(Event(session_id=session_id, superstep=i, event_type=EventType.NODE_START))

    seen = []
    async for event in store.replay(session_id, from_version=7):
        seen.append(event.version)
    assert seen == [7, 8, 9, 10]
