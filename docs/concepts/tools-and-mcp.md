# Tools, MCP, and A2A

An LLM by itself only generates text. **Tools** are how an agent acts on the world — searches the web, reads a file, runs code, calls an API, or delegates work to another agent. Hecate is an **MCP-first** platform: every external capability is exposed through the Model Context Protocol, and Hecate itself can be consumed as an MCP server by any MCP-aware client (Claude Desktop, Cursor, custom agents). For agent-to-agent interoperability across frameworks, Hecate also speaks the Linux Foundation **A2A** protocol.

Understanding the three tool sources, how the registry routes execution, and where MCP and A2A fit will let you predict what your agent can call, how it gets authorized, and how other systems can call your agent.

> **Custom tools are not yet wired in.** The `ToolModel.source` field accepts `"builtin"`, `"custom"`, and `"mcp"`, but the current `ToolRegistry` raises `NotImplementedError` for `"custom"` (see `services/tool/registry.py`). Custom-tool execution is not yet implemented; until then, extend the agent with MCP tools or sub-agent delegation.

---

## The three tool sources

Every tool registered in Hecate carries a `source` field on `ToolModel` (`models/tool.py`) that tells the registry how to route execution:

| Source | Where it lives | How it executes | Example |
|--------|---------------|-----------------|---------|
| **`builtin`** | Hardcoded in `services/tool/builtin.py` | In-process Python function | `web_search`, `read_file`, `write_file`, `list_files`, `execute_code` |
| **`custom`** | `tools` table (DB-persisted) | *Not yet implemented — currently raises `NotImplementedError`* | User-defined Python/HTTP tools |
| **`mcp`** | `tools` table, with `mcp_server` + `mcp_tool_name` fields | Routed to the originating MCP server via `MCPClientManager.call_tool()` | `tavily_search`, `github_create_issue`, … |

A fourth, special-case source is **`AgentTool`** (`engine/agent_tool.py`): it wraps another Hecate agent as a callable tool for sub-agent delegation. It is registered in the parent agent's tool list at runtime rather than via the `tools` table.

### The five built-in tools

| Tool | What it does | Risk level | Sandbox? |
|------|--------------|-----------|----------|
| `web_search` | Web search via Tavily, Serper, or DuckDuckGo (configurable backend in `services/tool/search/`) | LOW | No |
| `read_file` | Read a file from the agent's workspace | LOW | No |
| `write_file` | Write a file to the agent's workspace | MEDIUM | No |
| `list_files` | List directory contents | LOW | No |
| `execute_code` | Run Python in a Docker-isolated container | HIGH | **Yes** — uses `SandboxPool` |

`execute_code` is the only built-in that runs in the sandbox by default. The `EnginePort.tool_execute_sandbox()` optional method (`engine/ports.py`) routes through `SandboxPool` when sandboxing is enabled, and falls back to in-process execution otherwise.

---

## The Tool Registry: one routing point

All tool execution — regardless of source — flows through one method on the engine's `EnginePort`:

```
Worker (LLM emitted a tool_call)
    │
    ▼
EnginePort.tool_execute(name, args, context)        # abstract, engine/ports.py
    │
    ▼
ToolRegistry.execute(name, args, context)           # services/tool/registry.py
    │
    ├── name in BUILTIN_TOOL_DEFINITIONS  ──►  BuiltInToolExecutor.execute()
    │                                              (in-process; execute_code → SandboxPool)
    │
    ├── DB lookup → tool.source == "mcp"  ──►  MCPClientManager.call_tool(server, tool, args)
    │                                              (remote call to the MCP server)
    │
    ├── DB lookup → tool.source == "custom"  ──►  NotImplementedError  (not yet implemented)
    │
    └── ToolCache consult/store (when cacheable=True)
```

The registry keeps an in-memory set of built-in names for fast routing — built-in tools don't touch the database. Non-built-in tools are looked up in the `tools` table by name and workspace.

