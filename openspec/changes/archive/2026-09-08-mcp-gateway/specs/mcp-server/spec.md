## MODIFIED Requirements

### Requirement: MCP Server exposes Hecate capabilities as MCP tools
The system SHALL provide an MCP Server that exposes agent, knowledge, tool, session, and conversation operations as MCP tools via Streamable HTTP transport, mounted at `/mcp` on the FastAPI application. The server SHALL speak MCP protocol version `2026-07-28` (stateless core) and SHALL advertise the protocol era via the response body's `_meta` and result envelope (e.g. `resultType`, `cacheScope`) regardless of which server instance handled the request. The server SHALL NOT require session affinity at the transport layer; any request SHALL be serviceable by any server instance behind a round-robin load balancer. When `GATEWAY_ENABLED=true`, the server's tool catalog SHALL additionally include the gateway's federated tools (rest-converted and MCP-target tools) filtered by the caller's workspace identity; when `GATEWAY_ENABLED=false` (default), the catalog SHALL be exactly the first-party tool set. First-party tool behavior SHALL NOT change when the gateway is enabled.

#### Scenario: MCP client discovers available tools
- **WHEN** an MCP client connects to the server and calls `tools/list`
- **THEN** the server returns a list of tools including `agent_list`, `agent_chat`, `agent_create`, `knowledge_search`, `knowledge_list`, `tool_execute`, `tool_list`, `session_create`, `session_list`, and `conversation_history`

#### Scenario: Gateway-enabled catalog is caller-scoped and federated
- **WHEN** `GATEWAY_ENABLED=true` and a workspace-scoped caller calls `tools/list`
- **THEN** the response contains the first-party tools plus only the federated tools owned by the caller's workspace

#### Scenario: Gateway-disabled catalog is unchanged
- **WHEN** `GATEWAY_ENABLED=false` and any authenticated caller calls `tools/list`
- **THEN** the response contains exactly the first-party tool set, identical to pre-gateway behavior

#### Scenario: MCP server mounted on FastAPI app
- **WHEN** the FastAPI application starts with `MCP_SERVER_ENABLED=true`
- **THEN** the MCP Server ASGI app is mounted at `/mcp`, accepts Streamable HTTP POST requests carrying `MCP-Protocol-Version: 2026-07-28` and a self-describing `_meta` envelope, and rejects requests whose envelope is not a 2026-07-28 self-describing request

#### Scenario: MCP server disabled
- **WHEN** `MCP_SERVER_ENABLED=false` (default)
- **THEN** no MCP endpoint is mounted and the application behaves as before

#### Scenario: Stateless handling across instances
- **WHEN** two consecutive requests from the same client land on different server instances behind a load balancer
- **THEN** both requests succeed independently without any cross-instance coordination (no shared session store required)

### Requirement: MCP Server auth via API key
The system SHALL validate MCP requests using API key authentication. The `MCP_AUTH_TYPE` setting controls the auth mode: `api_key` (default), `jwt`, or `none`. Under `api_key`, the server SHALL accept both legacy global API keys (environment-configured) and database-backed keys managed by the API key service; database-backed keys SHALL be matched by hash and MUST be active and unexpired. A workspace-scoped database key SHALL resolve the caller's workspace identity and attach it to the request context; a legacy global or system-scope key SHALL resolve to platform scope. Authentication failure SHALL reject the request before any tool logic runs.

#### Scenario: Valid API key
- **WHEN** a client includes a valid API key in the `x-api-key` header of an MCP request
- **THEN** the request is authenticated and the tool executes normally

#### Scenario: Workspace-scoped key resolves caller identity
- **WHEN** a client authenticates with an active, unexpired workspace-scoped database key for workspace A
- **THEN** the request context carries workspace A's identity, which downstream catalog filtering and authorization use

#### Scenario: Inactive or expired database key rejected
- **WHEN** a client presents a database key that is revoked or expired
- **THEN** the server returns an error response and the tool does not execute

#### Scenario: Invalid API key
- **WHEN** a client includes an invalid API key or no key when `MCP_AUTH_TYPE=api_key`
- **THEN** the server returns an error response and the tool does not execute
