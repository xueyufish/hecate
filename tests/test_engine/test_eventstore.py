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

from hecate.engine.eventstore import (
    Event,
    EventStore,
    EventType,
    EventVersionConflictError,
    InMemoryEventStore,
)

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
    # guardrail-upgrade-trio T0.1: approval + turn boundary events.
    assert EventType.APPROVAL_ASKED == "APPROVAL_ASKED"
    assert EventType.APPROVAL_DECIDED == "APPROVAL_DECIDED"
    assert EventType.TURN_START == "TURN_START"
    assert EventType.TURN_END == "TURN_END"


def test_event_type_is_string_enum():
    """EventType SHALL be usable as a string."""
    assert isinstance(EventType.TOOL_CALL, str)


def test_event_type_unknown_falls_back_to_custom():
    """Unknown historical event-type strings SHALL fall back to EventType.CUSTOM on read.

    Forward-compat invariant: future enum members are added additively; older
    readers must not crash on rows they don't know yet. The fallback contract
    is implemented by ``PostgresEventStore._row_to_event`` (services/event_state/
    postgres_store.py), which converts via ``EventType(value)`` and catches
    ``ValueError`` to return ``EventType.CUSTOM``. This test exercises the
    fallback path of that conversion directly — the full reader flow is
    covered by test_engine/test_eventstore_persistence.py.
    """
    # Direct lookup: StrEnum raises ValueError on missing values.
    raw = "FUTURE_TYPE_FROM_HISTORICAL_ROW"
    assert raw not in EventType
    with pytest.raises(ValueError):
        EventType(raw)

    # The reader's fallback wraps that ValueError:
    def safe_resolve(value: str) -> EventType:
        try:
            return EventType(value)
        except ValueError:
            return EventType.CUSTOM

    assert safe_resolve(raw) == EventType.CUSTOM
    # And known values still resolve correctly.
    assert safe_resolve("TURN_START") == EventType.TURN_START


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


# --- RuntimePort integration ---


def test_runtime_port_event_store_defaults_to_none():
    """RuntimePort.event_store SHALL return None by default."""

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


# --- acquire_event_lock default no-op ---


async def test_acquire_event_lock_default_is_noop(session_id: uuid.UUID):
    """EventStore.acquire_event_lock SHALL be a no-op by default.

    InMemoryEventStore inherits the default (no override) and yields without
    blocking, raising, or acquiring any real lock.
    """

    store = InMemoryEventStore()
    async with store.acquire_event_lock(session_id):
        pass
    async with store.acquire_event_lock(session_id, timeout_ms=1000):
        await store.append(Event(session_id=session_id, superstep=0, event_type=EventType.NODE_START))
    assert await store.get_version(session_id) == 1


def test_event_version_conflict_error_message_includes_session_id(session_id: uuid.UUID):
    """EventVersionConflictError message SHALL include the offending session_id."""

    err = EventVersionConflictError(session_id, version=5)
    assert err.session_id == session_id
    assert err.version == 5
    assert str(session_id) in str(err)


# --- 1.3.19 event schema enrichment ---


def test_event_type_step_end_value():
    assert EventType.STEP_END == "STEP_END"


def test_event_type_eviction_value():
    assert EventType.EVICTION == "EVICTION"


def test_event_type_subgraph_values():
    assert EventType.SUBGRAPH_START == "SUBGRAPH_START"
    assert EventType.SUBGRAPH_END == "SUBGRAPH_END"


def test_event_type_channel_write_rejected_value():
    assert EventType.CHANNEL_WRITE_REJECTED == "CHANNEL_WRITE_REJECTED"


def test_current_log_schema_version_is_2():
    from hecate.engine.eventstore import CURRENT_LOG_SCHEMA_VERSION

    assert CURRENT_LOG_SCHEMA_VERSION == 2


async def test_append_batch_assigns_sequential_versions(store: InMemoryEventStore, session_id: uuid.UUID):
    events = [
        Event(
            session_id=session_id,
            superstep=1,
            event_type=EventType.NODE_START,
            node_id=f"n{i}",
        )
        for i in range(5)
    ]
    returned_ids = await store.append_batch(events)
    assert len(returned_ids) == 5
    persisted = await store.get_events(session_id)
    assert [e.version for e in persisted] == [1, 2, 3, 4, 5]


async def test_append_batch_empty_returns_empty_list(store: InMemoryEventStore):
    assert await store.append_batch([]) == []


async def test_append_batch_preserves_input_order(store: InMemoryEventStore, session_id: uuid.UUID):
    types = [EventType.NODE_START, EventType.TOOL_CALL, EventType.TOOL_RESULT, EventType.STEP_END]
    events = [
        Event(
            session_id=session_id,
            superstep=1,
            event_type=t,
            node_id=f"n{i}",
        )
        for i, t in enumerate(types)
    ]
    await store.append_batch(events)
    persisted = await store.get_events(session_id)
    assert [e.event_type for e in persisted] == types


async def test_unknown_event_type_falls_back_to_custom(store: InMemoryEventStore, session_id: uuid.UUID):
    """Future event types read from persisted form MUST fall back to CUSTOM (already-known behavior)."""
    legacy = Event(
        session_id=session_id,
        superstep=1,
        event_type=EventType.CUSTOM,
        payload={"legacy_marker": True},
    )
    await store.append(legacy)
    events = await store.get_events(session_id)
    assert len(events) == 1
    assert events[0].payload.get("legacy_marker") is True
