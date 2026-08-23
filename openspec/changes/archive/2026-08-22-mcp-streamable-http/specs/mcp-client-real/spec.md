## MODIFIED Requirements

### Requirement: Real MCP Client using official SDK
The system SHALL provide a production MCP Client using the official `mcp` Python SDK (version ≥ 2.0) that supports Streamable HTTP and stdio transports for connecting to external MCP servers. The client SHALL negotiate the protocol era with the remote server automatically: probing `server/discover` first and falling back to the `initialize` handshake when the server does not advertise a modern revision. The client SHALL speak MCP `2026-07-28` as its preferred protocol version when the server supports it.

The public surface SHALL be `HecateMCPClient.connect_http(server_url)`, `connect_stdio(command, args, env=None)`, `list_tools()`, `call_tool(tool_name, arguments)`, `disconnect()`, `health_check()`, plus a `connected` property and a `protocol_version` property.

#### Scenario: Connect to remote MCP server via Streamable HTTP
- **WHEN** `HecateMCPClient.connect_http("http://remote-server:8000/mcp")` is called
- **THEN** the client opens a v2 `mcp.Client(server_url, mode='auto')` session, enters its async context manager, negotiates the server's protocol era (preferring `2026-07-28`), and becomes ready to list and call tools

#### Scenario: Connect to local MCP server via stdio
- **WHEN** `HecateMCPClient.connect_stdio(command="python", args=["server.py"], env=None)` is called
- **THEN** the client opens a v2 `mcp.Client(StdioServerParameters(command=command, args=args, env=env), mode='auto')` session and negotiates the server's protocol era over stdio

#### Scenario: List tools from connected server
- **WHEN** `client.list_tools()` is called after successful connection
- **THEN** the client returns a list of tool dicts with `name`, `description`, and `input_schema` keys (snake_case, per MCP 2.0 wire format)

#### Scenario: Call tool on connected server
- **WHEN** `client.call_tool(tool_name="search", arguments={"query": "test"})` is called
- **THEN** the client sends a `tools/call` request to the MCP server and returns the result, honoring any `InputRequiredResult` retry loop for multi-round-trip requests when the server is on the 2026-07-28 protocol era

#### Scenario: Disconnect from server
- **WHEN** `client.disconnect()` is called
- **THEN** the client exits the underlying `mcp.Client` async context manager and cleans up resources

### Requirement: MCP Client connection configuration
The system SHALL provide `MCP_CLIENT_TIMEOUT: int` (default: `30`) setting for client connection and tool call timeouts.

#### Scenario: Timeout on slow server
- **WHEN** an MCP server does not respond within `MCP_CLIENT_TIMEOUT` seconds
- **THEN** the client raises a `TimeoutError` and the calling tool receives an error response

### Requirement: HecateMCPClient applies egress filters on tool response
The `HecateMCPClient` SHALL pass MCP tool responses through a configurable egress filter chain (list of `EgressFilter` instances) before returning to the caller.

#### Scenario: Filters applied
- **WHEN** `HecateMCPClient` is constructed with `egress_filters=[dlp_filter]`
- **THEN** `call_tool()` SHALL pass the response through the filter chain

#### Scenario: No filters configured
- **WHEN** `HecateMCPClient` is constructed without `egress_filters`
- **THEN** `call_tool()` SHALL return the raw MCP response (backward compatible)

#### Scenario: First filter BLOCK stops chain
- **WHEN** first filter returns `EgressResult(action=BLOCK)`
- **THEN** subsequent filters SHALL NOT be called and `call_tool()` SHALL return the block message

#### Scenario: Audit data written to SecurityFindingModel
- **WHEN** any filter returns `audit_data`
- **THEN** each entry SHALL be written to SecurityFindingModel with `rule_name` prefixed by the filter's name (e.g., `dlp:email_audit`)

## ADDED Requirements

### Requirement: Protocol era negotiation
The client SHALL automatically select the protocol era when connecting to a remote MCP server. The default mode SHALL be `auto`: probe `server/discover` and fall back to the legacy `initialize` handshake when the server returns `-32601` (method not found) or times out on the probe.

#### Scenario: Modern server negotiation
- **WHEN** the client connects to a server that advertises `protocolVersion="2026-07-28"`
- **THEN** the client speaks the 2026-07-28 stateless protocol for subsequent requests (no `Mcp-Session-Id`, no `initialize`)

#### Scenario: Legacy server fallback
- **WHEN** the client connects to a server that does not implement `server/discover` and responds with `-32601`
- **THEN** the client performs the legacy `initialize`/`initialized` handshake and operates as a 2025-era session

#### Scenario: Protocol version exposed for diagnostics
- **WHEN** `client.protocol_version` is accessed after connection
- **THEN** it returns the negotiated version string (e.g. `"2026-07-28"` or `"2025-11-25"`) for logging, telemetry, and connection-status reporting. The value is forwarded from the underlying `mcp.Client.protocol_version` property set during `__aenter__`

### Requirement: Multi-round-trip request handling
When the server is on the 2026-07-28 protocol era and a tool call returns an `InputRequiredResult`, the client SHALL automatically drive the input-required retry loop until the tool returns a terminal result, an explicit `InputRequiredResult` with `cancel=True`, or the configured number of attempts is exceeded.

#### Scenario: Tool asks for input, client resolves
- **WHEN** `client.call_tool(...)` returns an `InputRequiredResult` with one question
- **THEN** the client surfaces the question to the configured `elicitation_handler` callback and, once answered, re-issues the original call with the answers attached, transparently to the caller

#### Scenario: Caller explicitly disallows input-required
- **WHEN** the caller passes `allow_input_required=False` to `call_tool`
- **THEN** an `InputRequiredResult` raises an `InputRequiredNotAllowedError` instead of driving the retry loop

### Requirement: ttlMs cache hints honored
The client SHALL honor `_meta["io.modelcontextprotocol/cacheHint"].ttlMs` and `cacheScope` stamps returned by `tools/list` calls by caching the result for at most `ttlMs` and respecting the indicated `cacheScope` (per-server / per-tenant / global). When the cache hint is absent, the client falls back to the existing configurable TTL (default 5 minutes, defined by `MCP_TOOL_CACHE_TTL` or the 5.4c connection-management layer).

#### Scenario: Server-supplied ttlMs applied
- **WHEN** a `tools/list` response carries `_meta.cacheHint.ttlMs = 60000`
- **THEN** the client caches the tool list for no more than 60 seconds, regardless of the default TTL

#### Scenario: cacheScope honored
- **WHEN** a `tools/list` response carries `_meta.cacheHint.cacheScope = "per-tenant"`
- **THEN** the cached entry is not shared across tenants
