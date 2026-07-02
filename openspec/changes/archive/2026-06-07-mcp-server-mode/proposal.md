## Why

Hecate positions itself as an "MCP-first Agent platform" but currently only consumes MCP tools (Client, feature 5.3) — and that client is a mock stub with no real SDK integration. Without MCP Server mode, Hecate cannot be discovered or used by external AI tools (Claude Code, Cursor, VS Code, Google ADK agents), limiting its ecosystem reach. Meanwhile, every major platform — Google ADK (fastmcp), Salesforce Agentforce (MCP registry), Dify, Langflow — now exposes agent capabilities as MCP tools. MCP Server mode completes the bidirectional MCP architecture and is the final item in the Sprint 2 Infrastructure Extensibility chain (13.13 ✅ → 3.1.7 ✅ → 5.9a).

## What Changes

- Add `fastmcp` dependency and build an MCP Server that exposes Hecate's capabilities as MCP tools, resources, and prompts via Streamable HTTP transport
- MCP Server tools cover both runtime operations (agent execution, knowledge search, tool invocation, session management) and CRUD operations (create/list/update/delete agents, knowledge bases, tools, sessions)
- Fix the existing MCP Client (feature 5.3) — replace the mock `MCPClient` with a real implementation using the `mcp` Python SDK, supporting Streamable HTTP and stdio transports
- Mount the MCP Server onto the existing FastAPI app at `/mcp` using `fastmcp`'s ASGI integration
- Add configuration: `MCP_SERVER_ENABLED`, `MCP_SERVER_HOST`, `MCP_SERVER_PORT`, `MCP_AUTH_TYPE`, `MCP_TRANSPORT`
- Auth: reuse existing `HECATE_API_KEYS` for MCP tool access; support JWT Bearer tokens

## Capabilities

### New Capabilities

- `mcp-server`: MCP Server mode — expose Hecate's full capability surface (agents, knowledge bases, tools, sessions, conversations) as MCP tools/resources/prompts via Streamable HTTP transport, integrated with FastAPI via fastmcp
- `mcp-client-real`: Real MCP Client — replace mock MCPClient/MCPManager with actual `mcp` SDK ClientSession supporting Streamable HTTP and stdio transports, enabling Hecate agents to consume real external MCP tools

### Modified Capabilities

- `core-infrastructure`: Add MCP Server/Client configuration settings (`MCP_SERVER_ENABLED`, `MCP_SERVER_HOST`, `MCP_SERVER_PORT`, `MCP_AUTH_TYPE`, `MCP_TRANSPORT`, `MCP_CLIENT_TIMEOUT`) to `Settings` class
- `tool-registry`: Wire MCP tool execution path — when `source="mcp"`, route `ToolRegistry.execute()` through the real MCP Client instead of raising `NotImplementedError`
- `data-models`: Add `mcp_server_url` and `mcp_enabled` fields to ToolModel to support MCP Client tool registration; update schemas accordingly

## Impact

- **New dependency**: `fastmcp` (latest stable) added to `pyproject.toml` base dependencies; `mcp` (official SDK) added as base dependency for Client
- **Services layer**: New `services/mcp/server.py` (MCP Server), refactor `services/mcp/client.py` (real Client), new `services/mcp/session_manager.py` (MCP session → Hecate session mapping)
- **API layer**: MCP endpoint mounted at `/mcp` on existing FastAPI app via ASGI mount; no new REST routes needed
- **Config**: 5 new env vars in `core/config.py` + `.env.example`
- **Engine layer**: No changes — MCP Server operates at services layer, calling into existing service interfaces
- **Tests**: New `tests/test_services/test_mcp/` directory with tests for server tools, client integration, and session management
- **Migration**: No Alembic migration needed (field additions to ToolModel are additive, handled in existing migration path)
