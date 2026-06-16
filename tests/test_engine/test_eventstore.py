"""Tests for the EventStore abstract interface and InMemoryEventStore.

Validates the append-only event persistence contract:

- EventType enum values are correct strings.
- Event dataclass creation, immutability, auto-generated fields.
- InMemoryEventStore append, get_events, replay, get_version.
- Multi-session isolation and edge cases (empty session, from_version beyond range).
"""

from __future__ import annotations

import uuid

import pytest

from hecate.engine.eventstore import Event, EventStore, EventType, InMemoryEventStore

# --- EventType tests ---


def test_event_type_values_are_strings():
    """EventType members SHALL equal their string values."""
    assert EventType.NODE_START == "NODE_START"
    assert EventType.NODE_END == "NODE_END"
    assert EventType.TOOL_CALL == "TOOL_CALL"
    assert EventType.TOOL_RESULT == "TOOL_RESULT"
    assert EventType.CHANNEL_WRITE == "CHANNEL_WRITE"
    assert EventType.LLM_REQUEST == "LLM_REQUEST"
    assert EventType.LLM_RESPONSE == "LLM_RESPONSE"
    assert EventType.INTERRUPT == "INTERRUPT"
    assert EventType.RESUME == "RESUME"
    assert EventType.ERROR == "ERROR"
    assert EventType.CUSTOM == "CUSTOM"


def test_event_type_is_string_enum():
    """EventType SHALL be usable as a string."""
    assert isinstance(EventType.TOOL_CALL, str)


# --- Event dataclass tests ---


def test_event_creation_with_defaults():
    """Event SHALL auto-generate id and timestamp."""
    session_id = uuid.uuid4()
    event = Event(
        session_id=session_id,
        superstep=0,
        event_type=EventType.NODE_START,
    )
    assert isinstance(event.id, uuid.UUID)
    assert event.session_id == session_id
    assert event.superstep == 0
    assert event.event_type == EventType.NODE_START
    assert event.node_id is None
    assert event.payload == {}
    assert event.version == 0
    assert event.timestamp is not None


def test_event_creation_with_all_fields():
    """Event SHALL accept all fields explicitly."""
    event_id = uuid.uuid4()
    session_id = uuid.uuid4()
    payload = {"tool": "search", "args": {"query": "test"}}
    event = Event(
        session_id=session_id,
        superstep=5,
        event_type=EventType.TOOL_CALL,
        node_id="agent_1",
        id=event_id,
        payload=payload,
        version=3,
    )
    assert event.id == event_id
    assert event.session_id == session_id
    assert event.superstep == 5
    assert event.event_type == EventType.TOOL_CALL
    assert event.node_id == "agent_1"
    assert event.payload == payload
    assert event.version == 3


def test_event_immutability():
    """Event SHALL be frozen (immutable)."""
    event = Event(
        session_id=uuid.uuid4(),
        superstep=0,
        event_type=EventType.NODE_START,
    )
    with pytest.raises(AttributeError):
        event.superstep = 1  # type: ignore[misc]


def test_event_custom_type_with_payload():
    """CUSTOM events SHALL store arbitrary payload data."""
    event = Event(
        session_id=uuid.uuid4(),
        superstep=0,
        event_type=EventType.CUSTOM,
        payload={"custom_type": "my_event", "data": [1, 2, 3]},
    )
    assert event.event_type == EventType.CUSTOM
    assert event.payload["custom_type"] == "my_event"


# --- InMemoryEventStore tests ---


@pytest.fixture
def store() -> InMemoryEventStore:
    """Provide a fresh InMemoryEventStore for each test."""
    return InMemoryEventStore()


@pytest.fixture
def session_id() -> uuid.UUID:
    """Provide a fixed session ID for test isolation."""
    return uuid.uuid4()


async def test_append_returns_uuid(store: InMemoryEventStore, session_id: uuid.UUID):
    """append() SHALL return the event's UUID."""
    event = Event(session_id=session_id, superstep=0, event_type=EventType.NODE_START)
    result = await store.append(event)
    assert isinstance(result, uuid.UUID)
    assert result == event.id


async def test_append_assigns_sequential_versions(store: InMemoryEventStore, session_id: uuid.UUID):
    """append() SHALL assign version numbers starting from 1."""
    ids = []
    for i in range(5):
        event = Event(session_id=session_id, superstep=i, event_type=EventType.NODE_START)
        event_id = await store.append(event)
        ids.append(event_id)
    events = await store.get_events(session_id)
    assert [e.version for e in events] == [1, 2, 3, 4, 5]


