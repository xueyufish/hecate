## ADDED Requirements — 新增需求

### Requirement: MCP server registry — 需求：MCP 服务器注册表
The system SHALL maintain a registry of MCP servers with their capabilities (tools/resources/prompts). Servers register when their plugin is enabled, unregister when disabled. The registry supports capability-based discovery — clients can query which servers provide specific tools.

系统应维护 MCP 服务器的注册表及其能力（工具/资源/提示）。服务器在其插件启用时注册，禁用时注销。注册表支持基于能力的发现 — 客户端可以查询哪些服务器提供特定工具。

#### Scenario: Server registered on plugin enable — 场景：插件启用时注册服务器
- **WHEN** a plugin with `entry: mcp://endpoint` is enabled
- **THEN** the system registers the MCP server in the registry without connecting
- **当**具有 `entry: mcp://endpoint` 的插件启用时
- **那么**系统在注册表中注册 MCP 服务器但不连接

#### Scenario: Server unregistered on plugin disable — 场景：插件禁用时注销服务器
- **WHEN** an MCP server plugin is disabled
- **THEN** the system unregisters the server, closes any active connections, and clears the tool cache
- **当**MCP 服务器插件被禁用时
- **那么**系统注销服务器，关闭所有活跃连接，并清除工具缓存

#### Scenario: Capability discovery — 场景：能力发现
- **WHEN** a client queries available tools across all registered MCP servers
- **THEN** the system returns cached tool lists from all connected servers
- **当**客户端查询所有注册的 MCP 服务器中可用的工具时
- **那么**系统返回所有已连接服务器的缓存工具列表

### Requirement: Lazy connection with session reuse — 需求：懒连接与会话复用
The system SHALL create MCP connections lazily — on the first tool call to a registered server, not on registration. Subsequent calls to the same server reuse the existing connection/session.

系统应懒创建 MCP 连接 — 在首次调用已注册服务器的工具时，而不是在注册时。对同一服务器的后续调用复用现有连接/会话。

#### Scenario: First tool call creates connection — 场景：首次工具调用创建连接
- **WHEN** a tool call is made to a registered-but-not-connected MCP server
- **THEN** the system creates a connection (two-step probe), executes the tool, and keeps the session for reuse
- **当**对已注册但未连接的 MCP 服务器进行工具调用时
- **那么**系统创建连接（两步探测），执行工具，并保持会话以供复用

#### Scenario: Subsequent calls reuse connection — 场景：后续调用复用连接
- **WHEN** a tool call is made to an already-connected MCP server
- **THEN** the system reuses the existing session without creating a new connection
- **当**对已连接的 MCP 服务器进行工具调用时
- **那么**系统复用现有会话而不创建新连接

### Requirement: Two-step connection probe with error codes — 需求：带错误码的两步连接探测
The system SHALL perform a two-step probe before establishing MCP connections: (1) TCP reachability check, (2) MCP SDK protocol handshake. Each failure mode SHALL return a structured error code for diagnosis.

系统应在建立 MCP 连接前执行两步探测：(1) TCP 可达性检查，(2) MCP SDK 协议握手。每种失败模式应返回结构化错误码用于诊断。

#### Scenario: TCP probe succeeds, SDK handshake succeeds — 场景：TCP 探测和 SDK 握手都成功
- **WHEN** both TCP and SDK probes pass
- **THEN** the connection is established successfully
- **当**TCP 和 SDK 探测都通过时
- **那么**连接成功建立

#### Scenario: DNS resolution failure — 场景：DNS 解析失败
- **WHEN** the MCP server URL cannot be resolved
- **THEN** the system returns error code `MCP_DNS_FAILURE` with the hostname
- **当**MCP 服务器 URL 无法解析时
- **那么**系统返回错误码 `MCP_DNS_FAILURE` 及主机名

