## MODIFIED Requirements

### Requirement: Worker accepts optional EventStore
Worker ABC SHALL accept an optional `event_store: EventStore | None = None` parameter in its constructor. Worker.execute() SHALL accept an optional `execution_context: dict | None = None` parameter containing `session_id`, `superstep`, `event_store`, and `trace_id`. All events appended by Workers (`LLM_REQUEST`, `LLM_RESPONSE`, `TOOL_CALL`, `TOOL_RESULT`) SHALL carry the `trace_id` from `execution_context` when present, so that every engine-emitted event is attributable to exactly one execution (trace) within the session log.

#### Scenario: Default no event recording
- **WHEN** Worker is created without event_store
- **THEN** it SHALL execute without recording events (current behavior)

#### Scenario: Execution context passed by PregelRuntime
- **WHEN** PregelRuntime dispatches a worker
- **THEN** it SHALL pass `execution_context={"session_id": UUID, "superstep": int, "event_store": EventStore, "trace_id": str | None}`

#### Scenario: LLMWorker records LLM events
- **WHEN** LLMWorker executes with event_store in execution_context and trace_id present
- **THEN** it SHALL record LLM_REQUEST before the LLM call and LLM_RESPONSE after, both carrying `trace_id`

#### Scenario: ToolWorker records tool events
- **WHEN** ToolWorker executes with event_store in execution_context and trace_id present
- **THEN** it SHALL record TOOL_CALL before the tool invocation and TOOL_RESULT after, both carrying `trace_id`

#### Scenario: Absent trace_id does not break recording
- **WHEN** execution_context carries no trace_id (None)
- **THEN** Workers SHALL still record events with `trace_id=None`; event recording SHALL never fail due to correlation fields

## ADDED Requirements

### Requirement: Execution identity per invoke

`PregelRuntime.invoke()` SHALL establish an execution identity (trace_id) for each invocation and propagate it to all events emitted during that invocation (runtime-emitted and worker-emitted). When the OTel span context provides a valid (non-zero) trace id, the system SHALL use it; otherwise the runtime SHALL generate an explicit unique id so that events of different invocations never share a degenerate identity. The identity SHALL partition the session log: every event belongs to exactly one invocation, enabling per-execution timeline segmentation without new schema.

#### Scenario: OTel-configured environment
- **WHEN** an invoke runs with a valid OTel tracer configured
- **THEN** all events of that invoke SHALL carry the OTel trace_id (32-hex, non-zero)

#### Scenario: OTel not configured (noop tracer)
- **WHEN** an invoke runs without a configured OTel SDK
- **THEN** the runtime SHALL assign a generated unique trace_id to the invocation's events; two consecutive invokes on the same session SHALL NOT share the same identity

#### Scenario: Resume creates a new execution identity
- **WHEN** a session is interrupted and then resumed (new invoke)
- **THEN** events of the resume invoke SHALL carry a different trace_id from the interrupted invoke
