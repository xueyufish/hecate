# Tutorial: MCP Tool Integration

> **15 minutes** — Connect Hecate agents to external MCP servers as tool providers. Register a remote server, discover its tools, attach them to an agent, and watch the LLM call them.

This tutorial covers the **client side** of MCP — Hecate acting as a client that connects to remote MCP servers and uses their tools. The server side (exposing Hecate as an MCP server for Claude Desktop or Cursor to consume) is covered in [Enable MCP Server](../how-to/enable-mcp-server.md).

> **Protocol era (5.4b, recently)**: Hecate's MCP client speaks MCP protocol **2026-07-28** and automatically negotiates with servers that advertise a different (older) revision — falling back to the legacy `initialize` handshake when the remote server predates the stateless core. No client-side configuration is required.

---

## What you will learn

- How MCP integrates with Hecate's three-tier tool model (`builtin` / `custom` / `mcp`)
- How to **register an external MCP server** endpoint
- How to **discover tools** the remote server exposes
- How to **attach MCP tools** to an agent
- How to **chat with the agent** and watch it call MCP tools
- How to handle **connection failures, circuit breakers, and tool caching**

## Prerequisites

- Hecate running locally — see [Quickstart](../getting-started/quickstart.md)
- At least one LLM provider configured in `.env`
- `hecate` CLI on your `PATH`
- Completed [Build Your First Agent](01-first-agent.md)
- An MCP server to connect to. The tutorial assumes `http://localhost:9001/mcp`; substitute with any MCP server (public or self-hosted)

Throughout this tutorial we use `dev-key-change-me` as the API key. Replace it with your actual `HECATE_API_KEYS` value.

---

## Step 1 — Understand the three tool sources

Hecate tools come from three origins. The `ToolModel.source` field distinguishes them:

| Source | Origin | Examples |
|--------|--------|----------|
| `builtin` | Shipped with Hecate, seeded on startup | `web_search`, `read_file`, `write_file`, `list_files`, `execute_code`, plus six browser tools (6.27) |
| `custom` | User-defined via API or code | Company-specific functions, business logic wrappers |
| `mcp` | Discovered from a registered MCP server | GitHub, Slack, databases, internal APIs — anything MCP-compatible |

MCP tools look identical to builtin and custom tools from the agent's perspective. They all live in the `tools` table, are referenced by name in the agent's `tools` field, and the LLM receives their JSON Schema as function-calling definitions.

What makes MCP tools different:

- The **implementation** runs in the remote MCP server, not in Hecate
- Hecate holds a **connection pool** to the remote server
- **Discovery is lazy** — Hecate connects on first call, caches the tool list, and reconnects automatically if the server drops
- **Circuit breakers** protect Hecate from cascading failures when an MCP server is down

---

## Step 2 — Register an external MCP server

Hecate registers MCP servers through the **plugin system**. A plugin with an `entry` field starting with `mcp://` is treated as an MCP server registration. Enabling the plugin activates the MCP connection; disabling it tears down.

```bash
# Step 2a — Create a plugin describing the MCP server
curl -X POST http://localhost:8000/api/plugins/create \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "manifest": {
      "name": "github",
      "version": "1.0.0",
      "type": "mcp",
      "entry": "mcp://https://mcp.github.com/sse"
    }
  }'
```

Save the returned `id` — that's the plugin's UUID.

```bash
# Step 2b — Enable the plugin → activates the MCP connection
curl -X POST http://localhost:8000/api/plugins/<PLUGIN_ID>/enable \
  -H "Authorization: Bearer dev-key-change-me"
```

The `entry` field's `mcp://<endpoint>` scheme tells Hecate this is an MCP server. The part after `mcp://` becomes the connection endpoint. The plugin's `name` becomes the server name in Hecate's MCP registry.

| Plugin field | Value | Description |
|--------------|-------|-------------|
| `name` | `github` | Unique identifier — used by the agent's `tools` field |
| `type` | `mcp` | Plugin type |
| `entry` | `mcp://https://...` | MCP URL with `mcp://` prefix |

