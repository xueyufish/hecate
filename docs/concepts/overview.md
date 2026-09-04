# Architecture Overview

Hecate is organized in five code layers and ten product modules, with Security and Ecosystem as cross-cutting concerns. This page gives you the **mental model** — what Hecate is, how it fits together, and who should use it. For deep technical detail, see the [design documents](../design/).

---

## What Hecate is (in 30 seconds)

Hecate is an **open-source, self-hosted, Python-first agent platform** for engineering teams building production agents that need to live inside their own infrastructure. The five distinguishing features:

1. **Self-developed Pregel execution engine** — not a wrapper around LangGraph, a real runtime
2. **Self-hosted OSS (MIT)** — your data never leaves your network
3. **Multi-protocol by default** — MCP, A2A, and OpenAI-compatible API all first-class
4. **Multi-tenant by default** — Organization → Workspace → RBAC out of the box
5. **Engine-level extensibility** — many engine extension interfaces + multiple plugin SPI types, not just a plugin marketplace

---

## Five code layers

| Layer | Path | May import | Key rule |
|-------|------|-----------|----------|
| `runtime/` | `src/hecate/runtime/` | `jsonschema` only | Zero deps on services, api, models. Sole external exception: `jsonschema` for DSL validation. |
| domains (`studio/` `ops/` `tools/` `enterprise/` `channel/`) | `src/hecate/<domain>/` | `models/`, `runtime/ports`, `core/` | Domain modules depend on runtime abstract interfaces only, never on runtime implementations; cross-domain edges go through `core/composition/`. |
| `api/` | `src/hecate/api/` | `services/`, `models/` | Never imports `runtime/` directly — routes through services + `EnginePort`. |
| `models/` | `src/hecate/models/` | SQLAlchemy, Pydantic | Pure data definitions. No business logic. |
| `core/` | `src/hecate/core/` | config, database, DI, rate limiting | Infrastructure shared across all layers. |

### Layer dependency diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         api/                                │
│   ┌────────────────────────────────────────────────────────┐ │
│   │     FastAPI routes + middleware + dependencies        │ │
│   │     (REST API, OpenAI-compat, MCP, A2A endpoints)    │ │
│   └──────┬───────────────────────────────────────┬─────────┘ │
│          │                                       │         │
│          ▼                                       ▼         │
│   ┌────────────────────────────────────────────────────────┐ │
│   │                  services/                            │ │
│   │  LLM · KB · Memory · Tools · Backup · Audit · ...   │ │
│   │  (concrete implementations of engine abstractions)   │ │
│   └──────┬───────────────────────────────────────┬─────────┘ │
│          │                                       │         │
│          ▼                                       ▼         │
│   ┌─────────────────────┐            ┌────────────────────┐│
│   │      models/        │            │       core/        ││
│   │  SQLAlchemy ORM +   │            │  config · DB · DI  ││
│   │  Pydantic schemas   │            │  rate limiting     ││
│   └─────────────────────┘            └────────────────────┘│
│                                                              │
│   ┌────────────────────────────────────────────────────────┐ │
│   │                       engine/                          │ │
│   │  Pregel runtime · Channels · Event log · Workers      │ │
│   │  26 engine interfaces + multiple SPIs types                  │ │
│   │  (DEPS: only `jsonschema` — zero deps on services)   │ │
│   └────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

The engine layer defines 26 extension interfaces + multiple plugin SPI types. Services provide concrete implementations. The API layer orchestrates services. This separation keeps the engine testable with lightweight stubs.

---

## Ten product modules

Hecate comprises ten modules organized in a layered dependency hierarchy:

1. **Access Channel** — entry point for all external requests (OpenAI-compatible API, Management API, MCP Server, A2A endpoint)
2. **Agent Studio** — visual canvas (React Flow), agent configurator, prompt management, workflow builder
3. **Agent Engine** — self-built Pregel runtime with channel system, event-sourced execution state (Log-as-Truth) with checkpoint caches, and worker pool
4. **Ops Center** — observability, alerting, evaluation, cost governance, compliance
5. **Model Hub** — LiteLLM-powered LLM integration with routing, circuit breaker, A/B testing
6. **Tool Platform** — MCP-first tool ecosystem with Docker sandbox execution
7. **Knowledge & Memory** — RAG pipeline (Docling, BGE-M3, Qdrant) and four-level memory system
8. **Enterprise Foundation** — multi-tenancy (Org → Workspace → RBAC), async database, Alembic migrations
9. **Security** — cross-cutting: guardrail hooks, PII masking, LLM Guard, audit trail, RBAC
10. **Ecosystem** — cross-cutting: MCP, A2A, webhooks, OpenAI-compatible API

