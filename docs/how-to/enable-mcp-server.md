# How to Enable the MCP Server

> Expose Hecate agents, knowledge bases, and tools as [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) primitives — so any MCP-compatible client (Claude Desktop, Cursor, custom agents) can invoke them.

The MCP Server is **off by default**. Enabling it mounts a `FastMCP` sub-application at `/mcp` on the same Hecate process, no separate port or service needed.

---

## What you get

When enabled, external MCP clients can:

- **Create and chat with Hecate sessions** through `session_create` + `agent_chat`
- **List, create, update, and delete agents** through the `agent_*` tools
- **Query and ingest knowledge bases** through the `knowledge_*` tools
- **Discover and execute registered tools** (built-in, custom, MCP-imported)
- **Read catalogs** as MCP resources (`agent://list`, `knowledge://list`, `tool://list`)
- **Retrieve prompt templates** through the `system_template` prompt

This lets you wrap Hecate as a single tool provider for Claude Desktop, Cursor, or any agent that speaks MCP — without writing custom integration code.

---

## Step 1 — Configure environment

Edit `.env`:

```dotenv
# Enable the MCP Server (sub-app at /mcp)
MCP_SERVER_ENABLED=true

# Bind host/port (defaults: 0.0.0.0:8000 — same as the main Hecate app)
MCP_SERVER_HOST=0.0.0.0
MCP_SERVER_PORT=8000

# Transport: "http" (Streamable HTTP, the current MCP spec)
MCP_TRANSPORT=http

# Authentication: "api_key" (default), "jwt", or "none"
MCP_AUTH_TYPE=api_key
```

> **Same port as the main app.** The MCP Server is mounted as a FastAPI sub-application at `/mcp`, not a separate service. To expose it on a different port or behind a dedicated reverse-proxy path, use `MCP_SERVER_PORT` and configure your reverse proxy accordingly.

Restart Hecate after changing `.env`:

```bash
docker compose -f docker/docker-compose.yml restart hecate
# or, for bare metal:
# Ctrl+C and: uvicorn hecate.main:app --reload
```

---

## Step 2 — Verify the server is reachable

The MCP Server speaks Streamable HTTP. The FastMCP library exposes the standard MCP endpoints under `/mcp`:

```bash
# Initialize a session — should return server info
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "test-client", "version": "1.0"}
    }
  }'
```

A successful response includes `serverInfo` (name: `hecate-mcp-server`), `capabilities` (tools, resources, prompts), and a session ID header.

---

## Step 3 — Configure authentication

The default `MCP_AUTH_TYPE=api_key` requires every MCP request to send a valid `x-api-key` header that matches one of the keys in `HECATE_API_KEYS`.

### Option A: API key (default)

```dotenv
MCP_AUTH_TYPE=api_key
HECATE_API_KEYS=client-key-1,client-key-2
```

Clients send:

```
x-api-key: client-key-1
```

### Option B: JWT

```dotenv
MCP_AUTH_TYPE=jwt
JWT_SECRET=...
```

Clients send a Bearer token:

```
Authorization: Bearer <jwt-token>
```

### Option C: No authentication (development only)

```dotenv
MCP_AUTH_TYPE=none
```

> **Never** use `MCP_AUTH_TYPE=none` in production. The MCP Server has full agent CRUD and chat capabilities — unauthenticated access means anyone on the network can create agents or read your knowledge bases.

---

## Step 4 — Connect an MCP client

### Claude Desktop

Edit `~/.config/claude_desktop_config.json` (Linux/macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "hecate": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "x-api-key": "client-key-1"
      }
    }
  }
}
```

Restart Claude Desktop. The 16 Hecate tools appear in the tool picker (prefixed with the server name, e.g. `mcp__hecate__agent_chat`).

### Cursor

Edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "hecate": {
      "url": "http://localhost:8000/mcp",
      "headers": {
        "x-api-key": "client-key-1"
      }
    }
  }
}
```

Restart Cursor.

### Custom Python client (fastmcp or mcp SDK)

```python
from fastmcp import Client

async with Client(
    "http://localhost:8000/mcp",
    headers={"x-api-key": "client-key-1"},
) as client:
    tools = await client.list_tools()
    print(f"Discovered {len(tools)} tools:")
    for t in tools:
        print(f"  - {t.name}: {t.description.splitlines()[0]}")
```

---

## What the server exposes

### Tools (16)