> HTTP servers are reachable from the Hecate container. For local stdio servers (e.g., `mcp-server-filesystem`), the `entry` would be `mcp://stdio:npx -y @modelcontextprotocol/server-filesystem /tmp` — Hecate spawns the subprocess.

### Public MCP servers you can use

| Service | Endpoint pattern |
|---------|-----------------|
| GitHub | `https://mcp.github.com/sse` (auth required) |
| Filesystem (local) | `npx -y @modelcontextprotocol/server-filesystem /tmp` via stdio |
| Fetch (web) | `uvx mcp-server-fetch` via stdio |
| Slack | `https://slack.com/api/mcp` (with workspace auth) |

> **HTTP servers** are reachable from the Hecate container. For local stdio servers (like filesystem), Hecate must be able to spawn the subprocess — true for bare-metal deployments, requires extra config in Docker Compose.

---

## Step 3 — Discover the server's tools

Trigger discovery to fetch the tool list from the remote MCP server and persist it to Hecate's `tools` table:

```bash
curl -X POST http://localhost:8000/api/mcp/connections/github/sync \
  -H "Authorization: Bearer dev-key-change-me"
```

Response:

```json
{
  "status": "synced",
  "server": "github",
  "tool_count": 12
}
```

The 12 discovered tools are now in the `tools` table with `source: "mcp"`, `mcp_server: "github"`, and `mcp_tool_name` matching the server's tool name. List them:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/api/tools?source=mcp" | jq '.items[] | {name, mcp_server, description}'
```

```json
{"name": "search_repositories", "mcp_server": "github", "description": "Search GitHub repos by query"}
{"name": "create_issue", "mcp_server": "github", "description": "Create a new issue in a repository"}
{"name": "list_pull_requests", "mcp_server": "github", "description": "List pull requests for a repository"}
...
```

Or via CLI:

```bash
hecate tool list --source mcp
```

The CLI filters by source and shows the columns most useful for MCP tools (`id`, `name`, `description`, `source`).

---

## Step 4 — Check connection health

Inspect the connection pool and circuit-breaker state for any registered server:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  http://localhost:8000/api/mcp/connections/github | jq
```

Response (excerpt):

```json
{
  "name": "github",
  "registered": true,
  "endpoint": "https://mcp.github.com/sse",
  "transport": "http",
  "pool_size": 1,
  "circuit_breaker_state": "closed",
  "last_error": null,
  "tool_count": 12,
  "last_sync": "2026-01-15T10:30:00Z"
}
```

`circuit_breaker_state` cycles through `closed` (normal), `open` (failing, requests rejected), and `half-open` (testing recovery). See [Resilience](#resilience) below for tuning.

---

## Step 5 — Attach MCP tools to an agent

Create an agent and reference the discovered MCP tool by name. The tool name must match the remote server's tool name:

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "GitHub Assistant",
    "persona": "You are a GitHub assistant. Use the available tools to help users query repositories, create issues, and review pull requests.",
    "model_config": {"model": "gpt-4o-mini"},
    "mode": "chat",
    "tools": ["search_repositories", "create_issue", "list_pull_requests"]
  }'
```

Save the agent's `id`.

> **The agent's `tools` list is name-based.** Hecate resolves names to tools at chat time. If you reference a tool that doesn't exist (typo, server unregistered), the LLM is told the tool is unavailable — the agent fails gracefully rather than crashing.

You can mix sources freely — reference builtin, custom, and MCP tools in the same `tools` list:

```json
{
  "tools": ["web_search", "search_repositories", "internal_calc"]
}
```

---

## Step 6 — Chat with the agent — and watch the MCP tool fire

Send a chat request that requires GitHub access:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent/<AGENT_ID>",
    "messages": [
      {"role": "user", "content": "Find the top 3 most-starred Python repositories for MCP servers."}
    ]
  }'
```

What happens behind the scenes:

