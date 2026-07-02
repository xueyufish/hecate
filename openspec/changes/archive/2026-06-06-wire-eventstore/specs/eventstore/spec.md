## MODIFIED Requirements

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
