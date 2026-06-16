## 1. PregelRuntime EventStore Integration

- [x] 1.1 Add imports for `EventStore`, `EventType`, `Event` from `hecate.engine.eventstore` in `src/hecate/engine/pregel.py`
- [x] 1.2 Add `event_store: EventStore | None = None` parameter to `PregelRuntime.__init__()` — store as `self._event_store = event_store`
- [x] 1.3 Add private `_emit()` helper method to PregelRuntime that checks `if self._event_store` before appending
- [x] 1.4 Record RESUME event after `_restore_from_checkpoint()` when resume_value is provided
- [x] 1.5 Record CUSTOM SESSION_START event after initial_input write when starting fresh
- [x] 1.6 Record NODE_START event before dispatching each node
- [x] 1.7 Record NODE_END event after worker returns result
- [x] 1.8 Record ERROR event before raising worker error
- [x] 1.9 Record INTERRUPT event when interrupt detected
- [x] 1.10 Record CHANNEL_WRITE event after `_apply_writes()`
- [x] 1.11 Record CUSTOM SUPERSTEP_END event after checkpoint save

## 2. Worker ABC Changes

- [x] 2.1 Add `event_store: EventStore | None = None` parameter to `Worker.__init__()` in `src/hecate/engine/worker.py`
- [x] 2.2 Add `execution_context: dict | None = None` parameter to `Worker.execute()` abstract method
- [x] 2.3 Add `execution_context: dict | None = None` parameter to `WorkerPool.dispatch()` in `src/hecate/engine/worker.py`
- [x] 2.4 Update `DirectWorkerPool.dispatch()` to pass execution_context to worker.execute()
- [x] 2.5 Update PregelRuntime to pass execution_context with session_id, superstep, event_store when dispatching workers

## 3. Production Worker Updates

- [x] 3.1 Update `LLMWorker.__init__()` to accept and store `event_store` parameter
- [x] 3.2 Update `LLMWorker.execute()` to accept `execution_context` and record LLM_REQUEST/LLM_RESPONSE events
- [x] 3.3 Update `ToolWorker.__init__()` to accept and store `event_store` parameter
- [x] 3.4 Update `ToolWorker.execute()` to accept `execution_context` and record TOOL_CALL/TOOL_RESULT events
- [x] 3.5 Update `AgentWorker.__init__()` to accept `event_store` parameter
- [x] 3.6 Update `ConditionWorker`, `KnowledgeWorker`, `VariableSetWorker`, `SuggestionWorker` constructors to accept `event_store`

## 4. Test Stub Updates

- [x] 4.1 Update all 17 test Worker classes in `tests/test_engine/` to accept `event_store` parameter in constructor

## 5. Tests

- [x] 5.1 Add test `test_pregel_records_lifecycle_events` in `tests/test_engine/test_pregel.py` — verify NODE_START, NODE_END, CHANNEL_WRITE, SUPERSTEP_END events recorded
- [x] 5.2 Add test `test_pregel_records_resume_event` — verify RESUME event on resume_value
- [x] 5.3 Add test `test_pregel_records_interrupt_event` — verify INTERRUPT event when worker returns interrupt command
- [x] 5.4 Add test `test_pregel_records_error_event` — verify ERROR event when worker returns error
- [x] 5.5 Add test `test_pregel_no_recording_without_event_store` — verify no events recorded when event_store is None

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/ tests/`
- [x] 6.2 Run `ruff format --check src/ tests/`
- [x] 6.3 Run `mypy src/`
- [x] 6.4 Run `python -m pytest tests/ -q` — no regressions
