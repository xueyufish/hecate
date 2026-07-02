## 1. Dependencies & Configuration

- [x] 1.1 Add `fastmcp` to `pyproject.toml` base dependencies (latest stable)
- [x] 1.2 Add `mcp` (official SDK) to `pyproject.toml` base dependencies
- [x] 1.3 Add MCP configuration settings to `core/config.py`: `MCP_SERVER_ENABLED`, `MCP_SERVER_HOST`, `MCP_SERVER_PORT`, `MCP_AUTH_TYPE`, `MCP_TRANSPORT`, `MCP_CLIENT_TIMEOUT`
- [x] 1.4 Update `.env.example` with all new MCP settings and comments
- [x] 1.5 Install new dependencies: `uv pip install -e ".[dev]"`

## 2. MCP Client — Replace Mock with Real SDK

- [x] 2.1 Rewrite `services/mcp/client.py` — create `HecateMCPClient` class using `mcp` SDK's `ClientSession` with `streamable_http_client` and `stdio_client` support
- [x] 2.2 Create `services/mcp/connection.py` — `MCPClientManager` that manages multiple server connections, tool discovery, and call routing
- [x] 2.3 Update `services/mcp/sync.py` — `MCPToolSync` uses real `HecateMCPClient.list_tools()` instead of mock data
- [x] 2.4 Wire `services/tool/registry.py` — replace `NotImplementedError` for `source="mcp"` with routing through `MCPClientManager.call_tool()`
- [x] 2.5 Remove module-level singletons (`mcp_manager`, `mcp_tool_sync`) — use factory/lazy initialization pattern

## 3. MCP Server — Core Infrastructure

- [x] 3.1 Create `services/mcp/server.py` — `create_mcp_server()` factory function that builds `FastMCP("hecate-mcp-server")` with Streamable HTTP transport
- [x] 3.2 Create `services/mcp/auth.py` — `verify_mcp_auth(ctx: Context)` helper that validates API key or JWT from MCP request headers based on `MCP_AUTH_TYPE`
- [x] 3.3 Create `services/mcp/session_manager.py` — `MCPSessionManager` that maps MCP sessions to Hecate `SessionModel`, handles `session_create` and session lookup
- [x] 3.4 Mount MCP Server onto FastAPI app in `main.py` — conditional mount at `/mcp` when `MCP_SERVER_ENABLED=true`, with lifespan combining

## 4. MCP Server — Agent Runtime Tools

- [x] 4.1 Implement `session_create(agent_id: str)` MCP tool — creates `SessionModel`, returns session_id
- [x] 4.2 Implement `agent_chat(session_id: str, message: str)` MCP tool — invokes `WorkflowExecutionService.execute()` with session context, returns response
- [x] 4.3 Implement `session_list(agent_id: str | None)` MCP tool — lists active sessions with pagination
- [x] 4.4 Implement `session_resume(session_id: str, message: str)` MCP tool — resumes interrupted session
- [x] 4.5 Implement `conversation_history(conversation_id: str)` MCP tool — retrieves message history

## 5. MCP Server — Agent CRUD Tools

- [x] 5.1 Implement `agent_list(workspace_id: str | None)` MCP tool — query AgentModel with pagination
- [x] 5.2 Implement `agent_create(name, persona, model_config, mode, tools, knowledge_base_ids)` MCP tool — create AgentModel
- [x] 5.3 Implement `agent_update(agent_id, **fields)` MCP tool — update AgentModel fields
- [x] 5.4 Implement `agent_delete(agent_id)` MCP tool — soft-delete agent

## 6. MCP Server — Knowledge Base Tools

- [x] 6.1 Implement `knowledge_list()` MCP tool — list all knowledge bases
- [x] 6.2 Implement `knowledge_search(kb_id, query, limit, mode)` MCP tool — call `KnowledgeBaseService.search()`
- [x] 6.3 Implement `knowledge_create(name, description, embedding_model, chunk_strategy)` MCP tool — create KnowledgeBaseModel + collection
- [x] 6.4 Implement `knowledge_ingest(kb_id, content, metadata)` MCP tool — call `KnowledgeBaseService.ingest_document_text()`

## 7. MCP Server — Tool Execution Tools

- [x] 7.1 Implement `tool_list(source: str | None)` MCP tool — query ToolModel with optional source filter
- [x] 7.2 Implement `tool_execute(tool_name, arguments)` MCP tool — call `ToolRegistry.execute()`
- [x] 7.3 Implement `tool_create(name, description, parameters, source)` MCP tool — create ToolModel

## 8. MCP Server — Resources & Prompts

- [x] 8.1 Implement `agent://list` MCP resource — return agent catalog as structured JSON
- [x] 8.2 Implement `knowledge://list` MCP resource — return KB catalog as structured JSON
- [x] 8.3 Implement `tool://list` MCP resource — return tool catalog as structured JSON
- [x] 8.4 Implement `system-template://{prompt_id}` MCP prompt — return prompt template content

## 9. Tests

- [x] 9.1 Create `tests/test_services/test_mcp/` directory with `__init__.py`
- [x] 9.2 Test `HecateMCPClient` — connection, list_tools, call_tool, disconnect with mock MCP server
- [x] 9.3 Test `MCPClientManager` — add_server, discover_tools, call_tool routing, disconnect_all
- [x] 9.4 Test MCP Server tools — agent_list, agent_create, agent_chat, knowledge_search with mock DB session
- [x] 9.5 Test MCP Server resources — agent://list, knowledge://list, tool://list
- [x] 9.6 Test MCP auth — verify_mcp_auth with valid/invalid/missing API key
- [x] 9.7 Test `MCPSessionManager` — session_create, session lookup, invalid session error
- [x] 9.8 Test ToolRegistry MCP routing — verify mcp tools route through MCPClientManager
- [x] 9.9 Test MCP Server mount — verify /mcp endpoint exists when enabled, absent when disabled

## 10. Verification

- [x] 10.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 10.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 10.3 Run `mypy src/` — zero errors
- [x] 10.4 Run `python -m pytest tests/ -q` — all tests pass (existing + new)
