# Hecate Implementation Roadmap

> **Date**: 2026-08-16
> **Status**: Active — P1 19/19 (100%), P2 65/65 (100%), P3 81/127 (64%, 2026-08-14 re-scope + 2026-08-16 plugin ecosystem adjustment; 8.20 Execution Replay shipped 2026-08-16), P4 4/101 (4%), P5 0/60
> **Scope**: 12-month implementation plan covering 201 unimplemented features across P3–P5. 2026-08-14 re-scope (per docs/research/2026-08-competitor-analysis.md + 2026-08-deepseek-harness-analysis.md): 5 features dropped, 17 deferred to P5, 7 added (1.3.18/1.3.19/8.20 → P3, 2.13/8.21/13.20/6.27a → P4), 11.11 moved P5→P4.
> **Basis**: Feature catalog (352 features, 162 done) + architecture compatibility assessment + competitive timeline benchmarks + 2026-06 deep competitive analysis + industry feature delivery timeline validation + Core vs Pluggable architecture framework (Platform SPI ABCs prioritized) + A2A Protocol Stack (MCP+A2A+AP2) convergence analysis + MCP/Skill Resource Management + Agentic RAG + Knowledge Graph (8 features) + Ontology Modeling (4 features) + Memory (11 features) + AIP Capabilities (29 features) + Access Channel (5 features) + Agent Studio enhancements (4 features + 5 enhancements) + Agent Engine enhancements (2 features + 4 enhancements) + Ops Center (9 new features + 6 enhancements) + Model Hub (3 new features + 5 enhancements) + Tool Platform (2 new features + 4 enhancements) + Knowledge & Memory (2 new features + 4 enhancements) + Enterprise Foundation (2 new features + 4 enhancements) + Security Shield (2 new features + 6 enhancements) + Ecosystem (2 new features + 4 enhancements) + Observability & Evaluation (2 new features + 8 enhancements)

---

## Current State

| Priority | Features | Done | Remaining |
|----------|----------|------|-----------|
| **P1 Usable** | 19 | 19/19 (100%) | 0 |
| **P2 Good** | 65 | 65/65 (100%) | 0 |
| **P3 Trustworthy** | 127 | 81/127 (64%) | 46 |
| **P4 Intelligent** | 101 | 4/101 (4%) | 97 |
| **P5 Ecosystem** | 60 | 0/60 (0%) | 60 |
| **Total** | **372** | **169/372 (45%)** | **203** |

---

## Architecture Readiness

Before starting implementation, the following architectural prerequisites have been assessed:

### Engine ABC Integration Status

| ABC | File | PregelRuntime | Workers | Status |
|-----|------|---------------|---------|--------|
| Worker / WorkerPool | worker.py | ✅ Integrated | — | 🟢 Production |
| CheckpointStore | checkpoint.py (ABC) + services/checkpoint_store.py (Postgres) | ✅ Integrated | — | 🟢 Production |
| EnginePort | ports.py | — (by design) | ✅ Via adapter | 🟢 Production |
| Guardrail Hooks (×4) | guardrail.py | ❌ | ✅ LLM/Tool Worker | 🟡 Worker-level |
| ConflictResolver | temporal/conflict.py | ✅ _apply_writes | — | 🟢 Production |
| SchedulerStrategy | scheduler.py | ✅ Wired (L70, L78, L145) | — | 🟢 Production |
| EvictionPolicy | eviction.py | ✅ Wired (channel.py L39, L104) | — | 🟢 Production |
| OptimizationPass | optimization.py | ✅ Wired (compiler.py L28, L38, L75) | — | 🟢 Production |
| EventStore | eventstore.py | ✅ Wired (pregel.py) | ✅ LLM/Tool Worker | 🟢 Production |
| ContextEngine | context.py | ✅ Wired | ✅ LLMWorker pipeline | 🟡 LLMWorker pipeline |
| RetryStrategy | retry.py | ✅ Wired (RetryExecutor) | ✅ LLM/Tool Worker | 🟢 Production |

**Action**: Sprint 1 complete — all 6 work items done. All engine ABCs wired or correctly layered. Zero layering violations in engine/.

### Known Architectural Constraints

| Constraint | Impact | Mitigation |
|-----------|--------|------------|
| ~~ChannelType is hardcoded StrEnum~~ | ~~Cannot register custom channel types~~ | ✅ Done — ChannelTypeRegistry with pluggable behaviors |
| ~~checkpoint.py imports from models/~~ | ~~Layering violation (engine → models)~~ | ✅ Done — PostgresCheckpointStore migrated to services/checkpoint_store.py |
| ~~`postgresql_where=` partial indexes (7 occurrences)~~ | ~~Multi-DB incompatible~~ | ✅ Done — replaced with composite `(col, deleted)` indexes |
| ~~workspace_id pre-reserved in 5 models~~ | ~~Multi-tenant foundation exists~~ | ✅ Done — Organization Management (10.1) + RBAC (10.2) implemented with auth context + workspace isolation |
| Single DATABASE_URL in core/database.py | No multi-DB support | Refactor to session factory registry in Sprint 2 |

---

## Sprint Overview

```
Sprint 1 (M1-2):   P1 Close-Out + Architecture Hardening
Sprint 2 (M3-4):   P2 Core — Canvas + Multi-Agent + Multi-DB
Sprint 3 (M5-6):   P2 Complete — Memory + Channels + Evaluation Foundation
Sprint 4 (M7-8):   P3 Core — Resilience + Multi-Tenant + Security + Observability + Platform SPI Core
Sprint 5 (M9-10):  P3 Enterprise — Platform SPI + Multi-Agent Protocol + Model Hub + Enterprise Identity
Sprint 6 (M11-12): P3 Security & Ops — Ops Center + Security Enhancement + Plugin System + Deployment
Sprint 7 (M13-14): P3 Complete — Log-as-Truth + Dynamic Orchestration + Run Replay + Browser Tool + Plugin Open-Standard Ingestion + Advanced RAG + Multi-Channel + Evaluation + Memory
Sprint 8 (M15-16): P4 Kickoff — Self-Learning + Agentic AI + Memory Intelligence
Sprint 9 (M17-18): P4 Complete — Knowledge Intelligence + Multi-Agent Intelligence + Execution Intelligence
Sprint 10 (M19-20): P5 Ecosystem — Marketplace + Community + Industry + Compliance
```

---

## Sprint 1: P1 Close-Out + Architecture Hardening (Month 1–2)

> **Goal**: Complete P1 (19/19), wire 4 unconnected ABCs, fix layering violations. Prepare the engine for P2 feature velocity.

### P1 Close-Out

| # | Feature | Effort | Notes |
|---|---------|--------|-------|
| 5.9 | Skill Loading & Management (⚠️ → ✅) | M | SKILL.md parsing + CRUD API + agent-skill association + SkillLoader injection ✅ |
| 5.1 | Built-in Tools (complete) | S | Code execution, Web search, file operations |

### Architecture Hardening

| Work Item | Location | LOC Est. | Unlocks | Status |
|-----------|----------|----------|---------|--------|
| Wire SchedulerStrategy into PregelRuntime | pregel.py __init__ + execute() | ~10 | Custom scheduling algorithms | ✅ Done |
| Wire EvictionPolicy into ChannelManager | channel.py write() | ~8 | Memory management, long-session control | ✅ Done |
| Wire OptimizationPass into Compiler | compiler.py compile() | ~5 | Graph optimization, dead node elimination | ✅ Done |
| Wire EventStore into PregelRuntime | pregel.py (each phase) | ~20 | Audit logging, time-travel debugging | ✅ Done |
| ChannelType enum → registry pattern | types.py + channel.py | ~30 | Custom channel types | ✅ Done |
| Migrate PostgresCheckpointStore → services/ | checkpoint.py → services/ | ~50 | Fix layering violation | ✅ Done |

### Milestone M1 (End of Sprint 1)

- [x] P1 = 19/19 (100%)
- [x] 4 ABCs wired — SchedulerStrategy ✅, EvictionPolicy ✅, OptimizationPass ✅, EventStore ✅
- [x] ChannelType extensible via registry
- [x] Zero layering violations in engine/
- [x] All existing tests pass

---

## Sprint 2: P2 Core (Month 3–4)

> **Goal**: Three marquee P2 deliverables — Visual Canvas, Multi-Agent orchestration, Multi-DB/Multi-Vector-DB.

### Visual Canvas + Workflow

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.1.2 | Visual Workflow Canvas (enhance) | Sprint 1 | M |
| 1.1.3 | Workflow Node Type Library (enhance) | 1.1.2 | M |
| 1.1.4 | Workflow Test Run (enhance) | 1.1.2 | S |
| 1.1.8 | Conversational vs Task Workflows ✅ | 1.1.2 | M |
| 1.1.9 | Workflow Version Management ✅ | 1.1.8 | M |

### Multi-Agent Orchestration

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 2.3 | Pipeline ✅ | Graph template | S |
| 2.4 | Broadcast ✅ | Graph template | S |
| 2.7a | Collaboration Pattern Selection ✅ | 1.1.2 + 2.3 | M |
| 2.7b | Agent Communication Configuration ✅ | 1.1.14 | S |
| 2.7c | Routing Rule Configuration ✅ | 2.7a | M |

### Canvas UI Enhancement

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.1.14 | Agent Node Config Enhancement ✅ | 1.1.2 | M |
| 1.1.15 | Template Customization ✅ | 1.1.2 | M |
| 1.1.16 | Typed Edge Visualization ✅ | 1.1.2 | S |
| 1.1.17 | Fan-Out/Merge Node Editing ✅ | 1.1.2 | M |

### Infrastructure Extensibility

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 13.13 | Multi-Database Support ✅ | Architecture assessment | M |
| 3.1.7 | Multi-Vector-DB Support ✅ | RAG service abstraction | M |
| 5.9a | MCP Server Mode ✅ | MCP Client ✅ | S |

### Multi-Channel (Start)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 11.2 | Web Widget (Simplified) ✅ *Wave 1 — iframe-embeddable for any Hecate deployment (Delivered 2026-08-16, see ADR-031)* | API ✅ | S |
| 11.3 | Feishu (Lark) ✅ | Channel SDK | S |

### Milestone M2 (End of Sprint 2)

- [x] P2 progress 63/63 (100%)
- [ ] Canvas usable with drag-and-drop nodes
- [x] Multi-Agent visually orchestrable with collaboration patterns ✅
- [x] Canvas UI: Agent node config, template customization, typed edges, fan-out/merge editing
- [ ] Multi-DB: PostgreSQL + MySQL + SQLite supported
- [x] Multi-Vector-DB: Qdrant + Chroma supported (Milvus P2)
- [x] MCP Server mode: Hecate exposed as MCP tool provider

