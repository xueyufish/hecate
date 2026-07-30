## ADDED Requirements — 新增需求

### Requirement: Tool result caching — 需求：工具结果缓存
The system SHALL cache tool execution results in memory with configurable TTL. On a cache hit, the cached result is returned without executing the tool. On a cache miss, the tool executes normally and the result is stored.

系统应在内存中缓存工具执行结果，具有可配置的 TTL。缓存命中时，返回缓存结果而不执行工具。缓存未命中时，工具正常执行并将结果存储。

#### Scenario: Cache miss executes and stores — 场景：缓存未命中的执行和存储
- **WHEN** a cacheable tool is called and no cached entry exists
- **THEN** the tool executes, the result is stored in cache with TTL, and the result is returned

- **当**调用可缓存工具且不存在缓存条目
- **则**工具执行，结果存储在缓存中并设置 TTL，然后返回结果

#### Scenario: Cache hit returns cached result — 场景：缓存命中返回缓存结果
- **WHEN** a cacheable tool is called with identical arguments and a valid (non-expired) cached entry exists
- **THEN** the cached result is returned without executing the tool

- **当**使用相同参数调用可缓存工具且存在有效（未过期）的缓存条目
- **则**返回缓存结果而不执行工具

#### Scenario: Cache entry expired — 场景：缓存条目过期
- **WHEN** a cached entry's TTL has elapsed
- **THEN** the entry is evicted and the next call executes the tool fresh

- **当**缓存条目的 TTL 已过
- **则**条目被驱逐，下一次调用重新执行工具

### Requirement: Cacheability priority chain — 需求：可缓存性优先级链
The system SHALL determine whether a tool call is cacheable using a 5-priority chain evaluated in order. The first matching priority determines the outcome.

系统应使用按顺序评估的 5 级优先级链确定工具调用是否可缓存。第一个匹配的优先级决定结果。

#### Scenario: Explicit cacheable flag overrides everything — 场景：显式可缓存标志覆盖一切
- **WHEN** a tool has `cacheable=True` set explicitly
- **THEN** the tool is cached regardless of name, source, or risk level

- **当**工具显式设置了 `cacheable=True`
- **则**工具被缓存，无论名称、来源或风险级别如何

#### Scenario: Explicit non-cacheable flag — 场景：显式不可缓存标志
- **WHEN** a tool has `cacheable=False` set explicitly
- **THEN** the tool is never cached

- **当**工具显式设置了 `cacheable=False`
- **则**工具永不缓存

#### Scenario: Side-effect name prefix skips caching — 场景：副作用名称前缀跳过缓存
- **WHEN** a tool name starts with `write_`, `create_`, `delete_`, `send_`, `update_`, or `remove_` and no explicit `cacheable` flag is set
- **THEN** the tool is not cached

- **当**工具名称以 `write_`、`create_`、`delete_`、`send_`、`update_` 或 `remove_` 开头，且未设置显式的 `cacheable` 标志
- **则**工具不缓存

#### Scenario: Dangerous builtin tools skip caching — 场景：危险内置工具跳过缓存
- **WHEN** a tool is a builtin with name in {execute_code, bash, write_file, edit_file} and no explicit `cacheable` flag is set
- **THEN** the tool is not cached

- **当**工具是名称在 {execute_code, bash, write_file, edit_file} 中的内置工具，且未设置显式的 `cacheable` 标志
- **则**工具不缓存

#### Scenario: Default heuristic caches safe tools — 场景：默认启发式缓存安全工具
- **WHEN** a tool has no explicit `cacheable` flag, no side-effect prefix, is not a dangerous builtin
- **AND** the tool's `risk_level` is LOW or MEDIUM and `sandbox_enabled` is False
- **THEN** the tool is cached

- **当**工具没有显式的 `cacheable` 标志、没有副作用前缀、不是危险内置工具
- **且**工具的 `risk_level` 为 LOW 或 MEDIUM 且 `sandbox_enabled` 为 False
- **则**工具被缓存

