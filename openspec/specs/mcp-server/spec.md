## ADDED Requirements

### Requirement: MCP Server exposes Hecate capabilities as MCP tools
The system SHALL provide an MCP Server that exposes agent, knowledge, tool, session, and conversation operations as MCP tools via Streamable HTTP transport, mounted at `/mcp` on the FastAPI application. The server SHALL speak MCP protocol version `2026-07-28` (stateless core) and SHALL advertise the protocol era via the response body's `_meta` and result envelope (e.g. `resultType`, `cacheScope`) regardless of which server instance handled the request. The server SHALL NOT require session affinity at the transport layer; any request SHALL be serviceable by any server instance behind a round-robin load balancer.

#### Scenario: MCP client discovers available tools
- **WHEN** an MCP client connects to the server and calls `tools/list`
- **THEN** the server returns a list of tools including `agent_list`, `agent_chat`, `agent_create`, `knowledge_search`, `knowledge_list`, `tool_execute`, `tool_list`, `session_create`, `session_list`, and `conversation_history`

#### Scenario: MCP server mounted on FastAPI app
- **WHEN** the FastAPI application starts with `MCP_SERVER_ENABLED=true`
- **THEN** the MCP Server ASGI app is mounted at `/mcp`, accepts Streamable HTTP POST requests carrying `MCP-Protocol-Version: 2026-07-28` and a self-describing `_meta` envelope, and rejects requests whose envelope is not a 2026-07-28 self-describing request

#### Scenario: MCP server disabled
- **WHEN** `MCP_SERVER_ENABLED=false` (default)
- **THEN** no MCP endpoint is mounted and the application behaves as before

#### Scenario: Stateless handling across instances
- **WHEN** two consecutive requests from the same client land on different server instances behind a load balancer
- **THEN** both requests succeed independently without any cross-instance coordination (no shared session store required)

### Requirement: Agent runtime tools
The system SHALL expose the following agent runtime MCP tools:
- `agent_chat(session_id: str, message: str)`: Send a message to an active session, invoking WorkflowExecutionService, and return the agent response
- `session_create(agent_id: str)`: Create a new Hecate session for an agent, returning `session_id`
- `session_list(agent_id: str | None)`: List active sessions, optionally filtered by agent
- `session_resume(session_id: str, message: str)`: Resume an interrupted session with a new message
- `conversation_history(conversation_id: str)`: Retrieve conversation message history

#### Scenario: Create session and chat
- **WHEN** a client calls `session_create(agent_id="<uuid>")`
- **THEN** the server creates a `SessionModel` with `agent_id` and `status="active"`, and returns `{"session_id": "<new-uuid>", "status": "active"}`
- **WHEN** the client then calls `agent_chat(session_id="<new-uuid>", message="Hello")`
- **THEN** the server invokes `WorkflowExecutionService.execute()` with the session context and returns the agent's response as text content

#### Scenario: Chat with non-existent session
- **WHEN** a client calls `agent_chat(session_id="<invalid>", message="Hello")`
- **THEN** the server returns an error: `{"error": "Session not found"}`

### Requirement: Agent CRUD tools
The system SHALL expose the following agent management MCP tools:
- `agent_list(workspace_id: str | None)`: List agents with pagination
- `agent_create(name, persona, model_config, mode, tools, knowledge_base_ids)`: Create a new agent
- `agent_update(agent_id: str, fields: dict[str, Any])`: Update agent fields via a single fields mapping (fastmcp 4 does not support `**kwargs` tool parameters)
- `agent_delete(agent_id: str)`: Soft-delete an agent

#### Scenario: Create agent via MCP tool
- **WHEN** a client calls `agent_create(name="Test Agent", model_config={"model": "gpt-4o"}, mode="chat")`
- **THEN** the server creates an `AgentModel` in the database and returns the agent's UUID and metadata

#### Scenario: List agents
- **WHEN** a client calls `agent_list()`
- **THEN** the server returns a list of all non-deleted agents with id, name, mode, and model_config

