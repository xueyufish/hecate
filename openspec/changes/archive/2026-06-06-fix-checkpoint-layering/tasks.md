## 1. Upgrade services/checkpoint_store.py

- [x] 1.1 Add `OrderedDict` import and change `_cache` from plain `dict` to `OrderedDict` with configurable `cache_size` parameter (default 128)
- [x] 1.2 Add `_update_cache()` LRU eviction method — move_to_end on hit, popitem(last=False) on overflow
- [x] 1.3 Update `save()` to call `_update_cache()` instead of direct dict assignment
- [x] 1.4 Update `load()` cache-miss path: when `checkpoint_id is None` and cache misses, query DB for latest checkpoint and cache the result (currently returns None on cache miss)
- [x] 1.5 Add `_checkpoint_to_dict()` static method for consistent model-to-dict conversion
- [x] 1.6 Replace inline dict construction in `load()` and `list_checkpoints()` with `_checkpoint_to_dict()`
- [x] 1.7 Add `logging` import and debug-level log messages for save, cache hit, and cache miss events
- [x] 1.8 Add full docstrings to class, `__init__`, and all public methods (match engine version quality)

## 2. Clean engine/checkpoint.py

- [x] 2.1 Delete the entire `PostgresCheckpointStore` class (lines 137-301)
- [x] 2.2 Remove `sqlalchemy` imports (`select`, `AsyncSession`)
- [x] 2.3 Remove `from hecate.models.checkpoint import CheckpointModel` import
- [x] 2.4 Remove `from collections import OrderedDict` import (no longer needed)
- [x] 2.5 Update module docstring to remove `PostgresCheckpointStore` reference
- [x] 2.6 Verify `engine/checkpoint.py` has zero imports from `models/`, `services/`, `sqlalchemy`

## 3. Migrate tests

- [x] 3.1 Create `tests/test_services/test_checkpoint_store.py`
- [x] 3.2 Add a `session_factory` fixture that wraps conftest's `db_session` into an `async_sessionmaker`
- [x] 3.3 Copy all test cases from `tests/test_engine/test_postgres_checkpoint.py`, updating imports to `from hecate.services.checkpoint_store import PostgresCheckpointStore`
- [x] 3.4 Update all test instantiations from `PostgresCheckpointStore(db_session)` to `PostgresCheckpointStore(session_factory)`
- [x] 3.5 Update `test_cache_eviction` to reference `_cache` as `OrderedDict` (assert eviction still works)
- [x] 3.6 Delete `tests/test_engine/test_postgres_checkpoint.py`

## 4. Verification

- [x] 4.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 4.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 4.3 Run `mypy src/` — zero errors
- [x] 4.4 Run `python -m pytest tests/ -q` — all tests pass
- [x] 4.5 Verify no file in `src/hecate/engine/` imports from `models/` (grep check)
