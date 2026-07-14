## ADDED Requirements

### Requirement: MCP server registry
The system SHALL maintain a registry of MCP servers with their capabilities (tools/resources/prompts). Servers register when their plugin is enabled, unregister when disabled. The registry supports capability-based discovery — clients can query which servers provide specific tools.

#### Scenario: Server registered on plugin enable
- **WHEN** a plugin with `entry: mcp://endpoint` is enabled
- **THEN** the system registers the MCP server in the registry without connecting

#### Scenario: Server unregistered on plugin disable
- **WHEN** an MCP server plugin is disabled
- **THEN** the system unregisters the server, closes any active connections, and clears the tool cache

#### Scenario: Capability discovery
- **WHEN** a client queries available tools across all registered MCP servers
- **THEN** the system returns cached tool lists from all connected servers

### Requirement: Lazy connection with session reuse
The system SHALL create MCP connections lazily — on the first tool call to a registered server, not on registration. Subsequent calls to the same server reuse the existing connection/session.

#### Scenario: First tool call creates connection
- **WHEN** a tool call is made to a registered-but-not-connected MCP server
- **THEN** the system creates a connection (two-step probe), executes the tool, and keeps the session for reuse

#### Scenario: Subsequent calls reuse connection
- **WHEN** a tool call is made to an already-connected MCP server
- **THEN** the system reuses the existing session without creating a new connection

### Requirement: Two-step connection probe with error codes
The system SHALL perform a two-step probe before establishing MCP connections: (1) TCP reachability check, (2) MCP SDK protocol handshake. Each failure mode SHALL return a structured error code for diagnosis.

#### Scenario: TCP probe succeeds, SDK handshake succeeds
- **WHEN** both TCP and SDK probes pass
- **THEN** the connection is established successfully

#### Scenario: DNS resolution failure
- **WHEN** the MCP server URL cannot be resolved
- **THEN** the system returns error code `MCP_DNS_FAILURE` with the hostname

#### Scenario: Connection timeout
- **WHEN** the TCP probe times out
- **THEN** the system returns error code `MCP_CONNECT_TIMEOUT` with the timeout value

#### Scenario: Port closed
- **WHEN** TCP connection is refused
- **THEN** the system returns error code `MCP_PORT_CLOSED` with the port number

#### Scenario: Path not found
- **WHEN** TCP connects but HTTP returns 404
- **THEN** the system returns error code `MCP_PATH_NOT_FOUND` with the URL path

#### Scenario: SSL certificate error
- **WHEN** SSL handshake fails
- **THEN** the system returns error code `MCP_SSL_ERROR` with certificate details

### Requirement: Connection pooling
The system SHALL maintain a per-server connection pool for HTTP connections (configurable min/max sessions). stdio connections use a single connection (not poolable). Pool supports borrow-with-timeout and return semantics.

#### Scenario: Borrow available connection
- **WHEN** a tool call requests a connection and an idle one is available
- **THEN** the system lends the idle connection immediately

#### Scenario: Pool exhausted
- **WHEN** all connections are in use and pool is at max capacity
- **THEN** the request waits up to `borrow_timeout` (default 5s), then fails with `MCP_POOL_EXHAUSTED`

#### Scenario: New connection created on demand
- **WHEN** no idle connection is available but pool is below max
- **THEN** the system creates a new connection and lends it

### Requirement: Automatic reconnection with exponential backoff
The system SHALL automatically attempt to reconnect when a connection drops. Reconnection uses exponential backoff with jitter: 1s → 2s → 4s → 8s → 16s → max 60s, maximum 5 retries.

#### Scenario: Connection drops, reconnection succeeds
- **WHEN** a connection drops and reconnection succeeds within retry limit
- **THEN** the connection is restored and pending requests can proceed

#### Scenario: Reconnection exhausted
- **WHEN** all 5 reconnection attempts fail
- **THEN** the connection is marked as `failed`, the circuit breaker opens, and subsequent requests return `MCP_CONNECTION_FAILED`

#### Scenario: Requests during reconnection
- **WHEN** a request is made while reconnection is in progress
- **THEN** the system returns `MCP_RECONNECTING` error immediately (does not block)