#### Scenario: Update agent fields
- **WHEN** a client calls `agent_update(agent_id="<uuid>", fields={"persona": "You are helpful", "model_config": {"model": "gpt-4o"}})`
- **THEN** the server updates the listed fields on the matching `AgentModel` and returns the updated metadata

### Requirement: Knowledge base tools
The system SHALL expose the following knowledge base MCP tools:
- `knowledge_list()`: List all knowledge bases
- `knowledge_search(kb_id, query, limit, mode)`: Search a knowledge base using dense/sparse/hybrid search
- `knowledge_create(name, description, embedding_model, chunk_strategy)`: Create a new knowledge base
- `knowledge_ingest(kb_id, content, metadata)`: Ingest text content into a knowledge base

#### Scenario: Search knowledge base
- **WHEN** a client calls `knowledge_search(kb_id="<uuid>", query="machine learning", limit=5, mode="hybrid")`
- **THEN** the server invokes `KnowledgeBaseService.search()` and returns a list of matching chunks with content, score, and metadata

#### Scenario: Ingest text into knowledge base
- **WHEN** a client calls `knowledge_ingest(kb_id="<uuid>", content="Some document text...")`
- **THEN** the server invokes `KnowledgeBaseService.ingest_document_text()` and returns the ingestion result

### Requirement: Tool execution tools
The system SHALL expose the following tool MCP tools:
- `tool_list(source: str | None)`: List registered tools, optionally filtered by source
- `tool_execute(tool_name, arguments)`: Execute a registered tool by name
- `tool_create(name, description, parameters, source)`: Register a new tool

#### Scenario: Execute a builtin tool
- **WHEN** a client calls `tool_execute(tool_name="web_search", arguments={"query": "Python async"})`
- **THEN** the server invokes `ToolRegistry.execute()` and returns the tool's result

#### Scenario: List tools by source
- **WHEN** a client calls `tool_list(source="builtin")`
- **THEN** the server returns only builtin tools with name, description, and parameters

### Requirement: MCP resources for catalog discovery
The system SHALL expose MCP resources:
- `agent://list`: Returns agent catalog as structured data
- `knowledge://list`: Returns knowledge base catalog as structured data
- `tool://list`: Returns tool catalog as structured data

#### Scenario: Client reads agent catalog resource
- **WHEN** an MCP client calls `resources/read` with URI `agent://list`
- **THEN** the server returns a JSON list of all agents with id, name, mode, and model_config

### Requirement: MCP prompts for system templates
The system SHALL expose MCP prompts:
- `system-template://{prompt_id}`: Returns a stored prompt template by ID

#### Scenario: Client retrieves prompt template
- **WHEN** an MCP client calls `prompts/get` with name `system-template://<prompt_id>`
- **THEN** the server returns the prompt template content from the Prompt CRUD system

### Requirement: MCP Server auth via API key
The system SHALL validate MCP tool calls using API key authentication. The `MCP_AUTH_TYPE` setting controls the auth mode: `api_key` (default), `jwt`, or `none`.

#### Scenario: Valid API key
- **WHEN** a client includes a valid API key in the `x-api-key` header of an MCP request
- **THEN** the tool executes normally

#### Scenario: Invalid API key
- **WHEN** a client includes an invalid API key or no key when `MCP_AUTH_TYPE=api_key`
- **THEN** the server returns an error response and the tool does not execute

### Requirement: MCP Server configuration
The system SHALL provide the following configuration settings in `Settings`:
- `MCP_SERVER_ENABLED: bool` (default: `False`) — enable/disable MCP server
- `MCP_SERVER_HOST: str` (default: `"0.0.0.0"`) — server bind host
- `MCP_SERVER_PORT: int` (default: `8000`) — server bind port
- `MCP_AUTH_TYPE: str` (default: `"api_key"`) — auth mode for MCP requests
- `MCP_TRANSPORT: str` (default: `"http"`) — transport mode (`http` for Streamable HTTP)

#### Scenario: Default configuration disables MCP
- **WHEN** no MCP-related env vars are set
- **THEN** `MCP_SERVER_ENABLED=False` and the MCP server does not start

