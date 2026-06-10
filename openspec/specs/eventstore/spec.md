## Purpose
Define the EventStore abstraction for recording and replaying granular agent execution events, enabling observability, debugging, and state recovery across sessions.
## Requirements
### Requirement: Event dataclass captures granular execution state
The engine SHALL define an immutable `Event` dataclass in `engine/eventstore.py` with fields: `id` (UUID, auto-generated), `session_id` (UUID), `superstep` (int), `event_type` (EventType enum), `node_id` (str | None), `timestamp` (datetime, auto-generated), `payload` (dict), `trace_id` (str | None, default None).

#### Scenario: Create a node execution event with trace correlation
- **WHEN** an Event is created with `session_id`, `superstep=3`, `event_type=EventType.NODE_START`, `node_id="agent_1"`, `trace_id="abc123"`
- **THEN** it SHALL have auto-generated `id` (UUID), `timestamp` (UTC now), default `payload={}`, and `trace_id="abc123"`

#### Scenario: Create event without trace context
- **WHEN** an Event is created without specifying `trace_id`
- **THEN** `trace_id` SHALL default to `None`

#### Scenario: Event immutability
- **WHEN** an Event instance exists
- **THEN** attempting to set any field SHALL raise `FrozenInstanceError`

### Requirement: EventType enum defines standard event categories
The engine SHALL define a string enum `EventType` with values: `NODE_START`, `NODE_END`, `TOOL_CALL`, `TOOL_RESULT`, `CHANNEL_WRITE`, `LLM_REQUEST`, `LLM_RESPONSE`, `INTERRUPT`, `RESUME`, `ERROR`, `CUSTOM`.

#### Scenario: Use standard event type
- **WHEN** `EventType.TOOL_CALL` is referenced
- **THEN** it SHALL equal the string `"TOOL_CALL"`

#### Scenario: Custom event type
- **WHEN** an event is created with `event_type=EventType.CUSTOM` and `payload={"custom_type": "my_event"}`
- **THEN** the event SHALL be valid and storeable

### Requirement: EventStore ABC defines append-only event persistence
The engine SHALL define an `EventStore` ABC with abstract methods: `append`, `get_events`, `replay`, `get_version`.

#### Scenario: Append an event
- **WHEN** `append(event)` is called with a valid Event
- **THEN** it SHALL persist the event and return its UUID

#### Scenario: Query events for a session
- **WHEN** `get_events(session_id, from_version=5)` is called
- **THEN** it SHALL return all events for the session with version >= 5, ordered by version ascending

#### Scenario: Replay events as stream
- **WHEN** `replay(session_id, from_version=0)` is called
- **THEN** it SHALL return an `AsyncGenerator[Event, None]` yielding events in order

#### Scenario: Get current version
- **WHEN** `get_version(session_id)` is called
- **THEN** it SHALL return the highest version number for the session, or 0 if no events exist

### Requirement: InMemoryEventStore provides test implementation
An `InMemoryEventStore` SHALL implement EventStore using an in-memory dict mapping session_id to a list of events, suitable for unit tests.

#### Scenario: Append and retrieve
- **WHEN** 3 events are appended for session A, then `get_events(session_a)` is called
- **THEN** it SHALL return exactly 3 events in append order

#### Scenario: Version tracking
- **WHEN** 5 events are appended for a session
- **THEN** `get_version(session_id)` SHALL return 5

#### Scenario: Replay from version
- **WHEN** 10 events exist and `replay(session_id, from_version=7)` is called
- **THEN** it SHALL yield events with version 7, 8, 9, 10

#### Scenario: Empty session
- **WHEN** `get_events(session_id)` is called for a session with no events
- **THEN** it SHALL return an empty list

#### Scenario: Multiple sessions isolated
- **WHEN** events are appended for session A and session B
- **THEN** `get_events(session_a)` SHALL NOT include events from session B

### Requirement: PregelRuntime accepts optional EventStore
PregelRuntime SHALL accept an optional `event_store: EventStore | None = None` parameter in its constructor. When provided, the runtime SHALL record execution events at key lifecycle points.

#### Scenario: Default no event recording
- **WHEN** PregelRuntime is created without event_store
- **THEN** it SHALL execute without recording events (current behavior)

#### Scenario: With EventStore
- **WHEN** PregelRuntime is created with `event_store=InMemoryEventStore()`
- **THEN** it SHALL record NODE_START, NODE_END, CHANNEL_WRITE, and SUPERSTEP_END events during execution

#### Scenario: Session start event
- **WHEN** PregelRuntime.execute() starts with initial_input
- **THEN** it SHALL record a CUSTOM event with `payload.event_name="SESSION_START"`

#### Scenario: Resume event
- **WHEN** PregelRuntime.execute() starts with resume_value
- **THEN** it SHALL record a RESUME event with the interrupted node ID in payload

#### Scenario: Interrupt event
- **WHEN** a worker returns Command(interrupt=...)
- **THEN** it SHALL record an INTERRUPT event before saving checkpoint

#### Scenario: Error event
- **WHEN** a worker returns a result with error
- **THEN** it SHALL record an ERROR event before raising the error

### Requirement: Worker accepts optional EventStore
Worker ABC SHALL accept an optional `event_store: EventStore | None = None` parameter in its constructor. Worker.execute() SHALL accept an optional `execution_context: dict | None = None` parameter containing `session_id`, `superstep`, and `event_store`.

#### Scenario: Default no event recording
- **WHEN** Worker is created without event_store
- **THEN** it SHALL execute without recording events (current behavior)

#### Scenario: Execution context passed by PregelRuntime
- **WHEN** PregelRuntime dispatches a worker
- **THEN** it SHALL pass `execution_context={"session_id": UUID, "superstep": int, "event_store": EventStore}`

#### Scenario: LLMWorker records LLM events
- **WHEN** LLMWorker executes with event_store in execution_context
- **THEN** it SHALL record LLM_REQUEST before the LLM call and LLM_RESPONSE after

#### Scenario: ToolWorker records tool events
- **WHEN** ToolWorker executes with event_store in execution_context
- **THEN** it SHALL record TOOL_CALL before the tool invocation and TOOL_RESULT after