| Tool | Purpose |
|------|---------|
| **Agent runtime** | |
| `session_create(agent_id)` | Create a new session for an agent |
| `agent_chat(session_id, message)` | Send a message; get the agent's response |
| `session_list(agent_id?)` | List active sessions |
| `session_resume(session_id, message)` | Resume an interrupted session |
| `conversation_history(conversation_id)` | Retrieve past messages |
| **Agent CRUD** | |
| `agent_list(workspace_id?)` | List all agents |
| `agent_create(name, model_config, mode?, persona?, tools?, knowledge_base_ids?)` | Create an agent |
| `agent_update(agent_id, **fields)` | Update agent fields |
| `agent_delete(agent_id)` | Soft-delete an agent |
| **Knowledge base** | |
| `knowledge_list()` | List all knowledge bases |
| `knowledge_search(kb_id, query, top_k?)` | Search for relevant chunks |
| `knowledge_create(name, description?, embedding_model?)` | Create a knowledge base |
| `knowledge_ingest(kb_id, content, metadata?)` | Ingest text content |
| **Tools** | |
| `tool_list(source?)` | List registered tools |
| `tool_execute(tool_name, arguments)` | Execute a tool by name |
| `tool_register(name, description, parameters, source?)` | Register a new tool |

### Resources (3)

Resources are read-only data sources the MCP client can browse without invoking a tool.

| URI | Returns |
|-----|---------|
| `agent://list` | JSON list of all agents (id, name, mode) |
| `knowledge://list` | JSON list of all knowledge bases (id, name, collection) |
| `tool://list` | JSON list of all registered tools (id, name, source) |

### Prompts (1)

| Prompt | Args | Purpose |
|--------|------|---------|
| `system_template(prompt_id)` | UUID | Retrieve a stored prompt template by ID (returns the latest version content) |

---

## Example: chat with an agent via MCP

```python
import asyncio
from fastmcp import Client


async def main():
    async with Client(
        "http://localhost:8000/mcp",
        headers={"x-api-key": "client-key-1"},
    ) as client:
        # 1. Create a session for an existing agent
        result = await client.call_tool(
            "session_create",
            {"agent_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"},
        )
        session_id = result.data["session_id"]
        print(f"Created session: {session_id}")

        # 2. Send a message
        result = await client.call_tool(
            "agent_chat",
            {"session_id": session_id, "message": "Explain OAuth2 in one sentence."},
        )
        print(f"Agent: {result.data['response']}")

        # 3. Send a follow-up (preserves context)
        result = await client.call_tool(
            "agent_chat",
            {"session_id": session_id, "message": "Give me an analogy."},
        )
        print(f"Agent: {result.data['response']}")


asyncio.run(main())
```

---

## Reverse proxy / production exposure

Put a TLS-terminating reverse proxy in front. Example nginx config:

```nginx
location /mcp/ {
    proxy_pass http://hecate_backend/mcp/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;

    # Pass through API key header
    proxy_set_header x-api-key $http_x_api_key;

    # Streaming (MCP uses SSE for some responses)
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}
```

If you're on a separate subdomain (`mcp.example.com`), point the entire vhost at the same backend — the `/mcp` prefix is handled by FastAPI's routing.

---

## Troubleshooting

### `MCP_SERVER_ENABLED=true` but `/mcp` returns 404

The server didn't initialize. Check the Hecate startup logs:

```bash
docker compose -f docker/docker-compose.yml logs hecate | grep -i mcp
```

If you see "MCP Server" in the logs, the route is mounted. A 404 then means you're hitting a different path — the mount point is exactly `/mcp`, not `/mcp/` (no trailing slash).

### `401 Unauthorized` / `403 Forbidden`

Authentication failed. Verify:
- `MCP_AUTH_TYPE` matches your client's auth mode (default: `api_key`)
- For `api_key`: `x-api-key` header value is in `HECATE_API_KEYS`
- For `jwt`: `Authorization: Bearer <token>` header is present and valid

### `agent_chat` returns `Session not found`

The `session_id` is wrong or expired. Sessions are tied to the agent lifecycle. Use `session_list` to discover valid session IDs, or create a fresh one with `session_create`.

### Tools are empty in Claude Desktop / Cursor

The MCP client may have cached an old tool list. Restart the client (not just reload). If still empty, check the client's logs — they usually show MCP handshake errors verbatim.

### `tool_execute` fails with permission error

The tool has `risk_level: HIGH` or `approval_required: True` and needs human-in-the-loop approval via the Hecate REST API. MCP clients can't display approval prompts natively — for now, use the REST API (`POST /api/sessions/{id}/resume`) for such tools.

### MCP Server conflicts with the main app on port 8000

The MCP Server is a **sub-app** at `/mcp`, not a separate listener. There is no port conflict. If you want to expose MCP on a different port (e.g. behind a dedicated reverse proxy), set `MCP_SERVER_PORT` and run a second uvicorn instance — but this is rarely needed.

---

## See also

- **[Enable the A2A Server](enable-a2a-server.md)** — expose Hecate to other agent frameworks via the A2A protocol.
- **[Tutorial: MCP Tool Integration](../tutorials/03-mcp-integration.md)** — connect *external* MCP servers (where Hecate acts as client) to your agents.
- **[MCP Specification](https://modelcontextprotocol.io/)** — the protocol reference.
- **[FastMCP Documentation](https://github.com/jlowin/fastmcp)** — the library Hecate uses to implement the server.