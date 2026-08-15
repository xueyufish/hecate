# Tutorial: A2A Protocol

> **25 minutes** — Connect Hecate to other A2A-compliant agents. Expose Hecate as an A2A server that external agents can discover and delegate to, and consume A2A agents from inside Hecate workflows.

The [A2A Protocol](https://a2a-protocol.org/) (Agent-to-Agent, Linux Foundation) is the standard for cross-framework agent interoperability. Hecate ships a full A2A v1.0+ server and client — you can plug Hecate into any other A2A-compatible ecosystem.

This tutorial covers both sides:

- **Server side** — expose Hecate as an A2A agent that other agents can call
- **Client side** — call external A2A agents from inside a Hecate workflow

For the enablement recipe (just enabling the A2A server with no client work), see [Enable A2A Server](../how-to/enable-a2a-server.md).

---

## What you will learn

- How the A2A protocol fits with Hecate's three execution modes (`chat`, `three_layer`, `workflow`)
- How to **expose Hecate as an A2A server** with an `AgentCard`
- How to **discover and call an external A2A agent** from a Hecate workflow
- How **task lifecycle states** (submitted → working → completed/failed) propagate
- How to **stream task updates** over Server-Sent Events
- How **AgentCard signing** establishes trust between agents

## Prerequisites

- Hecate running locally — see [Quickstart](../getting-started/quickstart.md)
- An LLM provider configured in `.env`
- Two Hecate instances (or one Hecate + one external A2A agent) for the cross-agent demo
- For the signing demo: `cryptography` installed (already a Hecate dependency)

Throughout this tutorial we use `dev-key-change-me` as the API key.

---

## Step 1 — Understand A2A primitives

A2A has three core concepts:

| Concept | What it is | Hecate equivalent |
|---|---|---|
| **AgentCard** | JSON manifest describing an agent's capabilities, endpoint, and skills | A subset of `Agent` config + public metadata |
| **Task** | A unit of work delegated from one agent to another, with a lifecycle state | One Hecate session + its messages |
| **Artifact** | The output a task produces (text, file, structured data) | Assistant message body + tool results |

```
┌──────────────────┐         ┌──────────────────────┐
│  Calling agent   │         │  Receiving agent     │
│  (Hecate client) │         │  (Hecate A2A server) │
│                  │         │                      │
│  1. Fetch        │  GET    │  /.well-known/       │
│     AgentCard    │ ──────▶ │  agent-card.json     │
│                  │         │                      │
│  2. Submit Task  │  POST   │  /a2a/               │
│     (JSON-RPC)   │ ──────▶ │  tasks/send          │
│                  │         │                      │
│  3. Poll / Stream│  GET    │  tasks/{id}/subscribe│
│     for status   │ ──────▶ │  (SSE)               │
│                  │         │                      │
│  4. Receive      │         │  Artifact            │
│     Artifact     │ ◀────── │  (text + files)      │
└──────────────────┘         └──────────────────────┘
```

JSON-RPC 2.0 carries task requests; Server-Sent Events (SSE) carry task updates.

---

## Step 2 — Expose Hecate as an A2A server

The A2A server is **off by default**. Enable it via env vars (or CLI):

```bash
# .env
A2A_SERVER_ENABLED=true
A2A_SERVER_URL=https://hecate.example.com   # public URL other agents reach
A2A_SERVER_NAME="Hecate (production)"
```

Restart Hecate to pick up the change:

```bash
hecate preflight  # confirm A2A endpoints will mount
docker compose restart api
```

### Verify the AgentCard is served

```bash
curl https://hecate.example.com/.well-known/agent-card.json | jq
```

Expected response (a JSON AgentCard describing Hecate):

```json
{
  "name": "Hecate (production)",
  "url": "https://hecate.example.com/a2a/",
  "version": "0.1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": true,
    "stateTransitionHistory": true
  },
  "skills": [
    {"id": "chat", "name": "OpenAI-compatible chat", "tags": ["chat", "rag", "tools"]},
    {"id": "workflow", "name": "Multi-agent workflow execution", "tags": ["workflow", "graph"]}
  ],
  "authentication": {
    "schemes": ["bearer"]
  }
}
```

> **`A2A_SERVER_URL` is the external URL** — what other agents see. Set this to your reverse proxy's public address (e.g. `https://hecate.example.com`), **not** the internal Docker hostname. This value goes into the `url` field of the AgentCard.

### Verify the JSON-RPC endpoint

```bash
# List supported JSON-RPC methods
curl -X POST https://hecate.example.com/a2a/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "rpc.discover", "params": {}}' | jq
```

You should see a list of methods including `tasks/send`, `tasks/sendSubscribe`, `tasks/get`, `tasks/cancel`.

---

## Step 3 — Call an external A2A agent from Hecate

You can call an external A2A agent **from inside a Hecate workflow** using the `a2a_call` tool. First, register the remote agent as a plugin:

```bash
curl -X POST http://localhost:8000/api/plugins/create \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "manifest": {
      "name": "external-research-agent",
      "version": "1.0.0",
      "type": "a2a",
      "entry": "a2a://https://research.example.com/a2a/"
    }
  }'
```

Save the returned `id` and enable the plugin to activate the connection:

```bash
curl -X POST http://localhost:8000/api/plugins/<PLUGIN_ID>/enable \
  -H "Authorization: Bearer dev-key-change-me"
```

Hecate fetches the AgentCard and registers all `skills` as tools under the name `a2a_external-research-agent_<skill_id>`. List them:

```bash
hecate tool list --source a2a
```

```
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━┓
┃ name                                   ┃ source ┃ description    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━┩
│ a2a_external-research-agent_arxiv      │ a2a    │ Search arXiv   │
│ a2a_external-research-agent_summary    │ a2a    │ Summarize text │
└────────────────────────────────────────┴────────┴────────────────┘
```

Bind one to a Hecate agent and chat — the LLM will call the external agent through the A2A protocol:

```bash
# Attach the tool to your existing research agent
hecate agent update <AGENT_ID> \
  --tools "a2a_external-research-agent_arxiv,web_search"
```

```bash
hecate chat send <AGENT_ID> "Find the three most-cited arXiv papers from 2025 about retrieval-augmented generation and summarize them."
```

The agent calls the remote A2A agent over HTTPS, receives an Artifact containing the search results, and uses them as context for the final answer — all transparently to the user.

---

## Step 4 — Watch task lifecycle states

Every A2A task transitions through five states. You can observe them in real time:

| State | Meaning |
|---|---|
| `submitted` | Task accepted, queued for execution |
| `working` | Receiving agent is processing |
| `completed` | Task finished, Artifact is ready |
| `failed` | Task failed (check error message) |
| `canceled` | Caller canceled before completion |

When Hecate calls an external A2A agent, the engine logs each transition. To see them:

```bash
hecate session get <SESSION_ID> --include-events
```

```
events:
  - type: a2a_task_submitted  task_id: 7c3f...   state: submitted
  - type: a2a_task_working    task_id: 7c3f...   state: working
  - type: a2a_task_artifact   task_id: 7c3f...   size: 2.3KB
  - type: a2a_task_completed  task_id: 7c3f...   state: completed
```

The Pregel runtime appends every state change to the execution event log (Log-as-Truth) — failed or canceled tasks are fully reproducible and resumable.

---

## Step 5 — Stream task updates over SSE

For long-running external tasks (e.g., a research agent that takes minutes), use SSE instead of polling. Hecate exposes a streaming task endpoint when the external agent supports `streaming`:

```python
# Inside a Hecate workflow / Python script
from hecate.a2a.client import A2AClient

client = A2AClient("https://research.example.com/a2a/")

async with client.stream_task(
    message="Generate a survey of LLM evaluation benchmarks from 2024-2026",
) as stream:
    async for update in stream:
        print(f"[{update.state}] {update.message}")
        if update.artifact:
            print(f"---ARTIFACT---\n{update.artifact.text}\n---")
```

The `stream_task` context manager yields each state change as it arrives. The connection is closed automatically when the task completes (or you exit the `async with` block early).

---

## Step 6 — Sign and verify AgentCards (trust)

The A2A protocol supports signed AgentCards so a calling agent can verify the receiving agent's identity before sending sensitive tasks. Hecate uses **ES256 + JWS + RFC 8785 JSON Canonicalization**.

### Generate a keypair for Hecate's AgentCard

```python
from hecate.a2a.signing import generate_es256_keypair, sign_agent_card, verify_agent_card

private_jwk, public_jwk = generate_es256_keypair()

# Print the public key to put in your well-known endpoint
print(public_jwk)
```

### Sign your AgentCard

```python
import json

agent_card = {
    "name": "Hecate (production)",
    "url": "https://hecate.example.com/a2a/",
    # ... full AgentCard payload
}

signed_jws = sign_agent_card(
    agent_card=agent_card,
    private_jwk=private_jwk,
    kid="hecate-prod-2026",  # Key ID — rotate per environment
)

# Publish at /.well-known/agent-card.json (the JWS itself, not the JSON)
```

### Verify a remote agent's signed card

```python
from hecate.a2a.signing import verify_agent_card

remote_jws = await fetch_signed_agent_card("https://research.example.com/.well-known/agent-card.json")
remote_card = verify_agent_card(remote_jws, expected_kid="research-prod-2026")

if remote_card is None:
    raise RuntimeError("AgentCard signature invalid or kid mismatch — refusing to send tasks")

# Safe to proceed
print(f"Verified agent: {remote_card['name']}")
```

> **Trust anchor decision**: For highest assurance, pin the `kid` (key ID) per remote agent rather than trusting any well-formed JWS. Rotate `kid` values during scheduled key rotations.

---

## How it fits together

```
┌──────────────────────────────────────────────────────────────────┐
│  Hecate Workflow                                                  │
│                                                                  │
│   ┌────────────┐    a2a_call    ┌──────────────────────────┐    │
│   │  LLM node  │ ──────────────▶│  External A2A agent       │    │
│   │  (Pregel)  │                │  (Hecate / LangGraph /    │    │
│   │            │ ◀──────────────│   CrewAI / Autogen / …)   │    │
│   └────────────┘    Artifact    └──────────────────────────┘    │
│         │                                                       │
│         │  tasks/send (JSON-RPC 2.0)                             │
│         ▼                                                       │
│   ┌──────────────────────────┐                                  │
│   │  A2A client connection   │  ← circuit breaker, retries,    │
│   │  pool (per remote agent) │     caching of AgentCard         │
│   └──────────────────────────┘                                  │
│                                                                  │
│   ┌──────────────────────────────────────────────────────────┐   │
│   │  Event store: each task transition (submitted/working/    │   │
│   │  completed/failed/canceled) recorded with timestamp       │   │
│   └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Troubleshooting

### AgentCard returns 404

The A2A server is not enabled or `A2A_SERVER_URL` is wrong. Run `hecate preflight` — it should list `a2a_server` in the output. Confirm `A2A_SERVER_ENABLED=true` in `.env` and restart `uvicorn`.

### External agent times out

Hecate's A2A client has a default timeout of 30 seconds. For longer tasks, use `stream_task` instead of `send_task`, and configure timeout in the agent config:

```python
from hecate.a2a.client import A2AClient, ClientConfig

client = A2AClient(
    "https://research.example.com/a2a/",
    config=ClientConfig(timeout_seconds=600, retry_policy="exponential_backoff"),
)
```

### Signature verification fails

The remote agent's `kid` doesn't match your trusted key registry. Either: (1) update your registry with the new `kid`, (2) reject the connection, or (3) loosen to "any valid JWS from a trusted domain". Never silently accept invalid signatures.

### Task stuck in `working`

The receiving agent has crashed or the network dropped mid-task. Use `tasks/get` (JSON-RPC) to check status, or `tasks/cancel` if you want to abandon and retry.

---

## Summary

You now know how to:

- **Expose Hecate as an A2A server** with `A2A_SERVER_ENABLED=true` and a public `A2A_SERVER_URL`
- **Discover external A2A agents** by registering them as plugins of `type: a2a`
- **Call external agents** transparently from Hecate workflows via the `a2a_*` tool prefix
- **Observe task lifecycle** (submitted → working → completed/failed/canceled) via the event store
- **Stream long tasks** over SSE instead of polling
- **Sign and verify AgentCards** with ES256 + JWS for cross-agent trust

## Next steps

- **[OpenAI SDK Compatibility](10-openai-compatibility.md)** — use Hecate as a drop-in replacement for OpenAI in your existing code.
- **[Multi-Agent Orchestration](04-multi-agent.md)** — wire A2A calls into a Hecate workflow alongside local agents.
- **[Enable A2A Server](../how-to/enable-a2a-server.md)** — operational checklist for production deployment (reverse proxy, TLS, signing keys).
- **[A2A Protocol spec](https://a2a-protocol.org/)** — full protocol reference.