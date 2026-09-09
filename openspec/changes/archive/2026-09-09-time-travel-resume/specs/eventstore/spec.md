## MODIFIED Requirements

### Requirement: EventType enum defines standard event categories
The engine SHALL define a string enum `EventType` with values: `NODE_START`, `NODE_END`, `TOOL_CALL`, `TOOL_RESULT`, `CHANNEL_WRITE`, `CHANNEL_WRITE_REJECTED`, `LLM_REQUEST`, `LLM_RESPONSE`, `INTERRUPT`, `RESUME`, `ERROR`, `PII_DETECTED`, `CUSTOM`, `STEP_END`, `EVICTION`, `SUBGRAPH_START`, `SUBGRAPH_END`, `FORK`.

#### Scenario: Use standard event type
- **WHEN** `EventType.TOOL_CALL` is referenced
- **THEN** it SHALL equal the string `"TOOL_CALL"`

#### Scenario: Custom event type
- **WHEN** an event is created with `event_type=EventType.CUSTOM` and `payload={"custom_type": "my_event"}`
- **THEN** the event SHALL be valid and storeable

#### Scenario: New boundary and semantic event types
- **WHEN** `EventType.STEP_END`, `EventType.EVICTION`, `EventType.SUBGRAPH_START`, `EventType.SUBGRAPH_END`, or `EventType.CHANNEL_WRITE_REJECTED` is referenced
- **THEN** each SHALL equal its string value and be storeable via the existing append contract

#### Scenario: FORK event type
- **WHEN** `EventType.FORK` is referenced
- **THEN** it SHALL equal the string `"FORK"` and be storeable via the existing append contract

#### Scenario: Unknown historical event types fall back on read
- **WHEN** `_row_to_event` reads an event_type string not present in the enum
- **THEN** it SHALL fall back to `EventType.CUSTOM`（既有行为保持）