#### Scenario: High-risk tools not cached by default — 场景：高风险工具默认不缓存
- **WHEN** a tool has `risk_level=HIGH` or `risk_level=CRITICAL` and no explicit `cacheable` flag
- **THEN** the tool is not cached by default

- **当**工具具有 `risk_level=HIGH` 或 `risk_level=CRITICAL` 且没有显式的 `cacheable` 标志
- **则**工具默认不缓存

### Requirement: Per-tool cache TTL configuration — 需求：每个工具的缓存 TTL 配置
The system SHALL support per-tool cache TTL via the `cache_ttl` field on `ToolModel`. When set, it overrides the global default TTL. When `None`, the global `TOOL_CACHE_DEFAULT_TTL` is used.

系统应通过 `ToolModel` 上的 `cache_ttl` 字段支持每个工具的缓存 TTL。设置后，它覆盖全局默认 TTL。当为 `None` 时，使用全局 `TOOL_CACHE_DEFAULT_TTL`。

#### Scenario: Per-tool TTL overrides global — 场景：每个工具的 TTL 覆盖全局
- **WHEN** a tool has `cache_ttl=60` and the global default is 300
- **THEN** the cached entry expires after 60 seconds

- **当**工具具有 `cache_ttl=60` 且全局默认值为 300
- **则**缓存条目在 60 秒后过期

#### Scenario: Global TTL used as fallback — 场景：全局 TTL 作为回退
- **WHEN** a tool has `cache_ttl=None`
- **THEN** the global `TOOL_CACHE_DEFAULT_TTL` (default 300s) is used

- **当**工具具有 `cache_ttl=None`
- **则**使用全局 `TOOL_CACHE_DEFAULT_TTL`（默认 300 秒）

### Requirement: Session-scoped cache entries — 需求：会话范围的缓存条目
The system SHALL scope cache entries by `session_id` by default. The same tool with the same arguments in different sessions uses separate cache entries.

系统默认应按 `session_id` 限定缓存条目范围。不同会话中相同工具和相同参数使用不同的缓存条目。

#### Scenario: Same tool same session hits cache — 场景：相同工具相同会话命中缓存
- **WHEN** tool `web_search` is called twice with `query="weather"` in the same session
- **THEN** the second call returns the cached result from the first call

- **当**在同一会话中两次调用工具 `web_search`，参数为 `query="weather"`
- **则**第二次调用返回第一次调用的缓存结果

#### Scenario: Same tool different sessions miss cache — 场景：相同工具不同会话缓存未命中
- **WHEN** tool `web_search` is called with `query="weather"` in session A, then again in session B
- **THEN** session B does not see session A's cached result; the tool executes fresh

- **当**在会话 A 中调用工具 `web_search`，参数为 `query="weather"`，然后在会话 B 中再次调用
- **则**会话 B 看不到会话 A 的缓存结果；工具重新执行

### Requirement: Canonical cache key generation — 需求：规范缓存键生成
The system SHALL generate deterministic cache keys using `sha256(tool_name + canonical_json(args))`. Canonical JSON sorts dict keys recursively and strips `ignored_args` before serialization.

系统应使用 `sha256(tool_name + canonical_json(args))` 生成确定性缓存键。规范 JSON 递归排序字典键，并在序列化前剥离 `ignored_args`。

#### Scenario: Different key order same cache key — 场景：不同键顺序相同缓存键
- **WHEN** tool A is called with `{"b": 2, "a": 1}` and then with `{"a": 1, "b": 2}`
- **THEN** both calls produce the same cache key

- **当**工具 A 被调用，先使用 `{"b": 2, "a": 1}`，然后使用 `{"a": 1, "b": 2}`
- **则**两次调用产生相同的缓存键

#### Scenario: Ignored args stripped from key — 场景：从键中剥离忽略的参数
- **WHEN** `ignored_args=["request_id"]` is configured and a call includes `{"query": "test", "request_id": "abc123"}`
- **THEN** the cache key is computed from `{"query": "test"}` only

- **当**配置了 `ignored_args=["request_id"]` 且调用包含 `{"query": "test", "request_id": "abc123"}`
- **则**缓存键仅从 `{"query": "test"}` 计算

