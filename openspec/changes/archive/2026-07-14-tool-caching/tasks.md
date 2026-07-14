## 1. Configuration

- [x] 1.1 Add tool cache settings to `src/hecate/core/config.py`: `TOOL_CACHE_ENABLED: bool = True`, `TOOL_CACHE_DEFAULT_TTL: int = 300`, `TOOL_CACHE_MAX_ENTRIES: int = 10000`, `TOOL_CACHE_SESSION_SCOPED: bool = True`

## 2. ToolModel Fields

- [x] 2.1 Add fields to `src/hecate/models/tool.py`: `cacheable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)`, `cache_ttl: Mapped[int | None] = mapped_column(Integer, nullable=True)`. Update CreateSchema and ReadSchema.
- [x] 2.2 Create Alembic migration for new columns.

## 3. ToolCache Implementation

- [x] 3.1 Create `src/hecate/services/tool/cache.py` — `CacheEntry` dataclass (result, created_at, ttl, last_accessed), `ToolCache` class with: `get(key) -> CacheEntry | None` (updates last_accessed), `set(key, result, ttl)`, `invalidate(tool_name)`, `invalidate_all()`, `stats() -> dict` (hits, misses, entries, hit_rate), `_canonical_json(args, ignored_args) -> str`, `_make_key(tool_name, args, session_id, ignored_args) -> str`, `_sweep_expired()`, LRU eviction at max_entries using `collections.OrderedDict`.
- [x] 3.2 Implement `is_cacheable(tool_meta) -> bool` priority chain: P1 explicit cacheable flag, P2 side-effect name prefix, P3 dangerous builtin set, P4 risk_level + sandbox_enabled default heuristic.

## 4. ToolRegistry Integration

- [x] 4.1 Update `src/hecate/services/tool/registry.py` — `ToolRegistry.__init__()` gains optional `cache: ToolCache | None`. In `execute()`: before execution, check `is_cacheable()` → generate key → `cache.get(key)` → return on hit. After execution, `cache.set(key, result, ttl)`.

## 5. REST API

- [x] 5.1 Create `src/hecate/api/management/tool_cache.py` — router prefix `/api/tools/cache`: `GET /stats` (hit/miss/entries/hit_rate), `DELETE /` (clear all, optional `?tool_name=` filter).
- [x] 5.2 Register `tool_cache_router` in `src/hecate/main.py`.

## 6. Tests

- [x] 6.1 Test `ToolCache` — set/get, TTL expiry, LRU eviction, canonical key (dict key ordering), ignored_args stripping, invalidate by tool name, invalidate all, stats accuracy.
- [x] 6.2 Test `is_cacheable()` priority chain — explicit True/False overrides, side-effect prefix skip, dangerous builtin skip, risk_level heuristic (LOW cache, HIGH skip), sandbox_enabled skip.
- [x] 6.3 Test ToolRegistry integration — cache miss executes, cache hit returns cached, non-cacheable tool bypasses cache, session scoping (different sessions don't share).
- [x] 6.4 Test REST API — GET stats, DELETE clear all, DELETE clear per-tool.

## 7. Verification

- [x] 7.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 7.2 Run `mypy src/` — 0 errors
- [x] 7.3 Run `python -m pytest tests/test_services/test_tool_cache.py -q` — all pass
