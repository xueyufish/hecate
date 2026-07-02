## 1. Event Types

- [x] 1.1 Create `src/hecate/engine/eventstore.py` with `EventType` string enum (NODE_START, NODE_END, TOOL_CALL, TOOL_RESULT, CHANNEL_WRITE, LLM_REQUEST, LLM_RESPONSE, INTERRUPT, RESUME, ERROR, CUSTOM)
- [x] 1.2 Define frozen `Event` dataclass with fields: id (UUID, default_factory), session_id (UUID), superstep (int), event_type (EventType), node_id (str | None), timestamp (datetime, default_factory utcnow), payload (dict, default_factory), version (int = 0)
- [x] 1.3 Verify `from __future__ import annotations` at top of file and all public symbols have docstrings

## 2. EventStore ABC

- [x] 2.1 Define `EventStore(ABC)` in `eventstore.py` with abstract methods: `append(event: Event) -> UUID`, `get_events(session_id, from_version=0) -> list[Event]`, `replay(session_id, from_version=0) -> AsyncGenerator[Event, None]`, `get_version(session_id) -> int`
- [x] 2.2 Add full docstrings to EventStore ABC and each abstract method (English, matching existing EnginePort/CheckpointStore style)

## 3. InMemoryEventStore

- [x] 3.1 Implement `InMemoryEventStore(EventStore)` using `dict[UUID, list[Event]]` internal storage
- [x] 3.2 `append` assigns sequential version numbers per session (starting from 1), appends to list, returns event.id
- [x] 3.3 `get_events` filters by session_id and from_version, returns matching events as list
- [x] 3.4 `replay` yields events via async generator (async for over filtered list)
- [x] 3.5 `get_version` returns highest version for session or 0 if no events exist
- [x] 3.6 Verify InMemoryEventStore handles edge cases: empty session, non-existent session, from_version beyond range

## 4. EnginePort Integration

- [x] 4.1 Add `event_store: EventStore | None = None` property to `EnginePort` in `ports.py` (optional, default None)
- [x] 4.2 Verify `ports.py` imports `EventStore` and `Event` from `hecate.engine.eventstore` using `TYPE_CHECKING` guard (avoid circular imports)

## 5. Tests

- [x] 5.1 Create `tests/test_engine/test_eventstore.py`
- [x] 5.2 Test EventType enum values are correct strings
- [x] 5.3 Test Event creation with auto-generated id and timestamp
- [x] 5.4 Test Event immutability (frozen dataclass raises on field assignment)
- [x] 5.5 Test InMemoryEventStore.append returns UUID and stores event
- [x] 5.6 Test InMemoryEventStore.get_events with from_version filtering
- [x] 5.7 Test InMemoryEventStore.replay yields events in order as AsyncGenerator
- [x] 5.8 Test InMemoryEventStore.get_version returns correct version number
- [x] 5.9 Test InMemoryEventStore with multiple sessions (isolation)
- [x] 5.10 Test InMemoryEventStore with empty/non-existent session returns empty list and version 0

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/engine/eventstore.py src/hecate/engine/ports.py tests/test_engine/test_eventstore.py`
- [x] 6.2 Run `ruff format --check src/hecate/engine/eventstore.py src/hecate/engine/ports.py tests/test_engine/test_eventstore.py`
- [x] 6.3 Run `mypy src/hecate/engine/eventstore.py src/hecate/engine/ports.py`
- [x] 6.4 Run `python -m pytest tests/test_engine/test_eventstore.py -v`
- [x] 6.5 Run full test suite `python -m pytest tests/ -q` to verify no regressions
