## Context

Hecate is an "MCP-first Agent platform" built on FastAPI. The MCP Client (feature 5.3) exists as data models and API stubs but uses a mock implementation — no real MCP SDK integration. There is no MCP Server at all. The platform needs bidirectional MCP: consume external tools (Client) and expose internal capabilities (Server).

Current state:
- `services/mcp/client.py` — `MCPClient` returns mock data (`{"result": "Mock result", "success": True}`)
- `services/mcp/sync.py` — `MCPToolSync` converts formats but never calls a real server
- `services/tool/registry.py` — MCP tool routing raises `NotImplementedError`
- No `fastmcp` or `mcp` SDK in `pyproject.toml`
- FastAPI app at `main.py` with 15+ management routers + OpenAI-compatible `/v1` endpoints
- Existing service layer: `WorkflowExecutionService`, `KnowledgeBaseService`, `ToolRegistry`, `AgentExecutionPort`, `LLMService`, `SessionModel`

Research findings:
- Google ADK official examples use `fastmcp` for building MCP servers
- `fastmcp` provides native FastAPI integration: `FastMCP.from_fastapi(app)`, `mcp.http_app()`, `app.mount()`
- MCP protocol has deprecated HTTP+SSE (2024-11-05) in favor of Streamable HTTP (2025-03-26+)
- `fastmcp.run(transport="http")` enables Streamable HTTP natively
- Major platforms (Salesforce, Google, Microsoft) all support Streamable HTTP as primary transport

## Goals / Non-Goals

**Goals:**

- Build an MCP Server using `fastmcp` that exposes Hecate's full capability surface as MCP tools, resources, and prompts
- Server tools cover runtime operations (agent execution, knowledge search, tool invocation, session management) AND CRUD operations (create/list/update/delete agents, KBs, tools)
- Mount MCP Server onto existing FastAPI app at `/mcp` using ASGI mount — shared DB sessions, shared auth
- Fix MCP Client to use real `mcp` SDK with Streamable HTTP + stdio transport support
- Wire ToolRegistry MCP routing through the real client
- Session management: MCP clients create Hecate sessions via `session_create(agent_id)` tool, then use `agent_chat(session_id, message)` for stateful conversations (方案 C)
- Auth: reuse `HECATE_API_KEYS` via MCP request headers; configurable via `MCP_AUTH_TYPE`

**Non-Goals:**