#### Scenario: Connection timeout — 场景：连接超时
- **WHEN** the TCP probe times out
- **THEN** the system returns error code `MCP_CONNECT_TIMEOUT` with the timeout value
- **当**TCP 探测超时时
- **那么**系统返回错误码 `MCP_CONNECT_TIMEOUT` 及超时值

#### Scenario: Port closed — 场景：端口关闭
- **WHEN** TCP connection is refused
- **THEN** the system returns error code `MCP_PORT_CLOSED` with the port number
- **当**TCP 连接被拒绝时
- **那么**系统返回错误码 `MCP_PORT_CLOSED` 及端口号

#### Scenario: Path not found — 场景：路径未找到
- **WHEN** TCP connects but HTTP returns 404
- **THEN** the system returns error code `MCP_PATH_NOT_FOUND` with the URL path
- **当**TCP 连接成功但 HTTP 返回 404 时
- **那么**系统返回错误码 `MCP_PATH_NOT_FOUND` 及 URL 路径

#### Scenario: SSL certificate error — 场景：SSL 证书错误
- **WHEN** SSL handshake fails
- **THEN** the system returns error code `MCP_SSL_ERROR` with certificate details
- **当**SSL 握手失败时
- **那么**系统返回错误码 `MCP_SSL_ERROR` 及证书详情

### Requirement: Connection pooling — 需求：连接池
The system SHALL maintain a per-server connection pool for HTTP connections (configurable min/max sessions). stdio connections use a single connection (not poolable). Pool supports borrow-with-timeout and return semantics.

系统应为 HTTP 连接维护每服务器连接池（可配置最小/最大会话数）。stdio 连接使用单一连接（不可池化）。连接池支持带超时的借用和归还语义。

#### Scenario: Borrow available connection — 场景：借用可用连接
- **WHEN** a tool call requests a connection and an idle one is available
- **THEN** the system lends the idle connection immediately
- **当**工具调用请求连接且有闲置连接可用时
- **那么**系统立即出借闲置连接

#### Scenario: Pool exhausted — 场景：连接池耗尽
- **WHEN** all connections are in use and pool is at max capacity
- **THEN** the request waits up to `borrow_timeout` (default 5s), then fails with `MCP_POOL_EXHAUSTED`
- **当**所有连接都在使用中且连接池已达最大容量时
- **那么**请求等待 `borrow_timeout`（默认 5s），然后以 `MCP_POOL_EXHAUSTED` 失败

#### Scenario: New connection created on demand — 场景：按需创建新连接
- **WHEN** no idle connection is available but pool is below max
- **THEN** the system creates a new connection and lends it
- **当**没有闲置连接可用但连接池未达最大值时
- **那么**系统创建新连接并出借

### Requirement: Automatic reconnection with exponential backoff — 需求：带指数退避的自动重连
The system SHALL automatically attempt to reconnect when a connection drops. Reconnection uses exponential backoff with jitter: 1s → 2s → 4s → 8s → 16s → max 60s, maximum 5 retries.

系统应在连接断开时自动尝试重连。重连使用带抖动的指数退避：1s → 2s → 4s → 8s → 16s → 最大 60s，最多 5 次重试。

#### Scenario: Connection drops, reconnection succeeds — 场景：连接断开，重连成功
- **WHEN** a connection drops and reconnection succeeds within retry limit
- **THEN** the connection is restored and pending requests can proceed
- **当**连接断开且在重试限制内重连成功时
- **那么**连接恢复，待处理请求可以继续

#### Scenario: Reconnection exhausted — 场景：重连耗尽
- **WHEN** all 5 reconnection attempts fail
- **THEN** the connection is marked as `failed`, the circuit breaker opens, and subsequent requests return `MCP_CONNECTION_FAILED`
- **当**所有 5 次重连尝试都失败时
- **那么**连接标记为 `failed`，断路器打开，后续请求返回 `MCP_CONNECTION_FAILED`

#### Scenario: Requests during reconnection — 场景：重连期间的请求
- **WHEN** a request is made while reconnection is in progress
- **THEN** the system returns `MCP_RECONNECTING` error immediately (does not block)
- **当**重连进行中时有请求发出时
- **那么**系统立即返回 `MCP_RECONNECTING` 错误（不阻塞）

