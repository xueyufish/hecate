## ADDED Requirements

### Requirement: MCP Server exposes Hecate capabilities as MCP tools
The system SHALL provide an MCP Server using `fastmcp` that exposes agent, knowledge, tool, session, and conversation operations as MCP tools via Streamable HTTP transport, mounted at `/mcp` on the FastAPI application.

#### Scenario: MCP client discovers available tools
- **WHEN** an MCP client connects to the server and calls `tools/list`
- **THEN** the server returns a list of tools including `agent_list`, `agent_chat`, `agent_create`, `knowledge_search`, `knowledge_list`, `tool_execute`, `tool_list`, `session_create`, `session_list`, and `conversation_history`

#### Scenario: MCP server mounted on FastAPI app
- **WHEN** the FastAPI application starts with `MCP_SERVER_ENABLED=true`
- **THEN** the MCP Server ASGI app is mounted at `/mcp` and accepts Streamable HTTP requests

#### Scenario: MCP server disabled
- **WHEN** `MCP_SERVER_ENABLED=false` (default)
- **THEN** no MCP endpoint is mounted and the application behaves as before

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
- `agent_create(name: str, persona: str | None, model_config: dict, mode: str, tools: list | None, knowledge_base_ids: list | None)`: Create a new agent
- `agent_update(agent_id: str, **fields)`: Update agent fields
- `agent_delete(agent_id: str)`: Soft-delete an agent

#### Scenario: Create agent via MCP tool
- **WHEN** a client calls `agent_create(name="Test Agent", model_config={"model": "gpt-4o"}, mode="chat")`
- **THEN** the server creates an `AgentModel` in the database and returns the agent's UUID and metadata

#### Scenario: List agents
- **WHEN** a client calls `agent_list()`
- **THEN** the server returns a list of all non-deleted agents with id, name, mode, and model_config

### Requirement: Knowledge base tools
The system SHALL expose the following knowledge base MCP tools:
- `knowledge_list()`: List all knowledge bases
- `knowledge_search(kb_id: str, query: str, limit: int, mode: str)`: Search a knowledge base using dense/sparse/hybrid search
- `knowledge_create(name: str, description: str, embedding_model: str, chunk_strategy: str)`: Create a new knowledge base
- `knowledge_ingest(kb_id: str, content: str, metadata: dict | None)`: Ingest text content into a knowledge base

#### Scenario: Search knowledge base
- **WHEN** a client calls `knowledge_search(kb_id="<uuid>", query="machine learning", limit=5, mode="hybrid")`
- **THEN** the server invokes `KnowledgeBaseService.search()` and returns a list of matching chunks with content, score, and metadata

#### Scenario: Ingest text into knowledge base
- **WHEN** a client calls `knowledge_ingest(kb_id="<uuid>", content="Some document text...")`
- **THEN** the server invokes `KnowledgeBaseService.ingest_document_text()` and returns the ingestion result

### Requirement: Tool execution tools
The system SHALL expose the following tool MCP tools:
- `tool_list(source: str | None)`: List registered tools, optionally filtered by source
- `tool_execute(tool_name: str, arguments: dict)`: Execute a registered tool by name
- `tool_create(name: str, description: str, parameters: dict, source: str)`: Register a new tool

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
