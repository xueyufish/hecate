# Architecture Center

The authoritative source for *why* Hecate is built the way it is: top-level architecture, subsystem deep dives, cross-cutting concerns, and Architecture Decision Records (ADRs). When you need to understand the reasoning behind a design choice — not just how to use it — start here.

For conceptual explanations aimed at users, see the [concepts](../concepts/) section. For task-oriented guides, see [how-to](../how-to/). For end-to-end business scenarios built on Hecate, see [use cases](../use-cases/README.md).

---

## Strategy & Planning

Read these first to understand what Hecate is, where it fits, and where it's going:

- **[Positioning & Competitive Landscape](positioning.md)** — where Hecate fits among Dify, LangGraph, Salesforce Agentforce, AWS Bedrock AgentCore, n8n, and 9 other agent platforms; when to choose Hecate and when not to.

## Deployment & Operations

For architects and SREs planning production deployments:

- **[Reference Architectures](reference-architectures.md)** — "what to choose and why": topology patterns, component selection matrix, scaling characteristics, failure mode analysis, and RPO/RTO targets.
- **[Backup & Recovery Architecture](backup-recovery-architecture.md)** — backup scopes (PG / Qdrant / MinIO / FS), storage backends, scheduler, verification (restore dry-run), recovery procedures, and compliance considerations.

## End-to-End Examples

- **[Customer support bot](../use-cases/01-customer-support-bot.md)** — RAG over docs + guardrails + human escalation + MCP (Zendesk/Slack).
- **[Code review agent](../use-cases/02-code-review-agent.md)** — MCP (GitHub) + multi-agent (3 reviewers + 1 aggregator) + evaluation.
- **[Research team](../use-cases/03-research-team.md)** — multi-agent pipeline (planner/researcher/writer) + streaming + context offload.

Each use case combines multiple subsystems below into a working end-to-end recipe.

---

## System Overview

- **[Hecate Architecture](architecture.md)** — top-level system architecture, design principles, and component relationships. Start here for the big picture. *(v2.0, Active)*
- **[Core Concepts](concepts.md)** — entity definitions, relationships, and the data model for the Hecate platform.

## Core Subsystems

- **[Engine Design](engine-design.md)** — the Pregel runtime, compiler, channel system, and event-sourced execution state (Log-as-Truth).
- **[Access Channel Design](access-channel-design.md)** — API surfaces, authentication, gateway control plane, multi-channel adaptation, and zero-trust identity.
- **[Agent Studio Design](agent-studio-design.md)** — visual development environment: canvas, agent configurator, multi-agent orchestration, NL2X workflow generation, and testing tools.
- **[Visual Canvas Architecture](visual-canvas-architecture.md)** — the React Flow + Next.js frontend (`web/`): bidirectional DSL sync, custom node types, multi-agent pattern templates, and canvas-to-backend protocol.

## Data and Knowledge

- **[Knowledge & Memory Design](knowledge-memory-design.md)** — knowledge management, RAG pipeline, knowledge graph, ontology system, and the multi-level memory architecture.
- **[RAG Pipeline Design](rag-pipeline-design.md)** — document ingestion, chunking, embedding, hybrid search, citation system, and planned GraphRAG/DRIFT enhancements.

## Cross-Cutting Concerns

- **[Security Architecture](security-architecture.md)** — guardrail hooks, PII anonymization, LLM Guard, authentication, audit trail, agent runtime protection, and OWASP ASI coverage.
- **[Threat Model](threat-model.md)** — STRIDE analysis of Hecate: what threats are defended against, current mitigations, and gaps tracked in our backlog.
- **[Tool Platform Design](tool-platform-design.md)** — MCP integration, plugin architecture, tool operations, security, observability, and AI-native tools.
- **[A2A Architecture](a2a-architecture.md)** — Linux Foundation A2A protocol implementation: AgentCard discovery, JSON-RPC 2.0 task lifecycle, SSE streaming, signed AgentCards (JWS + RFC 8785), and the client-side plugin bridge.
- **[Observability Architecture](observability-architecture.md)** — the four signals (trace / metric / log / audit): OTel-native tracing, Prometheus metrics, structured logging, audit pipeline with detection rules, and TimescaleDB storage.

## Platform Layer

- **[Model Hub Design](model-hub-design.md)** — LLM integration, model catalog, lifecycle management, governance, monitoring, fine-tuning, and cost management.
- **[Ops Center Design](ops-center-design.md)** — observability infrastructure, agent health monitoring, conversation analytics, testing center, budget governance, and compliance.
- **[Enterprise Foundation Design](enterprise-foundation-design.md)** — multi-tenancy, security, observability, compliance, deployment, data governance, and secret management.
- **[Multi-Tenancy Architecture](multi-tenancy-architecture.md)** — Organization → Workspace → User three-tier model, RBAC matrix, SSO (OIDC/SAML/LDAP), SCIM provisioning, cross-workspace operations, and audit boundaries.
- **[Ecosystem Design](ecosystem-design.md)** — integration protocols, marketplace, partner monetization, agent discovery, and community gallery.

