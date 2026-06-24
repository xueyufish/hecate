# Hecate Architecture

> **Version**: v2.0 | **Status**: Active

Hecate is an open-source, self-hosted, model-agnostic, MCP-first enterprise Agent platform. This document describes the system's architecture, design principles, and component relationships. For implementation details, see the [Engine Design](engine-design.md) and [Core Concepts](concepts.md) documents.

---

## Overview

Hecate enables enterprises to build, orchestrate, and run AI Agent applications on their own infrastructure. The system comprises ten modules organized in a layered dependency hierarchy, with Security and Ecosystem as cross-cutting concerns that span all modules.

![Hecate L1 Architecture](images/hecate_l1_architecture.png)

> **Legend**: ✅ Green = Implemented | 📋 Yellow dashed = Planned
>
> Security Shield (left sidebar) and Ecosystem (right sidebar) are cross-cutting concerns that span all platform modules. Each module in the L1 diagram has a corresponding L2 breakdown — see [Module Architecture](#module-architecture) below.

The execution engine is Hecate's heart — a self-built Pregel runtime with zero external framework dependencies. It receives compiled Graphs, executes them following the Bulk Synchronous Parallel (BSP) model, manages state through a Channel system, persists snapshots via Checkpoints, and dispatches node execution to a Worker Pool.

**15 pluggable extension points** — 11 Core + 4 SPI:

**Core Extension Points (11)** — engine-level extensibility:

| Extension Point | Purpose |
|-----|---------|
| `EnginePort` | Service-to-engine adapter (LLM, tools, knowledge, checkpoint) |
| `Worker` / `WorkerPool` | Node execution dispatch |
| `CheckpointStore` | State persistence and recovery |
| `EventStore` | Append-only event logging with replay |
| `ContextEngine` | Message selection, compression, token estimation |
| `SchedulerStrategy` | Node scheduling (FIFO default, pluggable) |
| `EvictionPolicy` | Channel memory management |
| `OptimizationPass` | Graph optimization (dead node elimination, parallel detection) |
| `ConflictResolver` | Concurrent channel update resolution |
| `Guardrail Hooks (×4)` | Pre/Post LLM/Tool interception |

**SPI Extension Points (4)** — pluggable extension interfaces (📋 Planned):

| Extension Point | Purpose |
|-----|---------|
| `Evaluator` | Evaluator interface; 40+ built-in evaluators as `BuiltinEvaluator` |
| `Channel` | Channel adapter; REST/WS/CLI as `BuiltinChannel` |
| `AuthProvider` | Auth provider; JWT/APIKey as `BuiltinAuthProvider` |
| `Notifier` | Notifier; Email/Webhook as `BuiltinNotifier` |

All SPI extension points depend on `Plugin SPI Core` (PluginRegistry + PluginManifest + PluginLifecycle) for registration and lifecycle management.

---

## Design Principles

### Open Over Closed

Hecate supports 100+ LLM providers via LiteLLM, adopts MCP (Model Context Protocol) and A2A (Agent-to-Agent) as first-class integration protocols, and maintains API compatibility with OpenAI's format. No vendor lock-in is the core brand promise.

### Composable Over Monolithic

All external capabilities are integrated via MCP, not hardcoded. The execution engine, memory service, RAG pipeline, and tool system are independently replaceable. The three-layer Agent (Guard→Plan→Sub-Agent) is a preset template, not a constraint — users can customize any orchestration topology.

### Observable Over Black Box

Every request is traced from gateway through execution to response, with a complete Trace→Span→Generation hierarchy. Checkpoint persistence enables "time-travel" debugging. Cost and token usage are tracked per user, agent, and session.

### Security Built-in, Not Bolted-on

Risk levels (LOW/MEDIUM/HIGH/CRITICAL) and approval scopes (once/session/project/global) are modeled on Tool and Agent entities from the start. Four engine-level guardrail hooks (Pre/Post LLM/Tool) provide interception points. Code execution runs in sandboxed containers with network, resource, and filesystem isolation.

### Progressive Complexity

Users don't need to understand all concepts upfront. Complexity increases naturally:

- **Level 0**: Conversation mode — chat directly (like ChatGPT)
- **Level 1**: Three-layer Agent template — one-click Guard→Plan→Sub-Agent
- **Level 2**: Visual canvas — drag-and-drop workflow orchestration
- **Level 3**: Code SDK — full programming control

Each level is backward compatible.

### Developer Experience First

Canvas and SDK are two interfaces to the same system, not separate products. Agent configurations and workflow modifications take effect in real-time. The underlying execution engine is identical regardless of interface.

---

## Module Architecture

Each module below corresponds to a block in the [L1 architecture diagram](images/hecate_l1_architecture.png). The L2 diagrams show internal component breakdown with implementation status.

### Access Channel

The entry point for all external requests. Exposes two API surfaces: an OpenAI-compatible interface at `/v1/` (for seamless integration with existing tools) and a management API at `/api/` (for Agent/Workflow/Session/Knowledge Base CRUD). Handles authentication (API Key + JWT with Argon2), rate limiting, quota enforcement, and multi-channel adaptation including the MCP Server endpoint at `/mcp`.

![Access Channel L2](images/access-channel-l2.png)

All requests are uniformly wrapped as `ExecutionRequest` objects containing the agent ID, messages, execution configuration, and request context (user info, session ID, permissions). This object flows down to the Agent Engine.

### Agent Studio

Visual development environment for building and configuring agents. Features a React Flow-based drag-and-drop canvas, agent configurator, prompt management with analytics, workflow builder with six multi-agent collaboration patterns (Hierarchical, Handoff, Pipeline, Broadcast, Negotiation, Debate), reusable templates, and developer tools (CLI). All multi-agent patterns are expressed as Graph topologies, not hardcoded paths — any pattern can be visualized and edited in the canvas.

![Agent Studio L2](images/agent-studio-l2.png)

Human-in-the-Loop is handled via `interrupt()` (pause execution, return control to user) and `Command` (resume with user input, or redirect execution flow). NL2Agent and code generation are planned.

### Agent Engine

The core differentiator — a self-built Pregel runtime with zero external framework dependencies (sole external dependency is `jsonschema` for DSL validation). Compiles Graph DSL definitions (JSON) into `CompiledGraph` objects, manages state through a four-type Channel system, persists snapshots via Checkpoints with EventStore replay, and dispatches node execution to a pluggable Worker Pool. Context Engineering provides a six-component pipeline (assembler, evidence tracker, phase detection, token budget governance, provider shaping, message prioritization).

![Agent Engine L2](images/agent-engine-l2.png)

The engine runs compiled Graphs following the Pregel/BSP model: read Channel values → dispatch ready nodes to Worker Pool → await results → write new Channel values → checkpoint state → evaluate conditional edges → repeat until no nodes remain. Workers receive read-only Channel snapshots and return results — they never directly modify Channels. See [Engine Design](engine-design.md) for a deep dive.

### Ops Center

Observability, alerting, and evaluation infrastructure. Provides distributed tracing (Trace→Span→Generation hierarchy via OpenTelemetry), structured logging, metrics collection with TimescaleDB store, and audit logging. The evaluation engine includes 41 built-in evaluators covering LLM quality, RAG retrieval, and agent-level assessment, with dataset management and regression testing support.

![Ops Center L2](images/ops-center-l2.png)

### Model Hub

LLM integration layer powered by LiteLLM, supporting 100+ providers. Features intelligent routing strategies, circuit breaker pattern for fault tolerance, A/B testing for model comparison, unified tool calling across providers, and provider configuration management. Planned: cost management, model lifecycle tracking, and gray release rollout.

![Model Hub L2](images/model-hub-l2.png)

### Tool Platform

MCP-first tool ecosystem with bidirectional support: MCP Client consumes external tools, MCP Server exposes Hecate as a tool provider. Includes a tool registry, Docker-based execution sandbox, built-in tools, agent tool system, search tools, and granular tool security policies. Planned: extension ecosystem, enterprise integration, and deep research capabilities.

![Tool Platform L2](images/tool-platform-l2.png)

### Knowledge & Memory

RAG pipeline and multi-level memory system. The RAG pipeline covers document ingestion (Docling parser, web crawler), chunking, BGE-M3 embedding (dense + sparse), vector storage (Qdrant or Chroma), and hybrid search. The memory system provides four levels: L1 working memory (named blocks in context window), L2 conversation memory (auto-compression pipeline), L3 user memory (cross-session persistent facts), and L4 knowledge memory (RAG-backed).

![Knowledge & Memory L2](images/knowledge-memory-l2.png)

### Enterprise Foundation

Infrastructure layer providing multi-tenancy (Organization → Workspace → User with data-level isolation via `workspace_id` on 15 data models), async SQLAlchemy 2.0 database access with Alembic migrations (PostgreSQL, MySQL, SQLite), Pydantic-based configuration, secret management, rate limiting, async task scheduling, Docker Compose deployment, and health checks.

![Enterprise Foundation L2](images/enterprise-foundation-l2.png)

### Security

Cross-cutting security shield spanning all platform layers. Engine-level guardrail hooks (Pre/Post LLM/Tool) provide interception at the four critical points in the execution loop. PII anonymization with encryption protects sensitive data in prompts and responses. LLM Guard scans inputs and outputs for harmful content. RBAC enforces role-based access at the workspace level. A structured audit trail records all security-relevant events.

![Security L2](images/security-l2.png)

### Ecosystem

Integration and extensibility layer. Native MCP support (Client + Server), webhook notifications, event dispatcher, and OpenAI-compatible API ensure broad interoperability. Planned: plugin system for third-party extensions, asset marketplace for community sharing, A2A protocol for agent-to-agent communication, multi-modal support, streaming API, i18n/L10n, and SDK generation.

![Ecosystem L2](images/ecosystem-l2.png)

---

## Code Architecture

The modules above are implemented across five code layers with strict dependency rules. These rules ensure the engine remains framework-agnostic and testable in isolation.

### Layer Dependencies

| Layer | Path | May Import | Key Rule |
|-------|------|-----------|----------|
| `engine/` | `src/hecate/engine/` | `jsonschema` only | Zero deps on `services/`, `api/`, `models/`. Sole external exception: `jsonschema` for DSL validation. |
| `services/` | `src/hecate/services/` | `models/`, `engine/ports`, external libs | Depends on engine abstract interfaces only, never on engine implementations. |
| `api/` | `src/hecate/api/` | `services/`, `models/` | Never imports `engine/` directly — routes through services + `EnginePort`. |
| `models/` | `src/hecate/models/` | SQLAlchemy, Pydantic | Pure data definitions (ORM + Pydantic schemas). No business logic. |
| `core/` | `src/hecate/core/` | config, database, DI, rate limiting | Infrastructure shared across all layers. |

> **Note**: Two legacy violations exist — `engine/checkpoint.py` (PostgresCheckpointStore imports from `models/`) and `engine/temporal/run_worker.py` (imports from `core/`). These are intentional and documented; do not replicate the pattern in new code.

The engine layer defines all abstract interfaces ([extension point inventory](../../AGENTS.md#engine-extension-point-inventory)). Services provide concrete implementations. The API layer orchestrates services. This separation keeps the engine testable with lightweight stubs instead of integration dependencies.

### Request Lifecycle

A typical chat request flows through all layers:

```
User sends message
    │
    ▼
┌─ Access Channel ─────────────────────────────────────────┐
│  1. Authenticate (API Key / JWT)                         │
│  2. Rate limit check                                     │
│  3. Parse request → ExecutionRequest                     │
└──────────────────────────┬───────────────────────────────┘
                           │
    ▼
┌─ Agent Studio → Agent Engine ────────────────────────────┐
│  4. Load Agent definition (persona, model, tools)        │
│  5. Resolve workflow (conversation template / custom)    │
│  6. Compile Graph DSL → CompiledGraph                    │
└──────────────────────────┬───────────────────────────────┘
                           │
    ▼
┌─ Agent Engine (Pregel Runtime) ──────────────────────────┐
│  7. Restore state from latest Checkpoint (if resuming)   │
│  8. Pregel superstep loop:                               │
│     a. Read Channel values for ready nodes               │
│     b. Dispatch to Worker Pool                           │
│     c. Workers call Capability Services via EnginePort:  │
│        - LLM invoke (with guardrail hooks)               │
│        - Tool execute (with permission check)            │
│        - Knowledge query (RAG retrieval)                 │
│     d. Collect results, write to Channels                │
│     e. Persist Checkpoint                                │
│     f. Evaluate conditional edges → determine next nodes │
│     g. Stream intermediate results to client             │
│  9. Loop until no ready nodes remain                     │
└──────────────────────────┬───────────────────────────────┘
                           │
    ▼
┌─ Access Channel ─────────────────────────────────────────┐
│  10. Assemble final response                             │
│  11. Return to client (streamed or complete)             │
└──────────────────────────────────────────────────────────┘
```

At any point during step 8, a node may call `interrupt()` to pause execution and wait for human input. The Checkpoint system ensures the session can be resumed from exactly that point.

---

## Multi-Tenancy Model

Hecate models tenancy as a three-level hierarchy:

- **Organization** — Top-level tenant boundary. Owns users and workspaces.
- **Workspace** — Isolated environment within an organization. Agents, workflows, knowledge bases, and tools belong to a workspace.
- **User** — Authenticated actor within an organization, with role-based access (admin/editor/viewer).

Tenant isolation is enforced via `workspace_id` foreign keys on 15 data models. This provides data-level isolation without requiring separate database instances per tenant.

---

## Deployment

Hecate runs as a single service in development (via Docker Compose) and can scale horizontally in production. The canonical Docker Compose setup includes:

```
┌─────────────────────────────────┐
│         Hecate Service           │
│  (FastAPI + Uvicorn)            │
└──────┬──────┬──────┬────────────┘
       │      │      │
       ▼      ▼      ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│PostgreSQL│ │ Qdrant   │ │  MinIO   │
│  (16)    │ │(vectors) │ │(objects) │
└──────────┘ └──────────┘ └──────────┘
```

For production deployments, each infrastructure component can be replaced with managed equivalents (e.g., Amazon RDS, Qdrant Cloud, S3).

---

## Further Reading

| Document | Description |
|----------|-------------|
| [Engine Design](engine-design.md) | Pregel runtime, compiler pipeline, channel system, checkpoint persistence, streaming modes |
| [RAG Pipeline Design](rag-pipeline-design.md) | Document ingestion, chunking, BGE-M3 embedding, hybrid search, RRF fusion, citation system |
| [Security Architecture](security-architecture.md) | Guardrail hooks, PII anonymization, LLM Guard, JWT/API Key auth, audit trail with policy engine |
| [Core Concepts](concepts.md) | Entity definitions, relationships, data model, storage design |
| [ADR Directory](adr/) | Architecture Decision Records (10 decisions with context and rationale) |
| [Graph DSL Schema](../../src/hecate/engine/graph-dsl.schema.json) | JSON Schema for graph definition (4 node types, 4 channel types) |
| [OpenSpec Specs](../../openspec/specs/) | Feature-level specifications with requirements and scenarios |
| [OpenSpec Archive](../../openspec/changes/archive/) | Completed change proposals with design docs and task tracking |
