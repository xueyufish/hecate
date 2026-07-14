## Why

Hecate's MCP Client (5.3 ✅) provides basic MCP server connections, and the Plugin System (5.5 ✅) supports `mcp://endpoint` loading. But the current `MCPClientManager` has no connection pooling, no auto-reconnection, no per-request timeout, no health checks, and no connection lifecycle tracking. Claude Code's production issues (SSE disconnections, hanging Promises, no auto-reconnect) demonstrate that "not managing connections" is unacceptable for an enterprise multi-tenant platform. This change merges former 5.4c (Server Registry) and 5.4d (Connection Management) into a single feature.

## What Changes

- **MCP Server Registry**: MCP servers register with capabilities (tools/resources/prompts), clients discover servers by capability query, tool list caching with TTL
- **Connection pooling**: Per-server connection pool for HTTP connections (min/max sessions configurable), single connection for stdio
- **Lazy connection + session reuse**: MCP server registered on plugin enable, connection created on first tool call (Bedrock pattern), session reused for subsequent calls
- **Two-step probe** (AgentArts pattern): TCP reachability check → MCP SDK protocol handshake, with structured error codes for diagnosis
- **Automatic reconnection**: Exponential backoff (1s → 2s → 4s → 8s → 16s → max 60s), max 5 retries, reconnection during which requests return ConnectionError
- **Per-request timeout**: Configurable per-request timeout (default 30s), request cancelled on timeout, connection released back to pool
- **Health checks**: Periodic `list_tools` ping (default 30s interval), 3 consecutive failures mark connection unhealthy, unhealthy connections not assigned new requests
- **Circuit breaker**: 5 consecutive failures open circuit, 30s half-open probe, probe success closes circuit
- **Tool discovery caching**: `tools/list` results cached with TTL (default 5 min), single-flight refresh on cache miss
- **Multi-tenant isolation**: Shared connection pool keyed by server name, isolation enforced at PluginModel.workspace_id registration layer (Bedrock pattern — shared infrastructure + identity-layer isolation)
- **Connection lifecycle events**: on_connect / on_disconnect / on_reconnect / on_health_check_fail, logged for observability
- **REST API**: `GET /api/mcp/connections` (list + status), `GET /api/mcp/connections/{name}` (detail), `POST /api/mcp/connections/{name}/reconnect` (manual reconnect), `POST /api/mcp/connections/{name}/sync` (refresh tool cache)
- **Frontend**: MCP connection status panel in plugin detail page (status badge, pool usage, Reconnect/Sync buttons)
- **Structured error codes**: AgentArts-style error codes for connection diagnosis (DNS failure, timeout, port closed, path 404, WAF block, SSL error)
- **No backward compatibility**: MCPClientManager API rewritten directly (development stage, no external consumers)

## Capabilities

### New Capabilities

- `mcp-connection-management`: Server registry, connection pooling, lazy connection, two-step probe, auto-reconnection, per-request timeout, health checks, circuit breaker, tool caching, multi-tenant isolation, lifecycle events, REST API, frontend status panel, structured error codes

### Modified Capabilities

- `plugin-system`: PluginService.enable_plugin() and disable_plugin() gain MCP server registration/unregistration logic (register on enable, unregister on disable, actual connection is lazy)

## Impact

- **New files**:
  - `src/hecate/services/mcp/pool.py` — ConnectionPool class (min/max sessions, borrow/return, health check)
  - `src/hecate/services/mcp/circuit_breaker.py` — CircuitBreaker class (open/half-open/closed states)
  - `src/hecate/services/mcp/registry.py` — MCPServerRegistry (server registration, capability discovery, tool caching)
  - `src/hecate/services/mcp/errors.py` — Structured error codes for connection diagnosis
  - `src/hecate/api/management/mcp.py` — REST API for MCP connection management
  - `web/src/app/(dashboard)/plugins/[id]/mcp-status.tsx` — MCP connection status panel component
- **Modified files**:
  - `src/hecate/services/mcp/connection.py` — MCPClientManager rewritten with pool, health check, reconnection
  - `src/hecate/services/mcp/client.py` — HecateMCPClient gains per-request timeout, health check ping
  - `src/hecate/services/plugin/service.py` — enable_plugin/disable_plugin gain MCP registration
  - `src/hecate/core/config.py` — New settings: MCP_POOL_MIN, MCP_POOL_MAX, MCP_HEALTH_CHECK_INTERVAL, MCP_RECONNECT_MAX_RETRIES, MCP_RECONNECT_BASE_DELAY, MCP_RECONNECT_MAX_DELAY, MCP_REQUEST_TIMEOUT, MCP_TOOL_CACHE_TTL
  - `web/src/app/(dashboard)/plugins/[id]/page.tsx` — Add MCP status panel for mcp:// plugins
- **Dependencies**: None new (uses existing `mcp` SDK, `httpx`)
