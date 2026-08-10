# Architecture Overview

Hecate is organized in five code layers and ten product modules, with Security and Ecosystem as cross-cutting concerns.

## Code layers

| Layer | Path | May import | Key rule |
|-------|------|-----------|----------|
| `engine/` | `src/hecate/engine/` | `jsonschema` only | Zero deps on services, api, models. Sole external exception: `jsonschema` for DSL validation. |
| `services/` | `src/hecate/services/` | `models/`, `engine/ports` | Depends on engine abstract interfaces only, never on engine implementations. |
| `api/` | `src/hecate/api/` | `services/`, `models/` | Never imports `engine/` directly — routes through services + `EnginePort`. |
| `models/` | `src/hecate/models/` | SQLAlchemy, Pydantic | Pure data definitions. No business logic. |
| `core/` | `src/hecate/core/` | config, database, DI, rate limiting | Infrastructure shared across all layers. |

The engine layer defines 11 core + 4 SPI extension points. Services provide concrete implementations. The API layer orchestrates services. This separation keeps the engine testable with lightweight stubs.

## Product modules

Hecate comprises ten modules organized in a layered dependency hierarchy:

1. **Access Channel** — entry point for all external requests (OpenAI-compatible API, Management API, MCP Server, A2A endpoint)
2. **Agent Studio** — visual canvas (React Flow), agent configurator, prompt management, workflow builder
3. **Agent Engine** — self-built Pregel runtime with channel system, checkpoint persistence, and worker pool
4. **Ops Center** — observability, alerting, evaluation, cost governance, compliance
5. **Model Hub** — LiteLLM-powered LLM integration with routing, circuit breaker, A/B testing
6. **Tool Platform** — MCP-first tool ecosystem with Docker sandbox execution
7. **Knowledge & Memory** — RAG pipeline (Docling, BGE-M3, Qdrant) and four-level memory system
8. **Enterprise Foundation** — multi-tenancy (Org → Workspace → RBAC), async database, Alembic migrations
9. **Security** — cross-cutting: guardrail hooks, PII masking, LLM Guard, audit trail, RBAC
10. **Ecosystem** — cross-cutting: MCP, A2A, webhooks, OpenAI-compatible API

## Request lifecycle

```
Client → Access Channel (auth, rate limit)
    → Agent Engine (load agent, compile graph, Pregel superstep loop)
        → EnginePort (LLM invoke / tool execute / knowledge query / checkpoint)
    → Response (streamed or complete)
```

At any point during execution, a node may call `interrupt()` to pause for human-in-the-loop approval. The Checkpoint system enables resuming from exactly that point.

## Further reading

- [Engine Design](../design/engine-design.md) — Pregel runtime, compiler pipeline, channel system, checkpoints
- [Core Concepts](../design/concepts.md) — entity definitions, relationships, data model
- [Architecture Decision Records](../design/adr/) — 28 decisions with context and rationale