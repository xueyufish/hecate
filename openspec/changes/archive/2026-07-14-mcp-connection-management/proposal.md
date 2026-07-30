## Why — 为什么

Hecate's MCP Client (5.3 ✅) provides basic MCP server connections, and the Plugin System (5.5 ✅) supports `mcp://endpoint` loading. But the current `MCPClientManager` has no connection pooling, no auto-reconnection, no per-request timeout, no health checks, and no connection lifecycle tracking. Claude Code's production issues (SSE disconnections, hanging Promises, no auto-reconnect) demonstrate that "not managing connections" is unacceptable for an enterprise multi-tenant platform. This change merges former 5.4c (Server Registry) and 5.4d (Connection Management) into a single feature.

Hecate 的 MCP 客户端（5.3 ✅）提供基本的 MCP 服务器连接，插件系统（5.5 ✅）支持 `mcp://endpoint` 加载。但当前的 `MCPClientManager` 没有连接池、没有自动重连、没有按请求超时、没有健康检查和连接生命周期跟踪。Claude Code 的生产问题（SSE 断开、挂起的 Promise、无自动重连）表明"不管理连接"对于企业级多租户平台是不可接受的。此变更将先前的 5.4c（服务器注册表）和 5.4d（连接管理）合并为一个功能。

## What Changes — 变更内容

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

- **MCP 服务器注册表**：MCP 服务器注册其能力（工具/资源/提示），客户端通过能力查询发现服务器，工具列表缓存带有 TTL
- **连接池**：HTTP 连接的每服务器连接池（可配置最小/最大会话数），stdio 使用单一连接
- **懒连接 + 会话复用**：插件启用时注册 MCP 服务器，首次工具调用时创建连接（Bedrock 模式），后续调用复用会话
- **两步探测**（AgentArts 模式）：TCP 可达性检查 → MCP SDK 协议握手，附带结构化错误码用于诊断
- **自动重连**：指数退避（1s → 2s → 4s → 8s → 16s → 最大 60s），最多 5 次重试，重连期间请求返回 ConnectionError
- **按请求超时**：可配置的按请求超时（默认 30s），超时取消请求，连接释放回池
- **健康检查**：定期 `list_tools` ping（默认 30s 间隔），3 次连续失败标记连接不健康，不健康连接不分配新请求
- **断路器**：5 次连续失败打开电路，30s 半开探测，探测成功关闭电路
- **工具发现缓存**：`tools/list` 结果以 TTL（默认 5 分钟）缓存，缓存未命中时 single-flight 刷新
- **多租户隔离**：以服务器名称为键的共享连接池，在 PluginModel.workspace_id 注册层强制隔离（Bedrock 模式 — 共享基础设施 + 身份层隔离）
- **连接生命周期事件**：on_connect / on_disconnect / on_reconnect / on_health_check_fail，记录用于可观测性
- **REST API**：`GET /api/mcp/connections`（列表+状态）、`GET /api/mcp/connections/{name}`（详情）、`POST /api/mcp/connections/{name}/reconnect`（手动重连）、`POST /api/mcp/connections/{name}/sync`（刷新工具缓存）
- **前端**：插件详情页中的 MCP 连接状态面板（状态徽章、池使用率、重连/同步按钮）
- **结构化错误码**：AgentArts 风格的连接诊断错误码（DNS 失败、超时、端口关闭、路径 404、WAF 拦截、SSL 错误）
- **无向后兼容**：MCPClientManager API 直接重写（开发阶段，无外部消费者）

## Capabilities — 能力

### New Capabilities — 新能力

- `mcp-connection-management`: Server registry, connection pooling, lazy connection, two-step probe, auto-reconnection, per-request timeout, health checks, circuit breaker, tool caching, multi-tenant isolation, lifecycle events, REST API, frontend status panel, structured error codes

### Modified Capabilities — 修改的能力

- `plugin-system`: PluginService.enable_plugin() and disable_plugin() gain MCP server registration/unregistration logic (register on enable, unregister on disable, actual connection is lazy)

## Impact — 影响

- **New files — 新文件**:
  - `src/hecate/services/mcp/pool.py` — ConnectionPool class (min/max sessions, borrow/return, health check)
  - `src/hecate/services/mcp/circuit_breaker.py` — CircuitBreaker class (open/half-open/closed states)
  - `src/hecate/services/mcp/registry.py` — MCPServerRegistry (server registration, capability discovery, tool caching)
  - `src/hecate/services/mcp/errors.py` — Structured error codes for connection diagnosis
  - `src/hecate/api/management/mcp.py` — REST API for MCP connection management
  - `web/src/app/(dashboard)/plugins/[id]/mcp-status.tsx` — MCP connection status panel component
- **Modified files — 修改的文件**:
  - `src/hecate/services/mcp/connection.py` — MCPClientManager rewritten with pool, health check, reconnection
  - `src/hecate/services/mcp/client.py` — HecateMCPClient gains per-request timeout, health check ping
  - `src/hecate/services/plugin/service.py` — enable_plugin/disable_plugin gain MCP registration
  - `src/hecate/core/config.py` — New settings: MCP_POOL_MIN, MCP_POOL_MAX, MCP_HEALTH_CHECK_INTERVAL, MCP_RECONNECT_MAX_RETRIES, MCP_RECONNECT_BASE_DELAY, MCP_RECONNECT_MAX_DELAY, MCP_REQUEST_TIMEOUT, MCP_TOOL_CACHE_TTL
  - `web/src/app/(dashboard)/plugins/[id]/page.tsx` — Add MCP status panel for mcp:// plugins
- **Dependencies — 依赖**: None new (uses existing `mcp` SDK, `httpx`)
