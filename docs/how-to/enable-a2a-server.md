# How to Enable the A2A Server

> Expose Hecate as an [A2A (Agent-to-Agent)](https://a2a-protocol.org/) endpoint so other agent frameworks — LangGraph, CrewAI, AutoGen, custom agents — can discover and invoke your agents through a standardized JSON-RPC protocol.

The A2A Server is **off by default**. When enabled, Hecate mounts two endpoints on the main process — an [AgentCard](https://a2a-protocol.org/#agent-card) for discovery and a JSON-RPC 2.0 handler for task execution.

---

## A2A vs MCP — which one to enable?

Hecate supports both protocols, but they serve different purposes:

| Protocol | Direction | When to use |
|----------|-----------|-------------|
| **MCP** ([enable guide](enable-mcp-server.md)) | Tool provider — Hecate exposes capabilities for an LLM client to call | Wrapping Hecate as a tool provider for Claude Desktop, Cursor, or any MCP-aware client |
| **A2A** (this guide) | Agent peer — Hecate is invoked *as an agent* by another agent framework | Interoperability between Hecate and other agent platforms (LangGraph, CrewAI, AutoGen, custom) |

Enable both if you need Hecate to act as both a tool provider and an agent peer.

---

## What you get

When enabled, external agents can:

- **Discover Hecate** via the standard `/.well-known/agent-card.json` endpoint
- **Send tasks** via JSON-RPC `SendMessage` and receive a `Task` object
- **Stream progress** via `SendStreamingMessage` (Server-Sent Events)
- **Poll task status** via `GetTask`
- **Cancel in-flight tasks** via `CancelTask`

The A2A Server delegates each task to Hecate's first registered agent (the "default agent") — so you need at least one agent configured before A2A requests can succeed.

---

## Step 1 — Configure environment

Edit `.env`:

```dotenv
# Enable the A2A Server (mounted at /.well-known/agent-card.json and /a2a/)
A2A_SERVER_ENABLED=true

# Public URL where other agents reach Hecate — used in the AgentCard
A2A_SERVER_URL=https://hecate.example.com

# Display name in the AgentCard
A2A_AGENT_NAME=Hecate Agent

# Authentication mode: "api_key" (default), "bearer", or "none"
A2A_AUTH_MODE=api_key
```

> **`A2A_SERVER_URL` is the external URL** — what other agents see. Set this to your reverse proxy's public address (e.g. `https://hecate.example.com`), **not** the internal Docker hostname. This value goes into the `url` field of the AgentCard.

> **A2A env vars are not in `.env.example` by default** — the example only covers MCP. You must add the A2A variables manually. They are documented in [Environment Variables](../reference/env-vars.md).

Restart Hecate after changing `.env`:

```bash
docker compose -f docker/docker-compose.yml restart hecate
# or, for bare metal:
# Ctrl+C and: uvicorn hecate.main:app --reload
```

---

## Step 2 — Verify the endpoints

The A2A Server exposes two endpoints on the same Hecate process:

### AgentCard (discovery)

```bash
curl https://hecate.example.com/.well-known/agent-card.json | jq
```

```json
{
  "name": "Hecate Agent",
  "description": "Enterprise-grade, self-hosted, model-agnostic Agent platform with MCP-first architecture",
  "version": "1.0.0",
  "url": "https://hecate.example.com",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "skills": [],
  "securitySchemes": {
    "apiKeyAuth": {
      "apiKeySecurityScheme": {
        "location": "header",
        "name": "X-API-Key"
      }
    }
  },
  "defaultInputModes": ["text/plain", "application/json"],
  "defaultOutputModes": ["text/plain", "application/json"]
}
```

Other agents fetch this URL to learn Hecate's capabilities and auth requirements.

### JSON-RPC handler

```bash
curl -X POST https://hecate.example.com/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: client-key-1" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "SendMessage",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"text": "What is the capital of France?"}]
      }
    }
  }' | jq
```

A successful response returns a `Task` object with `id`, `contextId`, `status.state` (`submitted` → `working` → `completed`), and `artifacts`:

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": {
    "task": {
      "id": "a1b2c3d4-...",
      "contextId": "e5f6g7h8-...",
      "status": {
        "state": "completed",
        "message": {
          "role": "agent",
          "parts": [{"text": "The capital of France is Paris."}]
        }
      },
      "artifacts": [
        {
          "artifactId": "...",
          "name": "response",
          "parts": [{"text": "The capital of France is Paris."}]
        }
      ],
      "history": [...]
    }
  }
}
```

---

## Step 3 — Configure authentication

### Option A: API key (default)

```dotenv
A2A_AUTH_MODE=api_key
HECATE_API_KEYS=agent-client-key,another-key
```

Clients send:

```
X-API-Key: agent-client-key
```

### Option B: Bearer token

```dotenv
A2A_AUTH_MODE=bearer
HECATE_API_KEYS=long-bearer-token-string
```

Clients send:

```
Authorization: Bearer long-bearer-token-string
```

> **Important:** The Bearer token must also be in `HECATE_API_KEYS`. The A2A auth layer reuses the same key store as the main API — there is no separate A2A-only token list.

### Option C: No authentication (development only)

```dotenv
A2A_AUTH_MODE=none
```

> **Never** use `A2A_AUTH_MODE=none` in production. A2A lets external agents send messages and run tasks against your default agent — unauthenticated access means anyone on the network can run up your LLM bill.

---

## Step 4 — Optional: enable response signing

A2A supports signed responses for non-repudiation. Hecate uses Ed25519 keys and exposes a JWKS endpoint for verification.

```dotenv
A2A_SIGNING_ENABLED=true
A2A_SIGNING_KEY_PATH=/etc/hecate/a2a-signing-key.pem