The 10 modules map to design docs in `docs/design/`:
- Agent Engine → [Engine Design](../design/engine-design.md)
- Agent Studio → [Visual Canvas Architecture](../design/visual-canvas-architecture.md)
- Knowledge & Memory → [Knowledge & Memory Design](../design/knowledge-memory-design.md)
- Tool Platform → [Tool Platform Design](../design/tool-platform-design.md)
- Model Hub → [Model Hub Design](../design/model-hub-design.md)
- Security → [Security Architecture](../design/security-architecture.md) + [Threat Model](../design/threat-model.md)
- Enterprise Foundation → [Multi-Tenancy Architecture](../design/multi-tenancy-architecture.md) + [Enterprise Foundation Design](../design/enterprise-foundation-design.md)
- Ops Center → [Observability Architecture](../design/observability-architecture.md) + [Ops Center Design](../design/ops-center-design.md)
- Access Channel → [Access Channel Design](../design/access-channel-design.md)
- Ecosystem → [A2A Architecture](../design/a2a-architecture.md) + [Ecosystem Design](../design/ecosystem-design.md)

---

## Request lifecycle

```
Client → Access Channel (auth, rate limit)
    → Agent Engine (load agent, compile graph, Pregel superstep loop)
        → EnginePort (LLM invoke / tool execute / knowledge query / checkpoint)
    → Response (streamed or complete)
```

At any point during execution, a node may call `interrupt()` to pause for human-in-the-loop approval. The event log commits up to the interrupt point, enabling resuming from exactly that point.

### Detailed flow with boundaries

```
[Client]
   │ HTTPS
   ▼
[Reverse proxy / TLS terminator]
   │
   ▼
[API layer: auth, rate limit, RBAC]
   │
   ├── /v1/chat/completions      → OpenAI-compatible (drop-in)
   ├── /api/*                    → Management API
   ├── /mcp/                     → MCP server
   ├── /a2a/                     → A2A server (JSON-RPC)
   │   └── /.well-known/agent-card.json (discovery)
   ▼
[Engine layer: load agent, compile graph]
   │
   ▼
[Pregel runtime: superstep loop]
   │
   ├── Pre-LLM hook → Guardrail checks
   ├── LLM call (via LiteLLM → 100+ providers)
   ├── Post-LLM hook → PII deanonymization
   ├── Tool call (via EnginePort)
   │   ├── Built-in tool
   │   ├── Custom tool (in-process)
   │   └── MCP tool (out-of-process)
    ├── Event log commit (after each step)
    └── Audit log event (every action)
```

---

## Key concepts cheat sheet

20 terms you need to know — each is a link to its full concept doc:

| Concept | One-line summary |
|---|---|
| **Agent** | A configured persona + model + tools; the unit of execution |
| [Agent Engine](engine.md) | The Pregel/BSP runtime that exemines agents |
| [Sessions](sessions.md) | The runtime unit holding conversation state |
| [Workflows](workflows.md) | A graph of nodes + edges compiled for the engine |
| [Skills](skills.md) | Named capabilities in the SkillRegistry |
| [Plugins](plugins.md) | Engine-internal code that extends Hecate |
| [MCP](tools-and-mcp.md) | Agent-to-tool protocol (external tools) |
| [A2A](a2a-protocol.md) | Agent-to-agent protocol (cross-framework) |
| [Knowledge](knowledge-rag.md) | RAG over documents stored in vector DB |
| [Memory](memory.md) | 4-level architecture (L1 working → L2 compressed → L3 user → L4 knowledge) |
| [Context Engineering](context-engineering.md) | Per-call pipeline that selects what goes in the LLM context |
| [Tools](tools-and-mcp.md) | Callable functions (built-in / custom / MCP) |
| [Guardrails](guardrails.md) | 4 hook types (Pre/Post LLM/Tool) that gate every action |
| [Model Hub](model-hub.md) | LLM provider layer (100+ providers via LiteLLM) |
| [Multi-Tenancy](multi-tenancy.md) | Org → Workspace → RBAC, many models with `workspace_id` |
| [Auth](auth-identity.md) | API keys, JWT, OIDC, SAML, LDAP, SCIM |
| [Observability](observability.md) | 4 signals: traces, metrics, logs, audit |
| [DLP](dlp.md) | Outbound content scanning (PII, secrets, leakage) |
| [CLI](cli.md) | 3 entry points: `hecate`, `hecate-migrate`, `hecate-flag-audit` |
| [Budget](budget.md) | Cost tracking per workspace / agent / user |

