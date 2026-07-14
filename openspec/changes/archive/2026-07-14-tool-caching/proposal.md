## Why

Hecate executes every tool call from scratch, even when the same tool is called with identical arguments within the same session. For read-only tools like `web_search`, `read_file`, or MCP `list_*` operations, this wastes latency and external API quota. Research across 14 platforms shows that code-execution frameworks (CrewAI, AgentScope, LangGraph) universally implement tool result caching, while the LangGraph-Redis middleware introduces a sophisticated 7-priority cacheability chain that determines whether a tool call should be cached based on metadata, side-effect prefixes, and volatile arguments.

## What Changes

- **ToolCache**: In-memory cache with TTL, integrated into `ToolRegistry.execute()`. On cache hit, returns cached result without executing the tool. On cache miss, executes normally and stores the result.
- **Cacheability priority chain** (inspired by LangGraph-Redis): `cacheable` flag → `source` heuristic → side-effect name prefix → `read_only + idempotent` → volatile arg detection → default deny
- **Per-tool cache configuration**: New `cacheable: bool | None` and `cache_ttl: int | None` fields on `ToolModel`. `None` means auto-detect via priority chain.
- **Session-scoped cache**: Cache entries are scoped to `session_id` by default. Cross-session caching is opt-in per tool.
- **Canonical key generation**: `hash(tool_name + canonical_json(args))` with optional `ignored_args` stripping (request_id, trace_id, etc.)
- **Cache invalidation**: TTL expiration + manual `DELETE /api/tools/cache` endpoint + automatic invalidation on tool config change
- **Cache metrics**: Hit rate, miss rate, entry count — exposed via `GET /api/tools/cache/stats`

## Capabilities

### New Capabilities

- `tool-caching`: Tool result caching with TTL, cacheability priority chain, session-scoped entries, canonical key generation, cache invalidation, and metrics

### Modified Capabilities

- _(none — caching is transparent to existing tool execution; ToolRegistry gains an optional cache parameter)_

## Impact

- **New files**:
  - `src/hecate/services/tool/cache.py` — ToolCache class (key generation, TTL, priority chain, session scoping)
  - `src/hecate/api/management/tool_cache.py` — REST API for cache management (stats, clear)
  - `tests/test_services/test_tool_cache.py` — cache unit tests
- **Modified files**:
  - `src/hecate/services/tool/registry.py` — ToolRegistry gains optional `cache: ToolCache` param; `execute()` checks cache before executing
  - `src/hecate/models/tool.py` — ToolModel gains `cacheable: bool | None` and `cache_ttl: int | None` columns
  - `src/hecate/core/config.py` — New settings: `TOOL_CACHE_ENABLED`, `TOOL_CACHE_DEFAULT_TTL`, `TOOL_CACHE_MAX_ENTRIES`, `TOOL_CACHE_SESSION_SCOPED`
  - `src/hecate/main.py` — Register tool_cache router
  - `alembic/versions/` — Migration for new ToolModel columns
- **Dependencies**: None new (uses existing stdlib `hashlib`, `json`, `time`)