# JWKS cache TTL in seconds (how long remote agents cache your public key)
A2A_JWKS_CACHE_TTL=3600
```

Generate a signing key:

```bash
openssl genpkey -algorithm Ed25519 -out /etc/hecate/a2a-signing-key.pem
chmod 600 /etc/hecate/a2a-signing-key.pem
```

Remote agents fetch the public key from the JWKS endpoint and verify response signatures.

---

## Step 5 — Connect from another agent framework

### Python (using Hecate's built-in A2A client)

Hecate ships a reusable A2A client in `hecate.a2a.client.A2AClient`:

```python
import asyncio
from hecate.a2a.client.client import A2AClient
from hecate.a2a.types import Message


async def main():
    client = A2AClient(
        agent_url="https://hecate.example.com",
        api_key="agent-client-key",
    )

    # Send a message
    task = await client.send_message(
        message=Message(
            role="user",
            parts=[{"text": "Summarize the last 3 news items"}],
        )
    )
    print(f"Task ID: {task.id}")
    print(f"State: {task.status.state.value}")
    print(f"Response: {task.status.message.parts[0]['text']}")

    # Poll status (or use send_streaming_message for SSE)
    final = await client.get_task(task.id)
    print(f"Final state: {final.status.state.value}")


asyncio.run(main())
```

### Generic JSON-RPC from any language

The protocol is plain HTTP + JSON-RPC 2.0. Any HTTP client works.

**Streaming (`SendStreamingMessage`):**

```bash
curl -N -X POST https://hecate.example.com/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: agent-client-key" \
  -H "Accept: text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "SendStreamingMessage",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"text": "Write a haiku about distributed systems"}]
      }
    }
  }'
```

The response is an SSE stream with `status-update` events as the task progresses:

```
event: status-update
data: {"task": {"id": "...", "status": {"state": "working"}}}

event: status-update
data: {"task": {"id": "...", "status": {"state": "completed"}, "artifacts": [...]}}
```

**Polling (`GetTask`):**

```bash
curl -X POST https://hecate.example.com/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: agent-client-key" \
  -d '{
    "jsonrpc": "2.0",
    "id": "2",
    "method": "GetTask",
    "params": {"id": "<task-uuid>"}
  }'
```

**Cancel (`CancelTask`):**

```bash
curl -X POST https://hecate.example.com/a2a/ \
  -H "Content-Type: application/json" \
  -H "X-API-Key: agent-client-key" \
  -d '{
    "jsonrpc": "2.0",
    "id": "3",
    "method": "CancelTask",
    "params": {"id": "<task-uuid>"}
  }'