### Requirement: Per-request timeout — 需求：按请求超时
The system SHALL enforce a configurable per-request timeout (default 30s). On timeout, the request is cancelled and the connection is released back to the pool.

系统应强制执行可配置的按请求超时（默认 30s）。超时时，请求被取消，连接释放回池。

#### Scenario: Request completes within timeout — 场景：请求在超时内完成
- **WHEN** a tool call completes before the timeout
- **THEN** the result is returned and the connection is released
- **当**工具调用在超时前完成时
- **那么**返回结果并释放连接

#### Scenario: Request exceeds timeout — 场景：请求超时
- **WHEN** a tool call exceeds the per-request timeout
- **THEN** the request is cancelled, the connection is released, and `MCP_REQUEST_TIMEOUT` is returned
- **当**工具调用超过按请求超时时
- **那么**请求被取消，连接被释放，返回 `MCP_REQUEST_TIMEOUT`

### Requirement: Health checks — 需求：健康检查
The system SHALL perform periodic health checks on connected MCP servers by calling `list_tools` (read-only, no side effects). Default interval is 30 seconds. Three consecutive failures mark the connection as unhealthy.

系统应通过调用 `list_tools`（只读，无副作用）对已连接的 MCP 服务器执行定期健康检查。默认间隔为 30 秒。三次连续失败将连接标记为不健康。

#### Scenario: Healthy connection — 场景：健康连接
- **WHEN** health check `list_tools` succeeds
- **THEN** the connection remains marked as healthy
- **当**健康检查 `list_tools` 成功时
- **那么**连接保持标记为健康

#### Scenario: Unhealthy connection — 场景：不健康连接
- **WHEN** three consecutive health checks fail
- **THEN** the connection is marked unhealthy and not assigned new requests
- **当**三次连续健康检查失败时
- **那么**连接被标记为不健康，不分配新请求

#### Scenario: Unhealthy connection recovers — 场景：不健康连接恢复
- **WHEN** a subsequent health check succeeds on an unhealthy connection
- **THEN** the connection is marked healthy again and available for new requests
- **当**后续健康检查在不健康连接上成功时
- **那么**连接再次标记为健康并可用于新请求

### Requirement: Circuit breaker — 需求：断路器
The system SHALL implement a circuit breaker per MCP server. After 5 consecutive failures (tool call failures or health check failures), the circuit opens. After 30 seconds, a half-open probe is sent. If the probe succeeds, the circuit closes; if it fails, the circuit reopens.

系统应为每个 MCP 服务器实现断路器。5 次连续失败（工具调用失败或健康检查失败）后，电路打开。30 秒后，发送半开探测。如果探测成功，电路关闭；如果失败，电路重新打开。

#### Scenario: Circuit opens on consecutive failures — 场景：连续失败导致电路打开
- **WHEN** 5 consecutive failures occur on a server
- **THEN** the circuit opens and all subsequent requests are rejected with `MCP_CIRCUIT_OPEN`
- **当**服务器发生 5 次连续失败时
- **那么**电路打开，所有后续请求被拒绝并返回 `MCP_CIRCUIT_OPEN`

#### Scenario: Half-open probe succeeds — 场景：半开探测成功
- **WHEN** the 30-second half-open timer expires and the probe succeeds
- **THEN** the circuit closes and requests are allowed
- **当**30 秒半开定时器到期且探测成功时
- **那么**电路关闭，允许请求

#### Scenario: Half-open probe fails — 场景：半开探测失败
- **WHEN** the half-open probe fails
- **THEN** the circuit reopens and the 30-second timer restarts
- **当**半开探测失败时
- **那么**电路重新打开，30 秒定时器重新启动

