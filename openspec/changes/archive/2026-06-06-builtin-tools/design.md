## Context

Two stub implementations currently block all tool execution:

1. `_ProductionEnginePort.tool_execute()` (engine_port_adapter.py L66) returns `f"Executed {name} with args {args}"` — a mock string.
2. `ConversationService._execute_tools_with_evidence()` (conversation.py L674) returns the same mock string.

The engine layer has a complete tool execution pipeline: `ToolWorker` extracts tool calls from messages, invokes PreToolHook/PostToolHook guardrails, calls `EnginePort.tool_execute()`, and returns tool result messages. The service layer has `tool_calling.py` for OpenAI function format conversion, `ToolFilter` for phase-based filtering, and `MCPClient` for external tool discovery. The data layer has `ToolModel` with `source` field supporting "builtin", "custom", "mcp".

What's missing is the **execution layer**: a registry that maps tool names to executable functions and routes by source type. The unified execution engine design (archived change `2026-06-05-unified-execution-engine`) defines tool calling as a cyclic graph pattern (`ConversationNode → ConditionNode → ToolNode → ConversationNode`) with `_ToolWorker` handling execution via EnginePort. This change plugs real execution into that architecture.

Existing infrastructure that can be leveraged:
- `SandboxExecutor` + `SandboxPool` (services/sandbox/) — Docker-based sandboxed execution with resource limits
- `ToolModel` (models/tool.py) — ORM with `source`, `parameters` (JSON Schema), `sandbox_enabled`, `risk_level`
- `ToolCreateSchema` / `ToolReadSchema` — API schemas already support source="builtin"
- Memory tools pattern (conversation.py `_build_memory_tools()`) — demonstrates tool schema + execution in one place

## Goals / Non-Goals

**Goals:**
- Implement ToolRegistry service that routes `tool_execute()` calls by source type
- Implement 5 built-in tools: web_search, read_file, write_file, list_files, execute_code
- Wire ToolRegistry into `_ProductionEnginePort`, replacing the mock stub
- Seed built-in tool definitions to DB on startup (hybrid registration pattern)
- Support configurable search providers (Tavily / Serper / DuckDuckGo) via environment variables
- ToolRegistry interface supports all three source types (builtin/custom/mcp)

**Non-Goals:**
- Custom tool execution (source="custom") — P2
- MCP tool execution routing (source="mcp") — P2 (MCPClient exists but routing not wired)
- POST /api/tools endpoint for creating tools via API — separate change
- MCP sync service (persist discovered MCP tools to DB) — separate change
- Modifying `ConversationService._execute_tools_with_evidence()` — will be replaced by unified engine migration
- Sandboxed file operations (read_file/write_file/list_files run unsandboxed in P1; sandbox is for execute_code only)
- Tool versioning or tool marketplace — P4

## Decisions

### D1: Hybrid registration — code-defined schemas, DB-seeded at startup

**Choice**: Built-in tool schemas (name, description, parameters JSON Schema) are defined in Python code (`services/tool/builtin.py`). On application startup, a seed function syncs these definitions to the `tools` DB table (`source="builtin"`, `workspace_id=00000000`).

**Alternatives considered**:
- Code-only (no DB) → rejected: tools not visible via API, frontend can't display them
- DB-only (migration seed) → rejected: schema and execution logic separated, harder to maintain
- No registration at all → rejected: `GET /api/tools?source=builtin` must return something

**Rationale**: Code-defined schemas co-locate definition with execution (single module to maintain). DB seeding makes tools queryable via API and visible in UI. Startup sync ensures DB always matches code — if a tool is added in code, it appears in DB automatically.

### D2: ToolRegistry is a service-layer singleton, not engine-internal

**Choice**: `ToolRegistry` lives in `services/tool/registry.py`. It accepts a DB session and looks up `ToolModel` by name + workspace_id to determine source type, then routes to the appropriate executor.

**Alternatives considered**:
- Engine-internal (engine/registry.py) → rejected: engine has zero external deps; ToolRegistry needs DB access
- Per-request instantiation → rejected: unnecessary overhead; registry is stateless aside from executor references

**Rationale**: ToolRegistry needs DB access (to look up tool definitions) and service references (SandboxExecutor, MCPClient). Both are service-layer concerns. Engine remains decoupled — it calls `EnginePort.tool_execute()` which delegates to registry.

### D3: Search provider abstraction with pluggable adapters

**Choice**: `SearchProvider` ABC with implementations for Tavily, Serper, DuckDuckGo. Configuration via `SEARCH_PROVIDER` and `SEARCH_API_KEY` environment variables. `BuiltInToolExecutor` resolves the provider at construction time.

**Alternatives considered**:
- Single hardcoded provider (Tavily) → rejected: locks users into one vendor
- Provider per request → rejected: over-engineering for P1

**Rationale**: Three providers with different API shapes but same input/output contract (query → results). ABC pattern matches existing EnginePort abstraction. Environment variable config matches existing `core/config.py` pattern (pydantic-settings).

### D4: execute_code delegates to existing SandboxExecutor

**Choice**: The `execute_code` built-in tool calls `SandboxExecutor.execute()` with the user's Python code, returns stdout/stderr. Uses existing `SandboxConfig` defaults (128MB RAM, 50% CPU, 30s timeout, no network).

**Alternatives considered**:
- Direct subprocess → rejected: no security isolation
- E2B cloud sandbox → rejected: external dependency, Docker sandbox already implemented

**Rationale**: `SandboxExecutor` + `SandboxPool` are already implemented (services/sandbox/). `ToolModel.sandbox_enabled` field exists. This just wires them together.

### D5: custom and mcp routing paths raise NotImplementedError

**Choice**: ToolRegistry's routing logic has three branches. Only `builtin` is implemented. `custom` and `mcp` paths raise `NotImplementedError` with a descriptive message.

**Rationale**: Explicit failure is better than silent stub (current behavior). The interface is correct; implementations follow in P2. Callers get a clear error instead of a fake string.

### D6: File operation tools work within a configurable workspace directory

**Choice**: `read_file`, `write_file`, `list_files` operate relative to a configurable `WORKSPACE_ROOT` directory (default: `./workspace`). Paths are sanitized to prevent directory traversal.

**Rationale**: Agents need file access but must be constrained. A workspace root prevents arbitrary filesystem access. Path sanitization (`os.path.normpath` + prefix check) is a minimum viable security measure.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| Search API key required for web_search | DuckDuckGo provider works without API key (default fallback); Tavily/Serper require key but have free tiers |
| Docker not available in all environments | `execute_code` gracefully degrades: if Docker daemon unavailable, return error message instead of crashing |
| Seed on startup may conflict with existing data | Upsert pattern: check by name + source="builtin" before inserting; update schema if definition changed |
| File tools with path traversal | Sanitize all paths against WORKSPACE_ROOT; reject `..` components and absolute paths |
| ToolRegistry adds latency for simple tool calls | In-memory dict of builtin tool names → executor functions; DB lookup only when name not in builtin set |