```

> **`CancelTask` only works on in-flight tasks.** Once a task reaches `completed`, `failed`, or `canceled`, the server returns error code `-32002`.

---

## JSON-RPC methods reference

| Method | Params | Returns |
|--------|--------|---------|
| `SendMessage` | `{"message": {"role", "parts", "messageId?"}}` | `{"task": {...}}` with final state |
| `SendStreamingMessage` | Same as `SendMessage` | SSE stream of `status-update` events |
| `GetTask` | `{"id": "<task-uuid>"}` | `{"task": {...}}` or error |
| `CancelTask` | `{"id": "<task-uuid>"}` | `{"task": {...}}` with `canceled` state, or error |

### Error codes

| Code | Meaning |
|------|---------|
| `-32700` | JSON parse error |
| `-32601` | Method not found |
| `-32602` | Invalid params (missing task ID, malformed message) |
| `-32001` | Task not found |
| `-32002` | Task already in terminal state (cannot cancel) |

---

## Reverse proxy / production exposure

Standard HTTPS reverse proxy with SSE support. Example nginx:

```nginx
location / {
    proxy_pass http://hecate_backend;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # A2A streams use SSE — same requirements as MCP streaming
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}
```

The A2A endpoints (`/.well-known/agent-card.json`, `/a2a/`) share the same vhost — no separate proxy needed.

---

## Troubleshooting

### `A2A_SERVER_ENABLED=true` but `/.well-known/agent-card.json` returns 404

The server didn't initialize. Check the Hecate startup logs:

```bash
docker compose -f docker/docker-compose.yml logs hecate | grep -i a2a
```

If the server is mounted but you still get 404, verify the path is exactly `/.well-known/agent-card.json` (note the leading dot in `.well-known`).

### `SendMessage` returns "No agent configured in Hecate"

The A2A Server delegates to the **first non-deleted agent** in the workspace. If no agents exist, the task fails with state `failed` and message "No agent configured in Hecate". Create at least one agent first:

```bash
hecate agent create --name "A2A Default Agent" --model "gpt-4o-mini" --mode chat
```

> **Current limitation:** the A2A Server always uses the first agent. If you need task routing to specific agents, you must arrange agent order in the database (or wait for multi-agent A2A routing — see [roadmap](../design/architecture.md)).

### `401 Unauthorized` / `403 Forbidden`

Authentication failed. Verify:
- `A2A_AUTH_MODE` matches what the client sends (`api_key` → `X-API-Key` header; `bearer` → `Authorization: Bearer`)
- The key/token is in `HECATE_API_KEYS`
- For `bearer`: the `Authorization` header must include the literal word `Bearer ` followed by the token

### `GetTask` returns "Task not found"

The task ID is wrong, or the task was created on a different Hecate instance and you're querying a replica that doesn't have it. Tasks are stored in PostgreSQL, so any replica should see them — but if you ran the migration or restored from a backup that excluded the `a2a_tasks` table, older tasks may be missing.

### `CancelTask` returns error code `-32002`

The task already finished. `CancelTask` only works on tasks in `submitted` or `working` state. If you need to abort a long-running task, send `CancelTask` *during* execution — or implement client-side timeouts.

### Signing key errors

If `A2A_SIGNING_ENABLED=true` but `A2A_SIGNING_KEY_PATH` is empty or unreadable, signing fails. Verify the key file exists, is readable by the Hecate process, and is a valid Ed25519 PEM:

```bash
openssl pkey -in /etc/hecate/a2a-signing-key.pem -text -noout
```

You should see `ED25519 Private-Key` in the output.

---

## See also

- **[Enable the MCP Server](enable-mcp-server.md)** — expose Hecate as a *tool provider* (different protocol, complementary role).
- **[Environment Variables](../reference/env-vars.md)** — all A2A-related env vars.
- **[A2A Protocol Specification](https://a2a-protocol.org/)** — the full protocol reference.
- **[Tutorial: Build Your First Agent](../tutorials/01-first-agent.md)** — create the default agent that A2A will invoke.