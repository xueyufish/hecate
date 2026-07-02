## MODIFIED Requirements

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
The engine SHALL define an `EventStore` ABC with abstract methods: `append`, `get_events`, `replay`, `get_version`. The `append` method SHALL accept an `Event` that may contain a `trace_id` field for correlating engine events with application-level traces.

#### Scenario: Append an event
- **WHEN** `append(event)` is called with a valid Event
- **THEN** it SHALL persist the event with an auto-assigned version number and return the event's UUID

#### Scenario: Append an event with trace_id
- **WHEN** `append(event)` is called with an Event that has `trace_id="trace_abc"`
- **THEN** the stored event SHALL retain `trace_id="trace_abc"` for later correlation queries