### Caching

A tool may set `cacheable=True` and `cache_ttl=<seconds>` on its `ToolModel`. When set, `ToolCache` (`services/tool/cache.py`) checks the cache before executing and stores the result afterward with the configured TTL. This is useful for deterministic, expensive tools (a search index lookup, a metadata fetch).

---

## MCP: bidirectional protocol for tool exchange

The Model Context Protocol is the open standard Hecate uses to exchange tools, resources, and prompts with external systems. Hecate is MCP-native in **both directions**:

### MCP Client (Hecate consumes external tools)

When you register an external MCP server with Hecate, the `MCPClientManager` (`services/mcp/connection.py`) opens a connection via the per-server `MCPClient` (`services/mcp/client.py`). Two transports are supported:

- **Streamable HTTP** (`connect_http`) — the recommended transport for remote servers, per [ADR-012](../design/adr/012-mcp-streamable-http.md).
- **stdio** (`connect_stdio`) — for local subprocess servers.

Once connected, `list_tools()` enumerates the server's tools, and `call_tool(server, tool, args)` invokes one. Discovered tools are persisted with `source="mcp"` plus `mcp_server` and `mcp_tool_name` so the registry can route later calls back to the originating server. Circuit breaker (`services/mcp/circuit_breaker.py`) and auth (`services/mcp/auth.py`) are layered on every call.

Manage MCP servers at runtime via `GET/POST /api/mcp/...` (see `api/management/mcp.py`).

### MCP Server (Hecate exposes its own capabilities)

When `MCP_SERVER_ENABLED=true`, Hecate mounts a FastMCP server at `/mcp` (see `services/mcp/server.py` and `main.py`). The server registers **16 tools**, **3 resources**, and **1 prompt** covering the core surface:

- **Tools** — `session_create`, `agent_chat`, knowledge base queries, tool invocation, and more.
- **Resources** — `agent://list`, `knowledge://list`, `tool://list` for catalog discovery.
- **Prompts** — a starter prompt for new conversations.

This means any MCP-aware client — Claude Desktop, Cursor, a custom Python script using the MCP SDK — can drive Hecate agents, knowledge bases, and tools without speaking Hecate's native REST API. See the [Enable MCP Server guide](../how-to/enable-mcp-server.md) for configuration.

---

## A2A: agent-to-agent interoperability

While MCP is the protocol for **tools**, A2A (Agent-to-Agent, Linux Foundation) is the protocol for **agents** — letting agents built on different frameworks discover and invoke each other. Hecate speaks A2A in both directions.

### A2A Server (Hecate is discoverable)

When `A2A_SERVER_ENABLED=true`, Hecate exposes two endpoints (`a2a/server/app.py`):

| Endpoint | Purpose |
|----------|---------|
| `GET /.well-known/agent-card.json` | Serves Hecate's `AgentCard` — name, capabilities, skills, security schemes |
| `POST /a2a/` | JSON-RPC 2.0 endpoint handling `SendMessage`, `GetTask`, `CancelTask`, `SendStreamingMessage` (SSE) |

The `AgentCard` (`a2a/types.py`) is the standard A2A discovery document. Cards may be JWS-signed for verified publisher identity (`a2a/signing.py`).

### A2A Client (Hecate consumes external agents)

`A2AClient` (`a2a/client/client.py`) sends messages to remote A2A agents. `discover_agent_card()` (`a2a/client/discovery.py`) fetches and parses a remote `AgentCard` from its well-known URL. Push notifications from async remote agents are received at `POST /a2a/webhook` (`a2a/client/push.py`).

Manage A2A keys and configuration via `GET/POST /api/a2a/...` (see `api/management/a2a.py`). For setup steps, see the [Enable A2A Server guide](../how-to/enable-a2a-server.md).

---

## Risk, approval, and tool policy