### Requirement: Per-request timeout
The system SHALL enforce a configurable per-request timeout (default 30s). On timeout, the request is cancelled and the connection is released back to the pool.

#### Scenario: Request completes within timeout
- **WHEN** a tool call completes before the timeout
- **THEN** the result is returned and the connection is released

#### Scenario: Request exceeds timeout
- **WHEN** a tool call exceeds the per-request timeout
- **THEN** the request is cancelled, the connection is released, and `MCP_REQUEST_TIMEOUT` is returned

### Requirement: Health checks
The system SHALL perform periodic health checks on connected MCP servers by calling `list_tools` (read-only, no side effects). Default interval is 30 seconds. Three consecutive failures mark the connection as unhealthy.

#### Scenario: Healthy connection
- **WHEN** health check `list_tools` succeeds
- **THEN** the connection remains marked as healthy

#### Scenario: Unhealthy connection
- **WHEN** three consecutive health checks fail
- **THEN** the connection is marked unhealthy and not assigned new requests

#### Scenario: Unhealthy connection recovers
- **WHEN** a subsequent health check succeeds on an unhealthy connection
- **THEN** the connection is marked healthy again and available for new requests

### Requirement: Circuit breaker
The system SHALL implement a circuit breaker per MCP server. After 5 consecutive failures (tool call failures or health check failures), the circuit opens. After 30 seconds, a half-open probe is sent. If the probe succeeds, the circuit closes; if it fails, the circuit reopens.

#### Scenario: Circuit opens on consecutive failures
- **WHEN** 5 consecutive failures occur on a server
- **THEN** the circuit opens and all subsequent requests are rejected with `MCP_CIRCUIT_OPEN`

#### Scenario: Half-open probe succeeds
- **WHEN** the 30-second half-open timer expires and the probe succeeds
- **THEN** the circuit closes and requests are allowed

#### Scenario: Half-open probe fails
- **WHEN** the half-open probe fails
- **THEN** the circuit reopens and the 30-second timer restarts

### Requirement: Tool discovery caching
The system SHALL cache `tools/list` results per server with a configurable TTL (default 5 minutes). On cache miss, a single-flight refresh is triggered — concurrent requests wait for the first refresh to complete rather than sending multiple `list_tools` calls.

#### Scenario: Cache hit
- **WHEN** tool list is requested and cache is fresh
- **THEN** the cached result is returned without calling the MCP server

#### Scenario: Cache miss triggers single-flight refresh
- **WHEN** multiple requests trigger a cache miss simultaneously
- **THEN** only one `list_tools` call is made, and all requests receive the same result

#### Scenario: Manual cache refresh
- **WHEN** `POST /api/mcp/connections/{name}/sync` is called
- **THEN** the cache is invalidated and refreshed on the next request

### Requirement: REST API for connection management
The system SHALL expose REST API endpoints for MCP connection management: `GET /api/mcp/connections` (list all connections with status), `GET /api/mcp/connections/{name}` (single connection detail), `POST /api/mcp/connections/{name}/reconnect` (manual reconnect), `POST /api/mcp/connections/{name}/sync` (refresh tool cache).

#### Scenario: List all connections
- **WHEN** a client requests `GET /api/mcp/connections`
- **THEN** the system returns all registered MCP servers with their connection status, pool usage, and tool count

#### Scenario: Manual reconnect
- **WHEN** a client requests `POST /api/mcp/connections/{name}/reconnect`
- **THEN** the system drops the current connection and creates a new one

#### Scenario: Connection not found
- **WHEN** a client requests a connection that is not registered
- **THEN** the system returns 404

### Requirement: Frontend MCP connection status panel
The system SHALL display an MCP connection status panel in the plugin detail page for plugins with `mcp://` entry. The panel shows: connection status badge (healthy=green, unhealthy=red, reconnecting=yellow, disconnected=gray), pool usage (active/idle/max), tool count, and action buttons (Reconnect, Sync Tools).

#### Scenario: MCP plugin detail shows status panel
- **WHEN** an administrator views a plugin detail page for an MCP-type plugin
- **THEN** the page displays the connection status panel with current status and pool metrics

#### Scenario: Non-MCP plugin does not show status panel
- **WHEN** an administrator views a plugin detail page for a non-MCP plugin
- **THEN** the connection status panel is not displayed
