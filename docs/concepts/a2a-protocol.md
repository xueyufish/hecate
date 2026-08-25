# A2A Protocol

The **Agent-to-Agent (A2A) protocol** is Hecate's standard for cross-framework, cross-vendor agent interoperability. It's how a Hecate agent talks to a LangGraph agent talks to a CrewAI agent — all speaking the same wire protocol.

This document explains the **conceptual model**: what A2A is, why it matters, and how it relates to MCP. For the implementation, see [A2A Architecture](../design/a2a-architecture.md). For hands-on usage, see [Tutorial: A2A Protocol](../tutorials/09-a2a-protocol.md).

---

## What A2A solves

Without a standard, every agent framework invents its own way to expose capabilities. Hecate might expose `/api/agents/execute`, LangGraph might expose `/runs`, CrewAI might expose `/kickoff`. To integrate them, you'd write a custom adapter for each pair — **N×M integration problem**.

A2A solves this with a **shared wire protocol** — like HTTP for the web, or SMTP for email. Once Hecate and LangGraph both implement A2A, they can talk to each other **without knowing about each other**.

```
                    ┌─────────────────────────────────┐
                    │   A2A protocol (Linux Foundation) │
                    │   - AgentCard discovery          │
                    │   - JSON-RPC 2.0 task submission │
                    │   - SSE streaming updates        │
                    │   - Signed AgentCards (JWS)      │
                    └─────────────────────────────────┘
                                ▲           ▲
                                │           │
              ┌─────────────────┘           └─────────────────┐
              │                                                 │
        ┌─────▼──────┐                                  ┌──────▼──────┐
        │  Hecate    │ ←──── A2A task ────────→      │  LangGraph  │
        │  A2A       │                                  │  A2A        │
        │  server    │ ──── A2A artifact ─────→      │  client     │
        └────────────┘                                  └─────────────┘
```

---

## A2A vs MCP: different axes

Hecate implements both A2A and MCP. They serve **different roles**:

| | A2A | MCP |
|---|---|---|
| **Direction** | Agent ↔ Agent (horizontal) | Agent ↔ Tool (vertical) |
| **Mental model** | "Hire another agent to do work" | "Use a tool to do something" |
| **Topology** | Peer-to-peer (no central server) | Client-server (tools hosted) |
| **Auth** | Signed AgentCards + bearer | Bearer / API key |
| **State** | Long-running tasks (minutes to hours) | Short requests (seconds) |
| **Use case** | Cross-agent delegation | External capability access |

**When to use which**:

