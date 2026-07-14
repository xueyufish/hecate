## Context

Hecate's `ToolRegistry.execute()` routes tool calls to builtin/custom/MCP executors. Every call executes from scratch — no result caching exists. For read-only tools (web_search, read_file, MCP list_*), identical calls within a session waste latency and API quota.

**Research basis** (14 platforms):
- CrewAI: `CacheHandler` (in-memory dict + RWLock), per-tool `cache_function(args, result) → bool`
- AgentScope: `ToolContext` in `AgentState`, LRU eviction (100 files / 25KB), file mtime validation
- LangGraph: Two-component (backend + policy), `InMemoryCache`/`SqliteCache` + `CachePolicy(ttl=60)`, per-node cache_policy
- LangGraph-Redis: `ToolResultCacheMiddleware` with 7-priority cacheability chain (metadata cacheable → destructive → volatile → read_only+idempotent → side-effect prefix → volatile args → whitelist), `ignored_arg_names`, Redis backend
- Amazon Bedrock AgentCore: Gateway caches tool definitions (not results)
- OpenClaw: Session-level MCP runtime cache (connection reuse, not result caching), prompt cache stability via deterministic tool ordering
- Enterprise platforms (Salesforce/Google/IBM/华为): No tool result caching

## Goals / Non-Goals

**Goals:**
- ToolCache with TTL, integrated into ToolRegistry.execute()
- Cacheability priority chain (cacheable flag → source heuristic → side-effect prefix → read_only)
- Per-tool cache configuration (cacheable, cache_ttl fields on ToolModel)
- Session-scoped cache entries
- Canonical key generation with ignored_args support
- Cache invalidation (TTL + manual API + on config change)
- Cache metrics (hit/miss/entry count)

**Non-Goals:**
- Redis backend (In-memory first; Redis as follow-up)
- File mtime validation (AgentScope pattern; deferred)
- Cross-session caching by default (opt-in per tool)
- Prompt cache stability (OpenClaw pattern; separate concern)
- Semantic caching (LangGraph-Redis SemanticCacheMiddleware; different feature)

## Decisions

### Decision 1: Cache in ToolRegistry.execute(), not ToolWorker

**Choice**: Cache check/store happens inside `ToolRegistry.execute()`, after routing but before execution.

**Rationale**: ToolRegistry is the single execution path for all tools (builtin, custom, MCP). Caching here covers all call sites — ToolWorker, MCP Server's tool_execute, and any future callers. ToolWorker-level caching would miss non-ToolWorker paths.

### Decision 2: Cacheability priority chain (LangGraph-Redis inspired)

**Choice**: 5-priority chain to determine if a tool call should be cached:

```
Priority 1: tool.cacheable is not None → use explicit flag
Priority 2: tool.source == "mcp" and tool.mcp_tool_name starts with write/create/delete/send → skip
Priority 3: tool.name matches side-effect prefix (write_, create_, delete_, send_, update_) → skip
Priority 4: tool.source == "builtin" and name in {execute_code, bash, write_file, edit_file} → skip
Priority 5: Default → cache if risk_level in {LOW, MEDIUM} and sandbox_enabled == False
```

**Rationale**: LangGraph-Redis's 7-priority chain is the most sophisticated approach found. We simplify to 5 priorities (merging volatile args and metadata checks). The `cacheable` flag (Priority 1) gives explicit override capability.

### Decision 3: Session-scoped cache

**Choice**: Cache entries include `session_id` in the key namespace. Same tool + same args in different sessions do not share cache.

**Rationale**: Session scoping is the safest default — prevents stale data leaking between conversations. AgentScope uses session-level caching. Cross-session caching can be enabled per-tool via `cacheable=True` (which implies global scope is acceptable for read-only tools like web_search).

### Decision 4: In-memory dict with TTL + LRU eviction

**Choice**: `dict[str, CacheEntry]` with periodic TTL sweep + LRU eviction at max_entries.

**Rationale**: CrewAI uses the same pattern (dict + lock). Redis is overkill for v1 — most Hecate deployments are single-instance. Redis backend can be added as a drop-in replacement later by abstracting behind a `CacheBackend` protocol.

### Decision 5: Canonical JSON for cache key

**Choice**: `key = sha256(f"{tool_name}:{canonical_json(args, ignored_args)}")`

`canonical_json` sorts dict keys recursively, strips `ignored_args` keys, serializes to compact JSON.

**Rationale**: Dict key ordering is non-deterministic in Python. LangGraph-Redis uses `ignored_arg_names` to strip `request_id`, `trace_id` etc. We adopt the same pattern.

## Risks / Trade-offs

- **[Stale data]** — Cached results may be outdated if the underlying data source changes. Mitigation: TTL (default 300s), manual invalidation API, session scoping.

- **[Memory usage]** — In-memory cache grows unbounded without eviction. Mitigation: LRU eviction at `max_entries` (default 10000), periodic TTL sweep.

- **[False cache hits]** — Different tools with same name+args but different sources could collide. Mitigation: Cache key includes `tool_name`, and ToolRegistry resolves source before caching, so different sources for the same name can't happen (ToolModel has unique name per workspace).

- **[MCP tool cacheability]** — MCP tools don't have explicit `read_only` or `idempotent` metadata. Mitigation: Priority chain uses name-prefix heuristic for MCP tools. Per-tool `cacheable` flag gives explicit override.