### Requirement: Server discovery and capability advertisement
The server SHALL respond to `server/discover` requests with its identity (`serverInfo.name`, `serverInfo.version`), advertised protocol version `2026-07-28`, and its capability set (tools / resources / prompts registry). A client that calls `tools/list`, `resources/list`, or `prompts/list` without a prior discovery SHALL receive the same response.

#### Scenario: server/discover returns identity and capabilities
- **WHEN** a client calls `server/discover`
- **THEN** the response includes `serverInfo.name="hecate-mcp-server"`, `serverInfo.version="<release version>"`, `protocolVersion="2026-07-28"`, and a `capabilities` object enumerating the registered tools, resources, and prompts

#### Scenario: Client may skip server/discover
- **WHEN** a client proceeds directly to `tools/list` without calling `server/discover`
- **THEN** the server returns the same tool list as it would have returned after discovery

### Requirement: Header-based routing surface
The server SHALL accept Streamable HTTP POST requests carrying `Mcp-Method` and (where applicable) `Mcp-Name` headers that mirror the JSON-RPC `method` and `params.name` / `params.uri` fields (SEP-2243). The server SHALL reject requests where the standard header values disagree with the body envelope, returning HTTP 400 with a JSON-RPC error code `-32020` (`HeaderMismatch`).

#### Scenario: Header/body agreement required
- **WHEN** a client sends `Mcp-Method: tools/call` but the JSON-RPC body has `"method": "resources/read"`
- **THEN** the server responds with HTTP 400 and a JSON-RPC error with code `-32020`

#### Scenario: Mcp-Name validated for tools/call
- **WHEN** a client sends a `tools/call` with `Mcp-Name` header that does not match `params.name` in the body
- **THEN** the server responds with HTTP 400 and a JSON-RPC error with code `-32020`

#### Scenario: Missing Mcp-Method header rejected
- **WHEN** a client sends a 2026-07-28 Streamable HTTP POST without an `Mcp-Method` header
- **THEN** the server responds with HTTP 400 and a JSON-RPC error with code `-32020`

### Requirement: Cache hints on list results
The server MAY stamp `ttlMs` and `cacheScope` hints in the `_meta` of `tools/list`, `resources/list`, and `prompts/list` responses. When a hint is present, the hint SHALL describe the minimum freshness interval and the cache scope class (per-server / per-tenant / global).

#### Scenario: tools/list declares cache hint
- **WHEN** a client calls `tools/list`
- **THEN** the response MAY include `result._meta["io.modelcontextprotocol/cacheHint"].ttlMs` (number) and `result._meta["io.modelcontextprotocol/cacheHint"].cacheScope` (one of `per-server`, `per-tenant`, `global`); Hecate defaults to no hint (consumers fall back to their own TTL)

### Requirement: Stateless request handling
The server SHALL NOT depend on `Mcp-Session-Id` headers or transport-level session state. All stateful semantics (`session_id`, conversation history, agent context) live in Hecate application-layer models keyed by explicit identifiers passed in tool arguments.

#### Scenario: Mcp-Session-Id header is ignored
- **WHEN** a client sends a request with an arbitrary `Mcp-Session-Id` header
- **THEN** the server does not validate, store, or correlate the value; the request is processed on its own `_meta` contents and tool arguments alone

#### Scenario: No session store required for horizontal scaling
- **WHEN** Hecate is deployed as multiple MCP server replicas behind a round-robin load balancer
- **THEN** any replica can serve any request independently; replicas do not share a session store

### Requirement: Body size limit on Streamable HTTP
The server SHALL reject Streamable HTTP POST bodies larger than 4 MiB with HTTP 413. Tool handlers (`agent_chat`, `knowledge_ingest`, etc.) that need to ingest larger payloads SHALL accept a multi-part identifier and use a separate upload pathway rather than overloading the MCP tool call body.

#### Scenario: Oversize body rejected
- **WHEN** a client sends a `POST /mcp` request with a body larger than 4 MiB
- **THEN** the server responds with HTTP 413 and does not invoke any tool handler
