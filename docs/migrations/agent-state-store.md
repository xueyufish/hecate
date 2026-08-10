# Migrating from AgentStateStore to SessionStateStore

> **Deprecated in:** 13.4a-6 (Aug 2026)
> **Hard removal planned for:** 13.4a-7 (≥ next minor version)
> **Replacement:** `hecate.engine.session_state.SessionStateStore`

---

## Why deprecated

`AgentStateStore` (in `src/hecate/services/state/store.py`) was the original
per-session state persistence abstraction. It used a two-part key
`(agent_id, session_id)` and had a single in-memory implementation
(`InMemoryStateStore`).

`SessionStateStore` (in `src/hecate/engine/session_state.py`) is the
production-ready replacement delivered across the 13.4a change series
(1/5 through 5/5, PR #46). It provides:

- **Multi-tenant keying**: `(org_id, user_id, session_id)` — enforces tenant
  isolation at the type level.
- **Multiple backends**: `InMemorySessionStateStore`, `RedisSessionStateStore`,
  `PostgresSessionStateStore`, `TieredSessionStateStore` — selected via
  `SESSION_STATE_STORE_BACKEND` env var.
- **Distributed lock semantics**: `acquire_session_lock()` async context
  manager with jitter retry, enabling multi-replica horizontal scaling (13.4).
- **Unified checkpoint**: `SessionState` aggregates channel state, agent state,
  event position, and metadata in a single frozen snapshot.

---

## Migration mapping

| `AgentStateStore` method | `SessionStateStore` equivalent | Notes |
|---|---|---|
| `save(agent_id, session_id, state)` | `save(org_id, user_id, session_id, state)` | Key changes from 2-tuple to 3-tuple; `state` changes from `AgentState` to `SessionState` |
| `load(agent_id, session_id)` | `load(org_id, user_id, session_id)` | Returns `SessionState` (with `.agent_state` dict) instead of `AgentState` |
| `list_sessions(agent_id)` | `list_recent(org_id, user_id, limit=10)` | Returns `SessionSummary` with richer fields (org_id, user_id, superstep) |
| `delete(agent_id, session_id)` | _Not available_ | `SessionStateStore` relies on TTL (`SESSION_STATE_TTL_DAYS`). For explicit deletion: `redis.delete(key)` (Redis) or `DELETE FROM session_states WHERE ...` (Postgres). |

---

## Key migration

The key changes from `(agent_id, session_id)` to `(org_id, user_id, session_id)`.

```python
# OLD: AgentStateStore
agent_id = uuid.uuid4()
session_id = uuid.uuid4()
await store.save(agent_id, session_id, agent_state)

# NEW: SessionStateStore
org_id = request.state.org_id      # from request context
user_id = request.state.user_id    # from request context
session_id = uuid.uuid4()
await store.save(org_id, user_id, session_id, session_state)
```

To obtain `org_id` and `user_id`, use the `RequestContext` from
`hecate.core.request_context` or extract from the FastAPI request's
authentication context.

---

## Code example

### Before (deprecated)

```python
from hecate.services.state.state import AgentState
from hecate.services.state.store import InMemoryStateStore

store = InMemoryStateStore()
agent_state = AgentState(
    session_id=session_id,
    agent_id=agent_id,
    summary="...",
    context=[{"role": "user", "content": "Hello"}],
)
await store.save(agent_id, session_id, agent_state)

# Load later
loaded = await store.load(agent_id, session_id)
```

### After (recommended)

```python
from hecate.engine.session_state import SessionState, InMemorySessionStateStore

store = InMemorySessionStateStore()
session_state = SessionState(
    agent_state=agent_state.model_dump(mode="json"),
    channel_state={},
    event_position=0,
    metadata={"superstep": 1},
)
await store.save(org_id, user_id, session_id, session_state)

# Load later
loaded = await store.load(org_id, user_id, session_id)
if loaded:
    from hecate.services.state.state import AgentState
    agent_state = AgentState.model_validate(loaded.agent_state)
```

---

## Configuration

Select the backend via the `SESSION_STATE_STORE_BACKEND` environment variable:

```bash
# Single-process (default, backward-compatible)
SESSION_STATE_STORE_BACKEND=memory

# Redis only (hot-path cache)
SESSION_STATE_STORE_BACKEND=redis
SESSION_STATE_REDIS_URL=redis://localhost:6379/0

# PostgreSQL only (durable)
SESSION_STATE_STORE_BACKEND=postgres

# Tiered (Redis cache + PG durable — recommended for production)
SESSION_STATE_STORE_BACKEND=tiered
SESSION_STATE_REDIS_URL=redis://localhost:6379/0
```

Additional settings:

| Setting | Default | Description |
|---|---|---|
| `SESSION_STATE_TTL_DAYS` | `7` | Idle TTL for both Redis `EX` and PG query filter |
| `SESSION_STATE_KEY_PREFIX` | `hecate:state:` | Redis key prefix for multi-app isolation |
| `SESSION_STATE_PG_TABLE` | `session_states` | PostgreSQL table name |

---

## Support window

- **Deprecated in:** 13.4a-6 (this release)
- **Hard removal in:** 13.4a-7 (planned for ≥ next minor version)

`AgentStateStore`, `InMemoryStateStore`, and the `WorkflowExecutionService.state_store`
parameter will continue to work (with `DeprecationWarning`) until 13.4a-7.
After 13.4a-7, they will be removed from the codebase.

To see deprecation warnings:

```bash
python -W default::DeprecationWarning
# or in pytest:
python -m pytest -W always::DeprecationWarning
```

---

## References

- [distributed-session-state-store spec](../../openspec/specs/distributed-session-state-store/spec.md)
- [ADR-020: Async Execution & Distributed State](../design/adr/020-async-execution-distributed-state.md)
- [OpenSpec change: deprecate-agent-state-store](../../openspec/changes/deprecate-agent-state-store/)

> **Hard removal scheduled for ≥ next minor version** (13.4a-7).
