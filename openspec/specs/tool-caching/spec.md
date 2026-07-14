## ADDED Requirements

### Requirement: Tool result caching
The system SHALL cache tool execution results in memory with configurable TTL. On a cache hit, the cached result is returned without executing the tool. On a cache miss, the tool executes normally and the result is stored.

#### Scenario: Cache miss executes and stores
- **WHEN** a cacheable tool is called and no cached entry exists
- **THEN** the tool executes, the result is stored in cache with TTL, and the result is returned

#### Scenario: Cache hit returns cached result
- **WHEN** a cacheable tool is called with identical arguments and a valid (non-expired) cached entry exists
- **THEN** the cached result is returned without executing the tool

#### Scenario: Cache entry expired
- **WHEN** a cached entry's TTL has elapsed
- **THEN** the entry is evicted and the next call executes the tool fresh

### Requirement: Cacheability priority chain
The system SHALL determine whether a tool call is cacheable using a 5-priority chain evaluated in order. The first matching priority determines the outcome.

#### Scenario: Explicit cacheable flag overrides everything
- **WHEN** a tool has `cacheable=True` set explicitly
- **THEN** the tool is cached regardless of name, source, or risk level

#### Scenario: Explicit non-cacheable flag
- **WHEN** a tool has `cacheable=False` set explicitly
- **THEN** the tool is never cached

#### Scenario: Side-effect name prefix skips caching
- **WHEN** a tool name starts with `write_`, `create_`, `delete_`, `send_`, `update_`, or `remove_` and no explicit `cacheable` flag is set
- **THEN** the tool is not cached

#### Scenario: Dangerous builtin tools skip caching
- **WHEN** a tool is a builtin with name in {execute_code, bash, write_file, edit_file} and no explicit `cacheable` flag is set
- **THEN** the tool is not cached

#### Scenario: Default heuristic caches safe tools
- **WHEN** a tool has no explicit `cacheable` flag, no side-effect prefix, is not a dangerous builtin
- **AND** the tool's `risk_level` is LOW or MEDIUM and `sandbox_enabled` is False
- **THEN** the tool is cached

#### Scenario: High-risk tools not cached by default
- **WHEN** a tool has `risk_level=HIGH` or `risk_level=CRITICAL` and no explicit `cacheable` flag
- **THEN** the tool is not cached by default

### Requirement: Per-tool cache TTL configuration
The system SHALL support per-tool cache TTL via the `cache_ttl` field on `ToolModel`. When set, it overrides the global default TTL. When `None`, the global `TOOL_CACHE_DEFAULT_TTL` is used.

#### Scenario: Per-tool TTL overrides global
- **WHEN** a tool has `cache_ttl=60` and the global default is 300
- **THEN** the cached entry expires after 60 seconds

#### Scenario: Global TTL used as fallback
- **WHEN** a tool has `cache_ttl=None`
- **THEN** the global `TOOL_CACHE_DEFAULT_TTL` (default 300s) is used

### Requirement: Session-scoped cache entries
The system SHALL scope cache entries by `session_id` by default. The same tool with the same arguments in different sessions uses separate cache entries.

#### Scenario: Same tool same session hits cache
- **WHEN** tool `web_search` is called twice with `query="weather"` in the same session
- **THEN** the second call returns the cached result from the first call

#### Scenario: Same tool different sessions miss cache
- **WHEN** tool `web_search` is called with `query="weather"` in session A, then again in session B
- **THEN** session B does not see session A's cached result; the tool executes fresh

### Requirement: Canonical cache key generation
The system SHALL generate deterministic cache keys using `sha256(tool_name + canonical_json(args))`. Canonical JSON sorts dict keys recursively and strips `ignored_args` before serialization.

#### Scenario: Different key order same cache key
- **WHEN** tool A is called with `{"b": 2, "a": 1}` and then with `{"a": 1, "b": 2}`
- **THEN** both calls produce the same cache key

#### Scenario: Ignored args stripped from key
- **WHEN** `ignored_args=["request_id"]` is configured and a call includes `{"query": "test", "request_id": "abc123"}`
- **THEN** the cache key is computed from `{"query": "test"}` only

### Requirement: LRU eviction
The system SHALL evict least-recently-used entries when the cache reaches `max_entries` (default 10000). Each read or write updates the access order.

#### Scenario: Eviction at max entries
- **WHEN** the cache has 10000 entries and a new entry is added
- **THEN** the least recently accessed entry is removed

#### Scenario: Read updates access order
- **WHEN** an old entry is read (cache hit) and then a new entry causes eviction
- **THEN** the recently-read old entry is not evicted

### Requirement: Cache invalidation
The system SHALL support three invalidation mechanisms: TTL expiration, manual API call, and automatic on tool config change.

#### Scenario: Manual cache clear
- **WHEN** `DELETE /api/tools/cache` is called
- **THEN** all cache entries are removed

#### Scenario: Manual per-tool cache clear
- **WHEN** `DELETE /api/tools/cache?tool_name=web_search` is called
- **THEN** only entries for `web_search` are removed

#### Scenario: Invalidation on tool config change
- **WHEN** a tool's `cacheable` or `cache_ttl` field is updated via API
- **THEN** all existing cache entries for that tool are invalidated

### Requirement: Cache metrics
The system SHALL track cache hit count, miss count, and current entry count. Metrics are exposed via REST API.

#### Scenario: Get cache stats
- **WHEN** a client requests `GET /api/tools/cache/stats`
- **THEN** the system returns `{"hits": N, "misses": N, "entries": N, "hit_rate": 0.XX}`

### Requirement: REST API for cache management
The system SHALL expose REST API endpoints for cache management.

#### Scenario: Get cache stats
- **WHEN** a client requests `GET /api/tools/cache/stats`
- **THEN** the system returns cache metrics

#### Scenario: Clear all cache
- **WHEN** a client requests `DELETE /api/tools/cache`
- **THEN** the system clears all entries and returns 204

#### Scenario: Clear per-tool cache
- **WHEN** a client requests `DELETE /api/tools/cache?tool_name=web_search`
- **THEN** the system clears entries for that tool only and returns 204
