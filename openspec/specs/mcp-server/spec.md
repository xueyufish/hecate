## Requirements

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

`agent_create`、`agent_update` 与 `agent_delete` 属于变更类动作,SHALL 要求调用者持有显式 workspace 的 editor 及以上角色;viewer 角色与受限身份(无 workspace 成员资格)SHALL 被拒绝且不产生任何写入。拒绝结果 SHALL 与 REST 入口对同一操作的授权结果一致(见 `action-authorization` 能力)。

#### Scenario: Create agent via MCP tool
- **WHEN** an editor-role client calls `agent_create(name="Test Agent", model_config={"model": "gpt-4o"}, mode="chat")`
- **THEN** the server creates an `AgentModel` in the caller's workspace and returns the agent's UUID and metadata

#### Scenario: List agents
- **WHEN** a client calls `agent_list()`
- **THEN** the server returns a list of all non-deleted agents visible to the caller's workspace with id, name, mode, and model_config

#### Scenario: Update agent fields
- **WHEN** an editor-role client calls `agent_update(agent_id="<uuid>", fields={"persona": "You are helpful", "model_config": {"model": "gpt-4o"}})`
- **THEN** the server updates the listed fields on the matching `AgentModel` and returns the updated metadata

#### Scenario: Viewer cannot create or modify agents
- **WHEN** a viewer-role client calls `agent_create`、`agent_update` 或 `agent_delete`
- **THEN** the server rejects the call with an explicit authorization error before any database write, and the agent catalog is unchanged

#### Scenario: Restricted identity cannot write agents
- **WHEN** an authenticated identity without a workspace membership (role is None) calls `agent_create`
- **THEN** the server rejects the call with an explicit authorization error, and no agent is created in any workspace including the shared zero workspace

### Requirement: Knowledge base tools
The system SHALL expose the following knowledge base MCP tools:
- `knowledge_list()`: List all knowledge bases
- `knowledge_search(kb_id, query, limit, mode)`: Search a knowledge base using dense/sparse/hybrid search
- `knowledge_create(name, description, embedding_model, chunk_strategy)`: Create a new knowledge base
- `knowledge_ingest(kb_id, content, metadata)`: Ingest text content into a knowledge base

`knowledge_create` 与 `knowledge_ingest` 属于变更类动作,SHALL 要求 editor 及以上角色;拒绝行为与 Agent CRUD tools 一致。

#### Scenario: Search knowledge base
- **WHEN** a client calls `knowledge_search(kb_id="<uuid>", query="machine learning", limit=5, mode="hybrid")`
- **THEN** the server invokes `KnowledgeBaseService.search()` and returns a list of matching chunks with content, score, and metadata

#### Scenario: Ingest text into knowledge base
- **WHEN** an editor-role client calls `knowledge_ingest(kb_id="<uuid>", content="Some document text...")`
- **THEN** the server invokes `KnowledgeBaseService.ingest_document_text()` and returns the ingestion result

#### Scenario: Viewer cannot create knowledge bases
- **WHEN** a viewer-role client calls `knowledge_create`
- **THEN** the server rejects the call with an explicit authorization error before any database write

### Requirement: Tool execution tools
The system SHALL expose the following tool MCP tools:
- `tool_list(source: str | None)`: List registered tools, optionally filtered by source
- `tool_execute(tool_name, arguments)`: Execute a registered tool by name
- `tool_create(name, description, parameters, source)`: Register a new tool

`tool_execute` SHALL 在服务端从已验证的调用者身份派生执行上下文(租户与文件资源根),不接受调用者提供的工作区根或身份;文件类工具的读写 SHALL 限定在该调用者 workspace 的资源根内,不同 workspace 的文件互不可见,解析后指向根外的路径(包括符号链接) SHALL 在读写前拒绝。用于授权检查的工具身份、来源与可信元数据 SHALL 与最终执行的工具定义/记录一致;同名记录造成歧义、来源不一致或风险元数据缺失时 SHALL 在副作用前拒绝。`approval_required=true`、`risk_level=HIGH`、缺少所需 agent/session 上下文或无法建立服务端授权绑定的工具 SHALL 显式拒绝。不得因为工具名称可解析或来自 builtin/custom/MCP/REST 而默认授权;只有具备明确资源范围及可信授权元数据的工具可在本入口执行。受限身份与无 workspace 身份 SHALL 在执行前被拒绝。`tool_create` 属于变更类动作,SHALL 要求 editor 及以上角色,且新工具 SHALL 落在调用者的显式 workspace,不得回退写入共享 zero workspace。

#### Scenario: Execute a builtin tool
- **WHEN** an editor-role client calls `tool_execute` for a uniquely resolved tool whose trusted definition permits this entry and whose required context is available
- **THEN** the server executes that same resolved definition with server-derived context and returns the tool's result

#### Scenario: File operations are isolated per workspace
- **WHEN** clients from two different workspaces call `tool_execute(tool_name="write_file", ...)` followed by `tool_execute(tool_name="read_file", ...)` with the same relative path
- **THEN** each workspace's `read_file` observes only its own write; neither workspace can read the other's file

#### Scenario: Unapproved high-risk tool rejected before side effects
- **WHEN** a client calls `tool_execute` for a tool marked `approval_required=true` 或 `risk_level=HIGH`
- **THEN** the server rejects the call with an explicit error stating the tool is not executable on this entry, and no side effect occurs

#### Scenario: Ambiguous tool identity rejected before execution
- **WHEN** two accessible tool records share the requested name or the definition checked for authorization differs from the definition selected for execution
- **THEN** the server rejects the call before any tool handler or external adapter is invoked

#### Scenario: Tool without an authorizable execution context is rejected
- **WHEN** a tool requires agent, session, approval, resource, or provenance context that this entry cannot establish and verify
- **THEN** the server returns an explicit error before any side effect

#### Scenario: Workspace file root cannot be escaped through a symlink
- **WHEN** a file tool follows a path whose resolved target is outside the caller's workspace root
- **THEN** the server rejects the operation before reading or writing the target

#### Scenario: List tools by source
- **WHEN** a client calls `tool_list(source="builtin")`
- **THEN** the server returns only builtin tools with name, description, and parameters

#### Scenario: Viewer cannot register tools
- **WHEN** a viewer-role client calls `tool_create`
- **THEN** the server rejects the call with an explicit authorization error before any database write

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

### Requirement: Fail-closed execution without transport identity
当传输层未提供已验证身份时(包括显式配置 `MCP_AUTH_TYPE=none` 的开发逃生门),MCP 工具 SHALL 失败关闭:变更类与执行类工具一律不可用并返回明确错误,SHALL NOT 回退到任何全局或匿名权限。默认配置 SHALL 强制传输认证。

#### Scenario: No-auth mode cannot execute tools
- **WHEN** the deployment sets `MCP_AUTH_TYPE=none` and a client calls `tool_execute` 或任一变更类工具
- **THEN** the call fails with an explicit error and no tool execution or database mutation occurs

#### Scenario: Default configuration enforces transport auth
- **WHEN** the deployment uses the default `MCP_AUTH_TYPE` and a request carries no valid credential
- **THEN** the transport rejects the request with 401 before any tool is invoked