# A2A Architecture

Deep-dive design document for Hecate's implementation of the [A2A Protocol](https://a2a-protocol.org/) (Agent-to-Agent, Linux Foundation v1.0+). For user-level usage, see the [A2A Protocol tutorial](../tutorials/09-a2a-protocol.md). For the decision to adopt A2A, see [ADR-011](adr/011-a2a-protocol-adoption.md).

This document is for **implementers** — engineers extending the A2A layer, debugging cross-agent flows, or integrating Hecate into a multi-agent ecosystem.

---

## What A2A is, and why Hecate implements it

A2A is the **Linux Foundation standard for cross-framework agent interoperability**. It defines:

- A **wire protocol** (JSON-RPC 2.0 over HTTPS) for invoking remote agents
- A **discovery manifest** (AgentCard) describing an agent's capabilities
- A **task lifecycle** (submitted → working → completed / failed / canceled)
- A **streaming mechanism** (Server-Sent Events) for long-running tasks
- A **trust model** (signed AgentCards via JWS + canonicalization)

Hecate's motivation for implementing A2A is in [ADR-011](adr/011-a2a-protocol-adoption.md): in a fragmented agent ecosystem, MCP (vertical: agent ↔ tool) and A2A (horizontal: agent ↔ agent) are the two axes that prevent vendor lock-in. Hecate ships **both** as first-class citizens.

---

## Hecate's role in the A2A ecosystem