---

## Five design principles

These guide every architectural decision ([ADR-002](../design/adr/002-five-layer-architecture.md) details them):

1. **Open over closed** — 100+ LLM providers, standard protocols (MCP, A2A, OpenAI-compat), no vendor lock-in
2. **Composable over monolithic** — engine has zero dependencies; services are pluggable
3. **Observable over black-box** — every action traced, metered, logged, audited
4. **Security built-in, not bolted-on** — 4 hook types, PII masking, audit trail at every boundary
5. **Progressive complexity** — start with `chat` mode, grow to `workflow` mode when needed

---

## Who should (and shouldn't) use Hecate

### Choose Hecate if you need

- **Self-hosted** agent platform (data residency / compliance / cost reasons)
- **Multi-tenancy** built-in (you're building for multiple teams / customers)
- **Protocol surface** matter (you use MCP / A2A / OpenAI-compatible clients)
- **Engine-level extensibility** (custom scheduler, guardrail hooks, checkpoint store)
- **MIT-licensed OSS** (no per-seat fees, no telemetry)

### Choose something else if you want

- Non-developers building chatbots in days → **Dify**
- Just a Python library → **LangGraph**
- Managed cloud with sales motion → **Salesforce Agentforce** / **CrewAI**
- AWS-native runtime → **AWS Bedrock AgentCore**
- General workflow automation → **n8n**
- Coding assistant in terminal → **Claude Code** / **Codex** / **Hermes Agent**

See [Positioning & Competitive Landscape](../design/positioning.md) for the full comparison.

---

## What's NOT in Hecate

- No-code mobile app for building agents
- Custom model training (Hecate is an inference platform)
- Built-in LLM provider (always proxies to upstream)
- Multi-cloud failover (deploy in your cloud, manage failover at infra layer)
- End-user chatbot widget (the OpenAI API IS the backend)
- Hosted SaaS tier (Hecate is OSS, self-hosted only)

---

## Where to go next

Based on your role:

| I want to... | Read | Then |
|---|---|---|
| Get Hecate running locally | [Quickstart](../getting-started/quickstart.md) | [Your First Agent Tutorial](../tutorials/01-first-agent.md) |
| Understand agents in depth | [Agents and Execution Modes](agents.md) | [Build Your First Agent](../tutorials/01-first-agent.md) |
| Build multi-agent workflows | [Workflows](workflows.md) | [Multi-Agent Orchestration Tutorial](../tutorials/04-multi-agent.md) |
| Deploy for production | [Reference Architectures](../design/reference-architectures.md) | [Deploy to Production How-to](../how-to/deploy-production.md) |
| Extend Hecate with custom code | [Plugins](plugins.md) | [Extension Architecture Design](../design/extension-architecture.md) |
| Connect to other agents | [A2A Protocol](a2a-protocol.md) | [A2A Protocol Tutorial](../tutorials/09-a2a-protocol.md) |
| Audit security posture | [Threat Model](../design/threat-model.md) | [Security Architecture](../design/security-architecture.md) |
| See what's coming |  | [GitHub Releases](https://github.com/xueyufish/hecate/releases) |

---

## Further reading

- [Engine Design](../design/engine-design.md) — Pregel runtime, compiler pipeline, channel system, event-sourced state + checkpoint caches
- [Core Concepts](../design/concepts.md) — entity definitions, relationships, data model
- [Architecture Decision Records](../design/adr/) — 32 decisions with context and rationale
- [Glossary](../reference/glossary.md) — comprehensive term definitions