- MCP Sampling capability (P3 — let external clients request LLM completions through Hecate)
- MCP Gateway / API-to-MCP auto-conversion (feature 5.4a, P3)
- MCP Sandbox Security (feature 5.12, P3)
- OAuth2/OIDC auth for MCP (future enhancement)
- stdio transport for Server (SSE/Streamable HTTP only for a platform)
- Automatic `FastMCP.from_fastapi()` conversion of all REST endpoints (we'll hand-craft tools for better control)

## Decisions

### D1: SDK — `fastmcp` for Server, `mcp` SDK for Client

**Server**: Use `fastmcp` (by jlowin/Community). Rationale:
- Native FastAPI integration (`http_app()`, `mount()`, `combine_lifespans()`)
- `@mcp.tool` / `@mcp.resource` decorator API — cleaner than raw `@app.call_tool()` handlers
- Google ADK official Codelabs use `fastmcp` for building MCP servers
- Streamable HTTP via `transport="http"` out of the box
- Used by openJiuwen (our reference platform)

**Client**: Use official `mcp` Python SDK (`modelcontextprotocol/python-sdk`). Rationale:
- Official Anthropic SDK — stable, spec-complete
- Supports Streamable HTTP, SSE, and stdio transports
- `ClientSession` class provides full protocol lifecycle management
- `fastmcp` client is higher-level but less flexible for production use

Alternatives considered:
- Using `mcp` SDK for both → More boilerplate for Server, no FastAPI integration
- Using `fastmcp` for both → Client API is less mature than official SDK

### D2: Transport — Streamable HTTP only

Use Streamable HTTP (`transport="http"`) as the sole transport for MCP Server.

Rationale:
- HTTP+SSE transport is deprecated since protocol version 2025-03-26
- Streamable HTTP is the current standard — single endpoint, supports both JSON and SSE responses
- Stateless server support — better infrastructure compatibility
- All major clients (Claude Code, Cursor, VS Code, Google ADK) now support Streamable HTTP

For MCP Client: support both Streamable HTTP and stdio (stdio for local subprocess tools).

### D3: FastAPI Integration — ASGI mount at `/mcp`

```python
# services/mcp/server.py
mcp = FastMCP("hecate-mcp-server")

# main.py
mcp_app = mcp.http_app(path="/mcp")
app.mount("/mcp", mcp_app)
```

Mounts MCP Server ASGI app onto existing FastAPI app at `/mcp`. Shares:
- Database sessions (tools create their own `AsyncSession` via `async_session_factory`)
- Auth (API key validation in tool functions)
- Lifespan (combined via `combine_lifespans`)

Alternative considered: Separate port via uvicorn → unnecessary operational complexity, no shared state.

### D4: Session Management — 方案 C (two-step stateful pattern)

MCP tool calls are inherently stateless, but agent conversations are stateful. Design:

1. `session_create(agent_id)` → returns `session_id` (creates a Hecate `SessionModel`)
2. `agent_chat(session_id, message)` → sends message to existing session (calls `WorkflowExecutionService`)

This matches Hecate's existing session architecture (`POST /api/sessions` + `POST /v1/chat/completions?session_id=...`).

### D5: Tool Surface — Full capability exposure

Runtime tools:
- `agent_list`, `agent_chat`, `agent_create`, `agent_update`, `agent_delete`
- `knowledge_list`, `knowledge_search`, `knowledge_create`, `knowledge_ingest`
- `tool_list`, `tool_execute`, `tool_create`
- `session_create`, `session_list`, `session_resume`
- `conversation_history`

Resources:
- `agent://{agent_id}` — Agent metadata
- `knowledge://list` — KB catalog
- `tool://list` — Tool catalog

Prompts:
- `system-template://{prompt_id}` — System prompt templates

### D6: Auth — API Key passthrough

MCP tools validate `HECATE_API_KEYS` from incoming request headers. The `fastmcp` Context object provides access to request headers.

```python
@mcp.tool
async def agent_chat(session_id: str, message: str, ctx: Context) -> dict:
    api_key = ctx.request_headers.get("x-api-key", "")
    await verify_api_key(api_key)
    ...
```

Config: `MCP_AUTH_TYPE=api_key|jwt|none` (default: `api_key`).

### D7: MCP Client Refactor — Replace mock with real SDK

Replace `MCPClient` / `MCPManager` singletons with a proper async client:

```python
# services/mcp/client.py (refactored)
class HecateMCPClient:
    async def connect(self, server_url: str, transport: str = "http") -> None
    async def list_tools(self) -> list[ToolDefinition]
    async def call_tool(self, tool_name: str, arguments: dict) -> Any
    async def disconnect(self) -> None
```

Uses `mcp` SDK's `ClientSession` + `streamable_http_client` or `stdio_client`.

Wire `ToolRegistry.execute()` for `source="mcp"` to route through `HecateMCPClient`.

## Risks / Trade-offs

- **[R1] `fastmcp` is community-maintained, not official** → Mitigated by: Google ADK uses it in official docs; active development; if it stagnates, migration to `mcp` SDK is straightforward (lower-level API)
- **[R2] MCP tool surface is large (20+ tools)** → Mitigated by: Tools are thin wrappers over existing service methods; `fastmcp` auto-generates schemas from type hints; each tool is independently testable
- **[R3] Auth header passthrough may not work with all MCP clients** → Mitigated by: `MCP_AUTH_TYPE=none` for trusted internal networks; JWT support as fallback; documented in .env.example
- **[R4] ASGI mount may conflict with CORS middleware** → Mitigated by: `fastmcp` docs explicitly address this — avoid app-wide CORS on MCP paths; test with `CORSMiddleware` present
- **[R5] Real MCP Client connecting to external servers introduces supply chain risk** → Mitigated by: Tool allowlisting in ToolModel; `risk_level` and `approval_required` fields already exist; execution sandbox available (feature 9.4c ✅)
- **[R6] Dual SDK dependency (`fastmcp` + `mcp`) increases dependency surface** → Mitigated by: Both are pure-Python, lightweight, and actively maintained; `fastmcp` itself depends on `mcp` SDK internally