### Requirement: Tool discovery caching — 需求：工具发现缓存
The system SHALL cache `tools/list` results per server with a configurable TTL (default 5 minutes). On cache miss, a single-flight refresh is triggered — concurrent requests wait for the first refresh to complete rather than sending multiple `list_tools` calls.

系统应缓存每台服务器的 `tools/list` 结果，使用可配置的 TTL（默认 5 分钟）。缓存未命中时，触发 single-flight 刷新 — 并发请求等待首次刷新完成，而不是发送多个 `list_tools` 调用。

#### Scenario: Cache hit — 场景：缓存命中
- **WHEN** tool list is requested and cache is fresh
- **THEN** the cached result is returned without calling the MCP server
- **当**请求工具列表且缓存有效时
- **那么**返回缓存结果，不调用 MCP 服务器

#### Scenario: Cache miss triggers single-flight refresh — 场景：缓存未命中触发 single-flight 刷新
- **WHEN** multiple requests trigger a cache miss simultaneously
- **THEN** only one `list_tools` call is made, and all requests receive the same result
- **当**多个请求同时触发缓存未命中时
- **那么**只进行一次 `list_tools` 调用，所有请求获得相同结果

#### Scenario: Manual cache refresh — 场景：手动缓存刷新
- **WHEN** `POST /api/mcp/connections/{name}/sync` is called
- **THEN** the cache is invalidated and refreshed on the next request
- **当**调用 `POST /api/mcp/connections/{name}/sync` 时
- **那么**缓存失效，在下一次请求时刷新

### Requirement: REST API for connection management — 需求：连接管理的 REST API
The system SHALL expose REST API endpoints for MCP connection management: `GET /api/mcp/connections` (list all connections with status), `GET /api/mcp/connections/{name}` (single connection detail), `POST /api/mcp/connections/{name}/reconnect` (manual reconnect), `POST /api/mcp/connections/{name}/sync` (refresh tool cache).

系统应暴露 MCP 连接管理的 REST API 端点。

#### Scenario: List all connections — 场景：列出所有连接
- **WHEN** a client requests `GET /api/mcp/connections`
- **THEN** the system returns all registered MCP servers with connection status, pool usage, and tool count
- **当**客户端请求 `GET /api/mcp/connections` 时
- **那么**系统返回所有注册的 MCP 服务器及其连接状态、池使用率和工具数量

#### Scenario: Manual reconnect — 场景：手动重连
- **WHEN** a client requests `POST /api/mcp/connections/{name}/reconnect`
- **THEN** the system drops the current connection and creates a new one
- **当**客户端请求 `POST /api/mcp/connections/{name}/reconnect` 时
- **那么**系统断开当前连接并创建新连接

#### Scenario: Connection not found — 场景：连接未找到
- **WHEN** a client requests a connection that is not registered
- **THEN** the system returns 404
- **当**客户端请求未注册的连接时
- **那么**系统返回 404

### Requirement: Frontend MCP connection status panel — 需求：前端 MCP 连接状态面板
The system SHALL display an MCP connection status panel in the plugin detail page for plugins with `mcp://` entry. The panel shows: connection status badge (healthy=green, unhealthy=red, reconnecting=yellow, disconnected=gray), pool usage (active/idle/max), tool count, and action buttons (Reconnect, Sync Tools).

系统应在插件详情页中为具有 `mcp://` 入口的插件显示 MCP 连接状态面板。

#### Scenario: MCP plugin detail shows status panel — 场景：MCP 插件详情显示状态面板
- **WHEN** an administrator views a plugin detail page for an MCP-type plugin
- **THEN** the page displays the connection status panel with current status and pool metrics
- **当**管理员查看 MCP 类型插件的详情页时
- **那么**页面显示连接状态面板及当前状态和池指标

#### Scenario: Non-MCP plugin does not show status panel — 场景：非 MCP 插件不显示状态面板
- **WHEN** an administrator views a plugin detail page for a non-MCP plugin
- **THEN** the connection status panel is not displayed
- **当**管理员查看非 MCP 插件的详情页时
- **那么**不显示连接状态面板