Every tool carries a **risk level** (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`) and an **approval scope** (`once`, `session`, `project`, `global`). The engine's [`PreToolHook`](guardrails.md) reads these to decide whether to let the call proceed, require human approval via `interrupt()`, or deny it outright.

On top of per-tool risk, Hecate supports declarative **tool policies** (`models/tool_policy.py`) with three layers:

| Layer | Scope | Action |
|-------|-------|--------|
| **Workspace baseline** (`ToolPolicyModel`) | Per workspace, glob patterns (`mcp__github__.*`) | `allow` / `deny` / `ask` |
| **Per-agent rules** (`ToolPolicyRuleModel`) | Per agent in a workspace | Override baseline (within bounds) |
| **Runtime guardrail config** | Per agent's `guardrail_config` JSON | Composed at worker startup into `SecurityHookSet` |

Workspace-level `deny` rules are a security baseline — they cannot be overridden by agent-level configuration. This prevents an over-permissive agent configuration from granting a tool the workspace forbids. See the [Configure tool permissions guide](../how-to/configure-tool-permissions.md) for setup.

---

## Sub-agent delegation: `AgentTool`

Beyond calling tools, an agent can invoke another agent as if it were a tool. `AgentTool` (`engine/agent_tool.py`) wraps a target agent in a callable interface with a per-invocation `AgentDefinition`:

- **Tool scoping** — whitelist (`tools`) and blacklist (`disallowed_tools`) of tools the sub-agent may use.
- **Context mode** — `inherited` shares the parent's conversation, `isolated` starts fresh.
- **Model override** — run the sub-agent on a different model.
- **Limits** — `max_turns` and `timeout_seconds` bound execution.

This is how Hecate implements [multi-agent collaboration patterns](../tutorials/04-multi-agent.md) — Hierarchical, Handoff, Pipeline, Broadcast, Negotiation, and Debate are all expressed as graphs whose nodes are `AgentTool` calls.

---

## Choosing the right integration

| You want to... | Use |
|----------------|-----|
| Use Hecate's built-in capabilities (search, files, code execution) | A built-in tool — no setup |
| Plug an external capability into your agent | Register an MCP server, then attach the discovered tools to your agent |
| Let Claude Desktop or Cursor drive your Hecate agent | Enable the MCP Server (`MCP_SERVER_ENABLED=true`) |
| Let a LangGraph/CrewAI/AutoGen agent invoke your Hecate agent | Enable the A2A Server (`A2A_SERVER_ENABLED=true`) |
| Have one Hecate agent delegate a sub-task to another Hecate agent | `AgentTool` with a scoped `AgentDefinition` |
| Run untrusted code from agent output | The `execute_code` tool, which uses the Docker `SandboxPool` |
| Restrict which tools an agent may call | Workspace `ToolPolicyModel` + per-agent `ToolPolicyRuleModel` (see [Configure tool permissions](../how-to/configure-tool-permissions.md)) |

---

## Further reading

- [Agents and Execution Modes](agents.md) — how tools bind to agents across `chat`, `three_layer`, and `workflow` modes
- [Guardrails and Hooks](guardrails.md) — `PreToolHook` / `PostToolHook` enforce the policy layers above
- [Extension Points](../reference/extension-points.md) — the `EnginePort.tool_execute` and `tool_execute_sandbox` method signatures
- [Tool Platform Design](../design/tool-platform-design.md) — full L2 breakdown, composable policy pipeline, and plugin taxonomy
- [Ecosystem Design](../design/ecosystem-design.md) — MCP, A2A, marketplace, and the broader integration architecture
- [ADR-011: A2A Protocol Adoption](../design/adr/011-a2a-protocol-adoption.md) — why Hecate adopted A2A for cross-framework interop
- [ADR-012: MCP Streamable HTTP](../design/adr/012-mcp-streamable-http.md) — transport decision for MCP servers
- [MCP Tool Integration tutorial](../tutorials/03-mcp-integration.md) — hands-on with MCP client and server