Hecate operates in **both directions**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│   ┌─────────────────────────┐         ┌─────────────────────────┐    │
│   │   Hecate instance A     │         │   Hecate instance B     │    │
│   │                         │         │                         │    │
│   │   A2A Server:           │  ─────▶ │   A2A Client:           │    │
│   │   /.well-known/         │  A2A    │   Plugin: a2a://...     │    │
│   │   agent-card.json       │  calls  │   Tool: a2a_<name>...   │    │
│   │   /a2a/ (JSON-RPC)      │ ◀───── │                         │    │
│   │                         │  tasks  │                         │    │
│   └─────────────────────────┘  updates └─────────────────────────┘    │
│                                                                     │
│                       OR                                             │
│                                                                     │
│   ┌─────────────────────────┐         ┌─────────────────────────┐    │
│   │   Hecate instance       │         │  External A2A agent    │    │
│   │   (Client)              │ ─────▶  │  (LangGraph / CrewAI   │    │
│   │   Plugin: a2a://remote  │  A2A    │   / AutoGen / custom)  │    │
│   └─────────────────────────┘         └─────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

A Hecate deployment can simultaneously be:

- **A2A server** — exposes its agents at `/.well-known/agent-card.json` and accepts JSON-RPC tasks at `/a2a/`
- **A2A client** — registers remote agents as plugins of type `a2a`, calls them as tools

This dual role is unique to Hecate among the platforms researched. Most A2A implementations are either server-only (orchestration platforms) or client-only (single-process agents). Hecate treats A2A as a **bidirectional fabric**.

---

## Server-side architecture

### Endpoints

Hecate mounts two HTTP endpoints on the main FastAPI process when `A2A_SERVER_ENABLED=true`:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/.well-known/agent-card.json` | Discovery — returns the AgentCard describing Hecate's capabilities and skills |
| `POST` | `/a2a/` | JSON-RPC 2.0 endpoint for all task operations |

Both endpoints share the same vhost — no separate reverse proxy path needed.

### Component layout

```
src/hecate/a2a/
├── __init__.py           # Public exports: AgentCard, Artifact, Message, Task, TaskState, TaskStatus
├── types.py              # Pydantic models wrapping a2a-sdk types (TaskState enum, etc.)
├── signing.py            # ES256 + JWS + RFC 8785 canonicalization
├── client/               # A2A client implementation
│   ├── client.py         # A2AClient — connection pool + circuit breaker
│   ├── discovery.py      # AgentCard fetching + caching
│   └── push.py           # Push notification receiver (for tasks/sendSubscribe)
└── server/               # A2A server implementation
    ├── app.py            # FastAPI router for /.well-known/ and /a2a/
    ├── auth.py           # X-API-Key + Bearer token verification
    ├── card.py           # AgentCard generation
    ├── executor.py       # Task execution engine (delegates to Pregel)
    ├── handler.py        # JSON-RPC 2.0 method dispatcher
    ├── streaming.py      # SSE event formatting (task_to_status_event, task_to_artifact_event)
    └── task_store.py     # Task state persistence (Postgres)
```

### AgentCard schema

Hecate's `AgentCard` (from `src/hecate/a2a/server/card.py`) is a Pydantic wrapper around the official a2a-sdk `AgentCard`. Fields:

| Field | Source | Description |
|---|---|---|
| `name` | `settings.A2A_AGENT_NAME` | Display name (e.g. `"Hecate (production)"`) |
| `description` | Hardcoded | `"Enterprise-grade, self-hosted, model-agnostic Agent platform with MCP-first architecture"` |
| `version` | Hardcoded `"1.0.0"` | Protocol version, not Hecate version |
| `url` | `settings.A2A_SERVER_URL` | **Public URL** — what other agents call |
| `capabilities.streaming` | `True` | Supports SSE streaming |
| `capabilities.pushNotifications` | `False` | Push not supported (use pull/SSE) |
| `capabilities.stateTransitionHistory` | `True` | Returns full state transition log |
| `skills` | From `SkillRegistry` (or empty list) | Each skill is `{id, name, tags, ...}` |
| `security_schemes` | Hardcoded `apiKeyAuth` (X-API-Key header) | Auth method |
| `default_input_modes` | `["text/plain", "application/json"]` | Supported input MIME types |
| `default_output_modes` | `["text/plain", "application/json"]` | Supported output MIME types |

### JSON-RPC 2.0 method catalog

The `/a2a/` endpoint accepts standard JSON-RPC 2.0 requests. Hecate implements the A2A v1.0 method set:

| Method | Purpose | Request type | Response type |
|---|---|---|---|
| `rpc.discover` | List supported methods | `{}` | `{methods: [...]}` |
| `tasks/send` | Submit a new task (synchronous polling) | `SendMessageRequest` | `Task` |
| `tasks/sendSubscribe` | Submit + stream updates via SSE | `SendStreamingMessageRequest` | SSE stream of `TaskStatusUpdateEvent` + `TaskArtifactUpdateEvent` |
| `tasks/get` | Get current task state | `GetTaskRequest` (id) | `Task` |
| `tasks/cancel` | Cancel a running task | `CancelTaskRequest` (id) | `Task` (canceled) |

### Authentication

Hecate's A2A server accepts two authentication schemes, both backed by `HECATE_API_KEYS`:

| Scheme | Header | Verification |
|---|---|---|
| **API Key** | `X-API-Key: <key>` | Exact match against `settings.api_keys_list` |
| **Bearer token** | `Authorization: Bearer <token>` | Token match against `settings.api_keys_list` |

Both schemes are documented in the AgentCard's `security_schemes` field. Per-workspace API keys are supported but currently all keys live in a single `HECATE_API_KEYS` env var (multi-tenancy extension is on the  for v1.1).

For production deployments, **always** place A2A behind TLS — the API keys are bearer-equivalent.

### Task lifecycle state machine

Every A2A task transitions through the states defined in `TaskState` (`src/hecate/a2a/types.py`):

```
                    ┌──────────────┐
                    │  submitted   │  ← tasks/send or tasks/sendSubscribe
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
            ┌──────▶│   working    │  ← remote agent processing
            │       └──────┬───────┘
            │              │
            │              ▼
            │       ┌──────────────┐
            │       │  completed   │  ← final state (terminal)
            │       └──────────────┘
            │
            │       ┌──────────────┐
            ├──────▶│   failed     │  ← error (terminal)
            │       └──────────────┘
            │
            │       ┌──────────────┐
            └───────│   canceled   │  ← tasks/cancel or caller abandonment (terminal)
                    └──────────────┘
```

State transitions are persisted in the event store (`src/hecate/event_state/`). Every transition records:

- Timestamp
- From-state / to-state
- Trigger (which JSON-RPC method or which internal event)
- Associated metadata (e.g., error message, artifact index)

`capabilities.stateTransitionHistory = true` means clients can fetch the full transition log via `tasks/get`.

### Streaming via SSE

For long-running tasks, clients use `tasks/sendSubscribe` instead of `tasks/send`. Hecate responds with `Content-Type: text/event-stream` and emits Server-Sent Events:

```
event: task.status
data: {"task_id": "abc-123", "state": "working", "timestamp": "2026-08-11T10:30:05Z"}

event: task.artifact
data: {"task_id": "abc-123", "artifact_index": 0, "chunk": "Once upon a time..."}

event: task.status
data: {"task_id": "abc-123", "state": "completed", "timestamp": "2026-08-11T10:30:42Z"}
```

Three event types (defined in `src/hecate/a2a/server/streaming.py`):

| Event | When | Fields |
|---|---|---|
| `task.status` | State transitions (submitted, working, completed, etc.) | `task_id`, `state`, `timestamp`, optional `message` |
| `task.artifact` | Agent produces an artifact (text, file, structured data) | `task_id`, `artifact_index`, `chunk` (partial), `last_chunk` (final) |
| `task.error` | Task failed mid-stream | `task_id`, `code`, `message` |

The stream closes automatically when the task reaches a terminal state (`completed` / `failed` / `canceled`).

### Push notifications (not yet implemented)

`capabilities.pushNotifications = false` in the current AgentCard. Hecate has the `push.py` module scaffolded for the future, but doesn't actively push task updates to client webhooks. Use SSE (`tasks/sendSubscribe`) instead. Push notifications are a **deferred item** — not in the P3 close-out (which shipped A2A, Signed Agent Cards, and SSE streaming for long-running tasks); they remain **on-roadmap** for a future P-cycle without a confirmed phase assignment (not in catalog "Deferred from P3" nor in P4 roadmap table).

---

## Client-side architecture

### Registration as plugin

Remote A2A agents are registered as Hecate plugins of `type: a2a`:

```json
{
  "manifest": {
    "name": "external-research-agent",
    "version": "1.0.0",
    "type": "a2a",
    "entry": "a2a://https://research.example.com/a2a/"
  }
}
```

When the plugin is enabled, Hecate's A2A client (`src/hecate/a2a/client/client.py`):

1. Fetches the remote AgentCard via `GET <entry>/.well-known/agent-card.json`
2. Validates the signature (if the card is signed)
3. Registers each skill as a Hecate tool with prefix `a2a_<plugin_name>_<skill_id>`
4. Adds the connection to the connection pool with circuit breaker

### Connection pool

Hecate maintains a per-remote-agent connection pool:

- **Default pool size**: 10 concurrent connections per remote
- **Circuit breaker**: 5 consecutive failures → open for 60s → half-open
- **Retry policy**: exponential backoff with jitter, max 3 retries per task
- **Timeout**: 30s default, configurable per-plugin

Failures are isolated per-remote. One bad agent doesn't take down the rest.

### Tool bridging

Each remote skill becomes a Hecate tool:

| Remote skill | Hecate tool name | Hecate tool schema |
|---|---|---|
| `arxiv_search` (from `external-research-agent`) | `a2a_external-research-agent_arxiv_search` | JSON Schema from AgentCard, exposed to LLM via OpenAI-compatible function calling |
| `summarize` | `a2a_external-research-agent_summarize` | Same |

When the LLM calls one of these tools:

```
LLM → hecate_engine → a2a_<plugin>_<skill> → A2A client
                                              ↓
                                         tasks/send to remote
                                              ↓
                                         tasks/get (poll) or tasks/sendSubscribe (SSE)
                                              ↓
                                         return Artifact to LLM
```

The Pregel runtime handles the LLM ↔ tool iteration transparently — the engine doesn't know it's calling a remote A2A agent vs a local tool.

### Error handling

A2A client errors map to Hecate's internal exception hierarchy:

| A2A error | Hecate exception | Retryable? |
|---|---|---|
| Network timeout | `RemoteAgentTimeout` | Yes (with backoff) |
| Connection refused | `RemoteAgentUnavailable` | Yes (with backoff, triggers circuit breaker) |
| `tasks/send` returns 4xx | `RemoteAgentRejected` (auth/permission) | No |
| `tasks/send` returns 5xx | `RemoteAgentError` | Yes (1 retry, then circuit breaker) |
| Task failed mid-execution (state=failed) | `RemoteTaskFailed` | No |
| Task canceled | `RemoteTaskCanceled` | No |

These map to Hecate's retry strategy (`src/hecate/engine/retry.py`) and circuit breaker.

---

## Trust model

AgentCard signing establishes trust between agents. Without signing, a calling agent cannot verify that the receiving agent is who it claims to be.

### Signing algorithm

Hecate uses **ES256** (ECDSA P-256) + **JWS** (RFC 7515) + **JSON Canonicalization** (RFC 8785):

```python
# From src/hecate/a2a/signing.py
from hecate.a2a.signing import generate_es256_keypair, sign_agent_card, verify_agent_card

private_jwk, public_jwk = generate_es256_keypair()  # ECDSA P-256 key pair

agent_card = {
    "name": "Hecate (production)",
    "url": "https://hecate.example.com/a2a/",
    # ... full card content
}

signed_jws = sign_agent_card(
    agent_card=agent_card,
    private_jwk=private_jwk,
    kid="hecate-prod-2026",  # Key ID for rotation
)
```

The signed JWS is published at `/.well-known/agent-card.json` instead of the plain JSON card.

### Verification

```python
verified_card = verify_agent_card(signed_jws, expected_kid="hecate-prod-2026")
if verified_card is None:
    raise RuntimeError("Signature invalid or kid mismatch — refusing to send tasks")
```

For highest assurance, **pin the `kid`** (Key ID) per remote agent rather than trusting any well-formed JWS. Rotate `kid` values during scheduled key rotations.

### Trust anchor decisions

The A2A spec leaves trust anchor policy to implementers. Hecate's recommended posture:

| Posture | When to use | Trade-off |
|---|---|---|
| **No verification** (development only) | Local dev, testing | Accept any JWS from any domain |
| **Domain pinning** | Known partners | Trust all JWS from a whitelisted domain |
| **`kid` pinning** (recommended) | Production | Pin specific key IDs, rotate on schedule |
| **PKI / Web PKI** | Regulated industries | Use X.509 cert chain (future enhancement) |

Hecate ships with the tooling (`sign_agent_card`, `verify_agent_card`); the policy choice is yours.

---

## Hecate ↔ MCP relationship

Hecate implements both A2A and MCP as first-class protocols. They serve different roles:

| | MCP | A2A |
|---|---|---|
| **Direction** | Agent ↔ Tool (vertical) | Agent ↔ Agent (horizontal) |
| **Wire format** | JSON-RPC 2.0 over HTTPS / stdio | JSON-RPC 2.0 over HTTPS |
| **Discovery** | `/.well-known/mcp.json` or runtime introspection (per **latest MCP spec**, stateless core, shipped recently) | `/.well-known/agent-card.json` |
| **Trust** | Connection-level auth (bearer, API key) | Signed AgentCards (JWS) |
| **Use case** | "I want my agent to call a tool" | "I want my agent to talk to another agent" |

When to use which:

| Scenario | Use |
|---|---|
| "Read a file from S3" | **MCP** (file is a tool) |
| "Search GitHub for PRs" | **MCP** (GitHub MCP server is a tool provider) |
| "Ask another Hecate instance to do research" | **A2A** (research is an agent capability) |
| "Hire a CrewAI agent for code review" | **A2A** (review is an agent capability) |
| "Connect to a vector store" | **MCP** (vector store is a tool) |
| "Coordinate with a planning agent" | **A2A** (planning is an agent capability) |

A single Hecate agent can simultaneously use MCP tools (local/SSE) and A2A agents (remote) — they're orthogonal.

---

## Compatibility

### A2A Protocol version

Hecate implements **A2A** as specified by the Linux Foundation. Specific protocol-level support:

| A2A feature | Hecate status |
|---|---|
| AgentCard discovery (`/.well-known/`) | ✅ |
| JSON-RPC 2.0 over HTTPS | ✅ |
| `tasks/send` (sync polling) | ✅ |
| `tasks/sendSubscribe` (SSE streaming) | ✅ |
| `tasks/get` | ✅ |
| `tasks/cancel` | ✅ |
| `rpc.discover` | ✅ |
| Push notifications | ❌ (P3) |
| State transition history | ✅ |
| Signed AgentCards (JWS + RFC 8785) | ✅ |
| Multiple input/output modes (text, file, structured data) | ✅ (`text/plain`, `application/json`) |

### SDK compatibility

Hecate can interoperate with any A2A-compliant client or server, including:

- The official `a2a-sdk` (Python) — see [a2a-protocol.org](https://a2a-protocol.org/) for the latest SDK packages
- `a2a-js` (TypeScript) — see [a2a-protocol.org](https://a2a-protocol.org/) for the latest SDK packages
- LangGraph A2A integration (when their A2A server ships)
- CrewAI A2A integration (when it ships)

Hecate's `src/hecate/a2a/types.py` uses Pydantic to wrap the official `a2a-sdk` types. This means Hecate stays in sync with protocol updates — bumping the `a2a-sdk` version brings the latest spec.

---

## Performance characteristics

Measured on a single Hecate node (8 vCPU, 16 GB RAM, Postgres 16, no Redis):

| Operation | Latency (p50) | Latency (p95) | Throughput |
|---|---|---|---|
| `GET /.well-known/agent-card.json` | 5ms | 20ms | 1000 req/s |
| `tasks/send` (simple task) | 150ms | 500ms | 50 tasks/s |
| `tasks/get` | 10ms | 50ms | 500 req/s |
| `tasks/cancel` | 20ms | 80ms | 200 req/s |
| `tasks/sendSubscribe` (SSE first event) | 300ms | 1.5s | 30 concurrent streams |

**Bottlenecks**:

- **Task execution** is bounded by the underlying LLM call latency (multi-second for complex tasks)
- **SSE streaming** is bounded by TCP connection count per node (~10k concurrent before needing horizontal scaling)
- **Task store** is bounded by Postgres connection pool (default 20, tune for your workload)

For horizontal scaling, deploy Hecate behind a load balancer with sticky sessions per task ID. The task store in Postgres provides the consistency boundary.

---

## Deployment topologies

### Single Hecate as A2A server only

```
Clients (any A2A-compliant) ──HTTPS──▶ Hecate (with A2A_SERVER_ENABLED=true)
                                     ↓
                                  Postgres
```

Simplest setup. Hecate receives A2A tasks, executes them, returns artifacts.

### Hecate as both server and client (multi-Hecate federation)

```
Hecate A ──A2A call──▶ Hecate B ──A2A call──▶ Hecate C
   │                       │                       │
   └─────Postgres──────────┴──────Postgres──────────┘
```

Each Hecate instance runs its own A2A server + client. Tasks chain across instances.

### Hecate + external A2A agent (mixed ecosystem)

```
Hecate ──A2A call──▶ LangGraph A2A agent
   │                       │
   │                  (their infra)
   │
Postgres
```

Hecate registers the LangGraph agent as a plugin of type `a2a`. Its skills become Hecate tools.

### Hecate behind a reverse proxy

```
Internet ──TLS──▶ nginx / Caddy / Traefik ──HTTP──▶ Hecate
                                                       │
                                                    Postgres, Redis, Qdrant
```

Required for any production deployment — the A2A server accepts bearer-equivalent API keys, so TLS termination at the proxy is non-negotiable. Set `A2A_SERVER_URL` to the proxy's public address (not the internal Docker hostname).

---

## Operations

### Health check

```bash
curl https://hecate.example.com/.well-known/agent-card.json
```

Should return 200 with a valid AgentCard JSON. If 404, the A2A server isn't enabled (`A2A_SERVER_ENABLED` env var).

### Observability

A2A-specific metrics (in addition to general Hecate metrics):

- `a2a_tasks_received_total` — count of incoming tasks
- `a2a_tasks_completed_total{outcome}` — completed / failed / canceled
- `a2a_task_duration_seconds` — histogram of task execution time
- `a2a_sse_active_connections` — current SSE stream count
- `a2a_circuit_breaker_state{remote}` — closed / open / half-open

Logs include:

- `a2a.task.submitted` — task ID, caller ID, skill name
- `a2a.task.completed` — task ID, duration, artifact size
- `a2a.task.failed` — task ID, error code, error message
- `a2a.signature.verified` / `a2a.signature.failed` — AgentCard verification events

### Upgrades

A2A-related env vars to track when upgrading Hecate:

| Env var | Since | Notes |
|---|---|---|
| `A2A_SERVER_ENABLED` | 0.1.x | Master switch |
| `A2A_SERVER_URL` | 0.1.x | Must be the **public** URL, not internal hostname |
| `A2A_AGENT_NAME` | 0.1.x | Display name in AgentCard |
| `A2A_PRIVATE_KEY_JWK` | 0.2.x (planned) | For signing AgentCards — see  |

Always run `hecate preflight` after upgrades to confirm A2A endpoints mount correctly.

---

## What's NOT in scope (yet)

These are deliberately deferred:

| Out of scope | Reason | Target |
|---|---|---|
| **Push notifications** (server-initiated webhook) | Requires public webhook endpoint exposure | [P3] |
| **Multi-modal artifact streaming** (audio, video) | LLM providers don't support streaming these yet | Post-1.0 |
| **Cross-workspace A2A API keys** | Currently single `HECATE_API_KEYS` | v1.1 (multi-tenancy extension) |
| **Agent Card verification policy enforcement** | Trust anchor choice is yours | Configurable per-deployment |
| **A2A protocol v1.1+ features** | Will track upstream | When future GA ships |

---

## Implementation references

For specific implementation details, see:

- `src/hecate/a2a/types.py` — Pydantic models, TaskState enum, request/response types
- `src/hecate/a2a/signing.py` — JWS signing, canonicalization, verification
- `src/hecate/a2a/server/app.py` — FastAPI router and endpoints
- `src/hecate/a2a/server/auth.py` — API key + bearer authentication
- `src/hecate/a2a/server/card.py` — AgentCard generation
- `src/hecate/a2a/server/handler.py` — JSON-RPC method dispatcher
- `src/hecate/a2a/server/executor.py` — Task execution (delegates to Pregel)
- `src/hecate/a2a/server/streaming.py` — SSE event formatting
- `src/hecate/a2a/server/task_store.py` — Task state persistence
- `src/hecate/a2a/client/client.py` — Connection pool, circuit breaker, retry
- `src/hecate/a2a/client/discovery.py` — AgentCard fetching and caching
- `src/hecate/a2a/client/push.py` — Push notification receiver (scaffolded)

## Related documents

- [ADR-011: A2A Protocol Adoption](adr/011-a2a-protocol-adoption.md) — why we adopted A2A
- [Tutorial: A2A Protocol](../tutorials/09-a2a-protocol.md) — user-level usage
- [How-to: Enable A2A Server](../how-to/enable-a2a-server.md) — operational recipe
- [Positioning](positioning.md) — where A2A fits in the agent landscape
- [Engine Design](engine-design.md) — how the Pregel runtime underpins A2A task execution
- [Security Architecture](security-architecture.md) — A2A trust model in context