---

## Sprint 3: P2 Complete (Month 5–6)

> **Goal**: Finish P2 — Memory enhancement, Multi-Channel, Evaluation foundation, Prompt management, Open Platform.

### Memory & Context

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 4.3 | User Memory (L3) — full integration | ContextEngine wiring | M |
| 1.1.5 | Scenario-based Agent Packaging | 1.1.2 | S |
| 1.1.10 | App Import/Export (enhance) | — | S |

### Multi-Channel + Deployment

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 11.4 | WeCom | 11.3 | S |
| 11.5 | DingTalk | 11.3 | S |
| 11.6 | WeChat Official Account | 11.3 | S |
| 11.7 | CLI ✅ | API ✅ | M |
| 13.9 | Scheduled Tasks ✅ | Agent Runtime ✅ | M |
| 9.7 | Internal/External Network Isolation | Security ✅ | S |
| 13.3 | Offline Deployment | Docker ✅ | M |

### Evaluation Foundation (for P3)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 7.1 | RAG Evaluation ✅ | RAG ✅ | M |
| 7.2 | Agent Evaluation ✅ | LLM ✅ | M |

### Prompt & Model

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.8 | Opening Remarks & Follow-up Suggestions (enhance) | — | S |
| 6.7 | Model Playground ✅ | Model API ✅ | M |

### Open Platform

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 14.1 | Open API Platform | API ✅ | M |
| 14.2 | Webhook Callbacks | Events ✅ | S |

### Milestone M3 (End of Sprint 3)

- [x] P2 = 63/63 (100%)
- [ ] Memory L1–L4 all accessible
- [ ] 5+ channels (API, Web, Feishu, WeCom, DingTalk)
- [x] Evaluation baseline: RAG + Agent evaluation operational
- [ ] Open API platform with developer registration

---

## Sprint 4: P3 Core (Month 7–8)

> **Goal**: Enterprise-grade core — Resilience infrastructure, Multi-Tenant RBAC, full security system, end-to-end observability, ContextEngine Phase 1 integration. Ops Center foundational features begin.

### Resilience & Safety Infrastructure (NEW — Competitive Analysis Driven)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.5g | Unified Exception Hierarchy (HecateError + ErrorCategory enum) ✅ | ErrorClassifier ✅ | M |
| 1.3.5h | Framework-Level Auto-Retry ✅ | 1.3.5g ✅ | S |
| 1.3.5f | Platform-Level Tool Gating ✅ | PreLLMHook ✅ | M |
| — | ContextEngine Phase 1: LLMWorker Context Pipeline ✅ | ContextEngine ABC ✅ | M |

### Platform SPI Core (NEW — Core vs Pluggable Framework)

> **Architecture Principle**: Define extension interfaces BEFORE building implementations. Channel adapters, evaluators, auth providers, and notifiers all depend on Plugin SPI Core (5.5a). Building SPIs first prevents hardwired implementations that must be refactored later.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 5.5a | Plugin SPI Core ✅ (PluginRegistry + PluginManifest + PluginLifecycle) | — | L |
| 7.2-abc | EvaluatorABC ✅ (refactor existing 40+ evaluators as BuiltinEvaluator) | 5.5a | M |

### Multi-Tenant & RBAC

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 10.1 | Organization Management ✅ | workspace_id ✅ | M |
| 10.2 | RBAC ✅ | 10.1 ✅ | M |
| 10.5 | Tenant Isolation ✅ | 10.1 | M |
| 10.6 | Authentication Service (enhance) | JWT ✅ | S |
| 10.3 | SSO/LDAP | 10.6 | M |
| 10.4 | Quota Management | 10.1 | S |

### Security (Complete System)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 9.1 | Input Security ✅ | Guardrails ✅ | S |
| 9.1a | Guardrails ✅ | PreLLMHook ✅ | M |
| 9.2 | Output Security ✅ | PostLLMHook ✅ | S |
| 9.4 | Execution Security ✅ | Sandbox ✅ + ToolAccessPolicy ✅ | M |
| 9.4a | Granular Operation Approval ✅ | 9.4 + DangerousPattern ✅ | M |
| 9.4b | Trusted Workspace ✅ | 9.4 + WorkspaceBoundaryPolicy ✅ | S |
| 9.5 | Data Security ✅ | PII masking ✅ | S |

### Observability (End-to-End)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 8.1 | Full-Chain Tracing ✅ | EventStore ✅ | M |
| 8.2 | Real-Time Monitoring ✅ | 8.1 | S |
| 8.3 | Cost Dashboard ✅ | 8.1 | S |
| 8.5 | Prompt Version Management ✅ | 8.5a ✅ | S |
| 8.5b | Prompt Analytics & Diff | 8.5a ✅ | M |
| 8.6 | Alerting ✅ | 8.2 | S |
| 8.7 | Audit Logs ✅ | EventStore ✅ | S |

### Evaluation (Expansion)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 7.2a | 40+ Built-in Evaluators | 7.2 | L |
| 7.3 | Workflow Evaluation | 7.1 | M |
| 7.4 | Human Annotation | 7.2 | M |
| 7.6 | Regression Test Set | 7.2 | S |

### Milestone M4 (End of Sprint 4)

- [x] P3 progress ~42/133 (32%)
- [x] Organization Management + RBAC implemented (10.1, 10.2)
- [x] Tenant Isolation — data-level workspace scoping (10.5)
- [x] Resilience infrastructure: exception hierarchy ✅ + auto-retry ✅ + tool gating ✅
- [x] ContextEngine Phase 1: LLMWorker context pipeline operational ✅
- [ ] Multi-Tenant RBAC + SSO operational (SSO remaining)
- [x] Full security stack: input/output/execution/data
- [x] Full-Chain Tracing (8.1) + Real-Time Monitoring (8.2) — done
- [x] Cost Dashboard (8.3) — done
- [x] Plugin SPI Core (5.5a) + EvaluatorABC (7.2-abc) defined — pluggable foundation ready
- [ ] 40+ evaluators available for regression testing

---

## Sprint 5: P3 Enterprise (Month 9–10)

> **Goal**: P3 enterprise core — Platform SPI, Multi-Agent Protocol (A2A), Model Hub, Enterprise Identity. Define extension interfaces before building implementations.

### Platform SPI ABCs (NEW — Core vs Pluggable Framework)

> Define remaining extension interfaces so all downstream implementations are plugin-based, not hardwired.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 11.1-abc | ChannelABC ✅ (REST/WS/CLI as BuiltinChannel) | 5.5a ✅ (Sprint 4) | M |
| 10.3-abc | AuthProviderABC ✅ (JWT/APIKey as BuiltinAuthProvider) | 5.5a ✅ (Sprint 4) | M |
| 8.6-abc | NotifierABC 🔀 (merged into ChannelABC — notification dispatchers as outbound Channel adapters) | 5.5a ✅ (Sprint 4) | S |
| 15.1 | i18n SPI ✅ (locale passing + message catalog loading + t() function) | 5.5a ✅ (Sprint 4) | M |

### A2A Protocol & Multi-Agent

| # | Feature | Dependencies | Effort | Status |
|:---|---------|------|--------|:------:|
| 2.10 | A2A Protocol | Multi-Agent ✅ | L | ✅ Done |
| 2.10a | Signed Agent Cards | 2.10 | S | ✅ Done |
| 2.9 | Unified Skill Registry | Skill ✅ + Agent ✅ | L | ✅ Done |
| 2.9a | Agent-Workflow Mutual Embedding | 2.9 | M | ✅ Done |
| 2.8 | Collaborative Conflict Handling | Session Locking ✅ | M | ✅ Done |

### Model Hub Features

> Model catalog, lifecycle management, monitoring, deployment, cost governance, fine-tuning pipeline.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 6.44 | Model Catalog | Model Management ✅ | L |
| 6.45 | Model Lifecycle Manager | 6.44 | M |
| 6.5 | Self-Hosted Inference (Managed Model Deployment) | Model Management ✅ | M |
| 6.6 | Model Fine-Tuning (Fine-Tuning Pipeline) | Model Management ✅ | L |
| 6.14 | Intelligent Router with Caching | RoutingStrategy ✅ | M |
| 6.4 | Model Cost Management | Cost Dashboard ✅ | S |
| 6.11 | Multi-Modal Model Classification | Model Management ✅ | S |
| O10+G4 | Model Management Console + Monitoring Dashboard | 6.44 + 6.45 | L |

### Enterprise Identity & Governance

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 10.3 | SSO/LDAP ✅ | Auth ✅ | M |
| 10.3b | SCIM Directory Sync ✅ | AuthProviderABC ✅ | M |
| 10.7 | Budget Management & Cost Governance ✅ | Cost Dashboard ✅ | M |
| 10.8 | Enterprise Vault Integration ✅ | Auth ✅ | M |

### Milestone M5 (End of Sprint 5)

- [x] Platform SPI complete: ChannelABC ✅ + AuthProviderABC ✅ + i18n SPI ✅ defined. NotifierABC 🔀 merged into ChannelABC as NotificationChannelAdapter
- [x] A2A Protocol with Signed Agent Cards operational
- [x] Collaborative Conflict Handling with session locking
- [x] Unified Skill Registry with Skill-Workflow mutual embedding
- [x] Model Catalog with capability badges and provider comparison
- [x] Model Lifecycle Manager with staging channels and deprecation scheduling
- [x] Managed Model Deployment workflow
- [x] Fine-Tuning Pipeline operational
- [x] Intelligent Router with caching
- [x] Model Cost Management with budgets and anomaly detection
- [x] Multi-Modal Model Classification
- [x] Model Management Console + Monitoring Dashboard
- [x] SSO/LDAP operational
- [x] SCIM Directory Sync for Azure AD/Okta
- [x] Budget Management with cost governance
- [x] Enterprise Vault Integration with dynamic secrets

---

## Sprint 6: P3 Security & Ops (Month 11–12)

> **Goal**: Production hardening — Ops Center, Security Enhancement, Plugin System, Deployment infrastructure. Close the enterprise trust gap.

### Ops Center

> Unified administrative control plane consolidating monitoring, evaluation, deployment, cost governance, and compliance.