1. **Pregel runtime** loads the agent config and resolves `search_repositories` to the MCP-discovered tool
2. The agent's prompt is sent to the LLM with `search_repositories`'s JSON Schema as a tool definition
3. The LLM decides to call `search_repositories(query="python mcp server", sort="stars", limit=3)`
4. Hecate's MCP client looks up the tool's `mcp_server` (`github`) and **acquires a connection from the pool**
5. The JSON-RPC `tools/call` request goes to `https://mcp.github.com/sse`
6. The remote server executes the search and returns results
7. The LLM receives the results as a tool message and synthesizes the final answer
8. The **connection returns to the pool** for reuse

Total added latency: 100–500ms depending on the remote server and network. The MCP call is visible in the trace as a `tool_execution` span.

### Verify the tool fired

Check the trace:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/api/traces?agent_id=<AGENT_ID>&limit=1" | jq '.items[0].spans[] | select(.name | contains("tool")) | {name, duration_ms, output_preview: (.output | tostring | .[0:200])}'
```

You should see a span named `tool_execution:search_repositories` with the GitHub API response in the output.

---

## Step 7 — Update the agent's tools

Add or remove MCP tools without recreating the agent:

```bash
curl -X PUT http://localhost:8000/api/agents/<AGENT_ID> \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "tools": ["search_repositories", "create_issue", "list_pull_requests", "get_file_contents"]
  }'
```

If you added a tool, sync the server first to pull its schema into Hecate's `tools` table:

```bash
curl -X POST http://localhost:8000/api/mcp/connections/github/sync \
  -H "Authorization: Bearer dev-key-change-me"
```

---

## Step 8 — Refresh tool cache after the remote server updates

When the remote MCP server adds new tools or changes existing schemas, your Hecate tool list is stale. The cache TTL controls automatic refresh; force a sync to refresh immediately:

```bash
curl -X POST http://localhost:8000/api/mcp/connections/github/sync \
  -H "Authorization: Bearer dev-key-change-me"
```

This invalidates the cache, re-runs `tools/list` against the remote server, and updates the `tools` table. Configure the TTL:

```dotenv
# .env
MCP_TOOL_CACHE_TTL=300  # seconds; default 300 (5 minutes)
```

Set shorter TTLs for fast-evolving tool sets, longer for stable servers.

---

## Step 9 — Handle server outages

When a remote MCP server goes down, Hecate's circuit breaker prevents cascading failures. After `MCP_CIRCUIT_BREAKER_THRESHOLD` consecutive failures, the breaker opens and rejects new tool calls immediately (without trying to connect) for `MCP_CIRCUIT_BREAKER_RECOVERY_TIMEOUT` seconds.

```dotenv
# .env (defaults shown)
MCP_CIRCUIT_BREAKER_THRESHOLD=5       # consecutive failures before opening
MCP_CIRCUIT_BREAKER_RECOVERY_TIMEOUT=60  # seconds before half-open probe
```

After the recovery timeout, Hecate sends a probe request. If it succeeds, the breaker closes; if it fails, the timer resets.

### Manual reconnect

If the breaker is open and you've verified the server is back, force a reconnect:

```bash
curl -X POST http://localhost:8000/api/mcp/connections/github/reconnect \
  -H "Authorization: Bearer dev-key-change-me"
