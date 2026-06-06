## Why

`engine/checkpoint.py` contains a `PostgresCheckpointStore` that imports `sqlalchemy` and `hecate.models.checkpoint.CheckpointModel` at module level, violating the engine layer's "zero external deps (no imports from services/, api/, models/)" rule. This violation was introduced in P2 (commit `df40810`) when a second `PostgresCheckpointStore` was created inside `engine/` — ignoring the P1 implementation that already existed at `services/checkpoint_store.py` in the correct location.

## What Changes

- **Remove `PostgresCheckpointStore` from `engine/checkpoint.py`** — restore the file to its P1 state (ABC + InMemoryCheckpointStore only, zero external deps)
- **Upgrade `services/checkpoint_store.py`** — merge the engine version's improvements (LRU `OrderedDict` cache with configurable size, cache-miss DB fallback on `load()`, `_checkpoint_to_dict` helper) into the services version
- **Move tests** from `tests/test_engine/test_postgres_checkpoint.py` to `tests/test_services/test_checkpoint_store.py` and update imports to reference `services.checkpoint_store`
- **Adapt tests to `session_factory` constructor** — the services version uses `async_sessionmaker` (self-managed sessions + commits) instead of a raw `AsyncSession`

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `pregel-runtime`: CheckpointStore implementation location clarified — PregelRuntime takes `CheckpointStore` ABC from engine; concrete `PostgresCheckpointStore` provided by services layer
- `engine-ports`: No spec changes, but the `PostgresCheckpointStore` that was incorrectly colocated with the ABC is removed from the engine layer

## Impact

- **Removed code**: `PostgresCheckpointStore` class (~170 lines) from `engine/checkpoint.py`
- **Modified code**: `services/checkpoint_store.py` gains LRU cache and cache-miss DB fallback (~40 lines net addition)
- **Moved code**: Test file from `tests/test_engine/` to `tests/test_services/` with updated imports and constructor adaptation
- **Zero breaking changes**: `CheckpointStore` ABC and `InMemoryCheckpointStore` remain unchanged; `PregelRuntime` depends on the ABC only
- **No dependency changes**: Both `sqlalchemy` and `hecate.models.checkpoint` are already available to the services layer