> **Execution Plan — split into 4 OpenSpec changes** (decided 2026-07-07, see explore notes):
>
> | Order | OpenSpec Change | Scope | Status |
> |-------|-----------------|-------|--------|
> | 1 | `otel-trace-bridge-tool-analytics` | OTel SpanProcessor → TraceModel bridge + Tool Execution Analytics (8.9c) | done ✅ |
> | 2 | `agent-health-monitoring` | Agent Health Monitoring (8.9a) — status taxonomy, SQL-derived health, fleet dashboard | done ✅ |
> | 3 | `conversation-analytics` | Conversation Analytics (8.9b) v1 statistics + user feedback AND v2 async LLM quality scoring — **v1+v2 must ship together** | done ✅ |
> | 4 | `ops-center-dashboard` | Unified Ops Center Dashboard (8.9) — aggregation layer on top of 8.9a/b/c data sources | done ✅ |
>
> **Rationale**: 8.9a/b/c are data-source services that 8.9 aggregates. Building data sources first ensures the unified dashboard has real data to show. The OTel trace bridge is foundational infrastructure — without it, TraceModel receives no tool/LLM spans from workers.
>
> **v2 integrity safeguard**: Change 3 (`conversation-analytics`) contains both v1 (statistics + user feedback) and v2 (async LLM quality scoring via EvaluationEngine + QualityScoringScheduler). These MUST ship in the same change — v1 is incomplete without v2 quality scores.

| # | Feature | Dependencies | Effort | Change |
|---|---------|------|--------|--------|
| 8.9 ✅ | Unified Ops Center Dashboard | 8.9a + 8.9b + 8.9c | M | #4 ops-center-dashboard |
| 8.9a | Agent Health Monitoring Dashboard ✅ | Observability ✅ | M | #2 agent-health-monitoring |
| 8.9b | Conversation Analytics & Quality Scoring (v1+v2) ✅ | Evaluation ✅ | L | #3 conversation-analytics |
| 8.9c | Tool Execution Analytics Dashboard ✅ | otel-trace-bridge | M | #1 otel-trace-bridge-tool-analytics |
| 8.10 | CI/CD Evaluation Gating | Evaluation ✅ | M | TBD (separate change) |
| 8.12 | Agent Catalog Governance & Quality Gateway | Evaluation ✅ | M | TBD (separate change) |

### Security Enhancement

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 5.14 | Environment Security (P0: 9.12 + 9.13 + 9.14 + 9.15) ✅ *(renamed from 5.9 on 2026-08-14 — resolves ID collision with 5.9 Skill Loading)* | 5.6 ✅ + 1.3.15 ✅ + 9.4 ✅ | L |
| 9.12 | Environment Network Egress Control ✅ | 1.3.15a ✅ | M |
| 9.13 | Sandbox Enforcement Integration ✅ | 9.4 ✅ + 9.4c ✅ + 1.3.15a ✅ | S |
| 9.14 | Structured Security Audit Pipeline ✅ | 9.4 ✅ + 8.7 ✅ | M |
| 9.15 | Per-Execution Credential Scoping ✅ | 10.8 ✅ | S |
| 9.10 | Outbound DLP Engine ✅ (2026-08-10) | PII Masking ✅ | L |
| 9.11 | Agent Runtime Protection | Guardrail Hooks ✅ | L |
| 7.10 | Automated Continuous Red Teaming | Security Testing ✅ | L |
| 9.1a | Injection Type Detection | Guardrails ✅ | S |
| 9.2 | System Prompt Leakage Protection | Output Security ✅ | S |
| 8.7 | Security Event SIEM Pipeline ✅ | Audit Logs ✅ + 9.14 ✅ | M |
| 2.10b | Multi-Agent Trust Verification | A2A ✅ | M |
| 9.6 | Compliance Framework | Security ✅ | M |
| 9.8 | Full-Chain Network Security | Security ✅ | M |
| 7.7 | Security Testing | Evaluation ✅ | M |

### Plugin System & Tool Platform

> **Scope Boundary** (decided 2026-07-12, see explore notes):
>
> - **5.5** ✅ = Runtime engine: plugin.yaml loading, directory discovery, basic compat check, lifecycle, config, permissions, REST API + frontend, MCP endpoint UI. In-process + MCP hybrid (no daemon).
> - **TP5** = Type taxonomy + SDK: 8 plugin types by capability (Tool/Extension/Trigger/Model/Channel/Evaluator/Auth/Secret), hecate.plugin SDK, hecate plugin init CLI, hot-reload, full install-time compat validation, API-type plugin creation UI. Datasource + AgentStrategy deferred.
> - **5.5b** = Packaging + distribution: .hecate-plugin bundle, packaging CLI, upload/install UI, version management, marketplace foundation.
>
> Execution order: 5.5 ✅ → TP5 → 5.5b (strict serial dependency).

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 5.5 ✅ | Plugin System (runtime engine) | MCP ✅ | L |
| 5.5 (TP5) ✅ | Plugin Type Taxonomy + Developer SDK | 5.5 | L |
| 5.5b ✅ | Plugin Packaging & Distribution | 5.5 + TP5 | M |
| 5.6 ✅ | Tool Permission Control (Composable Policy Pipeline) | PreLLMHook ✅ | M |
| 5.7 ✅ | Tool Caching | Tool ✅ | S |
| 5.8 | Enterprise System Integration Framework (Per-Tool Auth Scope) | MCP ✅ | M |
| 5.4c | MCP Server Registry & Connection Management | MCP ✅ | M |
| 5.4b (upg) | MCP 2026-07-28 Spec Migration (NEW work item, 2026-08-14 re-verification) — MCP shipped its largest revision 2026-07-28: stateless core (initialize/session removed, `_meta` self-describing requests), mandatory Mcp-Method/Mcp-Name header routing, Multi Round-Trip Requests replacing held-open elicitation streams, cacheable list results (ttlMs), RFC 9207 auth, DCR→CIMD, Roots/Sampling/Logging deprecated (12-month window). Hecate's 5.4b (2025-03-26 spec) client + server must migrate; official registry live (1,000+ servers) | 5.4b client ✅ | M |
| 1.3.5i ✅ | Session Events + Tool Matchers | Settings ✅ | S |
| 1.3.15 ✅ | Agent Environment | Session ✅ + Context ✅ | M |
| 1.3.16 ✅ | Agent State Separation | 1.3.15 ✅ | S |
| 1.3.17 ✅ | Agent Invocation Mode (agent_execute pipeline parity + DSL invocation_mode) | 1.3.1 ✅ + 5.1 ✅ + 2.3d ✅ | M |
| 1.3.15a ✅ | Environment Backend: Docker | 1.3.15 ✅ | M |
| 1.3.15b ✅ | Context Offloading | 1.3.15 ✅ + 4.13 | S |
| 1.3.15c ✅ | Sandbox Environment Mount | 1.3.15 ✅ + 9.4c ✅ | M |

### Deployment & Operations

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 13.1 | SaaS Deployment | Docker ✅ | L |
| 13.1a | Canary Release | EventStore ✅ | M |
| 13.1b | Agent Identity Service | Auth ✅ | M |
| 13.4 | Horizontal Scaling | Stateless ✅ | M |
| 13.4a | Distributed Session State Store (Redis) ✅ (5/5) (deprecated AgentStateStore in 13.4a-6) | CheckpointStore ✅ | L |
| 13.4b | K8s Scaling Test Enhancements (L2/L3 chainsaw e2e + locust + AgentScope startup validation + PgBouncer) *Deferred — bundle with 13.1 SaaS Deployment* | 13.4 ✅ | M |
| 13.5 | Data Backup & Recovery ✅ | Database ✅ | S |
| 13.6 | Version Upgrade ✅ (Aug 2026) | Deployment ✅ | M |
| 13.17 | Environment Management & ALM Pipeline | 13.5 + 13.6 | L |
| 13.18 | API Management & Developer Portal | API ✅ | M |

### Milestone M6 (End of Sprint 6)