```

This closes any stale connections in the pool and starts a fresh one.

### Auto-reconnect

By default, Hecate auto-reconnects after transient failures with exponential backoff:

```dotenv
MCP_RECONNECT_MAX_RETRIES=5
MCP_RECONNECT_BASE_DELAY=1.0   # seconds
MCP_RECONNECT_MAX_DELAY=60.0  # cap
```

If all retries exhaust, the circuit breaker takes over.

---

## Resilience

| Setting | Default | Purpose |
|---------|---------|---------|
| `MCP_POOL_MIN_SIZE` | 1 | Minimum warm connections per server |
| `MCP_POOL_MAX_SIZE` | 5 | Maximum connections (prevents overload) |
| `MCP_BORROW_TIMEOUT` | 5 | Seconds to wait for a free connection |
| `MCP_HEALTH_CHECK_INTERVAL` | 30 | Seconds between background health probes |
| `MCP_REQUEST_TIMEOUT` | 30 | Per-request timeout in seconds |
| `MCP_TOOL_CACHE_TTL` | 300 | Tool list cache TTL in seconds |
| `MCP_CIRCUIT_BREAKER_THRESHOLD` | 5 | Failures before opening |
| `MCP_CIRCUIT_BREAKER_RECOVERY_TIMEOUT` | 60 | Seconds before half-open |
| `MCP_RECONNECT_MAX_RETRIES` | 5 | Auto-reconnect retries |
| `MCP_RECONNECT_BASE_DELAY` | 1.0 | Initial backoff |
| `MCP_RECONNECT_MAX_DELAY` | 60.0 | Backoff cap |

Tune these for your deployment. Lower pool sizes mean less memory and fewer concurrent MCP calls; higher pool sizes improve throughput under load.

---

## Mixing builtin, custom, and MCP tools

The agent's `tools` field accepts a heterogeneous list. A common pattern: builtin tools for general operations, MCP tools for external systems, custom tools for company-specific logic.

```json
{
  "name": "Engineering Assistant",
  "model_config": {"model": "gpt-4o-mini"},
  "mode": "chat",
  "tools": [
    "web_search",
    "read_file",
    "search_repositories",
    "create_issue",
    "internal_jira_lookup"
  ]
}
```

The LLM sees all five as callable functions and picks whichever fits the user's intent. Hecate routes each call to the right backend — builtin executor, MCP client, or custom Python function — without the agent needing to know.

---

## Troubleshooting

### Sync returns `Failed to sync tools`

The remote MCP server is unreachable or rejected the connection. Check:

```bash
curl -fsS https://mcp.github.com/sse -o /dev/null -w "%{http_code}\n"
```

- **HTTP server down** — verify the URL is correct and the server is running
- **Auth required** — some MCP servers require a Bearer token in headers; check the server's docs
- **Firewall** — outbound HTTPS from the Hecate container to the server is blocked

### Tool call fails at chat time

The circuit breaker may be open. Check `/api/mcp/connections/{name}` — if `circuit_breaker_state` is `open`, wait for the recovery timeout or trigger a manual reconnect.

### Tool name in agent doesn't match server's tool

Run `sync` again to refresh the cache, then list tools to see the exact name (case-sensitive). MCP tool names use the remote server's naming convention — typically `snake_case` like `search_repositories`.

### Two MCP servers expose a tool with the same name

Hecate's tool names are globally unique. If `github` and `gitlab` both expose `search_repositories`, only the first one registered wins. Reference the tool by its full disambiguated path or rename on the remote server.

### Stdio MCP server doesn't start in Docker

Stdio MCP servers require Hecate to spawn a subprocess. In Docker Compose, ensure the hecate container has the necessary binaries (e.g., `npm`, `uvx`, `python`) and that the user inside the container can exec them. For production, prefer HTTP-based MCP servers — they're simpler to deploy.

### Tools cache stale after server update

Either wait for the TTL to expire or force a sync:

```bash
curl -X POST http://localhost:8000/api/mcp/connections/<name>/sync
```

---

## Summary

You now know how to:

- **Register external MCP servers** via the API
- **Discover tools** from remote servers with `sync`
- **Attach MCP tools** to agents by name (alongside builtin and custom tools)
- **Verify tool invocations** via the trace API
- **Handle outages** with circuit breakers, auto-reconnect, and manual intervention
- **Tune resilience** parameters for your deployment's needs

## Next steps

- **[Enable MCP Server](../how-to/enable-mcp-server.md)** — flip the direction: expose Hecate as an MCP server for Claude Desktop, Cursor, or custom clients.
- **[Tutorial: Knowledge Base and RAG](02-knowledge-base.md)** — give your agents domain expertise with RAG.
- **[Tutorial: Multi-Agent Orchestration](04-multi-agent.md)** — coordinate multiple agents with shared MCP tools.
- **[Tool Platform Design](../design/tool-platform-design.md)** — architecture-level details on the tool system.