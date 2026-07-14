## 1. Configuration

- [x] 1.1 Add MCP connection management settings to `src/hecate/core/config.py`: `MCP_POOL_MIN_SIZE: int = 1`, `MCP_POOL_MAX_SIZE: int = 5`, `MCP_BORROW_TIMEOUT: int = 5`, `MCP_HEALTH_CHECK_INTERVAL: int = 30`, `MCP_RECONNECT_MAX_RETRIES: int = 5`, `MCP_RECONNECT_BASE_DELAY: float = 1.0`, `MCP_RECONNECT_MAX_DELAY: float = 60.0`, `MCP_REQUEST_TIMEOUT: int = 30`, `MCP_TOOL_CACHE_TTL: int = 300`, `MCP_CIRCUIT_BREAKER_THRESHOLD: int = 5`, `MCP_CIRCUIT_BREAKER_RECOVERY_TIMEOUT: int = 30`

## 2. Structured Error Codes

- [x] 2.1 Create `src/hecate/services/mcp/errors.py` with error code enum: `MCP_DNS_FAILURE`, `MCP_CONNECT_TIMEOUT`, `MCP_PORT_CLOSED`, `MCP_PATH_NOT_FOUND`, `MCP_SSL_ERROR`, `MCP_WAF_BLOCKED`, `MCP_POOL_EXHAUSTED`, `MCP_RECONNECTING`, `MCP_CONNECTION_FAILED`, `MCP_REQUEST_TIMEOUT`, `MCP_CIRCUIT_OPEN`. Each with description and diagnostic hints.

## 3. Circuit Breaker

- [x] 3.1 Create `src/hecate/services/mcp/circuit_breaker.py` with `CircuitBreaker` class: states (CLOSED/OPEN/HALF_OPEN), `record_success()`, `record_failure()`, `can_proceed() -> bool`, configurable threshold and recovery timeout.

## 4. Connection Pool

- [x] 4.1 Create `src/hecate/services/mcp/pool.py` with `ConnectionPool` class: min/max sessions, `async borrow(timeout) -> HecateMCPClient`, `async return(client)`, health check task (periodic `list_tools` ping), unhealthy connection marking (3 consecutive failures), pool metrics (active/idle/total/max).

## 5. MCP Server Registry

- [x] 5.1 Create `src/hecate/services/mcp/registry.py` with `MCPServerRegistry` class: `register(name, endpoint, transport, workspace_id)`, `unregister(name)`, `discover_tools() -> list[dict]` (with TTL cache + single-flight refresh), `get_server(name) -> ServerInfo`, `list_servers() -> list[ServerInfo]`.

## 6. Rewrite MCPClientManager

- [x] 6.1 Rewrite `src/hecate/services/mcp/connection.py` — `MCPClientManager` now uses `ConnectionPool` per server, `CircuitBreaker` per server, `MCPServerRegistry` for registration. Lazy connection on first `call_tool()`. Two-step probe (TCP → SDK handshake) with structured error codes. Auto-reconnection with exponential backoff + jitter. Per-request timeout via `asyncio.wait_for()`. Health check background task. Lifecycle events (on_connect/on_disconnect/on_reconnect/on_health_check_fail).
- [x] 6.2 Update `src/hecate/services/mcp/client.py` — `HecateMCPClient` gains `async health_check() -> bool` method (calls `list_tools`), per-request timeout support, connection state tracking.

## 7. Plugin System Integration

- [x] 7.1 Update `src/hecate/services/plugin/service.py` — `enable_plugin()` calls `MCPServerRegistry.register()` for mcp:// plugins (no connection). `disable_plugin()` calls `MCPServerRegistry.unregister()` (closes connections, clears cache).

## 8. REST API

- [x] 8.1 Create `src/hecate/api/management/mcp.py` router with prefix `/api/mcp`: `GET /connections` (list all with status/pool metrics/tool count), `GET /connections/{name}` (detail), `POST /connections/{name}/reconnect` (manual reconnect), `POST /connections/{name}/sync` (refresh tool cache).
- [x] 8.2 Register `mcp_router` in `src/hecate/main.py`.

## 9. Backend Tests

- [x] 9.1 Test `CircuitBreaker` — closed→open on 5 failures, open→half-open after timeout, half-open→closed on success, half-open→open on failure.
- [x] 9.2 Test `ConnectionPool` — borrow/return, pool exhaustion with timeout, new connection creation on demand.
- [x] 9.3 Test `MCPServerRegistry` — register/unregister, tool cache hit/miss, single-flight refresh.
- [x] 9.4 Test two-step probe — mock TCP failure, mock SDK handshake failure, verify structured error codes.
- [x] 9.5 Test auto-reconnection — mock connection drop, verify exponential backoff retry, verify `MCP_RECONNECTING` during reconnection.
- [x] 9.6 Test per-request timeout — mock slow tool call, verify `MCP_REQUEST_TIMEOUT` after timeout.
- [x] 9.7 Test health check — mock failing `list_tools`, verify unhealthy marking after 3 failures, verify recovery.
- [x] 9.8 Test REST API — list connections, get detail, reconnect, sync via httpx AsyncClient.

## 10. Frontend — MCP Status Panel

- [x] 10.1 Create `web/src/app/(dashboard)/plugins/[id]/mcp-status.tsx` — connection status badge (healthy/unhealthy/reconnecting/disconnected), pool usage (active/idle/max), tool count, "Reconnect" button (POST /api/mcp/connections/{name}/reconnect), "Sync Tools" button (POST /api/mcp/connections/{name}/sync).
- [x] 10.2 Update `web/src/app/(dashboard)/plugins/[id]/page.tsx` — render MCP status panel when plugin entry starts with `mcp://`.

## 11. Verification

- [x] 11.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 11.2 Run `mypy src/` — 0 errors
- [x] 11.3 Run `python -m pytest tests/test_mcp/ tests/test_plugin/ -q` — all pass
- [x] 11.4 Manual verification: register an MCP server plugin, verify lazy connection on first tool call, verify health check, verify manual reconnect via API
