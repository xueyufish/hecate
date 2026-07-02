# ADR-016: Platform SPI Architecture with 15 Extension Points

> **Status**: Accepted
> **Date**: 2026-07-01

## Context

Hecate's engine layer is designed to have zero external dependencies (except jsonschema). However, real-world deployments require extensive customization: different scheduling strategies, eviction policies, evaluation metrics, authentication providers, notification channels, and more. The design needed to determine how to make the platform extensible without violating the layer dependency rules or introducing coupling between the engine and external systems.

Prior art includes LangGraph's checkpoint backends, Temporal's worker model, and Palantir Foundry's plugin SDK. The challenge was defining extension point boundaries that are granular enough for real customization but cohesive enough to be understandable.

## Decision

Adopt a **two-tier extension point architecture** with 15 extension points divided into Core (11) and SPI (4) categories, unified under a `PluginRegistry + PluginManager` core:

### Core Extension Points (11) — Engine Layer

These are engine-internal interfaces with abstract definitions in `engine/` and InMemory default implementations:

| Extension Point | File | Purpose |
|----------------|------|---------|
| EnginePort | `ports.py` | Service-to-engine adapter (LLM, tools, knowledge, checkpoint) |
| Worker / WorkerPool | `worker.py` | Node execution dispatch |
| CheckpointStore | `checkpoint.py` | State persistence and recovery |
| EventStore | `eventstore.py` | Append-only event logging with replay |
| ContextEngine | `context.py` | Message selection, compression, token estimation |
| SchedulerStrategy | `scheduler.py` | Node scheduling (FIFO default) |
| EvictionPolicy | `eviction.py` | Channel memory management |
| OptimizationPass | `optimization.py` | Graph optimization passes |
| ConflictResolver | `temporal/conflict.py` | Concurrent channel update resolution |
| Guardrail Hooks (x4) | `guardrail.py` | Pre/Post LLM/Tool interception |
| RetryStrategy | `retry.py` | Retry policies with backoff |

### SPI Extension Points (4) — Platform Layer

These are platform-level interfaces for deployment customization, managed through `PluginRegistry`:

| Extension Point | Purpose | Built-in Implementations |
|----------------|---------|--------------------------|
| Evaluator | Evaluation metric interface | 41 built-in evaluators |
| Channel | External channel adapter | REST, CLI (WebSocket planned) |
| AuthProvider | Authentication provider | JWT, APIKey (OAuth2, mTLS planned) |
| Notifier | Notification delivery | Email, Webhook (Slack, DingTalk planned) |

## Rationale

- **Core vs SPI boundary**: Core extension points are engine-internal (affect execution correctness); SPI extension points are deployment-facing (affect integration surface). Separating them prevents engine pollution with deployment-specific concerns.

- **InMemory defaults**: Every Core extension point has an InMemory default, enabling zero-config startup. Production deployments swap in PostgreSQL, Neo4j, or custom implementations without code changes.

- **PluginRegistry centralization**: All SPI extensions register through a single `PluginRegistry`, providing a uniform discovery, lifecycle, and health-check mechanism. This avoids the "N different plugin systems" anti-pattern seen in some platforms.

- **No engine imports from services**: The engine layer defines abstract interfaces (e.g., `EnginePort`); the service layer provides concrete adapters. This dependency inversion keeps the engine pure and testable in isolation.

- **Progressive adoption**: Deployments can start with all defaults (InMemory + built-in SPI) and progressively swap in custom implementations as needs evolve. No upfront configuration is required.

## Consequences

- The engine layer has 11 abstract interfaces that must remain stable across versions
- Adding a new Core extension point requires updating the engine's `__init__.py` exports and the extension point inventory in AGENTS.md
- SPI extension points are deployed as installable plugins (Python entry points or mounted modules)
- The PluginRegistry must handle versioning, dependency resolution, and conflict detection between plugins
- Third-party plugins cannot import from `engine/` internals — only from published SPI interfaces
