## 1. AgentState Data Model

- [x] 1.1 Create `src/hecate/services/state/__init__.py` — export AgentState, AgentStateStore, InMemoryStateStore
- [x] 1.2 Create `src/hecate/services/state/state.py` — AgentState Pydantic model with fields: session_id (UUID), agent_id (UUID), summary (str), context (list[dict]), permission_context (dict), tool_context (dict), task_context (dict), environment_root (str | None), metadata (dict)

## 2. AgentStateStore

- [x] 2.1 Create `src/hecate/services/state/store.py` — AgentStateStore ABC with abstract methods: save(agent_id, session_id, state), load(agent_id, session_id), delete(agent_id, session_id), list_sessions(agent_id)
- [x] 2.2 Implement InMemoryStateStore in store.py — dict-based storage, asyncio.Lock per session key for concurrent safety

## 3. WorkflowExecutionService Integration

- [x] 3.1 Add `state_store: AgentStateStore | None = None` parameter to WorkflowExecutionService.__init__
- [x] 3.2 Add state load at execute() entry — load existing state or create fresh AgentState, inject into execution_context["_agent_state"]
- [x] 3.3 Add state save at execute() exit — save AgentState after both _non_stream_execute and _stream_execute complete
- [x] 3.4 Populate AgentState.environment_root from EnvironmentManager when available

## 4. Tests

- [x] 4.1 Test AgentState model — creation with defaults, creation with explicit values, model_dump round-trip, model_validate from dict
- [x] 4.2 Test InMemoryStateStore — save/load, load non-existent returns None, delete, list_sessions, different sessions independent
- [x] 4.3 Test InMemoryStateStore concurrent safety — two coroutines save same key, no corruption
- [x] 4.4 Test WorkflowExecutionService integration — state loaded at entry, saved at exit, persists across calls, environment_root populated

## 5. Verification

- [x] 5.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 5.2 Run `mypy src/` — 0 errors
- [x] 5.3 Run `python -m pytest tests/test_services/test_state/ -q` — all pass