- [ ] P3 Security & Ops complete
- [x] **Ops Center Change 1**: OTel trace bridge operational + Tool Execution Analytics (8.9c)
- [x] **Ops Center Change 2**: Agent Health Monitoring (8.9a) with fleet overview
- [x] **Ops Center Change 3**: Conversation Analytics (8.9b) — v1 statistics + user feedback AND v2 async LLM quality scoring
- [x] **Ops Center Change 4**: Unified Ops Center Dashboard (8.9) — aggregation layer
- [ ] CI/CD Evaluation Gating operational
- [ ] Agent Catalog Governance with quality gateway
- [x] Outbound DLP Engine with 3-point scanning (2026-08-10, PR #58)
- [ ] Agent Runtime Protection with 5 detector types
- [ ] Automated Continuous Red Teaming operational
- [ ] Injection Type Detection for downstream systems
- [ ] System Prompt Leakage Protection
- [x] Security Event SIEM Pipeline for SOC teams
- [x] **Environment Security P0**: Network Egress Control (9.12) + Sandbox Enforcement (9.13) + Structured Audit Pipeline (9.14) + Per-Execution Credential Scoping (9.15)
- [ ] Multi-Agent Trust Verification
- [ ] Compliance Framework operational
- [x] Plugin System with extensible architecture
- [x] Plugin Type Taxonomy + Developer SDK
- [x] Plugin Packaging & Distribution
- [x] Composable Tool Policy Pipeline
- [ ] Per-Tool Auth Scope
- [x] MCP Server Registry & Connection Management
- [ ] SaaS Deployment ready
- [ ] Canary Release operational
- [ ] Horizontal Scaling with Redis state store
  - 2026-08-05: 13.4a all 5/5 changes completed (1/5 engine abstraction, 2/5 Redis/PostgreSQL/Tiered + factory, 3/5 production wiring: WorkflowExecutionService DI + chat.py Depends + lifespan singleton, 4/5 horizontal-scaling validation: session locks + jitter retry + OTel + perf benchmarks + streaming save fix, 5/5 EventStore PG wiring: PostgresEventStore + factory + FastAPI DI + _sync_event_position + retention deferred)
- [x] Data Backup & Recovery
- [ ] Environment Management & ALM Pipeline
- [ ] API Management & Developer Portal

### Pending Cleanups

- [ ] **13.4a-7**: AgentStateStore hard removal (delete `services/state/store.py`, remove `WorkflowExecutionService.state_store` parameter) — scheduled ≥ next minor after 13.4a-6
- [ ] **C2 (1.3.19 follow-up)**: `checkpoints` 表硬删除（drop `checkpoints` table）——1.3.19 将 checkpoint 降级为物化缓存（`channel_state + log_version`），`PostgresCheckpointStore` 已软废弃（DeprecationWarning），存量物化缓存迁移到 SessionStateStore 后 drop；调度 ≥ 1.3.19 之后一个 minor（ADR-030 Consequences 节）
- [ ] **A2 (1.3.19 follow-up)**: 双重记账统一 —— Conversation/Message 表与事件日志（execution-state-log）双写收敛；目标为日志单一事实源、Conversation/Message 成为派生投影（或明确双向一致性策略）——挂账项，无固定排期

---

## Sprint 7: P3 Complete (Month 13–14)

> **Goal**: Complete P3 — engine state architecture (log-as-truth), competitive gap features (dynamic orchestration, run replay, browser tool), rescoped Advanced RAG, Multi-Channel Wave 1, Evaluation suite, Memory improvements. 2026-08-14 re-scope per competitor analysis: 5 dropped, 15 deferred to P5, 4 added.

### Engine Architecture: Event-Sourced State (NEW — 2026-08-14, Q4=A decision)

> **Ordering**: 1.3.19 must land FIRST — 8.20 Execution Replay, HITL durable audit pairs, and middleware waterfall events all consume the enriched event log. 1.3.18 / 6.27 / 5.9-enh are parallelizable.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.19 | Event-Sourced Execution State (Log-as-Truth) ✅ *(shipped 2026-08-15, [ADR-030](../design/adr/030-event-sourced-execution-state.md))* — EventStore from observation log to state carrier: "model-visible ⟺ logged" runtime invariant, derive_messages() projection for model context, checkpoint = log-replay fold (snapshots demoted to materialized caches), incremental delta storage (O(N²)→near-linear; note: deer-flow's DeltaChannel is the reference but sits UNRELEASED in its 2.1.0 milestone — corrected 2026-08-14; OMA v1.15.0 durable-approval checkpoint schema v4 is the shipped production reference). Include a dsh-invariants-style runtime relational invariant layer (openTurn/openStep/pendingCalls, frozen result snapshots, dispatch-tree consistency) — gap found in 2026-08-14 re-verification | EventStore ✅ + CheckpointStore ✅ | L |
| 5.9 | Skill Provider Registry (enhancement) — provider registry (source origins: project/user/bundled/custom) + rank precedence (lower wins) + kebab-case name grammar + model/user invocation policy separation; replaces plain directory scan | 5.9 Skill Loading ✅ | M |

### Competitive Gap Features (NEW — 2026-08-14 competitor analysis)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.18 | Dynamic Orchestration — coordinator node: goal + agent roster → runtime task DAG → dispatch workers → synthesize result. 7th multi-agent pattern alongside 6 static ones; coordinator is a special node type emitting sub-graphs on Pregel at runtime | 2.7a ✅ + Pregel ✅ | M |
| 8.20 | Execution Replay & Debug Dashboard (Phase 1: timeline replay) — session → trace-partitioned timeline (superstep × channel changes × tool calls × LLM request/response × guardrail blocks) + DAG step-through + time-travel (fold-to-version + `derive_messages`); web UI on EventStore + OTel. **Vocabulary**: `session`（多轮容器）→ `trace`（一次执行，回放锚点）→ `event`（记录）；不再用 "runId"。**回放覆盖范围 = Pregel 路径**（path A/C 不在日志内，UI 横幅标注；空日志会话不渲染回放 tab）。 Phase 2 (version binding) deferred to P5 | 1.3.19 (enriched log) | M |
| 6.27 | Browser Automation Tool (moved from P4) — Playwright builtin: navigate/click/type/screenshot/extract/fill; headless/headful; sandboxed via DockerEnvironment. Computer-use half split to 6.27a (stays P4) | 5.1 ✅ + 9.4c ✅ | M |

### Completed-Feature Upgrades (NEW — 2026-08-14 research)

> Upgrades to already-shipped features whose architecture the industry has moved past (dsh source analysis + 18-platform competitor survey). All sequence after 1.3.19 (they consume the enriched event log); spill into early Sprint 8 is acceptable if capacity is tight. No new feature IDs — recorded as planned enhancements on 1.3.5i (E3), 1.3.4, 9.4 in the catalog.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.5i (E3) | GuardrailHooks → Ordered Waterfall Middleware Chain — 4 flat hooks become stages in an ordered `next()`-delegation chain (agent/pre-step, agent/request, tools/pre-execute / execute / post-execute / result), scope-filtered per agent; HITL clarification becomes one stage (deer-flow ClarificationMiddleware pattern) | 1.3.19 + GuardrailHooks ✅ | M |
| 1.3.4 | HITL Fail-Closed Approval — no answerer → deny; only allowed-once grants; ask/never policy state machine; approval/asked + approval/decided durable audit pair (turn-enclosed) | 1.3.4 ✅ + 1.3.19 (audit events) | M |
| 9.4 | Content-Aware Tool Gating — bash pipeline static analysis beyond risk_level + monotonic denial invariant (guards can only deny, never resurrect) | 9.4 ✅ + 1.3.19 | M |

### Plugin Ecosystem: Open-Standard Ingestion (NEW — 2026-08-16 adjustment)

> **Rationale**: Agent Plugins 1.0 (open standard, 2026-08-06, backed by OpenAI/Microsoft/Google/Amazon/Cursor/GitHub) has converged the industry on "plugin = declarative package of Skills + MCP config"; SKILL.md layer already landed in Bedrock AgentCore/watsonx/Microsoft Agent Framework/Salesforce. Hecate adopts it as the ecosystem-facing third-party format; `.hecate-plugin` narrows to P-tier deep-integration (catalog notes on 5.5/5.5b). **Ordering**: independent of 1.3.19 — parallelizable with any Sprint 7 work item; 5.5c → 5.13a strict serial (scanning gates the ingest pipeline's go-live).

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 5.5c | Agent Plugins 1.0 Standard Ingestion — directory/git-URL/zip install, closed-manifest validation, fixed-location discovery (skills/ + mcp.json) with skip-and-continue + path containment, SKILL.md→SkillModel import (source/origin + pin-by-hash), mcp.json→MCPServerRegistry, **component-level trust dispatch** (skills→T4 & http-MCP→T2 = workspace admin; stdio = org admin only, SaaS default-deny via config gate; self-hosted stdio runs in 9.4c container sandbox — decided 2026-08-16), bare-SKILL.md-directory acceptance | 5.5 ✅ + 5.4c ✅ + 5.9 ✅ | L |
| 5.13a | Plugin Content Scanning (split from 5.13, pulled to P3) — prompt-injection detection (incl. invisible-Unicode), secret detection, allowed-tools audit; fail-closed install (block/warn/allow, org threshold), rescan on enable, results API + Ops Center display. V1 = pure rule engine; LLM second-pass review optional in v2 (decided 2026-08-16) | 5.5c | M |
| 5.5 (enh) | T0 Tightening — loader rejects runtime-installed non-first-party `python:` entries (SaaS reject; self-hosted default-deny + allowlist) — operationalizes ADR-029 "runtime artifacts never T0"; near-zero cost while installed-code-plugin base is ~empty | 5.5 ✅ | S |

### Advanced RAG & Knowledge (rescoped 2026-08-14)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 3.2.4 | Reranking | Vector Search ✅ | M |
| 3.3.2 | Incremental Update | RAG ✅ | M |
| 3.3.3 | Knowledge Quality Evaluation | Ragas | S |

> **Deferred to P5 (2026-08-14)**: 3.1.2-3.1.4 (OCR / Table / Layout — integrate Docling/Unstructured instead of building), 3.1.8 (Extended Document Processing, from P4), 3.5.1-3.5.3 (Knowledge Graph suite — integrate GraphRAG/LlamaIndex), 3.5.5 (Knowledge Graph API, from P4), 3.4.2 (High-Throughput Retrieval — Qdrant deployment guide covers it). See Sprint 10 deferred table for triggers.

### Multi-Channel Expansion

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 11.2 | Web Widget (Simplified) *Wave 1 — internal Portal / embeddable for any Hecate deployment* | API ✅ | S (simplified) |
| 11.3 | Feishu (Lark) ✅ *Wave 1 — China market anchor + first ChannelABC reference impl (Delivered 2026-08-13)* | Channel SDK | M |
| 11.4 | WeCom (WeChat Work) *P5 deferred — reuse 11.3 pattern when needed* | 11.3 ✅ | S |
| 11.5 | DingTalk *P5 deferred — reuse 11.3 pattern when needed* | 11.3 ✅ | S |
| 11.8 | Intent Recognition & Routing | Multi-Agent ✅ | M |
| 11.9 | Slack ✅ / Discord/Telegram *Slack (Delivered 2026-08-13); Discord/Telegram — P5 deferred, reuse Slack pattern when needed* | ChannelABC ✅ | M (Slack) + S (Discord/Telegram) |

**Deferred to P5 (按需触发, 不在当前 P3 主线)**:
- 11.2 (full) — Web Widget 完整版（匿名 to-C 场景），trigger = 第一个公开网站/营销/客服场景的客户
- 11.6 — WeChat Official Account / Mini Program（to-C niche），trigger = 第一个 to-C 客户
- 11.10 — Custom Channel SDK（长尾 niche），trigger = 社区/客户明确请求

### 11.x Wave 节奏（2026-08-12 规划）

| Wave | Feature | 触发条件 | 时机 |
|---|---|---|---|
| Wave 1 | 11.2 简化版 + 11.3 飞书 + 11.9 Slack | 主动 | **2026-08-16: 11.2 简化版 ✅ + 11.3 ✅ + 11.9 Slack ✅ 全部交付** |
| Wave 2 (P5 deferred) | 11.4 企微 + 11.5 钉钉 + 11.9 Discord/Telegram | 按客户需求触发 (wechat wecom / dingtalk / discord / telegram 客户需求) | 暂停 |
| Wave 3（按需） | 11.2 完整版 + 11.6 微信 + 11.10 Custom SDK | 不预定时间，等明确客户需求 | 不预定 |
| 11.16 | Per-Token-Type Auth Pipeline | Auth ✅ | M |
| 11.17 | Two-Tier Identity Model | Auth ✅ + RBAC ✅ | M |

### Evaluation Suite

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 7.2a | 40+ Built-in Evaluators | Evaluation ✅ | L |
| 7.2b | AI-Synthesized Evaluation Dataset | 7.2a | M |
| 7.2c | Online/Offline Evaluation Tasks | 7.2a | M |
| 7.2d | Trace Backflow Dataset | 7.2a | S |
| 7.2e | Evaluation Report Dashboard | 7.2a | M |
| 7.3 | Workflow Evaluation | 7.1 ✅ | M |
| 7.4 | Human Annotation | 7.2 ✅ | M |
| 7.4a | Human Score Calibration | 7.4 | S |
| 7.5 | A/B Testing (rescoped to Agent-Level 2026-08-14) — agent/version-level controlled experiments; reuses 6.8a traffic-splitting + z-test machinery generalized from model routing; prompt comparison delegated to eval-platform integration (7.6b drop rationale) | 6.8a ✅ | S |

> **Dropped (2026-08-14)**: 7.6a Prompt Auto-Optimization, 7.6b Prompt Comparison — specialized frameworks (DSPy, IBM AgentOps GEPA) and evaluation platforms (LangSmith, Salesforce A/B Testing API) have standardized this; self-building is negative ROI.

> **TBD — Quality Regression Detection (G4 remainder)**: Once the Evaluation Suite produces per-model quality scores, add quality regression monitoring to the Model Monitoring Dashboard (O10+G4). Compare current-period quality scores against historical baseline; trigger alert when degradation exceeds threshold. Originated from `model-hub-completion` change where drift detection was shipped but quality regression was deferred pending evaluation data.

### Canvas Enhancements

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.1.24 | Human Input / Form Node | interrupt() ✅ + Canvas ✅ | M |
| 1.1.25 | Trigger Node | Scheduled Tasks ✅ + Webhook ✅ | M |

> **Deferred to P5 (2026-08-14)**: 1.1.18 Agent-Workflow Canvas Embedding, 1.1.19 Unified Skill Selector, 1.1.20 Nested Graph Visualization — Canvas enhancements without user feedback are speculative; Dify's collaborative editing (Loro CRDT) is the direction to aim for when triggered.

### Memory Enhancement

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 4.3a | Memory Engine Enhancement | Memory System ✅ | L |
| 4.14 | Memory Importance Scoring | Memory System ✅ | M |
| 4.15 | Multi-Signal Fusion Retrieval | 4.14 | M |
| 4.17 | Memory Pressure Alert | Token Budget ✅ | S |
| 4.25 | Layered Memory System | 1.3.15 Agent Environment | M |

### AIP Capabilities (P3 Foundation)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 6.16 | NL2Agent / NL2Flow | Canvas ✅ + Graph DSL ✅ | M |
| 6.18 | Trace Annotation | EventStore ✅ + Audit ✅ | S |

> **Dropped (2026-08-14)**: 6.17 DSL Conversion Framework — MCP/A2A protocol standardization plus Salesforce open-sourcing Agent Script indicates the industry is converging on standard agent definitions, not DSL compatibility layers.
> **Deferred to P5 (2026-08-14)**: 6.21 Decision Lineage — full decision lineage requires an Ontology foundation (data + function + app version binding per trace, Palantir standard); effort was underestimated in the initial analysis.

### Milestone M7 (End of Sprint 7)

- [ ] P3 re-scoped 125/125 (100%) — 2026-08-14 re-scope basis
- [ ] Event-sourced execution state: log-as-truth invariant + derive_messages projection + DeltaChannel incremental checkpoint
- [ ] Dynamic Orchestration (7th multi-agent pattern) on Pregel
- [ ] Execution Replay Phase 1 (timeline replay) operational
- [ ] Browser Automation builtin tool operational
- [ ] Skill Provider Registry (rank + invocation policy) operational
- [ ] Completed-feature upgrades: waterfall middleware chain (1.3.5i E3) + HITL fail-closed (1.3.4) + content-aware tool gating (9.4)
- [ ] Advanced RAG: Reranking + Incremental Update + Knowledge Quality Evaluation
- [x] Multi-Channel Wave 1 complete (11.2 simplified)
- [ ] Per-Token-Type Auth Pipeline + Two-Tier Identity Model
- [ ] 40+ Built-in Evaluators + Evaluation Suite (AI-synthesized datasets, online/offline tasks, trace backflow, workflow eval, human annotation, A/B testing — 7.6a/b dropped)
- [ ] Canvas: Human Input/Form Node + Trigger Node (1.1.18-20 deferred P5)
- [ ] Memory Enhancement: importance scoring, multi-signal fusion, pressure alerts, layered memory
- [ ] NL2Agent/NL2Flow + Trace Annotation (6.17 dropped, 6.21 deferred P5)

---

---

## Sprint 8: P4 Kickoff — Intelligence (Month 15–16)

> **Goal**: P4 intelligence features — Self-Learning, Agentic AI (RL, Prompt Optimization, Ontology Actions, OAG), Memory Intelligence. Make agents genuinely smart.

### Self-Learning & Evolution

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.5e | Hallucination Detection & Mitigation | PostLLMHook ✅ + ContextEngine | L |
| 1.3.6 | Self-Learning Agent Runtime | 1.3.6a–d ✅ | M |
| 1.3.6e | Self-Evolution Closed Loop | 1.3.6 ✅ | S |
| 1.3.10 | Multi-Level Intent Recognition | LLM ✅ | M |

### Agentic AI (Moved from P3)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 6.15 | Agentic RL Framework | Evaluation ✅ + LLM ✅ | L |
| 6.19 | Prompt Self-Optimization | Evaluation ✅ + LLM ✅ | M |
| 6.20 | Ontology Action System | Knowledge Graph (P5 deferred 2026-08-14 — rebase on GraphRAG/LlamaIndex integration when triggered) | L |
| 6.22 | OAG (Ontology-Augmented Generation) | 6.20 + RAG ✅ *(blocked by 6.20's P5 KG dependency)* | L |

### Memory Intelligence

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 4.5 | Sleep-time Memory Consolidation | Memory System ✅ | M |
| 4.13 | Context Engine Processor Chain (+ LLM-managed compaction via surface replacement — compaction events as checkpoint sources, per 2026-08-14 research) | ContextEngine ✅ + 1.3.19 | M |
| 4.16 | LLM-Managed Memory | ContextEngine ✅ | M |
| 4.18 | Conversation Recall Storage | Memory System ✅ | M |
| 4.19 | Self-Editing Memory | Memory System ✅ | M |
| 4.20 | Multi-Step Memory Retrieval | Memory System ✅ | M |
| 4.21 | Task Memory | Memory System ✅ | M |
| 4.22 | Tool Memory | Memory System ✅ | M |
| 4.23 | Cross-Thread Memory Store | Memory System ✅ | M |

### Plugin Ecosystem (NEW — 2026-08-16 adjustment)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 5.5d | Dual-Format Plugin Convergence & Export — Hecate-private plugin content migrates into `io.hecate/` namespace dir inside Agent Plugins packages (one package = conformant for all clients + deep-integration for Hecate); `hecate plugin export` packages workspace skills as Agent Plugins bundles; ZIP demoted to transport-only (directory/git-URL install) | 5.5c (P3) + 5.5b ✅ | M |

### Milestone M8 (End of Sprint 8)

- [ ] Hallucination detection operational
- [ ] Self-Learning loop operational
- [ ] Agentic RL Framework with data flywheel
- [ ] Prompt Self-Optimization with ACE/GEPA
- [ ] Ontology Action System with writeback
- [ ] OAG complete (RAG + Logic + Actions)
- [ ] Sleep-time Memory Consolidation operational
- [ ] LLM-Managed Memory with self-management
- [ ] All memory intelligence features delivered

---

## Sprint 9: P4 Complete — Knowledge & Execution Intelligence (Month 17–18)

> **Goal**: Complete P4 — Knowledge Intelligence (GraphRAG, Agentic RAG), Multi-Agent Intelligence (Peer Selection, Agent Teams, ACP), Execution Intelligence (Simulation, Computer-use, DataAgent, VibeCoding, Voice Pipeline).

### Knowledge Intelligence

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 3.2.10 | Agentic RAG | RAG ✅ + ContextEngine | M |
| 3.5.4 | GraphRAG Query Engine | 3.5.1 + 3.5.2 + 3.5.3 *(P5 deferred 2026-08-14 — rebase on GraphRAG/LlamaIndex integration when triggered)* | L |
| 3.5.6 | Agent-Native Graph Memory | 3.5.2 + Memory System *(P5 deferred 2026-08-14 — same trigger)* | M |
| 3.5.13 | Temporal Memory & Reasoning | Memory System ✅ | M |
| 3.5.14 | Lazy GraphRAG | 3.5.1 + 3.5.2 *(P5 deferred 2026-08-14 — same trigger)* | L |

### Multi-Agent Intelligence

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 2.5 | Peer Selection (Selector) | Multi-Agent ✅ | M |
| 2.5a | Expert Panel Deliberation | 2.5 | M |
| 2.6 | Inter-Agent Communication | Multi-Agent ✅ | M |
| 2.6a | Multi-Agent Central Controller | 2.6 | M |
| 2.11 | Agent Team Templates | Graph template ✅ | M |
| 2.13 | ACP (Agent Client Protocol) Support (NEW — 2026-08-14) — external coding agents (Claude Code, Codex, Gemini CLI) as worker nodes in Hecate orchestration; subagent provider seam (in-process/fork/ACP); complements A2A (agent-to-agent) — ACP is host-to-coding-agent | A2A ✅ | M |
| 13.15 | Distributed Team Orchestration | A2A ✅ (P3) | M |

### Execution Intelligence

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.5i ✅ | Deterministic Hooks (Lifecycle Events) | Settings system | M |
| 1.3.11 | Asynchronous Execution API Mode | Streaming ✅ + Session ✅ | M |
| 6.23 | 5-Level Intent Recognition | LLM ✅ | M |
| 6.24 | Object Log & Decision Log | EventStore ✅ | M |
| 6.25 | Object History Analysis | EventStore ✅ | M |
| 6.26 | Simulation Environment | Ontology Actions (6.20) ✅ | L |
| 6.28 | DataAgent (NL2SQL) | RAG ✅ + LLM ✅ | M |
| 6.29 | VibeCoding Tool Set | CLI Tools ✅ | M |
| 6.30 | Fine-Grained Permission Control | Ontology Actions (6.20) ✅ | M |
| 6.31 | Data Integration Framework | MCP ✅ | L |
| 6.32 | gVisor Enhanced Sandbox | Docker ✅ | M |
| 6.32a | Kata Containers Sandbox | Docker ✅ | M |
| 6.33 | Decision Simulation | Ontology Actions (6.20) ✅ | M |
| 11.18 | Multi-Stream Modes | Pregel Runtime ✅ | M |
| 9.16 | External Policy Engine Interface (Cedar/OPA/**Dogwood** — AWS open-sourced Dogwood 2026-08-06: Cedar superset + MFOTL temporal logic governing action *sequences* (prerequisites, ordering, rate limits, escalation approval), deny-by-default, Apache 2.0, built into Bedrock AgentCore) | 9.4 ✅ + 5.14 | L |
| 9.16a | Chaos Engineering for Multi-Replica (chaos-mesh + toxiproxy + pod-kill + node-drain) | 13.4 ✅ + 13.4b ✅ + 9.4 ✅ | L |
| 9.17 | AI Auto-Approval | 9.4 ✅ + 9.14 | M |

### Canvas Intelligence

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.1.21 | Multi-Agent Controller Canvas | 2.6a | M |
| 1.1.22 | Orchestration Mode Switching | 1.1.21 | M |
| 1.1.23 | Execution State Visualization | Canvas ✅ | M |
| 1.1.26 | Object CRUD Node | KG Construction (P5 deferred 2026-08-14 — rebase on integration when triggered) | M |
| 1.1.27 | Side-by-side Chat + Canvas | 1.1.23 | M |

### 2026-08-14 Re-scope Additions (NEW)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 8.21 | Projection Registry — named projections over the event log (permission presets, agent profiles, derived views) + canonical setter events; zero-database state derivation with consistent rebuild semantics | 1.3.19 ✅ (P3) | M |
| 13.20 | Atomic File Writes & Cross-Process File Locking — `<file>.lock` advisory locking + write-to-temp + atomic rename commit for multi-process file mutation (skills, plugin configs, state files); stale-lock recovery; ~150 LOC utility | — | S |
| 11.11 | Voice Agent Pipeline (moved from P5) — STT → agent workflow → TTS end-to-end pipeline with configurable providers; barge-in support (interrupt TTS when user speaks) | API ✅ | M |

### Milestone M9 (End of Sprint 9)

- [ ] P4 = 96/96 (100%) — 2026-08-14 re-scope basis (100 features incl. 4 done; 3.5.5/3.1.8 deferred P5, 11.11/2.13/8.21/13.20/6.27a added)
- [ ] GraphRAG Query Engine with Global/Local/Hybrid search (rebased on P5 KG integration when triggered)
- [ ] Agentic RAG with iterative retrieval
- [ ] Temporal Memory with time-aware retrieval
- [ ] Lazy GraphRAG with cost-optimized indexing (same trigger)
- [ ] Peer Selection and Expert Panel operational
- [ ] Agent Team Templates available
- [ ] ACP support — external coding agents as worker nodes (2.13)
- [ ] Distributed Team Orchestration functional
- [ ] Deterministic Hooks with lifecycle events
- [ ] Asynchronous Execution API operational
- [ ] 5-Level Intent Recognition with controller evolution
- [ ] Simulation Environment for safe verification
- [ ] Computer-use (6.27a) for GUI automation — browser half delivered in P3 as 6.27
- [ ] Voice Agent Pipeline with barge-in (11.11, moved from P5)
- [ ] DataAgent with NL2SQL capabilities
- [ ] VibeCoding CLI tool set available
- [ ] Multi-Stream Modes operational
- [ ] Projection Registry + Atomic File Locks (8.21, 13.20)
- [ ] Canvas Intelligence features delivered

---

## Sprint 10: P5 Ecosystem (Month 19–20)

> **Goal**: Build the ecosystem — Marketplace, Community, Industry capabilities, Compliance certification, Distribution.

### Asset Marketplace

> **Rescoped (2026-08-16)**: 12.0 v1 = Agent Plugins installer (5.5c, P3) + static git-index directory (Claude Code marketplace pattern) + scan-results display (5.13a, P3); **E-tier only** (T4 packages + T2 MCP registrations, never code plugins). 12.5 Partner Monetization + EC1 ARD (14.1) + EC4 (13.14 enhancement) + EC5 (11.13 enhancement) **frozen** until T4 supply/traction evidence exists (GPT Store decline; ClawHavoc governance lesson). Full rationale in feature-catalog 12.0/12.5/14.1 notes.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 12.0 | Asset Marketplace (rescoped 2026-08-16: installer + git index, E-tier only) | Plugin System ✅ + 5.5c (P3) | M (was L) |
| 12.1 | Industry Templates | 12.0 | M |
| 12.2 | Industry Knowledge Packs | 12.0 | M |
| 12.3 | Industry Skill Packs | 12.0 | M |
| 12.4 | Industry Integration Guide | 12.0 | S |
| 12.5 | Partner Monetization Infrastructure (frozen 2026-08-16, pending supply evidence) | 12.0 | L |

### Community & Distribution

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.1.6 | Template Marketplace | Canvas ✅ | M |
| 1.1.12 | Dify Workflow Import | Graph DSL ✅ | S |
| 5.10 | Prompt Template Marketplace | Prompt Management ✅ | S |
| 13.14 | PyPI Package Distribution | SDK ✅ | M |
| 11.13 | AgentSpace SDK (Embedded Integration) | API ✅ | M |
| 11.14 | End-User Application | API ✅ | L |
| 11.15 | Mobile GUI | 11.14 | M |
| 15.2 | Community Translations | i18n SPI (15.1) ✅ | M |

### Compliance & Governance

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 9.6 | Compliance Framework | Security ✅ | M |
| 9.6a | EU AI Act Compliance | 9.6 | M |
| 9.9 | Compliance & Audit Center | 9.6 | M |
| 6.46 | Model Governance | 6.45 ✅ | M |

### Knowledge Graph Visualization & Ontology

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 3.5.7 | Knowledge Graph Visualization | 3.5.2 ✅ | M |
| 3.5.8 | Knowledge Graph UI Editor | 3.5.7 | M |
| 3.5.9 | Ontology Schema Definition | 3.5.1 ✅ | M |
| 3.5.10 | SHACL Validation | 3.5.9 | M |
| 3.5.11 | Ontology Import/Export | 3.5.9 | M |
| 3.5.12 | Ontology Versioning | 3.5.9 | M |

### Advanced Modalities & Edge

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 11.11a | Vision Input (Multi-Modal) | LLM Vision ✅ | M |
| 11.12 | Desktop Client | API ✅ | L |
| 13.16 | Edge/Lite Deployment | SQLite ✅ | M |

### Deferred from P3/P4 (2026-08-14 competitor-analysis re-scope)

> Trigger-based — no scheduled delivery. Rationale and evidence in `docs/research/2026-08-competitor-analysis.md` §Feature decisions.

| # | Feature | Original Priority | Trigger |
|---|---------|------------------|---------|
| 3.1.2 / 3.1.3 / 3.1.4 | OCR / Table Extraction / Layout Analysis | P3 | Customer demand; integrate Docling/Unstructured instead of building |
| 3.4.2 | High-Throughput Retrieval | P3 | Qdrant native sharding + deployment guide covers it |
| 3.5.1 / 3.5.2 / 3.5.3 | Knowledge Graph Construction / Graph DB / Community Detection | P3 | Integrate GraphRAG/LlamaIndex when KG use case lands |
| 3.5.5 | Knowledge Graph API | P4 | Same trigger as 3.5.1-3 |
| 3.1.8 | Extended Document Processing | P4 | Same trigger as 3.1.2-4 |
| 6.9 / 6.10 / 6.12 / 6.13 | Model Management UI (4 items) | P3 | After 13.1 SaaS launch + real user feedback |
| 1.1.18 / 1.1.19 / 1.1.20 | Canvas Embedding / Skill Selector / Nested Graph | P3 | User feedback; Loro CRDT collaborative editing is the target direction |
| 6.21 | Decision Lineage | P3 | Requires Ontology foundation first (Palantir standard: data + function + app version binding per trace) |

### AIP Advanced Capabilities

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 6.34 | AI Office | Document APIs | M |
| 6.35 | Industrial Data Integration | MQTT/OPC UA | L |
| 6.36 | Asset Marketplace Operations | 12.0 | M |
| 5.13 | Plugin Security & Signing (rescoped 2026-08-16: signing/digest/score only — content scanning split to 5.13a, P3) | Plugin System ✅ | M |
| 6.37 | Memory Clustering & Conflict Resolution | Memory System ✅ | M |
| 6.38 | Self-Planning (PDDL + MCTS) | LLM ✅ | L |
| 6.39 | Tool Auto-Creation | LLM ✅ | M |
| 6.40 | Firecracker microVM Backend | 1.3.15a ✅ + 6.32a | L |
| 13.4c | Session-Level microVM Isolation (Paradigm B — Bedrock AgentCore model, requires PregelRuntime RPC refactor) | 6.40 ✅ + 13.4 ✅ + engine refactor | XL |
| 13.19 | Service Mesh Integration (Istio/Linkerd mTLS + east-west traffic + canary routing) | 13.1 + 13.4 ✅ | M |
| 6.41 | WASM Runtime Backend (deprioritized 2026-08-16: T4/T2 cover third-party scenarios; sequence behind 5.5c) | 5.3 ✅ + 6.40 | M |
| 6.42 | Global Branching | Ontology ✅ | M |
| 6.43 | Embedded Ontology | Ontology ✅ | M |
| 11.19 | Platform-Level Governance | Auth ✅ | M |
| 11.20 | Zero Trust Architecture | IAM ✅ | M |
| 14.1 | Agentic Resource Discovery (ARD) | A2A ✅ | L |
| 2.12 | Agent Payments (AP2) | A2A ✅ | M |
| 7.8a | Agent Benchmark Integration | Evaluation ✅ | M |
| 4.24 | Memory Versioning | Memory System ✅ | S |

### Milestone M10 (End of Sprint 10)

- [ ] P5 = 60/60 (100%) — 2026-08-14 re-scope basis (44 original − 11.11 moved to P4 + 17 deferred from P3/P4)
- [ ] Asset Marketplace operational
- [ ] Partner Monetization with Stripe integration
- [ ] Industry Templates and Knowledge Packs available
- [ ] PyPI package published (hecate-sdk)
- [ ] End-user web application beta
- [ ] Mobile GUI available
- [ ] Community translation framework ready
- [ ] EU AI Act compliance certified
- [ ] Knowledge Graph Visualization and Ontology tools
- [ ] Vision input operational (Voice moved to P4 as 11.11 Voice Agent Pipeline, 2026-08-14)
- [ ] Edge/Lite deployment available
- [ ] Plugin Security & Signing for marketplace
- [ ] Agentic Resource Discovery (ARD) operational
- [ ] All P5 features delivered

---

## Critical Path Analysis

```
P1 Close-Out → Multi-DB → Multi-Tenant RBAC → SaaS Deployment
                                                          ↑
Canvas → Canvas UI Enhancement → Collaboration Patterns → A2A Protocol → Agent Teams → Distributed Team │
                                                          │
EventStore Wiring → Tracing → Monitoring → Alerting ─────┘
```

### Three Critical Paths

| Path | Sprint Span | Blocker Impact |
|------|------------|----------------|
| **Multi-Tenant Path** | Sprint 2→4→6 | Multi-DB (Sprint 2) ✅ → Org/User models (Sprint 4) ✅ → RBAC ✅ → SSO → SaaS. Org Management + RBAC done; SSO and Tenant Isolation remaining. |
| **Multi-Agent Path** | Sprint 2→5→6 | Canvas (Sprint 2) → Canvas UI Enhancement (Sprint 2) → Collaboration Patterns (Sprint 2) → A2A Protocol (Sprint 5, moved from P4) → Peer Selection (Sprint 6, moved to P4) → Agent Teams → Distributed Team Orchestration (Sprint 6). This is the product differentiation path. |
| **Observability Path** | Sprint 1→4 | EventStore wiring (Sprint 1) → Tracing → Monitoring → Audit. ALL evaluation and security features depend on this. |

### Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| workspace_id pre-reserved in 5 models; changes concentrated in API/Service layer, no engine impact | ✅ Done — workspace_id added to 14 unscoped models, vector store filtering, migration + backfill, service/API enforcement |
| Canvas development takes longer than estimated | P2 delivery delayed | React Flow is mature; frontend can develop in parallel with backend. Canvas UI features (1.1.14-1.1.17) are incremental enhancements to existing canvas. |
| Collaboration pattern UI complexity | Sprint 2 overloaded | Start with 3 core patterns (Sequential, Parallel, Handoff) in Sprint 2, add remaining (Broadcast, Negotiation, Debate) in Sprint 3. |
| A2A protocol spec instability | Sprint 5 affected | ✅ Resolved — A2A v1.0 GA (Linux Foundation, v1.0.0 2026-03-12 + patch v1.0.1 2026-05-26); core implemented (AgentCard + Task lifecycle). Correction (2026-08-14): earlier note cited a "v1.2 (March 2026)" which does not exist in official releases — v1.0.x is current. |
| ABC wiring introduces regressions | Sprint 1 delayed | Each ABC wiring uses optional constructor parameters with default behavior; existing tests cover all paths |
| Evaluation system too broad (40+ evaluators) | Sprint 4 overloaded | Start with 10 core evaluators in Sprint 4, add remaining in Sprint 5 |

---

## Competitive Timeline Benchmarks

Based on research of AutoGen, CrewAI, Coze, Dify, Bisheng, LangFuse, Langflow, and Google A2A:

| Feature Area | Industry Benchmark | Hecate Sprint | Assessment |
|--------------|-------------------|---------------|------------|
| Multi-Agent Orchestration | 0–14 months (0 if core, 14 if evolving) | Sprint 2 (M3–4) | ✅ Reasonable — Graph template foundation exists |
| Canvas UI Enhancement | 2–4 months | Sprint 2 (M3–4) | ✅ Reasonable — incremental enhancement to existing React Flow canvas |
| Collaboration Patterns | 4–8 months | Sprint 2 (M3–4) | ✅ Reasonable — 6 patterns, start with 3 core patterns |
| Evaluation System | 8–12 months | Sprint 3–4 (M5–8) | ✅ Reasonable — progressive build |
| Multi-Tenant RBAC | 12–18 months | Sprint 4 (M7–8) | ✅ Reasonable — workspace_id pre-reserved |
| A2A Protocol | 12 months to spec GA | Sprint 5 (M9–10) | ✅ Moved to P3 — Google A2A v1.0 GA (Linux Foundation), adopted by IBM/Salesforce/Cisco. Early adoption reduces integration debt. |
| Visual Canvas | 0–16 months | Sprint 2 (M3–4) | ✅ Reasonable — React Flow mature |

**Key Insight**: Framework-first platforms (AutoGen, CrewAI, Langflow) ship core features in 0 months. Platform-first products (Dify, Coze) take 12–18 months for enterprise features. Hecate is at the inflection point — Graph engine + 11 ABCs + 123 features already built. The roadmap timelines are well within industry benchmarks. Canvas UI enhancement (1.1.14-1.1.23) and collaboration patterns (2.7a-2.7c) are incremental additions that build on the existing React Flow foundation, not greenfield development. MCP/Skill resource management (5.4c, 5.9d, 5.9e) addresses the operational maturity gap identified in competitive analysis. Knowledge Graph (3.5.1-3.5.8) and Ontology Modeling (3.5.9-3.5.12) provide structured knowledge capabilities matching Google ADK, LangGraph, and Microsoft GraphRAG. Memory Retrieval Quality (4.14, 4.15) closes the gap with Mem0's multi-signal fusion approach. MemGPT-Inspired Memory (4.16-4.20) adds LLM self-management, pressure alerts, conversation recall, self-editing, and multi-step retrieval. AgentScope-Inspired Memory (4.21-4.24) adds task memory, tool memory, cross-thread store, and memory versioning. AIP-Inspired Capabilities (6.15-6.43) — from AgentArts (fka Versatile) and Palantir — add Agentic RL, NL2Agent, DSL conversion, ontology actions, decision lineage, OAG, intent recognition, simulation, Browser/Computer-use, DataAgent, VibeCoding, fine-grained permissions, data integration, gVisor sandbox, AI Office, industrial data, asset operations, self-planning, tool auto-creation, Firecracker microVM, cloud doc connector, global branching, and embedded ontology. **Hecate's positioning: an open-source enterprise Agent platform with ontology enhancements — not an AIP (where ontology is the architectural organizing principle), but an AgentArts-level (fka Versatile) platform where ontology is an enabler alongside the Pregel engine, Graph DSL, and 15 extension points.**

---

## Milestone Summary

| Milestone | Date | Verification Criteria |
|-----------|------|-----------------------|
| **M1: P1 Complete** | Month 2 | 19/19 features ✅; 4 ABCs wired; 0 layering violations; all tests green |
| **M2: Platform Ready** | Month 4 | Canvas usable; Multi-Agent orchestrable; Multi-DB + Multi-Vector-DB supported |
| **M3: Feature Complete** | Month 6 | P2 63/63 (100%); 5+ channels; Evaluation baseline operational |
| **M4: Enterprise Ready** | Month 8 | Resilience infrastructure (exception hierarchy + auto-retry + tool gating) ✅; ContextEngine Phase 1 (LLMWorker context pipeline) ✅; Plugin SPI Core + EvaluatorABC defined ✅; Multi-Tenant RBAC + SSO; full security stack ✅; end-to-end observability ✅ |
| **M5: P3 Enterprise** | Month 10 | Platform SPI complete: ChannelABC + AuthProviderABC + i18n SPI ✅ (NotifierABC merged into ChannelABC); A2A Protocol with Signed Agent Cards; Model Hub (Catalog + Lifecycle Manager); Enterprise Identity: SSO + SCIM + Vault + Budget Management ✅ |
| **M6: P3 Security & Ops** | Month 12 | Ops Center (Dashboard + Agent Health + Conversation Analytics + Tool Execution Analytics + CI/CD Gating + Agent Catalog Governance); Security (DLP + Runtime Protection + Red Teaming); Plugin System; Deployment infrastructure (SaaS + Canary + Horizontal Scaling + Backup) |
| **M7: P3 Complete** | Month 14 | P3 re-scoped 125/125 (100%); Event-Sourced State (log-as-truth + DeltaChannel); Dynamic Orchestration; Run Replay Phase 1; Browser Automation Tool; Skill Provider Registry; Advanced RAG (Reranking + Incremental + Quality Eval); Multi-Channel Wave 1 (11.2 simplified ✅ + 11.3 ✅ + 11.9 Slack ✅); Evaluation Suite (7.6a/b dropped); Canvas (Human Input/Form + Trigger; 1.1.18-20 deferred); Memory Enhancement |
| **M8: P4 Intelligence** | Month 16 | Hallucination Detection operational; Self-Learning loop; Agentic RL Framework; Prompt Self-Optimization; Ontology Action System; OAG complete; Sleep-time Memory Consolidation; LLM-Managed Memory; Memory Intelligence features |
| **M9: P4 Complete** | Month 18 | P4 96/96 remaining (100%); GraphRAG Query Engine (P5-trigger); Agentic RAG; Temporal Memory; Lazy GraphRAG (P5-trigger); Peer Selection; Agent Team Templates; ACP Support (2.13); Distributed Team Orchestration; Deterministic Hooks; Asynchronous Execution API; 5-Level Intent Recognition; Simulation Environment; Computer-use (6.27a); Voice Agent Pipeline (11.11); DataAgent; VibeCoding; Multi-Stream Modes; Projection Registry (8.21) + Atomic File Locks (13.20); Canvas Intelligence |
| **M10: P5 Complete** | Month 20 | P5 46/46 (100%); Asset Marketplace; Partner Monetization; Industry Templates; PyPI SDK; End-User App; Mobile GUI; EU AI Act Compliance; Knowledge Graph Visualization; Ontology tools; Voice/Vision; Edge/Lite; Plugin Security; Agentic Resource Discovery; All P5 features delivered |

---

## Dependency Chains (by Sprint)

### P1 (Sprint 1)

```
Execution Engine → Model Access → Tool System → Skill Loading → Agent Runtime → Basic RAG → API → Conversation Logs
```

### P2 (Sprint 2–3)

```
GraphDSL → Canvas → Workflow Nodes → Agent Configurator → Scenario Packaging → Multi-Agent → Memory → Multi-Channel
Canvas → Agent Node Config → Template Customization → Typed Edges → Fan-Out/Merge Editing
Canvas → Collaboration Pattern Selection → Agent Communication Config → Routing Rule Config
Self-Hosted Deployment → Offline Deployment → Container Orchestration → Environment Adaptation
Security Isolation → Internal/External Network Isolation
API → Open Platform → Webhook
Memory → Context Engineering → Validation → Session Locking
Multi-Agent → Agent Message Bus → Negotiation → Task Allocation
Model Access → Model Routing → Prompt Management
Multi-Database → Multi-Vector-DB → MCP Server Mode
```

### P3 (Sprint 4–5)

```
Organization Management → RBAC/SSO → Evaluation → Security → Observability → Operations
Resilience → Exception Hierarchy (1.3.5g) → Auto-Retry (1.3.5h) → Tool Gating (1.3.5f)
Plugin SPI Core (5.5a) → EvaluatorABC (7.2-abc) + ChannelABC (11.1-abc) + AuthProviderABC (10.3-abc) + NotifierABC (8.6-abc) + i18n SPI (15.1)
Failure Analysis → Meta-Agent Scheduler → Garbage Collector + Drift Detector + Compliance Checker
Model Routing → A/B Testing → Gray Release → Circuit Breaker → Key Encryption → Intelligent Router
Security → Sandbox Executor → Sandbox Pool → Event Store → Tracing → Metrics → NotifierABC
Authentication → Authorization → Multi-Tenant → Tenant Isolation
Multi-Agent → A2A Protocol (2.10) → Signed Agent Cards (2.10a) → Conflict Handling → Skill Registry → Mutual Embedding
MCP Client → MCP Server Mode → MCP Streamable HTTP (5.4b) → MCP Server Registry & Connection Management (5.4c) → MCP Gateway → Plugin System (5.5, via 5.5a) → Tool Permission
Skill Loading → Skill Versioning (5.9d) → Resource Versioning
Knowledge Graph Construction (3.5.1) → Graph Database Integration (3.5.2) → Community Detection (3.5.3) *(P5 deferred 2026-08-14)*
Memory Isolation (4.6) → Memory Importance Scoring (4.14) → Multi-Signal Fusion Retrieval (4.15)
LLM-Managed Memory (4.16) → Memory Pressure Alert (4.17) → ContextEngine Integration
Task Memory (4.21) → Trajectory Learning → Experience Retrieval
Evaluation → Agentic RL Framework (6.15) → Data Flywheel → Model Optimization
Canvas + Graph DSL → NL2Agent (6.16) → NL2Flow → Workflow Auto-Generation
EventStore → Trace Annotation (6.18) → Evaluation Datasets → Agentic RL
Evaluation → Prompt Self-Optimization (6.19) → ACE/GEPA Algorithm → Auto-Optimized Prompts
Knowledge Graph (3.5.1, P5 deferred) → Ontology Action System (6.20) → Object Actions → Writeback
EventStore → Decision Lineage (6.21, P5 deferred 2026-08-14) → Decision Audit → Compliance
6.20 + RAG → OAG (6.22) → RAG + Logic + Actions Closed Loop *(blocked by 6.20's P5 KG dependency)*
Auth Service → Per-Token-Type Auth (11.16) → Two-Tier Identity (11.17) → Fine-Grained Access Control
Canvas → Human Input/Form Node (1.1.24) → Trigger Node (1.1.25) → Event-Driven Workflows
CheckpointStore → Distributed Session State Store (13.4a) ✅ (5/5) → Horizontal Scaling (13.4) → Stateless Multi-Replica
EventStore → Event-Sourced Execution State (1.3.19) → Run Replay (8.20) + Projection Registry (8.21, P4)
EventStore (1.3.19) → HITL durable audit pairs + middleware waterfall events
Pregel + Collaboration Patterns (2.7a ✅) → Dynamic Orchestration (1.3.18) → runtime task DAG → 7th pattern
Built-in Tools (5.1 ✅) → Browser Automation (6.27, P3) → Computer-use (6.27a, P4)
Skill Loading (5.9 ✅) → Skill Provider Registry (5.9 enhancement) → community skills ecosystem
A2A (2.10 ✅) → ACP Support (2.13, P4) → external coding agents as worker nodes
```

### P4 (Sprint 6)

```
Self-Learning → Trajectory Analysis → Policy Evolution → Constraint Injection → Self-Evolution Closed Loop
Hallucination Detection → Self-Learning → Intent Recognition → Deep Research
Deterministic Hooks (1.3.5i) → Lifecycle Event Handlers → Tool/File Automation
Skill Auto-Detection (5.9c) → Context-Based Skill Invocation → Progressive Disclosure
Skill Dependency Declaration (5.9e) → Dependency Resolution → Composable Skill Packages
MCP Server Registry & Connection Management (5.4c) → Connection Pool → Reconnection → Timeout Control → Health Check → Circuit Breaker
Plugin Packaging (5.5b) → Distributable Plugin Bundles → Community Ecosystem
Agentic RAG (3.2.10) → Iterative Retrieval → Query Reformulation → Multi-Step Reasoning
GraphRAG Query Engine (3.5.4) → Global/Local/Hybrid Search → Multi-Granularity Retrieval
Knowledge Graph API (3.5.5) → CRUD + Cypher Queries → Text-to-Cypher
Agent-Native Graph Memory (3.5.6) → Graph-Aware Context Assembly → Persistent Graph Memory
Conversation Recall Storage (4.18) → Semantic Search over History → Long-Term Context
Self-Editing Memory (4.19) → LLM-Driven Memory Correction → Memory Quality Improvement
Multi-Step Memory Retrieval (4.20) → Function Chaining → Complex Query Resolution
Tool Memory (4.22) → Tool Usage Learning → Parameter Tuning
Cross-Thread Memory Store (4.23) → Cross-Session Facts → Shared Knowledge
A2A Protocol (P3) → Peer Selection → Agent Team Templates → Distributed Team Orchestration
AuthProviderABC (P3) → SCIM Directory Sync (10.3b)
i18n SPI (P3) → Community Translations (15.2)
Asset Marketplace → Industry Capabilities → Marketplace → Advanced RAG → Distributed → SDK → Compliance
PyPI Distribution → End-User Application → Mobile GUI
Knowledge Graph Construction (3.5.1) → Graph Database Integration (3.5.2) → Ontology Schema Definition (3.5.9) → SHACL Validation (3.5.10)
Ontology Schema Definition (3.5.9) → Ontology Import/Export (3.5.11) → Ontology Versioning (3.5.12)
LLM → 5-Level Intent Recognition (6.23) → Controller Self-Evolution → Intent Caching
EventStore → Object Log & Decision Log (6.24) → Object History Analysis (6.25) → State Replay
Ontology Action System (P3) → Decision Simulation (6.33) → Simulation Environment (6.26) → Safe Verification
LLM → Browser Automation (6.27, P3) → Computer-use (6.27a, P4) → GUI Automation
RAG + LLM → DataAgent (6.28) → NL2SQL + Data Analysis + Chart Generation
CLI Tools → VibeCoding (6.29) → File IO + Command Execution + Code Execution
Ontology Actions (P3) → Fine-Grained Permissions (6.30) → Object/Attribute/Row-Level Access Control
Docker (1.3.15a) → gVisor Enhanced Sandbox (6.32) → Kata Containers (6.32a) → Firecracker microVM (6.40) → WASM Runtime (6.41)
Pregel Runtime → Multi-Stream Modes (11.18) → values/updates/messages/debug
Knowledge Graph (3.5.1-3.5.3, P5 deferred) → Object CRUD Node (1.1.26) → Ontology-Native Canvas Development
Execution State Visualization (1.1.23) → Side-by-side Chat+Canvas (1.1.27) → Integrated Dev/Test View
Streaming → Asynchronous Execution API (1.3.11) → Long-Running Workflow Support
P3 Observability (8.0-8.8) → Unified Ops Center Dashboard (8.9) → Agent Health Monitoring (8.9a) → Conversation Analytics (8.9b)
P3 Deployment (13.0-13.4) + Data Backup (13.5) + Version Upgrade (13.6) → Environment Management & ALM Pipeline (13.17) → API Management & Developer Portal (13.18)
P3 Cost Dashboard (8.3) → Budget Management & Cost Governance (10.7)
P3 AB Testing (7.4) + P3 Evaluators (7.2) → Testing Center / Sandbox (7.9)
P3 Model Management (6.8-6.13) → Model Catalog (6.44) → Model Lifecycle Manager (6.45) → Model Governance (6.46-P5)
P3 Model Deployment (6.1) → Self-Hosted Inference (6.5) → Managed Model Deployment (G5)
P3 Model Classification (6.11) → Multi-Modal Model Classification (G6)
P3 Fine-Tuning (6.6) → Fine-Tuning Pipeline (G7)
P3 Cost Tracking (6.4) + Ops Center Budget (10.7) → Model Cost Management (G8)
Model Management Console (O10) → Model Monitoring Dashboard (G4) → Model Lifecycle Integration
Plugin System (5.5) → Plugin Type Taxonomy + SDK (TP5) → Plugin Packaging (5.5b) → Plugin Security & Signing (5.13, P5)
Tool Permission (5.6) → Composable Tool Policy Pipeline (TP3) → Multi-Layer Tool Access
Enterprise Integration (5.8) → Per-Tool Auth Scope (TP6) → Per-Execution Credential Scoping (9.15) → Tool Credential Vault
Deterministic Hooks (1.3.5i) → Session Events + Tool Matchers (TP4) → Per-Tool Hook Config
Ops Center (8.9) → Agent Health (8.9a) → Conversation Analytics (8.9b) → Tool Execution Analytics (8.9c/TP2)
Knowledge Graph (3.5.1-3.5.3, P5 deferred) → GraphRAG Query Engine (3.5.4) → DRIFT Search (KM4) + Schema-Aware Traversal (KM5) → Lazy GraphRAG (3.5.14/KM2)
Memory Integration (4.5) → Sleep-time Consolidation (KM3) → Overnight Synthesis
Task Memory (4.21) → Work Context Graph (KM6) → Self-Improving Work Memory
Agent-Native Graph Memory (3.5.6) → Temporal Memory & Reasoning (3.5.13/KM1) → Time-Aware Retrieval
PII Masking (9.5) → Outbound DLP Engine (9.10/EF1) → Multi-Point Exfiltration Prevention
Secret Management → Enterprise Vault Integration (10.8/EF2) → Dynamic Secrets
Decision Lineage (6.21, P5 deferred) → Data Lineage Pipeline (EF3) → RAG Provenance
Version Upgrade (13.6) → Multi-Region Data Sovereignty (EF4) → GDPR Compliance
Multi-Auth (6.8) → Zero Data Retention Policy (EF5) → Provider Retention Control
Edge/Lite (13.16) → Confidential Computing Mode (EF6) → HYOK + Air-Gapped
Guardrail Hooks (9.1a) → Agent Runtime Protection (9.11/SS1) → Stateful Session Monitoring
Security Testing (7.7) → Automated Continuous Red Teaming (7.10/SS2) → CI/CD Adversarial Testing
Output Security (9.2) → System Prompt Leakage Protection (SS4) → OWASP LLM07
Audit Logs (8.7) → Structured Security Audit Pipeline (9.14) → Security Event SIEM Pipeline (SS5) → SOC Integration
Signed Agent Cards (2.10a) → Multi-Agent Trust Verification (2.10b) → ASI03/07/09
Execution Security (9.4) → Environment Security P0 (5.14: 9.12+9.13+9.14+9.15) → External Policy Engine Interface (9.16/Cedar/OPA) → AI Auto-Approval (9.17)
P3 Full-Chain Tracing (8.1) → OTel GenAI Semantic Conventions (OE2) → Multi-Agent Distributed Tracing (8.1d/OE6)
P3 Evaluators (7.2a) → Evaluation Three-Dimension Structuring (OE8) → Reasoning Efficiency Evaluator (OE9, frozen 2026-08-14 → OTel export)
P3 Online/Offline Eval (7.2c) → Production Online Scoring (OE3) → CI/CD Evaluation Gating (8.10/OE1)
Decision Lineage (6.21, P5 deferred) → Data-to-Decision Traceability (OE4) → Ontology-Level Provenance
Testing Center (7.9) + Regression Test Set (7.6) → CI/CD Evaluation Gating (8.10/OE1) → Deployment Quality Gate
Agent Evaluation (7.2) + Agent Benchmarks (7.8a) → Agent Catalog Governance (8.12/OE7) → Quality-Gated Publishing
Red Teaming (7.10) → Adversarial Test Generation (OE10) → Pre-Publish Robustness Verification
```
