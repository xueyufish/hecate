## Context

`engine/checkpoint.py` currently contains three classes:
1. `CheckpointStore` ABC — correct, belongs in engine/
2. `InMemoryCheckpointStore` — correct, test helper with zero external deps
3. `PostgresCheckpointStore` — **violation**: imports `sqlalchemy` and `hecate.models.checkpoint.CheckpointModel`

The engine layer rule (from AGENTS.md): "Zero external deps (no imports from services/, api/, models/); jsonschema is sole exception."

Meanwhile, `services/checkpoint_store.py` already has a `PostgresCheckpointStore` from P1, but with fewer features (no LRU eviction, no cache-miss DB fallback). It uses `session_factory: async_sessionmaker` instead of a raw `AsyncSession`, which is the correct production pattern (self-managed transactions).

No one imports the services version. The engine version is used only by `tests/test_engine/test_postgres_checkpoint.py`.

## Goals / Non-Goals

**Goals:**

1. Eliminate the engine → models layering violation
2. Produce a single, canonical `PostgresCheckpointStore` in services/ with the best features from both versions
3. Ensure all tests pass after migration

**Non-Goals:**

1. No changes to `CheckpointStore` ABC or `InMemoryCheckpointStore`
2. No changes to `PregelRuntime` (it depends on the ABC, not the concrete class)
3. No changes to `EnginePort.checkpoint_save/checkpoint_load` stubs (separate concern)
4. No changes to `engine/temporal/run_worker.py` (separate layering violation, different scope)

## Decisions

### D1: Keep `session_factory` constructor (services pattern)

**Choice**: `__init__(self, session_factory: async_sessionmaker[AsyncSession], cache_size: int = 128)`

**Rationale**: The services version's `session_factory` pattern is correct for production — it creates and commits its own sessions (self-contained transactions). The engine version's `db_session: AsyncSession` pattern requires the caller to manage transactions, which couples lifecycle management.

**Alternative considered**: Keep `db_session` for test convenience — rejected because it creates two constructor patterns and tests can easily adapt to `session_factory`.

### D2: Port LRU cache from engine version

**Choice**: Use `OrderedDict`-based LRU cache with configurable `cache_size` (default 128).

**Rationale**: The engine version's LRU eviction prevents memory leaks in long-running production processes. The services version's plain dict has no eviction — a session that's touched once stays in cache forever.

### D3: Port cache-miss DB fallback from engine version

**Choice**: When `load(session_id)` is called without `checkpoint_id` and the session is not in cache, query the database for the latest checkpoint (then cache the result).

**Rationale**: The P1 services version only checked `_cache.get()` and returned `None` on miss. The P2 engine version added the DB fallback, which is correct — a cache miss should not mean "no checkpoint exists."

### D4: Move tests to `test_services/`

**Choice**: Rename `tests/test_engine/test_postgres_checkpoint.py` → `tests/test_services/test_checkpoint_store.py`.

**Rationale**: `PostgresCheckpointStore` is a services-layer class. Its tests belong in `test_services/`. The test file will need constructor adaptation (create a `session_factory` from the conftest `db_session` fixture).

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| Test fixture adaptation — conftest provides `AsyncSession`, not `session_factory` | Create a helper fixture that wraps `db_session` into an `async_sessionmaker`; tests can also use `InMemoryCheckpointStore` from engine for pure engine tests |
| Services version uses lazy imports — slight overhead on first call | Negligible; lazy imports are the established pattern in services/ to avoid circular deps |
| `session_factory` creates separate sessions — no shared transaction with caller | This is correct for services layer; checkpoint persistence should be transactionally independent |