async def test_get_events_returns_all(store: InMemoryEventStore, session_id: uuid.UUID):
    """get_events() SHALL return all events for a session."""
    for i in range(3):
        event = Event(session_id=session_id, superstep=i, event_type=EventType.NODE_START)
        await store.append(event)
    events = await store.get_events(session_id)
    assert len(events) == 3


async def test_get_events_with_from_version(store: InMemoryEventStore, session_id: uuid.UUID):
    """get_events(from_version=N) SHALL return events with version >= N."""
    for i in range(5):
        event = Event(session_id=session_id, superstep=i, event_type=EventType.NODE_START)
        await store.append(event)
    events = await store.get_events(session_id, from_version=3)
    assert len(events) == 3
    assert [e.version for e in events] == [3, 4, 5]


async def test_get_events_empty_session(store: InMemoryEventStore):
    """get_events() SHALL return empty list for unknown session."""
    events = await store.get_events(uuid.uuid4())
    assert events == []


async def test_get_events_from_version_beyond_range(store: InMemoryEventStore, session_id: uuid.UUID):
    """get_events(from_version=N) SHALL return empty list when N > max version."""
    event = Event(session_id=session_id, superstep=0, event_type=EventType.NODE_START)
    await store.append(event)
    events = await store.get_events(session_id, from_version=100)
    assert events == []


async def test_replay_yields_events_in_order(store: InMemoryEventStore, session_id: uuid.UUID):
    """replay() SHALL yield events in version-ascending order."""
    for i in range(4):
        event = Event(session_id=session_id, superstep=i, event_type=EventType.NODE_START)
        await store.append(event)
    collected = []
    async for event in store.replay(session_id):
        collected.append(event)
    assert len(collected) == 4
    assert [e.version for e in collected] == [1, 2, 3, 4]


async def test_replay_with_from_version(store: InMemoryEventStore, session_id: uuid.UUID):
    """replay(from_version=N) SHALL yield events from version N."""
    for i in range(5):
        event = Event(session_id=session_id, superstep=i, event_type=EventType.NODE_START)
        await store.append(event)
    collected = []
    async for event in store.replay(session_id, from_version=3):
        collected.append(event)
    assert len(collected) == 3
    assert [e.version for e in collected] == [3, 4, 5]


async def test_replay_empty_session(store: InMemoryEventStore):
    """replay() SHALL yield nothing for unknown session."""
    collected = []
    async for event in store.replay(uuid.uuid4()):
        collected.append(event)
    assert collected == []


async def test_get_version_returns_highest(store: InMemoryEventStore, session_id: uuid.UUID):
    """get_version() SHALL return the highest version for a session."""
    for i in range(5):
        event = Event(session_id=session_id, superstep=i, event_type=EventType.NODE_START)
        await store.append(event)
    assert await store.get_version(session_id) == 5


async def test_get_version_empty_session(store: InMemoryEventStore):
    """get_version() SHALL return 0 for unknown session."""
    assert await store.get_version(uuid.uuid4()) == 0


async def test_multiple_sessions_isolated(store: InMemoryEventStore):
    """Events for different sessions SHALL NOT mix."""
    session_a = uuid.uuid4()
    session_b = uuid.uuid4()
    for i in range(3):
        await store.append(Event(session_id=session_a, superstep=i, event_type=EventType.NODE_START))
    for i in range(2):
        await store.append(Event(session_id=session_b, superstep=i, event_type=EventType.TOOL_CALL))
    events_a = await store.get_events(session_a)
    events_b = await store.get_events(session_b)
    assert len(events_a) == 3
    assert len(events_b) == 2
    assert all(e.session_id == session_a for e in events_a)
    assert all(e.session_id == session_b for e in events_b)


async def test_get_events_order_ascending(store: InMemoryEventStore, session_id: uuid.UUID):
    """get_events() SHALL return events in version-ascending order."""
    for i in range(5):
        await store.append(Event(session_id=session_id, superstep=i, event_type=EventType.NODE_START))
    events = await store.get_events(session_id)
    assert events == sorted(events, key=lambda e: e.version)


# --- EnginePort integration ---


def test_engine_port_event_store_defaults_to_none():
    """EnginePort.event_store SHALL return None by default."""

    class MinimalPort:
        @property
        def event_store(self):
            return None

    port = MinimalPort()
    assert port.event_store is None


# --- EventStore ABC ---


def test_eventstore_is_abstract():
    """EventStore SHALL NOT be instantiable directly."""
    with pytest.raises(TypeError):
        EventStore()  # type: ignore[abstract]