---

## Architecture Decision Records (ADRs)

ADRs capture the "why" behind major design choices. Each record documents the context, decision, and consequences of a single architectural decision.

### Foundation (ADR 001–010)

| ADR | Title |
|-----|-------|
| [ADR-001](adr/001-graph-first-orchestration.md) | Graph-First Orchestration with Three-Layer Agent as Preset Template |
| [ADR-002](adr/002-five-layer-architecture.md) | Five-Layer System Architecture |
| [ADR-003](adr/003-checkpoint-persistence.md) | Checkpoint Persistence with Memory Cache *(superseded by [ADR-030](adr/030-event-sourced-execution-state.md))* | State persistence |
| [ADR-004](adr/004-skill-system.md) | SKILL.md Format for Skill System |
| [ADR-005](adr/005-progressive-worker-pool.md) | Progressive Worker Pool for Distributed Execution |
| [ADR-006](adr/006-four-level-memory.md) | Four-Level Memory with Progressive Implementation |
| [ADR-007](adr/007-multi-agent-as-graph-templates.md) | All Multi-Agent Patterns Unified as Graph Templates |
| [ADR-008](adr/008-security-via-hooks.md) | Security via Engine-Level Hooks and Plugin Extension Points |
| [ADR-009](adr/009-dual-api-design.md) | OpenAI-Compatible API + Management API Dual Track |
| [ADR-010](adr/010-react-flow-canvas.md) | React Flow Canvas with JSON DSL Bidirectional Sync |

### Protocols and Architecture (ADR 011–020)

| ADR | Title |
|-----|-------|
| [ADR-011](adr/011-a2a-protocol-adoption.md) | A2A Protocol Adoption for Cross-Framework Agent Interoperability |
| [ADR-012](adr/012-mcp-streamable-http.md) | MCP Streamable HTTP Transport Upgrade |
| [ADR-013](adr/013-agentic-rl-framework.md) | Agentic RL Framework for Agent Self-Optimization |
| [ADR-014](adr/014-ontology-action-system.md) | Ontology Action System for Decision Execution |
| [ADR-015](adr/015-ontology-augmented-generation.md) | Ontology-Augmented Generation (OAG) |
| [ADR-016](adr/016-platform-spi-architecture.md) | Platform SPI Architecture with 15 Extension Points |
| [ADR-017](adr/017-knowledge-graph-architecture.md) | Knowledge Graph Architecture with GraphStore ABC |
| [ADR-018](adr/018-zero-trust-identity-architecture.md) | Zero Trust Identity Architecture for Enterprise Agent Access |
| [ADR-019](adr/019-visual-workflow-node-types.md) | Visual Workflow Node Types for Event-Driven HITL and Ontology Operations |
| [ADR-020](adr/020-async-execution-distributed-state.md) | Asynchronous Execution and Distributed Session State |

### Enhancement Architectures (ADR 021–028)

| ADR | Title |
|-----|-------|
| [ADR-021](adr/021-ops-center-architecture.md) | Ops Center Architecture |
| [ADR-022](adr/022-model-hub-enhancement.md) | Model Hub Enhancement Architecture |
| [ADR-023](adr/023-tool-platform-enhancement.md) | Tool Platform Enhancement Architecture |
| [ADR-024](adr/024-knowledge-memory-enhancement.md) | Knowledge & Memory Enhancement Architecture |
| [ADR-025](adr/025-enterprise-foundation-enhancement.md) | Enterprise Foundation Enhancement Architecture |
| [ADR-026](adr/026-security-shield-enhancement.md) | Security Shield Enhancement Architecture |
| [ADR-027](adr/027-ecosystem-enhancement.md) | Ecosystem Enhancement Architecture |
| [ADR-028](adr/028-observability-evaluation-enhancement.md) | Observability & Evaluation Enhancement |
| [ADR-029](adr/029-trust-tiered-kernel-plugin-architecture.md) | Trust-Tiered Kernel and Plugin Architecture |
| [ADR-030](adr/030-event-sourced-execution-state.md) | Event-Sourced Execution State (Log-as-Truth) |
| [ADR-031](adr/031-web-widget-iframe-architecture.md) | Web Widget Iframe Architecture (Bypassing ChannelBase) |
| [ADR-032](adr/032-dynamic-orchestration.md) | Dynamic Orchestration via Runtime-Emitted TaskDAG |
