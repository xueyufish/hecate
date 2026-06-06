## Why

EventStore ABC and InMemoryEventStore exist in `engine/eventstore.py` but are never invoked during graph execution. PregelRuntime has no way to emit execution events, and Workers have no way to record internal details (LLM calls, tool invocations). This blocks audit logging, time-travel debugging, and the observability path (Sprint 4 features 8.1 Full-Chain Tracing, 8.7 Audit Logs).

## What Changes

**PregelRuntime layer (Plan A):**
- Constructor accepts optional `event_store: EventStore | None = None`
- Records lifecycle events during execution: NODE_START, NODE_END, INTERRUPT, RESUME, ERROR, CHANNEL_WRITE, SUPERSTEP_END
- Uses a private `_emit()` helper to reduce boilerplate

**Worker layer (Plan B):**
- Worker ABC gains optional `event_store` constructor parameter
- Worker.execute() gains optional `execution_context` dict with `session_id`, `superstep`, `event_store`
- Production workers (LLMWorker, ToolWorker, etc.) emit LLM_REQUEST, LLM_RESPONSE, TOOL_CALL, TOOL_RESULT events
- Test stubs remain unchanged (ignore execution_context)

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `eventstore`: EventStore SHALL be wired into PregelRuntime and Worker for event recording during graph execution

## Impact

- `src/hecate/engine/pregel.py` — constructor + execute() event recording (~20 LOC)
- `src/hecate/engine/worker.py` — ABC gains optional event_store parameter
- `src/hecate/engine/workers/*.py` — 8 production workers accept event_store
- `tests/test_engine/test_pregel.py` — integration test for event recording
- `tests/test_engine/test_eventstore.py` — integration test with PregelRuntime
- Backward-compatible: all existing code continues to work with no event_store