| Scenario | Use |
|---|---|
| "Ask another Hecate agent to research" | **A2A** |
| "Call GitHub to look at a PR file" | **MCP** (use GitHub's MCP server) |
| "Send a notification to Slack" | **MCP** (Slack MCP server) or Notifier plugin |
| "Coordinate with a planning agent" | **A2A** |
| "Connect to a vector store" | **MCP** |

A single agent can simultaneously use **both** — they're orthogonal.

---

## The five core concepts

### 1. AgentCard

A JSON manifest that describes an agent's identity and capabilities. Served at a well-known URL:

```http
GET https://hecate.example.com/.well-known/agent-card.json
```

```json
{
  "name": "Hecate (production)",
  "url": "https://hecate.example.com/a2a/",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "stateTransitionHistory": true
  },
  "skills": [
    {"id": "chat", "name": "OpenAI-compatible chat", "tags": ["chat", "rag", "tools"]},
    {"id": "workflow", "name": "Multi-agent workflow execution"}
  ],
  "securitySchemes": {
    "apiKeyAuth": {"location": "header", "name": "X-API-Key"}
  }
}
```

**Purpose**: lets other agents discover what this agent can do, without trying it first.

### 2. Task

The unit of work. When one agent wants another to do something, it submits a task. A task has:

- A unique `id`
- A `status` (lifecycle state — see below)
- An optional list of `messages` (the inputs / conversation)
- A list of `artifacts` (the outputs / results)

Tasks are **stateful** and **resumable** — you can poll, stream updates, or cancel them.

### 3. Message

A unit of input or output within a task. Same shape as OpenAI's chat messages:

```json
{
  "role": "user",
  "parts": [
    {"type": "text", "text": "Find the latest papers on RAG evaluation"}
  ]
}
```

### 4. Artifact

A unit of output from a completed task. Can be text, file, or structured data:

```json
{
  "type": "text",
  "text": "The latest research suggests..."
}
```

### 5. Skill

A **named capability** that an agent exposes. Skills are listed in the AgentCard and become "tools" from the calling agent's perspective.

| Concept | Analogy |
|---|---|
| Agent | Company |
| AgentCard | Company website |
| Skill | Department / service |
| Task | Project / work item |
| Message | Email in the project |
| Artifact | Deliverable |

---

## Task lifecycle

Every task transitions through five states:

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
            │       │  completed   │  ← terminal
            │       └──────────────┘
            │
            │       ┌──────────────┐
            ├──────▶│   failed     │  ← terminal
            │       └──────────────┘
            │
            │       ┌──────────────┐
            └───────│   canceled   │  ← terminal
                    └──────────────┘
```

| State | Meaning |
|---|---|
| `submitted` | Task accepted, queued |
| `working` | Remote agent is processing |
| `completed` | Terminal — artifact available |
| `failed` | Terminal — error in task |
| `canceled` | Terminal — caller abandoned |

Every transition is recorded with timestamp and trigger, enabling full state transition history (`stateTransitionHistory: true`).

---

## The trust problem

Without trust, A2A has a security hole: an attacker could create a fake AgentCard pointing at their malicious agent, claiming to be "GitHub Research Agent". The calling agent would send tasks to the attacker.

A2A solves this with **signed AgentCards** using JWS (RFC 7515) + RFC 8785 JSON Canonicalization + ES256 algorithm:

```
1. Hecate A2A server signs its AgentCard with its private ES256 key:
   signed_jws = sign(agent_card, private_jwk, kid="hecate-prod-2026")
   publishes signed_jws at /.well-known/agent-card.json

2. Hecate A2A client fetches the remote card:
   remote_card = fetch("https://remote.example.com/.well-known/agent-card.json")

3. Hecate A2A client verifies the signature with the public key:
   if not verify(remote_card, expected_kid="remote-prod-2026"):
       raise "refusing to send tasks to unverified agent"
```

**Trust anchor policy** is yours to choose:

| Posture | When to use | Trade-off |
|---|---|---|
| **No verification** | Local dev | Accepts any card |
| **Domain pinning** | Known partners | Trusts all from whitelisted domain |
| **`kid` pinning** (recommended) | Production | Trust specific keys, rotate on schedule |

---

## Discovery: finding agents

The discovery flow:

```
1. Agent A wants to talk to Agent B
2. Agent A has Agent B's URL (out-of-band, like DNS)
3. Agent A fetches B's AgentCard: GET B/.well-known/agent-card.json
4. Agent A validates the signature (if signed)
5. Agent A reads B's skills, decides which to call
6. Agent A submits a task: POST B/a2a/ with JSON-RPC 2.0
7. Agent A polls or streams updates
```

There's no central registry in A2A — agent addresses are passed out-of-band (like email addresses). For registries, see [post-1.0] (community Agent Gallery).

---

## Streaming long tasks

For tasks that take minutes (e.g., "research this question"), use `tasks/sendSubscribe` instead of `tasks/send`. The server streams updates via Server-Sent Events (SSE):

```
event: task.status
data: {"task_id": "abc", "state": "working"}

event: task.artifact
data: {"task_id": "abc", "artifact_index": 0, "chunk": "Once upon a time..."}

event: task.status
data: {"task_id": "abc", "state": "completed"}
```

This gives the calling agent real-time feedback as the remote agent works.

---

## Hecate's role

Hecate operates in **both directions** in A2A:

| Role | When Hecate acts as it |
|---|---|
| **A2A server** | Other agents call Hecate's agents |
| **A2A client** | Hecate's agents call other agents |

This duality is unique among the platforms researched. Most A2A implementations are either server-only (orchestration platforms) or client-only (single-process agents). Hecate treats A2A as a **bidirectional fabric**.

---

## Compatibility

Hecate implements **A2A** (Linux Foundation). Interop with:

- Official `a2a-sdk` (Python) — see [a2a-protocol.org](https://a2a-protocol.org/) for SDK packages
- `a2a-js` (TypeScript) — see [a2a-protocol.org](https://a2a-protocol.org/) for SDK packages
- Any A2A-compliant agent framework (when they ship A2A support)

Hecate's types wrap the official `a2a-sdk` types, so bumping the SDK brings the latest spec.

---

## What's NOT in A2A (yet)

| Out of scope | Why | Schedule |
|---|---|---|
| Centralized agent registry | A2A has no central server | Community Gallery (P5) |
| Push notifications (server → client webhook) | Not yet implemented | P3 |
| Multi-modal streaming (audio, video) | LLM providers don't support | Post-1.0 |
| Cross-workspace A2A API keys | Currently single `HECATE_API_KEYS` | v1.1 |

---

## Related documents

- [A2A Architecture](../design/a2a-architecture.md) — implementation details, JSON-RPC method catalog, server endpoints
- [Tutorial: A2A Protocol](../tutorials/09-a2a-protocol.md) — hands-on usage, both server and client sides
- [How-to: Enable A2A Server](../how-to/enable-a2a-server.md) — operational recipe
- [Tools, MCP, and A2A](tools-and-mcp.md) — when to use A2A vs MCP
- [ADR-011: A2A Protocol Adoption](../design/adr/011-a2a-protocol-adoption.md) — why we adopted A2A
- [Threat Model](../design/threat-model.md) — A2A trust model in security context