### Requirement: LRU eviction — 需求：LRU 驱逐
The system SHALL evict least-recently-used entries when the cache reaches `max_entries` (default 10000). Each read or write updates the access order.

当缓存达到 `max_entries`（默认 10000）时，系统应驱逐最近最少使用的条目。每次读取或写入都会更新访问顺序。

#### Scenario: Eviction at max entries — 场景：达到最大条目时驱逐
- **WHEN** the cache has 10000 entries and a new entry is added
- **THEN** the least recently accessed entry is removed

- **当**缓存有 10000 个条目且添加新条目时
- **则**最近最少访问的条目被移除

#### Scenario: Read updates access order — 场景：读取更新访问顺序
- **WHEN** an old entry is read (cache hit) and then a new entry causes eviction
- **THEN** the recently-read old entry is not evicted

- **当**读取旧条目（缓存命中）后新条目导致驱逐
- **则**最近读取的旧条目不会被驱逐

### Requirement: Cache invalidation — 需求：缓存失效
The system SHALL support three invalidation mechanisms: TTL expiration, manual API call, and automatic on tool config change.

系统应支持三种失效机制：TTL 过期、手动 API 调用和工具配置变更时自动失效。

#### Scenario: Manual cache clear — 场景：手动清除缓存
- **WHEN** `DELETE /api/tools/cache` is called
- **THEN** all cache entries are removed

- **当**调用 `DELETE /api/tools/cache`
- **则**所有缓存条目被移除

#### Scenario: Manual per-tool cache clear — 场景：手动清除每个工具的缓存
- **WHEN** `DELETE /api/tools/cache?tool_name=web_search` is called
- **THEN** only entries for `web_search` are removed

- **当**调用 `DELETE /api/tools/cache?tool_name=web_search`
- **则**仅移除 `web_search` 的条目

#### Scenario: Invalidation on tool config change — 场景：工具配置变更时失效
- **WHEN** a tool's `cacheable` or `cache_ttl` field is updated via API
- **THEN** all existing cache entries for that tool are invalidated

- **当**通过 API 更新工具的 `cacheable` 或 `cache_ttl` 字段
- **则**该工具的所有现有缓存条目被失效

### Requirement: Cache metrics — 需求：缓存指标
The system SHALL track cache hit count, miss count, and current entry count. Metrics are exposed via REST API.

系统应追踪缓存命中次数、未命中次数和当前条目数。指标通过 REST API 暴露。

#### Scenario: Get cache stats — 场景：获取缓存统计
- **WHEN** a client requests `GET /api/tools/cache/stats`
- **THEN** the system returns `{"hits": N, "misses": N, "entries": N, "hit_rate": 0.XX}`

- **当**客户端请求 `GET /api/tools/cache/stats`
- **则**系统返回 `{"hits": N, "misses": N, "entries": N, "hit_rate": 0.XX}`

### Requirement: REST API for cache management — 需求：缓存管理的 REST API
The system SHALL expose REST API endpoints for cache management.

系统应公开用于缓存管理的 REST API 端点。

#### Scenario: Get cache stats — 场景：获取缓存统计
- **WHEN** a client requests `GET /api/tools/cache/stats`
- **THEN** the system returns cache metrics

- **当**客户端请求 `GET /api/tools/cache/stats`
- **则**系统返回缓存指标

#### Scenario: Clear all cache — 场景：清除所有缓存
- **WHEN** a client requests `DELETE /api/tools/cache`
- **THEN** the system clears all entries and returns 204

- **当**客户端请求 `DELETE /api/tools/cache`
- **则**系统清除所有条目并返回 204

#### Scenario: Clear per-tool cache — 场景：清除每个工具的缓存
- **WHEN** a client requests `DELETE /api/tools/cache?tool_name=web_search`
- **THEN** the system clears entries for that tool only and returns 204

- **当**客户端请求 `DELETE /api/tools/cache?tool_name=web_search`
- **则**系统仅清除该工具的条目并返回 204
