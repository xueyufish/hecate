# Hecate Implementation Roadmap

> **Status**: Active — P1, P2 complete; **P3 complete (87/87)** — all four close-out items delivered (9.1a + 9.2 / 6.27 / 5.4b); P4, P5 in progress.
> **2026-08-22 release-scope reclassification**: P3 close-out items (5.4b / 9.1a / 9.2 / 6.27) → 9.1a + 9.2 delivered (change `output-side-typed-findings`), leaving **2 remaining** (5.4b MCP Streamable HTTP Server 端 / 6.27 Browser Automation). 48 items moved P3→P4 (see feature-catalog "Deferred from P3"); 11.4/11.5/11.9-Discord/Telegram moved P3→P5 (channel Wave 2/3); 11.8 Intent Recognition & Routing dropped (overlaps chat routing + multi-agent handoff + CONDITION intent routing). P4/P5 scope grows accordingly — no feature lost; one dropped.
> **Update (2026-08-22, later)**: 6.27 Browser Automation delivered (change `browser-automation`) — **P3 down to 1 remaining item: 5.4b**.
> **Update (2026-08-22, latest)**: **5.4b delivered (change `mcp-streamable-http`)** — MCP 栈升级到 2026-07-28 规范（fastmcp 4.0.0b3 + mcp SDK 2.0 + mcp-types 2.0；stateless `/mcp` 端点 + 客户端自动协议时代协商）。**P3 全部关闭：87/87。**
> **Scope**: 12-month implementation plan covering unimplemented features across P3–P5.
> **Basis**: Feature catalog (352 features, 162 done) + architecture compatibility assessment + competitive timeline benchmarks + 2026-06 deep competitive analysis + industry feature delivery timeline validation + Core vs Pluggable architecture framework (Platform SPI ABCs prioritized) + A2A Protocol Stack (MCP+A2A+AP2) convergence analysis + MCP/Skill Resource Management + Agentic RAG + Knowledge Graph (8 features) + Ontology Modeling (4 features) + Memory (11 features) + AIP Capabilities (29 features) + Access Channel (5 features) + Agent Studio enhancements (4 features + 5 enhancements) + Agent Engine enhancements (2 features + 4 enhancements) + Ops Center (9 new features + 6 enhancements) + Model Hub (3 new features + 5 enhancements) + Tool Platform (2 new features + 4 enhancements) + Knowledge & Memory (2 new features + 4 enhancements) + Enterprise Foundation (2 new features + 4 enhancements) + Security Shield (2 new features + 6 enhancements) + Ecosystem (2 new features + 4 enhancements) + Observability & Evaluation (2 new features + 8 enhancements)

---

## Current State

| Priority | Features | Done | Remaining |
|----------|----------|------|-----------|
| **P1 Usable** | 19 | 19/19 (100%) | 0 |
| **P2 Good** | 65 | 65/65 (100%) | 0 |
| **P3 Trustworthy** | 87 | 87/87 (100%) | **0** — closed |
| **P4 Intelligent** | 151 | 25/151 (16%) | 126 |
| **P5 Ecosystem** | 71 | 0/71 (0%) | 71 |
| **Total** | **392** | **195/392 (50%)** | **197** |

> Row counts are physical feature-catalog rows (2026-08-22 basis; verified by grep). Prior figures (127/101/60, total 372) used audit-counting that predated the reclassification — see feature-catalog overview note for the reconciliation.

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
| 11.2 | Web Widget (Simplified) ✅ *Wave 1 — iframe-embeddable for any Hecate deployment (see ADR-031)* | API ✅ | S |
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
| 7.2-abc | EvaluatorBase ✅ (refactor existing 40+ evaluators as BuiltinEvaluator) | 5.5a | M |

### Multi-Tenant & RBAC

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 10.1 | Organization Management ✅ | workspace_id ✅ | M |
| 10.2 | RBAC ✅ | 10.1 ✅ | M | 10.2 enhancement receives `is_platform_admin` role + `plugin:install:platform` fine-grained permission string (recorded in feature-catalog 5.5c "Deferred to 10.2 RBAC" note) |
| 10.5 | Tenant Isolation ✅ | 10.1 | M |
| 10.6 | Authentication Service (enhance) | JWT ✅ | S |
| 10.3 | SSO/LDAP | 10.6 | M |
| 10.4 | Quota Management | 10.1 | S |

### Security (Complete System)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 9.1 | Input Security ✅ | Guardrails ✅ | S |
| 9.1a | Output Injection Type Detection ✅ | Guardrails ✅ | M |
| 9.2 | System Prompt Leakage Protection ✅ | Output Security ✅ | S |
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

- [x] P3 progress ~43/133 (32%)
- [x] Organization Management + RBAC implemented (10.1, 10.2)
- [x] Tenant Isolation — data-level workspace scoping (10.5)
- [x] Resilience infrastructure: exception hierarchy ✅ + auto-retry ✅ + tool gating ✅
- [x] ContextEngine Phase 1: LLMWorker context pipeline operational ✅
- [ ] Multi-Tenant RBAC + SSO operational (SSO remaining)
- [x] Full security stack: input/output/execution/data
- [x] Full-Chain Tracing (8.1) + Real-Time Monitoring (8.2) — done
- [x] Cost Dashboard (8.3) — done
- [x] Plugin SPI Core (5.5a) + EvaluatorBase (7.2-abc) defined — pluggable foundation ready
- [ ] 40+ evaluators available for regression testing

---

## Sprint 5: P3 Enterprise (Month 9–10)

> **Goal**: P3 enterprise core — Platform SPI, Multi-Agent Protocol (A2A), Model Hub, Enterprise Identity. Define extension interfaces before building implementations.

### Platform SPI ABCs (NEW — Core vs Pluggable Framework)

> Define remaining extension interfaces so all downstream implementations are plugin-based, not hardwired.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 11.1-abc | ChannelBase ✅ (REST/WS/CLI as BuiltinChannel) | 5.5a ✅ (Sprint 4) | M |
| 10.3-abc | AuthProvider ✅ (JWT/APIKey as BuiltinAuthProvider) | 5.5a ✅ (Sprint 4) | M |
| 8.6-abc | NotifierABC 🔀 (merged into ChannelBase — notification dispatchers as outbound Channel adapters) | 5.5a ✅ (Sprint 4) | S |
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
| 10.3b | SCIM Directory Sync ✅ | AuthProvider ✅ | M |
| 10.7 | Budget Management & Cost Governance ✅ | Cost Dashboard ✅ | M |
| 10.8 | Enterprise Vault Integration ✅ | Auth ✅ | M |

### Milestone M5 (End of Sprint 5)

- [x] Platform SPI complete: ChannelBase ✅ + AuthProvider ✅ + i18n SPI ✅ defined. NotifierABC 🔀 merged into ChannelBase as NotificationChannelAdapter
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
| 5.14 | Environment Security (P0: 9.12 + 9.13 + 9.14 + 9.15) ✅ *(renamed from 5.9 — resolves ID collision with 5.9 Skill Loading)* | 5.6 ✅ + 1.3.15 ✅ + 9.4 ✅ | L |
| 9.12 | Environment Network Egress Control ✅ | 1.3.15a ✅ | M |
| 9.13 | Sandbox Enforcement Integration ✅ | 9.4 ✅ + 9.4c ✅ + 1.3.15a ✅ | S |
| 9.14 | Structured Security Audit Pipeline ✅ | 9.4 ✅ + 8.7 ✅ | M |
| 9.15 | Per-Execution Credential Scoping ✅ | 10.8 ✅ | S |
| 9.10 | Outbound DLP Engine ✅ | PII Masking ✅ | L |
| 9.11 | Agent Runtime Protection | Guardrail Hooks ✅ | L |
| 7.10 | Automated Continuous Red Teaming | Security Testing ✅ | L |
| 9.1a | Output Injection Type Detection ✅ | Guardrails ✅ | S |
| 9.2 | System Prompt Leakage Protection ✅ | Output Security ✅ | S |
| 8.7 | Security Event SIEM Pipeline ✅ | Audit Logs ✅ + 9.14 ✅ | M |
| 2.10b | Multi-Agent Trust Verification | A2A ✅ | M |
| 9.6 | Compliance Framework | Security ✅ | M |
| 9.8 | Full-Chain Network Security | Security ✅ | M |
| 7.7 | Security Testing | Evaluation ✅ | M |

### Plugin System & Tool Platform

> **Scope Boundary** (decided 2026-07-12, see explore notes):
>
> - **5.5** ✅ = Runtime engine: plugin.yaml loading, directory discovery, basic compat check, lifecycle, config, permissions, REST API + frontend, MCP endpoint UI. In-process + MCP hybrid (no daemon).
> - **TP5** = Type taxonomy + SDK: 8 plugin types by capability (Tool/Extension/Trigger/Model/Channel/Evaluator/Auth/Secret), hecate.plugin SDK, `python -m hecate.plugin.cli init` scaffold, hot-reload, full install-time compat validation, API-type plugin creation UI. Datasource + AgentStrategy deferred.
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
| ~~5.4b (upg)~~ | ~~MCP 2026-07-28 Spec Migration~~ ✅ delivered 2026-08-22 (change `mcp-streamable-http`) — MCP shipped its largest revision 2026-07-28: stateless core (initialize/session removed, `_meta` self-describing requests), mandatory Mcp-Method/Mcp-Name header routing, Multi Round-Trip Requests replacing held-open elicitation streams, cacheable list results (ttlMs), RFC 9207 auth, DCR→CIMD, Roots/Sampling/Logging deprecated (12-month window). fastmcp 4.0.0b3 + mcp SDK 2.0 + mcp-types 2.0；stateless `/mcp` endpoint + client 自动协议时代协商 | ~~5.4b client ✅~~ | — |
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
- [x] Outbound DLP Engine with 3-point scanning (PR #58)
- [ ] Agent Runtime Protection with 5 detector types
- [ ] Automated Continuous Red Teaming operational
- [x] Injection Type Detection for downstream systems *(delivered via change `output-side-typed-findings`)*
- [x] System Prompt Leakage Protection *(delivered via change `output-side-typed-findings`)*
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

- [x] **13.4a-7**: AgentStateStore hard removal — `services/state/store.py` deleted; `WorkflowExecutionService.state_store` parameter removed; dual-write deprecation tests retired; `checkpoints` table dropped via migration `c5d6e7f8a9b0`
- [ ] **C2 (1.3.19 follow-up)**: `checkpoints` 表硬删除 — **closed** by 13.4a-7 migration `c5d6e7f8a9b0`; PostgresCheckpointStore also deleted
- [ ] **A2 (1.3.19 follow-up)**: 双重记账统一 —— Conversation/Message 表与事件日志（execution-state-log）双写收敛；目标为日志单一事实源、Conversation/Message 成为派生投影（或明确双向一致性策略）——挂账项，无固定排期

---

## Sprint 7: P3 Complete (Month 13–14)

> **Goal (2026-08-22 release-scope reclassification)**: P3 closes with **2 close-out items** — 5.4b MCP Streamable HTTP Server 端 / 6.27 Browser Automation. 9.1a + 9.2 delivered via change `output-side-typed-findings` (regex recognizer registry + winnowing fingerprint; typed findings persist to `SecurityFindingModel` and emit `EventType.INJECTION_DETECTED` / `EventType.PROMPT_LEAKAGE_DETECTED`). All other remaining Sprint 7 scope moved to P4 (48 items — see feature-catalog "Deferred from P3") or P5 (channel Wave 2/3); 11.8 dropped. Sub-tables below retain delivery history (✅ rows) with → P4/P5 annotations on moved items.

### P3 Close-Out (the only remaining P3 work)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| ~~5.4b~~ | ~~MCP Streamable HTTP Transport — **Server 端**~~ ✅ delivered 2026-08-22 (change `mcp-streamable-http`) — fastmcp 4.0.0b3 + mcp SDK 2.0 + mcp-types 2.0；stateless `/mcp` 端点（POST/GET，无状态核心、header 路由、4 MiB limit）；client 升级 `mcp.Client(mode='auto')` 自动协议时代协商；73 MCP 测试绿；顺手修复 pre-existing `/mcp` mount bug | 5.4c ✅ | — |
| ~~9.1a~~ | ~~Injection Type Detection~~ ✅ delivered 2026-08-22 (change `output-side-typed-findings`) | Guardrails ✅ | — |
| ~~9.2~~ | ~~System Prompt Leakage Protection~~ ✅ delivered 2026-08-22 (change `output-side-typed-findings`) | Output Security ✅ | — |
| ~~6.27~~ | ~~Browser Automation Tool~~ ✅ delivered 2026-08-22 (change `browser-automation`) — 6 builtin tools `browser_navigate/click/type/extract/screenshot/fill_form`; headless v1; dedicated `hecate-browser-sandbox` image; per-agent-session `BrowserSessionManager`; NetworkPolicy fail-closed + HIGH upgrade off allow-list; DLP on extract/screenshot | 5.1 ✅ + 9.4c ✅ + 9.4 内容门控 ✅ | — |

### Engine Architecture: Event-Sourced State (NEW, Q4=A decision)

> **Ordering**: 1.3.19 must land FIRST — 8.20 Execution Replay, HITL durable audit pairs, and middleware waterfall events all consume the enriched event log. 1.3.18 / 6.27 / 5.9-enh are parallelizable.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.19 | Event-Sourced Execution State (Log-as-Truth) ✅ *(see [ADR-030](../design/adr/030-event-sourced-execution-state.md))* — EventStore from observation log to state carrier: "model-visible ⟺ logged" runtime invariant, derive_messages() projection for model context, checkpoint = log-replay fold (snapshots demoted to materialized caches), incremental delta storage (O(N²)→near-linear; note: deer-flow's DeltaChannel is the reference but sits UNRELEASED in its 2.1.0 milestone; OMA v1.15.0 durable-approval checkpoint schema v4 is the shipped production reference). Include a dsh-invariants-style runtime relational invariant layer (openTurn/openStep/pendingCalls, frozen result snapshots, dispatch-tree consistency) | EventStore ✅ + CheckpointStore ✅ | L |
| 5.9 | Skill Provider Registry (enhancement) — provider registry (source origins: project/user/bundled/custom) + rank precedence (lower wins) + kebab-case name grammar + model/user invocation policy separation; replaces plain directory scan | 5.9 Skill Loading ✅ | M |

### Competitive Gap Features (NEW — competitor analysis)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.18 | Dynamic Orchestration ✅ *(see [ADR-032](../design/adr/032-dynamic-orchestration.md) — per deep-dive survey: Magentic-One 论文 / OMA v1.14 / DeerFlow subagent 契约 / Deep Agents interpreters / AgentScope / dsh)* — coordinator node: goal + agent roster → runtime task DAG → dispatch workers → synthesize result. 7th multi-agent pattern alongside 6 static ones; coordinator is a special node type emitting sub-graphs on Pregel at runtime. 范式 = data-as-plan（LLM 出建议性 TaskDAG，executor 确定性物化）。**Phase 1（已交付）**: COORDINATOR NodeType + DYNAMIC 枚举、TaskDAG 契约（fail-closed 前置校验 + `validate_task_requirements`）、executor 物化子图（独立 child session）、Magentic 双循环图化（stall ≤2、append-only 计划修订、replan-with-carryover）、三轴预算（max_iterations=5 / stall_limit=2 / max_total_tasks=6 / max_concurrent=3 + token 预算，additive stop_reason + capped 结果可见指导）、benefit-based 委派 rubric（prompt + 测试钉死）、五重隔离断言、可选 per-task verifier + ORCHESTRATOR_DECISION/EVALUATION 事件、synthesis 确定性 transform、planner/evaluator 模型分离。**Phase 2 = 1.3.18a（P4）**: consensus proposer→judge、append-only PlanPatch repair API、异步编排 + 中途 steering、plan 冻结 artifact + 精确重放（与 8.20 配对）。**UI companion（P3，Phase 1 后 follow-up change，无新 ID）**: pattern-selector 第 7 模式 / canvas COORDINATOR 节点 / 8.20 回放 coordinator 卡片 | 2.7a ✅ + Pregel ✅ + 1.3.19 ✅ | M |
| 8.20 | Execution Replay & Debug Dashboard (Phase 1: timeline replay) — session → trace-partitioned timeline (superstep × channel changes × tool calls × LLM request/response × guardrail blocks) + DAG step-through + time-travel (fold-to-version + `derive_messages`); web UI on EventStore + OTel. **Vocabulary**: `session`（多轮容器）→ `trace`（一次执行，回放锚点）→ `event`（记录）；不再用 "runId"。**回放覆盖范围 = Pregel 路径**（path A/C 不在日志内，UI 横幅标注；空日志会话不渲染回放 tab）。 Phase 2 (version binding) deferred to P5 | 1.3.19 (enriched log) | M |
| 6.27 ✅ | Browser Automation Tool *(delivered 2026-08-22, change `browser-automation`)* — Playwright builtin: navigate/click/type/screenshot/extract/fill_form; headless v1; sandboxed via dedicated `hecate-browser-sandbox` image; per-agent-session lifecycle; NetworkPolicy fail-closed egress. Computer-use half split to 6.27a (stays P4) | 5.1 ✅ + 9.4c ✅ | M |

### Completed-Feature Upgrades (NEW — research)

> Upgrades to already-shipped features whose architecture the industry has moved past (dsh source analysis + 18-platform competitor survey). All sequence after 1.3.19 (they consume the enriched event log); spill into early Sprint 8 is acceptable if capacity is tight. No new feature IDs — recorded as planned enhancements on 1.3.5i (E3), 1.3.4, 9.4 in the catalog.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.5i (E3) | GuardrailHooks → Ordered Waterfall Middleware Chain — 4 flat hooks become stages in an ordered `next()`-delegation chain (agent/pre-step, agent/request, tools/pre-execute / execute / post-execute / result), scope-filtered per agent; HITL clarification becomes one stage (deer-flow ClarificationMiddleware pattern) | 1.3.19 + GuardrailHooks ✅ | M |
| 1.3.4 | HITL Fail-Closed Approval — no answerer → deny; only allowed-once grants; ask/never policy state machine; approval/asked + approval/decided durable audit pair (turn-enclosed) | 1.3.4 ✅ + 1.3.19 (audit events) | M |
| 9.4 | Content-Aware Tool Gating — bash pipeline static analysis beyond risk_level + monotonic denial invariant (guards can only deny, never resurrect) | 9.4 ✅ + 1.3.19 | M |
| _All three above_ | **已交付 ✅**（PR #86 + #87；`openspec/changes/archive/2026-08-21-guardrail-upgrade-trio/` — T0 事件地基 + 生产接线、T1 瀑布链、T2 fail-closed 审批、T3 内容门控全部落地） | — | — |

### Plugin Ecosystem: Open-Standard Ingestion (NEW — adjustment)

> **Rationale**: Agent Plugins 1.0 (open standard, 2026-08-06, backed by OpenAI/Microsoft/Google/Amazon/Cursor/GitHub) has converged the industry on "plugin = declarative package of Skills + MCP config"; SKILL.md layer already landed in Bedrock AgentCore/watsonx/Microsoft Agent Framework/Salesforce. Hecate adopts it as the ecosystem-facing third-party format; `.hecate-plugin` narrows to P-tier deep-integration (catalog notes on 5.5/5.5b). **Ordering**: independent of 1.3.19 — parallelizable with any Sprint 7 work item; 5.5c → 5.13a strict serial (scanning gates the ingest pipeline's go-live).

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 5.5c | Agent Plugins 1.0 Standard Ingestion — directory/git-URL/zip install, closed-manifest validation, fixed-location discovery (skills/ + mcp.json) with skip-and-continue + path containment, SKILL.md→SkillModel import (source/origin + pin-by-hash), mcp.json→MCPServerRegistry, **component-level trust dispatch** (skills→T4 & http-MCP→T2 = workspace admin; stdio = org admin only, SaaS default-deny via config gate; self-hosted stdio runs in 9.4c container sandbox — decided), bare-SKILL-md-directory acceptance ✅ (`openspec/changes/archive/2026-08-18-agent-plugins-ingestion/`): single adapter module `plugin/agent_plugins.py` + `plugin/stdio_sandbox.py`; `PluginModel` rows carry `origin`/`content_hash`/`scan_result`; `SkillModel` rows carry `source="plugin"` + `origin` + `plugin_id` FK; SkillLoader filters by enabling plugin; MCP projection uses `<plugin>__<server>` prefix; stdio via docker wrapper into 9.4c container; `AGENT_PLUGINS_INGESTION_ENABLED` default-off + `PLATFORM_PLUGIN_INSTALLERS` config allowlist (5.10.2 RBAC enhancement receives `is_platform_admin` role + `plugin:install:platform` permission string) | 5.5 ✅ + 5.4c ✅ + 5.9 ✅ | L |
| 5.13a | Plugin Content Scanning (split from 5.13, pulled to P3) — prompt-injection detection (incl. invisible-Unicode), secret detection, allowed-tools audit; fail-closed install (block/warn/allow, org threshold), rescan on enable, results API + Ops Center display. V1 = pure rule engine; LLM second-pass review optional in v2 (decided) — **strictly serial after 5.5c; 5.5c ships with a no-op scan stage reserving the slot, 5.13a keeps the go-live gate**. Design refined (17+-platform research: DeerFlow SkillScan / Hermes-agent / Dify / Google GE Governing Agent Skills / FortiCNAPP SK-* / IBM script deny-list converge on deterministic-first + CRITICAL fail-closed + warn ack + optional LLM second pass; Bedrock AgentCore & AgentArts/openJiuwen (same vendor) & AgentScope & CatPaw substitute sandbox/permissions — refs in feature-catalog 5.13a row): obfuscation layer v1 = NFKC + bounded base64/hex rescan, file-role × rule severity matrix, verdict = line vs `AGENT_PLUGIN_SCAN_BLOCK_AT`, oversize text → finding (22MB-padding lesson), ack suppression keyed (content_hash, rule_id), URL rules = paste-site/IP-literal/punycode ✅ (`openspec/changes/archive/2026-08-18-plugin-content-scanning/`): `plugin/content_scanner.py` + install/enable fail-closed wiring + SecurityFindingModel projection/ack + `GET /api/plugins/{id}/scan`; master switch default flipped on | 5.5c ✅ | M |
| 5.5 (enh) ✅ | T0 Tightening — loader rejects runtime-installed non-first-party `python:` entries (SaaS reject; self-hosted default-deny + `PLUGIN_PYTHON_ENTRY_ALLOWLIST` allowlist, segment-boundary prefix match); install-time pre-check + directory rollback on rejection; SaaS skips runtime `uv pip install` — operationalizes ADR-029 "runtime artifacts never T0"; near-zero cost while installed-code-plugin base is ~empty (`openspec/changes/archive/2026-08-19-t0-runtime-plugin-tightening/`) | 5.5 ✅ | S |

### Advanced RAG & Knowledge (rescoped) — → P4 (2026-08-22)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 3.2.4 | Reranking → P4 | Vector Search ✅ | M |
| 3.3.2 | Incremental Update → P4 | RAG ✅ | M |
| 3.3.3 | Knowledge Quality Evaluation → P4 | Ragas | S |

> **Deferred to P5**: 3.1.2-3.1.4 (OCR / Table / Layout — integrate Docling/Unstructured instead of building), 3.1.8 (Extended Document Processing, from P4), 3.5.1-3.5.3 (Knowledge Graph suite — integrate GraphRAG/LlamaIndex), 3.5.5 (Knowledge Graph API, from P4), 3.4.2 (High-Throughput Retrieval — Qdrant deployment guide covers it). See Sprint 10 deferred table for triggers.

### Multi-Channel Expansion

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 11.2 | Web Widget (Simplified) ✅ *Wave 1 — internal Portal / embeddable for any Hecate deployment* | API ✅ | S (simplified) |
| 11.3 | Feishu (Lark) ✅ *Wave 1 — China market anchor + first ChannelBase reference impl* | Channel SDK | M |
| 11.8 | ~~Intent Recognition & Routing~~ **Dropped (2026-08-22)** — overlaps chat routing (`agent/<id>`) + multi-agent handoff + CONDITION intent routing (RoutingMode.INTENT) | — | — |
| 11.9 | Slack ✅ *Wave 1*；Discord/Telegram → P5 (Wave 2) | ChannelBase ✅ | M (Slack) |

**Deferred to P5 (按需触发, 不在当前 P3 主线)**:
- 11.2 (full) — Web Widget 完整版（匿名 to-C 场景），trigger = 第一个公开网站/营销/客服场景的客户
- 11.6 — WeChat Official Account / Mini Program（to-C niche），trigger = 第一个 to-C 客户
- 11.10 — Custom Channel SDK（长尾 niche），trigger = 社区/客户明确请求

### 11.x Wave 节奏

| Wave | Feature | 触发条件 | 时机 |
|---|---|---|---|
| Wave 1 | 11.2 简化版 + 11.3 飞书 + 11.9 Slack | 主动 | 全部交付 |
| Wave 2 (P5 deferred) | 11.4 企微 + 11.5 钉钉 + 11.9 Discord/Telegram | 按客户需求触发 (wechat wecom / dingtalk / discord / telegram 客户需求) | 暂停 |
| Wave 3（按需） | 11.2 完整版 + 11.6 微信 + 11.10 Custom SDK | 不预定时间，等明确客户需求 | 不预定 |
| 11.16 | Per-Token-Type Auth Pipeline | Auth ✅ | M |
| 11.17 | Two-Tier Identity Model | Auth ✅ + RBAC ✅ | M |

### Evaluation Suite — → P4 (2026-08-22)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 7.2a | 40+ Built-in Evaluators ✅（OE8/OE9 增强 → P4） | Evaluation ✅ | L |
| 7.2b | AI-Synthesized Evaluation Dataset ✅（2026-09-10） | 7.2a ✅ | M |
| 7.2c | Online/Offline Evaluation Tasks ✅（2026-09-11） | 7.2a ✅ | M |
| 7.2d | Trace Backflow Dataset ✅（2026-09-12） — backflow 规则一等对象（online task × dataset × 分数带 filter AND + `limit`/`max_turns`）+ 显式批量 run API（周期编排交给 scheduled-tasks）+ 跨路径幂等（与 7.4 标注路径共享 `trace_dedup`）+ 多轮 `conversation_history` 随 item 落库（承接 7.2c 延后项）+ 机分溯源 `metadata_.backflow`；archive `2026-09-12-automated-trace-backflow` | 7.2a ✅ / 7.2c ✅ / 7.4 ✅ | S |
| 7.2e | Evaluation Report Dashboard ✅（2026-09-12） — `GET /api/evaluation/reports/{overview,trends,distributions,breakdowns,sessions}` 按需聚合（零 schema 变更）+ ops-center `/evaluation` **六视图**（Overview 四卡含低样本 run 占比 / Online 采样预算 + agent×metric + session 下钻 + "加入标注队列"入口 / Run 报告直方图 + 低分 reasoning / Compare 渲染 7.3 runs/compare 配对 delta + drift / **Annotation** 队列列表 + 工作台 / **Calibration** 指标卡 + 10×10 热图）+ 侧边栏入口；session rollup 视图承接 7.2c 延后项（session 级打分归 7.2d）；overview/trends/distributions/session-rollup 接 7.4a 协调口径（最新人评 override 胜出），breakdowns 保留 raw | 7.2a ✅ / 7.2c ✅ / 7.3 ✅ / 7.4 ✅ | M |
| 7.3 | Workflow Evaluation ✅（2026-09-11） — workflow answer_source + repetitions + dataset snapshot + diff API + cost guardrail + publish report + CLI 三态；follow-ups 7.3c ✅（2026-09-13）/ 7.3a + 7.3b ✅（2026-09-13，archive `2026-09-13-eval-publish-gate-versions`）+ 7.2e 承接 UI | 7.1 ✅ / 7.2a ✅ / 7.2c ✅ | M |
| 7.4 ✅ (2026-09-12) | Human Annotation — `annotation_queues` + `annotation_queue_items`（pending→claimed→completed/skipped，唯一 per target），manual bulk + from-task low-score intake，metric defs (numeric/categorical/boolean)，suggestion-prefilled workbench；人评行 `source="human"`、`task_id=NULL` 落入 `evaluation_task_scores` 与机分共存；完成 item 一键物化 dataset（`tags="human-annotation"` + 队列名，幂等）；Annotation 视图 + Calibration 视图（Kappa / MAE / 10×10 热图） | 7.1 / 7.2 / 7.2a ✅ / 7.2c ✅ / 7.2e ✅ / 7.3 ✅ | M×1 + S×1 |底座 ✅; 已交付 (archive `2026-09-12-human-annotation-trace-backflow`)。`source` 字段 docstring 早已预留 human，partial unique index 让人评行豁免 idempotency；同标注者同 (target, metric) 自动 upsert。报表 reconciliation 已接入 overview/trends/distributions/session-rollup，breakdowns 保留 raw。Sibling items（7.2d 自动化回流 / 7.3a/b/c / 7.2f）仍 pending |
| 7.5 | A/B Testing (rescoped to Agent-Level) → P4 — reuses 6.8a traffic-splitting + z-test machinery | 6.8a ✅ | S |

> **Dropped**: 7.6a Prompt Auto-Optimization, 7.6b Prompt Comparison — specialized frameworks (DSPy, IBM AgentOps GEPA) and evaluation platforms (LangSmith, Salesforce A/B Testing API) have standardized this; self-building is negative ROI.

> **TBD — Quality Regression Detection (G4 remainder)**: Once the Evaluation Suite produces per-model quality scores, add quality regression monitoring to the Model Monitoring Dashboard (O10+G4). Compare current-period quality scores against historical baseline; trigger alert when degradation exceeds threshold. Originated from `model-hub-completion` change where drift detection was shipped but quality regression was deferred pending evaluation data.

### Canvas Enhancements — → P4 (2026-08-22)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.1.24 | Human Input / Form Node → P4 | interrupt() ✅ + Canvas ✅ | M |
| 1.1.25 | Trigger Node → P4 | Scheduled Tasks ✅ + Webhook ✅ | M |

> **Deferred to P5**: 1.1.18 Agent-Workflow Canvas Embedding, 1.1.19 Unified Skill Selector, 1.1.20 Nested Graph Visualization — Canvas enhancements without user feedback are speculative; Dify's collaborative editing (Loro CRDT) is the direction to aim for when triggered.

### Memory Enhancement — → P4 (2026-08-22)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 4.3a | Memory Engine Enhancement → P4 | Memory System ✅ | L |
| 4.14 | Memory Importance Scoring → P4 | Memory System ✅ | M |
| 4.15 | Multi-Signal Fusion Retrieval → P4 | 4.14 → P4 | M |
| 4.17 | Memory Pressure Alert → P4 | Token Budget ✅ | S |
| 4.25 | Layered Memory System → P4 | 1.3.15 ✅ | M |

### AIP Capabilities (P3 Foundation) — → P4 (2026-08-22)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 6.16 | NL2Agent / NL2Flow → P4 | Canvas ✅ + Graph DSL ✅ | M |
| 6.18 | Trace Annotation → P4 | EventStore ✅ + Audit ✅ | S |

> **Dropped**: 6.17 DSL Conversion Framework — MCP/A2A protocol standardization plus Salesforce open-sourcing Agent Script indicates the industry is converging on standard agent definitions, not DSL compatibility layers.
> **Deferred to P5**: 6.21 Decision Lineage — full decision lineage requires an Ontology foundation (data + function + app version binding per trace, Palantir standard); effort was underestimated in the initial analysis.

### Milestone M7 (End of Sprint 7)

> **2026-08-22 reclassification**: M7 = P3 release close-out. Items marked → P4/P5 above are excluded from M7; see feature-catalog "Deferred from P3" for the go-forward home of each.

- [x] Event-sourced execution state: log-as-truth invariant + derive_messages projection (1.3.19, ADR-030)
- [x] Dynamic Orchestration — 7th multi-agent pattern (1.3.18, ADR-032)
- [x] Execution Replay Phase 1 — timeline replay (8.20)
- [x] Guardrail upgrade trio: waterfall middleware + HITL fail-closed + content-aware gating (PR #86/#87)
- [x] Plugin ecosystem: Agent Plugins 1.0 ingestion (5.5c) + Content Scanning (5.13a) + T0 Tightening — go-live (master switch on)
- [x] Multi-Channel Wave 1: 11.2 simplified + 11.3 Feishu + 11.9 Slack
- [x] Release hardening: migration drift gate + E2E suite (F3/F4/F7/A7) + dead-code sweep + 13.4a-7/C2/A2 closures (PR #88/#89, net −2159 lines)
- [x] **5.4b MCP Streamable HTTP Server 端**（按 2026-07-28 规范）— change `mcp-streamable-http`：fastmcp 4.0.0b3 + mcp 2.0.0 + mcp-types 2.0.0；server stateless `/mcp` endpoint；client 升级到 `mcp.Client(mode='auto')`；73 MCP 测试绿；pre-existing `/mcp` mount bug 顺手修了
- [x] **9.1a Injection Type Detection** *(delivered)*
- [x] **9.2 System Prompt Leakage Protection** *(delivered)*
- [x] **6.27 Browser Automation Tool** *(delivered — 6 builtin tools `browser_navigate/click/type/extract/screenshot/fill_form`, headless v1, sandboxed via dedicated `hecate-browser-sandbox` Docker image; per-agent-session lifecycle via `BrowserSessionManager`; NetworkPolicy fail-closed default + HIGH upgrade for non-allow-listed domains; DLP scan on extract text + screenshot; Computer-use split to 6.27a P4)*

---

---

## Sprint 8: P4 Kickoff — Intelligence (Month 15–16)

> **Goal**: P4 intelligence features — Self-Learning, Agentic AI (RL, Prompt Optimization, Ontology Actions, OAG), Memory Intelligence. Make agents genuinely smart.
>
> **2026-08-22 reclassification**: Sprint 8 scope also absorbs the 48 items deferred from P3 (see feature-catalog → P4 → "Deferred from P3"): Evaluation Suite (7.2b-e/7.3/7.4/7.4a/7.5 + 8.10/8.12), Security remainder (9.5a/9.11/7.10/2.10b/7.7), Deployment & Ops (13.1/13.1a/13.1b/13.4/13.4b/13.17/13.18), Advanced KB (3.2.4/3.3.2/3.3.3/3.4.1), Canvas nodes (1.1.24/1.1.25), Memory (4.3a/4.14/4.15/4.16/4.17/4.25/4.21), AIP (6.16/6.18), Auth (11.16/11.17), 5.4a/5.8/5.9-enh, and 8 shipped-feature enhancements. Sequencing within Sprint 8 is open — no forced ordering inherited from P3.
>
> **2026-09-04 reorder (this change)**: Sprint 8 now opens with an **Opening Queue** of 4 items — all底座 ✅, all directly shippable or with shallow blockers — followed by the original three blocks as **Absorption Pool** (原 Sprint 8 块级，按原章节迁移；可在 Opening Queue 进展后择机启动), plus two appended blocks: Plugin Ecosystem (5.5d) and **Model Management (6.47/6.48, added 2026-09-06 from the AgentArts comparison — publish lifecycle pairs with 1.3.20's 提交/发布 semantics)**. The original chapter ordering was shaped by an earlier vision (deep intelligence first); it front-loaded two L-grade items (6.20 Ontology Action System, 6.22 OAG) that depend on the P5-deferred Knowledge Graph integration. Surfacing the Opening Queue first fixes a structural defect: 6.20 / 6.22 cannot close inside Sprint 8 in their current form (closure condition = P5 KG integration trigger). See change `openspec/changes/roadmap-p4-reorder/` for full rationale.
>
> **2026-09-07 addition (this change)**: an **Engine Parity** block (1.3.21, from the deer-flow/LangGraph engine comparison) is appended after Model Management. Sequenced inside Sprint 8 — not Sprint 9 — because Sprint 9 consumers (6.26 E5 what-if branching, 8.20 executable replay, 11.18 debug stream modes, 5.11 parallel research) sit on its outputs; internal order ①declarative interrupts → ②time-travel resume → ③dynamic fan-out.

### Sprint 8 Opening Queue

Order is priority, not mandate — any change still goes through independent `/opsx:propose`. The 4 items are the closest-to-market, lowest-blocker subset of Sprint 8 scope, identified by re-grepping the catalog against AgentArts capability gaps (see `docs/research/2026-09-agentarts-product-comparison.md`).

| # | Feature | Dependencies | Effort | Why first |
|---|---------|------|--------|-----------|
| 5.4a ✅ | MCP Gateway — REST/MCP multi-source tools unified to single endpoint, agent/workspace-scoped authz | FastMCP server ✅ + tool registry ✅ | M |底座 ✅; AgentArts 把网关做成组件库独立卡 ("重磅上新") — 市场验证独立产品价值。已交付（`GATEWAY_ENABLED` flag 门控，见 feature-catalog 5.4a 行） |
| 7.2b ✅ (2026-09-10) | AI-Synthesized Evaluation Dataset — generation / evolution / adversarial strategies, sync/async job lifecycle, dedupe+quality+DLP filter pipeline, dataset `tags` provenance | 7.1 / 7.2 / 7.2a ✅ | M |底座 ✅; 已交付 (archive `2026-09-10-ai-synthesized-evaluation-dataset`)。Sibling items (7.2d/7.2e/7.3/7.4/7.4a) remain on Opening Queue |
| 7.2c ✅ (2026-09-11) | Online/Offline Evaluation Tasks — `EvaluationTask` 一等任务定义（offline/online），离线异步 run（202 + job 生命周期）+ `answer_source=agent` 被测 agent 调用 + threshold/baseline summary；在线任务 = traces 表常驻消费者（确定性采样 + ingest-then-score），target 类型化 `evaluation_task_scores`，runtime 请求路径零改动 | 7.2a ✅ / 7.2b ✅ | M |底座 ✅; 已交付 (`EVALUATION_ONLINE_SCORING_ENABLED` flag 默认关, archive `2026-09-11-online-offline-evaluation-tasks`)。7.2c 延后项（session 级多轮评估）已记入 7.2d/7.2e 条目。Sibling items (7.2d/7.2e/7.4/7.4a) remain on Opening Queue |
| 7.3a / 7.3b / 7.3c | Evaluation Suite remainder（7.3 follow-ups）— **7.3c ✅（2026-09-13，archive `2026-09-13-known-bad-exemption`）**：item 级 `known_bad` + 必填 reason + 服务端 provenance（`marked_by`/`marked_at`，预留 `expires_at`）、仍执行仍记分但聚合分子分母排除（pass_rate / consistency_rate / metric_averages / is_regression）、summary 暴露 `exempted_items` + 自愈信号 `known_bad_passed_item_ids`（只报警不自动解除）、豁免冻结进快照且不参与 content hash（标记不触发 dataset_drift）、PATCH 标记 API + import/export round-trip；业界无先例（Langfuse ARCHIVED / promptfoo weight:0 为最近原语，调研沉淀 `docs/research/2026-09-agent-eval-practices-survey.md`）。**7.3a + 7.3b ✅（2026-09-13，archive `2026-09-13-eval-publish-gate-versions`）**：publish 评估门禁（三态 off/warn/require，确定性-only 重算口径，409 EVALUATION_GATE_BLOCKED + force 审计）+ 命名 dataset 版本（与 run 快照同一 hash 实现点、checkout/diff、run 版本绑定、`require_dataset_version` 组合闭环）；env binding 留待门禁真实使用反馈 | 7.1 / 7.2 / 7.2a ✅ / 7.2c ✅ / 7.2d ✅ / 7.2e ✅ / 7.3 ✅ / 7.4 ✅ | M + S |底座 ✅; AgentArts 评估页是产品完成度最高的面, 报告仪表盘已由 7.2e 补齐, 标注已由 7.4 补齐, 回流已由 7.2d 补齐, 豁免已由 7.3c 补齐; 调研结论（2026-09 竞品对比）已沉淀于各自 change 的 design |
| 2.6a + 1.1.21 + 1.3.10⊕6.23 (+6.49) | Multi-Agent Controller Family — central controller (2.6a), controller canvas (1.1.21), 5-Level Intent Recognition (6.23 merges 1.3.10), Intent Package Asset (6.49 — intent categories + sample utterances as few-shot evidence for the recognition engine) | 2.6 (same Sprint) + 2.7c intent routing ✅ | M×3 + M | AgentArts 控制器 = 子智能体/子工作流组合调度; 一族三编号跨 Sprint 8/9 收口为一个 change; 6.49 为 6.23 的配套数据面 |
| 1.3.20 | Agent Versioning & Channel Publishing — agent-level versioning + channel binding (API/embed/feishu/slack/webhook 锁定版本快照); carries Resource Versioning (14.x) mechanism referenced by 5.9d / 3.5.12 / 7.5 | 1.1.9 ✅ + channels ✅ + 1.3.15 ✅ | M | 唯一规划外空白; AgentArts 把版本+渠道做成三种形态统一生命周期步骤 |

### Absorption Pool (formerly Sprint 8 main body)

> Items below are unchanged in scope; they were originally the visible chapter order. They remain Sprint 8 candidates and may launch after Opening Queue progress.

#### Self-Learning & Evolution

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.5e | Hallucination Detection & Mitigation | PostLLMHook ✅ + ContextEngine | L |
| 1.3.6 | Self-Learning Agent Runtime | 1.3.6a–d ✅ | M |
| 1.3.6e | Self-Evolution Closed Loop | 1.3.6 ✅ | S |

#### Agentic AI (Moved from P3)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 6.15 | Agentic RL Framework | Evaluation ✅ + LLM ✅ | L |
| 6.19 | Prompt Self-Optimization | Evaluation ✅ + LLM ✅ | M |
| 6.20 | Ontology Action System | Knowledge Graph (P5 deferred — rebase on GraphRAG/LlamaIndex integration when triggered) | L |
| 6.22 | OAG (Ontology-Augmented Generation) | 6.20 + RAG ✅ *(blocked by 6.20's P5 KG dependency)* | L |

#### Memory Intelligence

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 4.5 | Sleep-time Memory Consolidation | Memory System ✅ | M |
| 4.13 | Context Engine Processor Chain (+ LLM-managed compaction via surface replacement — compaction events as checkpoint sources, per research) | ContextEngine ✅ + 1.3.19 | M |
| 4.16 | LLM-Managed Memory | ContextEngine ✅ | M |
| 4.18 | Conversation Recall Storage | Memory System ✅ | M |
| 4.19 | Self-Editing Memory | Memory System ✅ | M |
| 4.20 | Multi-Step Memory Retrieval | Memory System ✅ | M |
| 4.21 | Task Memory | Memory System ✅ | M |
| 4.22 | Tool Memory | Memory System ✅ | M |
| 4.23 | Cross-Thread Memory Store | Memory System ✅ | M |

### Plugin Ecosystem (NEW — adjustment)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 5.5d | Dual-Format Plugin Convergence & Export — Hecate-private plugin content migrates into `io.hecate/` namespace dir inside Agent Plugins packages (one package = conformant for all clients + deep-integration for Hecate); `hecate plugin export` packages workspace skills as Agent Plugins bundles; ZIP demoted to transport-only (directory/git-URL install) | 5.5c (P3) + 5.5b ✅ | M |

### Model Management (NEW — AgentArts comparison pull-forward)

> Publish lifecycle semantics pair with 1.3.20 (Agent Versioning) in the Opening Queue — same 提交/发布 pattern, different surface (model services vs agents). 6.48 is S-grade and may ride along with any Opening Queue change.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 6.47 | Model Service Publishing — wire 6.45 ✅ staging/promotion machinery into settings/models: publish state (draft → testing → published) on `model_registry`; unpublished models hidden from application reference surface (`/v1/models` → Create Agent dropdown) but still testable inline; publish button + status badge + filter | 6.45 ✅ | S |
| 6.48 | Model Management Quick Wins — list-level search/filter (providers + models) + provider call-count wiring from existing traces/costs aggregates; pure frontend + one aggregation query | traces/costs ✅ | S |

### Engine Parity (NEW — 2026-09-07 deer-flow/LangGraph engine comparison)

> Source: engine-level diff of Hecate's Pregel vs LangGraph (deer-flow's inherited engine), recorded as catalog row **1.3.21**. deer-flow itself uses almost none of these (v1 research loop = sequential `Command(goto)` cycles, zero `Send()`; v2 retreats to single-agent + middleware) — the gaps are vs the **engine**, not the app. Also carries Hecate's 5 confirmed leads (log-as-truth, self-hosted replay, tenant/governance plane, pluggable conflict resolution, coordinator isolation) — no parity anxiety; this is targeted catch-up on 3 items.
>
> **Why Sprint 8** (not Sprint 9): Sprint 9 consumers depend on it — 6.26 E5 what-if checkpoint branching needs ②; 8.20's upgrade from read-only replay toward executable time-travel needs ②; 11.18 debug/tasks stream modes and 5.11 Deep Research parallel collection sit on ③'s fan-out machinery. **Order ①→②→③**: smallest first; ② reuses the existing 1.3.19 `fold_session` machinery (only the execute-path exposure is new); ③ is the largest and benefits from ①'s test breakpoints and ②'s checkpoint anchoring.

| Order | Item | Dependencies | Effort | Unblocks |
|---|---|---|---|---|
| ① first ✅ | 1.3.21① Declarative interrupts — compile-time `interrupt_before`/`interrupt_after` node lists + `remaining_steps` signal in execution_context (graceful degradation before MaxSuperstepsError) | Command/interrupt ✅ | S | HITL plan-review flows without worker-authored interrupts; G7 inspector breakpoints. 已交付(2026-09-08,change `openspec/changes/archive/2026-09-08-declarative-interrupts/`):整步暂停 + 日志推导 phase-aware resume + WAL 提交序修复 + session status 接线,HITL 端到端可达;细节见 feature-catalog 1.3.21 行。② 的 checkpoint 锚点已就位(phase 描述符即 ② 要折叠的形状) |
| ② second ✅ | 1.3.21② Time-travel resume + update_state — 已交付(2026-09-09,change `openspec/changes/archive/2026-09-09-time-travel-resume/`):锚点为日志提交点(刻意偏离本表原文的 `CheckpointStore.load(checkpoint_id)`——生产 store 无历史,ADR-030 缓存可弃);`GET /commit-points` + `POST /fork`(子会话 FORK 快照引导,父日志零侵入)+ `POST /state`(追加记录式修改);引擎 `resume_from` tail-only 守卫;顺带修复 initial_input 从未入 WAL 的投影等价空转缺陷(D11)。细节见 feature-catalog 1.3.21 行 | 1.3.19 ✅ | M | 6.26 E5 what-if branching; 8.20 executable replay; HITL re-plan flows |
| ③ third ✅ | 1.3.21③ Send-style dynamic fan-out — 已交付(2026-09-09,change `openspec/changes/archive/2026-09-09-dynamic-fanout/`):CONDITION + `fanout` DSL 块(`over` 通道逐元素生成 `_dispatch` 包,`target`/`state_key`/`max_fanout` 编译期校验);引擎调度单元升级为 `Invocation`(同子节点同 superstep 并行 N 次,语义去重被刻意保留);`_dispatch` 受日志控制通道 + STEP_END `fanout` 段(折叠重建子通道)闭合 T2b 不变式(`FORK` 载荷保真,缓存缺失 log-only 恢复后 MERGE 正确);`continuation.py` `_dispatch` 采纳规则带 live-planner 守卫;分层扇出上限(节点 64/superstep 256,绝对 1024)fail-closed;per-branch 容错粒度(`on_branch_error: fail_fast | collect`);`_execute_merge` 动态分支聚合(按 `branch_index`)+ 静态路径回归;可插拔 ACCUMULATOR reducer(模块级 `_REDUCERS` 注册表 + 内建 `add`/`append` + 未知名编译期报错);①+②+③ 闭环——planner ∈ `interrupt_after` 实现 HITL 计划评审,target ∈ `interrupt_before` 整批预中断。`on_branch_error` collect 模式 + planner 的 `_dispatch` 写入属 executor `AgentExecute` 依赖项。细节见 feature-catalog 1.3.21 行 | FAN_OUT/MERGE ✅ + 13.10 ConflictResolver ✅ | M/L | 5.11 Deep Research parallel collection; map-reduce aggregation patterns; what-if fork across fanout window (T2b closure) |
| ride-along ✅ | 1.3.21 sub-items — 已交付(2026-09-09,change `openspec/changes/archive/2026-09-09-node-cache-policy/`):**④ 节点级 CachePolicy**——节点 `cache` 块(`ttl` 必填正整数 + `key_func` 具名注册 + `scope` session 默认/tenant opt-in,`global` 排除);`runtime/node_cache.py`(内存 LRU+TTL,`register_node_key_func` 注册表镜像 reducer 先例);默认键 = scope 命名空间 + node id + CONVERSATION model 配置哈希(模型升级自失效)+ 可读通道切片 canonical JSON;dispatch 缝命中跳过 worker(含静态/动态扇出分支),日志轨迹与 miss 逐字一致(NODE_END 双路带 `cached`+`cache_key`),命中不合成 LLM 事件,fold/replay/fork 零影响;tenant 无租户上下文 fail-closed;2026-09 行业调研(19 项目)确认节点级结果缓存业界仅 LangGraph 有先例。**reducer 半项随 ③ 交付**(`register_reducer` 注册表 + 内建 `add`/`append` + 编译期未知名报错) | 5.7 tool-cache pattern ✅ | S | Expensive KB/LLM node caching; custom merge semantics |

> Deliberately **not** in scope (research conclusion, see 1.3.21 row): Functional API (`@entrypoint`/`@task`) — GraphDSL covers the expression; delta-checkpoint cache layer — log-as-truth + materialized checkpoints is the stronger answer. Stream-mode parity (debug/tasks/checkpoints) stays in 11.18; inspector UI stays in G7 (1.1.23); cross-thread store stays in 4.23.

> **② 延后跟进项**(design 阶段确认,change `time-travel-resume`,防遗忘):FORK 载荷压缩策略(eviction 感知裁剪 vs blobs 拆表,待真实载荷分布)、悬挂 TURN 清扫与 update_state 门控联动(依赖 liveness/崩溃检测)、commit-points 列表富化(→ 8.20 消费侧)、fork 配额/同父分支上限(平台配额层统一;fork 执行复用既有预算门,无绕过敞口)。

### Milestone M8 (End of Sprint 8)

> **Honest closure note (2026-09-04)**: M8 still includes the "Ontology Action System with writeback" and "OAG complete" items, but their closure condition is **P5 Knowledge Graph integration trigger**, not Sprint 8 internal delivery. These two lines stay in M8 for plan consistency, but do not block Sprint 8's other Opening Queue deliverables — when P5 KG integration fires, the 6.20 / 6.22 closure is backfilled into M8 (and into whichever Sprint hosts that trigger). All other M8 lines are unaffected.

- [-] **Opening Queue** shipped: MCP Gateway (5.4a ✅), Evaluation Suite tasks (7.2b ✅ 2026-09-10 — archived `2026-09-10-ai-synthesized-evaluation-dataset`; 7.2c ✅ 2026-09-11 — archived `2026-09-11-online-offline-evaluation-tasks`; 7.3 ✅ 2026-09-11 — archived `2026-09-11-workflow-evaluation`; 7.2e ✅ 2026-09-12 — archived `2026-09-12-evaluation-report-dashboard`; 7.4/7.4a ✅ 2026-09-12 — archived `2026-09-12-human-annotation-trace-backflow`; 7.2d ✅ 2026-09-12 — archived `2026-09-12-automated-trace-backflow`; 7.3c ✅ 2026-09-13 — archived `2026-09-13-known-bad-exemption`; 7.3a/7.3b ✅ 2026-09-13 — archived `2026-09-13-eval-publish-gate-versions`), Controller Family (2.6a+1.1.21+1.3.10⊕6.23+6.49), Agent Versioning (1.3.20)
- [ ] Model Service Publishing (6.47) + Quick Wins (6.48) operational
- [x] Engine Parity (1.3.21): declarative interrupts (① ✅ 2026-09-08) + time-travel resume (② ✅ 2026-09-09) + dynamic fan-out (③ ✅ 2026-09-09) operational — unblocks Sprint 9 consumers (6.26 E5 what-if branching, 8.20 executable replay, 11.18/5.11 fan-out)
- [ ] Hallucination detection operational
- [ ] Self-Learning loop operational
- [ ] Agentic RL Framework with data flywheel
- [ ] Prompt Self-Optimization with ACE/GEPA
- [ ] Ontology Action System with writeback *(closure = P5 KG integration trigger; backfilled when triggered)*
- [ ] OAG complete (RAG + Logic + Actions) *(closure = P5 KG integration trigger; backfilled when triggered)*
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
| 3.5.4 | GraphRAG Query Engine | 3.5.1 + 3.5.2 + 3.5.3 *(P5 deferred — rebase on GraphRAG/LlamaIndex integration when triggered)* | L |
| 3.5.6 | Agent-Native Graph Memory | 3.5.2 + Memory System *(P5 deferred — same trigger)* | M |
| 3.5.13 | Temporal Memory & Reasoning | Memory System ✅ | M |
| 3.5.14 | Lazy GraphRAG | 3.5.1 + 3.5.2 *(P5 deferred — same trigger)* | L |

### Multi-Agent Intelligence

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 2.5 | Peer Selection (Selector) | Multi-Agent ✅ | M |
| 2.5a | Expert Panel Deliberation | 2.5 | M |
| 2.6 | Inter-Agent Communication | Multi-Agent ✅ | M |
| 2.11 | Agent Team Templates | Graph template ✅ | M |
| 2.13 | ACP (Agent Client Protocol) Support (NEW) — external coding agents (Claude Code, Codex, Gemini CLI) as worker nodes in Hecate orchestration; subagent provider seam (in-process/fork/ACP); complements A2A (agent-to-agent) — ACP is host-to-coding-agent | A2A ✅ | M |
| 13.15 | Distributed Team Orchestration | A2A ✅ (P3) | M |

### Execution Intelligence

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 1.3.5i ✅ | Deterministic Hooks (Lifecycle Events) | Settings system | M |
| 1.3.11 | Asynchronous Execution API Mode | Streaming ✅ + Session ✅ | M |
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
| 1.1.22 | Orchestration Mode Switching | 1.1.21 (Sprint 8 Opening Queue) | M |
| 1.1.23 | Execution State Visualization | Canvas ✅ | M |
| 1.1.26 | Object CRUD Node | KG Construction (P5 deferred — rebase on integration when triggered) | M |
| 1.1.27 | Side-by-side Chat + Canvas | 1.1.23 | M |

### Re-scope Additions (NEW)

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 8.21 | Projection Registry — named projections over the event log (permission presets, agent profiles, derived views) + canonical setter events; zero-database state derivation with consistent rebuild semantics | 1.3.19 ✅ (P3) | M |
| 13.20 | Atomic File Writes & Cross-Process File Locking — `<file>.lock` advisory locking + write-to-temp + atomic rename commit for multi-process file mutation (skills, plugin configs, state files); stale-lock recovery; ~150 LOC utility | — | S |
| 11.11 | Voice Agent Pipeline (moved from P5) — STT → agent workflow → TTS end-to-end pipeline with configurable providers; barge-in support (interrupt TTS when user speaks) | API ✅ | M |

### Milestone M9 (End of Sprint 9)

- [ ] P4 complete — 138 physical rows (grep basis, re-aligned 2026-09-07; includes the 48 P3-deferred reclassification + 11.11/2.13/8.21/13.20/6.27a + 6.47/6.48/6.49 AgentArts pull-forward + 1.3.21 engine parity; 22 done at Sprint 8 start)
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

> **Rescoped**: 12.0 v1 = Agent Plugins installer (5.5c, P3) + static git-index directory (Claude Code marketplace pattern) + scan-results display (5.13a, P3); **E-tier only** (T4 packages + T2 MCP registrations, never code plugins). 12.5 Partner Monetization + EC1 ARD (14.1) + EC4 (13.14 enhancement) + EC5 (11.13 enhancement) **frozen** until T4 supply/traction evidence exists (GPT Store decline; ClawHavoc governance lesson). Full rationale in feature-catalog 12.0/12.5/14.1 notes.

| # | Feature | Dependencies | Effort |
|---|---------|------|--------|
| 12.0 | Asset Marketplace (rescoped: installer + git index, E-tier only) | Plugin System ✅ + 5.5c (P3) | M (was L) |
| 12.1 | Industry Templates | 12.0 | M |
| 12.2 | Industry Knowledge Packs | 12.0 | M |
| 12.3 | Industry Skill Packs | 12.0 | M |
| 12.4 | Industry Integration Guide | 12.0 | S |
| 12.5 | Partner Monetization Infrastructure (frozen, pending supply evidence) | 12.0 | L |

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

### Deferred from P3/P4 (competitor-analysis re-scope)

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
| 5.13 | Plugin Security & Signing (rescoped: signing/digest/score only — content scanning split to 5.13a, P3) | Plugin System ✅ | M |
| 6.37 | Memory Clustering & Conflict Resolution | Memory System ✅ | M |
| 6.38 | Self-Planning (PDDL + MCTS) | LLM ✅ | L |
| 6.39 | Tool Auto-Creation | LLM ✅ | M |
| 6.40 | Firecracker microVM Backend | 1.3.15a ✅ + 6.32a | L |
| 13.4c | Session-Level microVM Isolation (Paradigm B — Bedrock AgentCore model, requires PregelRuntime RPC refactor) | 6.40 ✅ + 13.4 ✅ + engine refactor | XL |
| 13.19 | Service Mesh Integration (Istio/Linkerd mTLS + east-west traffic + canary routing) | 13.1 + 13.4 ✅ | M |
| 6.41 | WASM Runtime Backend (deprioritized: T4/T2 cover third-party scenarios; sequence behind 5.5c) | 5.3 ✅ + 6.40 | M |
| 6.42 | Global Branching | Ontology ✅ | M |
| 6.43 | Embedded Ontology | Ontology ✅ | M |
| 11.19 | Platform-Level Governance | Auth ✅ | M |
| 11.20 | Zero Trust Architecture | IAM ✅ | M |
| 14.1 | Agentic Resource Discovery (ARD) | A2A ✅ | L |
| 2.12 | Agent Payments (AP2) | A2A ✅ | M |
| 7.8a | Agent Benchmark Integration | Evaluation ✅ | M |
| 4.24 | Memory Versioning | Memory System ✅ | S |

### Milestone M10 (End of Sprint 10)

- [ ] P5 = 60/60 (100%) — re-scope basis (44 original − 11.11 moved to P4 + 17 deferred from P3/P4)
- [ ] Asset Marketplace operational
- [ ] Partner Monetization with Stripe integration
- [ ] Industry Templates and Knowledge Packs available
- [ ] PyPI package published (hecate-sdk)
- [ ] End-user web application beta
- [ ] Mobile GUI available
- [ ] Community translation framework ready
- [ ] EU AI Act compliance certified
- [ ] Knowledge Graph Visualization and Ontology tools
- [ ] Vision input operational (Voice moved to P4 as 11.11 Voice Agent Pipeline)
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
| A2A protocol spec instability | Sprint 5 affected | ✅ Resolved — A2A v1.0 GA (Linux Foundation, v1.0.0 2026-03-12 + patch v1.0.1 2026-05-26); core implemented (AgentCard + Task lifecycle). Correction: earlier note cited a "v1.2 (March 2026)" which does not exist in official releases — v1.0.x is current. |
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

**Key Insight**: Framework-first platforms (AutoGen, CrewAI, Langflow) ship core features in 0 months. Platform-first products (Dify, Coze) take 12–18 months for enterprise features. Hecate is at the inflection point — Graph engine + 26 engine extension interfaces + 160+ shipped features already built. The roadmap timelines are well within industry benchmarks. Canvas UI enhancement (1.1.14-1.1.23) and collaboration patterns (2.7a-2.7c) are incremental additions that build on the existing React Flow foundation, not greenfield development. MCP/Skill resource management (5.4c, 5.9d, 5.9e) addresses the operational maturity gap identified in competitive analysis. Knowledge Graph (3.5.1-3.5.8) and Ontology Modeling (3.5.9-3.5.12) provide structured knowledge capabilities matching Google ADK, LangGraph, and Microsoft GraphRAG. Memory Retrieval Quality (4.14, 4.15) closes the gap with Mem0's multi-signal fusion approach. MemGPT-Inspired Memory (4.16-4.20) adds LLM self-management, pressure alerts, conversation recall, self-editing, and multi-step retrieval. AgentScope-Inspired Memory (4.21-4.24) adds task memory, tool memory, cross-thread store, and memory versioning. AIP-Inspired Capabilities (6.15-6.43) — from AgentArts (fka Versatile) and Palantir — add Agentic RL, NL2Agent, DSL conversion, ontology actions, decision lineage, OAG, intent recognition, simulation, Browser/Computer-use, DataAgent, VibeCoding, fine-grained permissions, data integration, gVisor sandbox, AI Office, industrial data, asset operations, self-planning, tool auto-creation, Firecracker microVM, cloud doc connector, global branching, and embedded ontology. **Hecate's positioning: an open-source enterprise Agent platform with ontology enhancements — not an AIP (where ontology is the architectural organizing principle), but an AgentArts-level (fka Versatile) platform where ontology is an enabler alongside the Pregel engine, Graph DSL, and 26 engine extension interfaces.**

---

## Milestone Summary

| Milestone | Date | Verification Criteria |
|-----------|------|-----------------------|
| **M1: P1 Complete** | Month 2 | 19/19 features ✅; 4 ABCs wired; 0 layering violations; all tests green |
| **M2: Platform Ready** | Month 4 | Canvas usable; Multi-Agent orchestrable; Multi-DB + Multi-Vector-DB supported |
| **M3: Feature Complete** | Month 6 | P2 63/63 (100%); 5+ channels; Evaluation baseline operational |
| **M4: Enterprise Ready** | Month 8 | Resilience infrastructure (exception hierarchy + auto-retry + tool gating) ✅; ContextEngine Phase 1 (LLMWorker context pipeline) ✅; Plugin SPI Core + EvaluatorBase defined ✅; Multi-Tenant RBAC + SSO; full security stack ✅; end-to-end observability ✅ |
| **M5: P3 Enterprise** | Month 10 | Platform SPI complete: ChannelBase + AuthProvider + i18n SPI ✅ (NotifierABC merged into ChannelBase); A2A Protocol with Signed Agent Cards; Model Hub (Catalog + Lifecycle Manager); Enterprise Identity: SSO + SCIM + Vault + Budget Management ✅ |
| **M6: P3 Security & Ops** | Month 12 | Ops Center (Dashboard + Agent Health + Conversation Analytics + Tool Execution Analytics + CI/CD Gating + Agent Catalog Governance); Security (DLP + Runtime Protection + Red Teaming); Plugin System; Deployment infrastructure (SaaS + Canary + Horizontal Scaling + Backup) |
| **M7: P3 Complete** | Month 14 | P3 re-scoped 125/125 (100%); Event-Sourced State (log-as-truth + DeltaChannel); Dynamic Orchestration; Run Replay Phase 1; Browser Automation Tool; Skill Provider Registry; Advanced RAG (Reranking + Incremental + Quality Eval); Multi-Channel Wave 1 (11.2 simplified ✅ + 11.3 ✅ + 11.9 Slack ✅); Evaluation Suite (7.6a/b dropped); Canvas (Human Input/Form + Trigger; 1.1.18-20 deferred); Memory Enhancement |
| **M8: P4 Intelligence** | Month 16 | Hallucination Detection operational; Self-Learning loop; Agentic RL Framework; Prompt Self-Optimization; Ontology Action System; OAG complete; Sleep-time Memory Consolidation; LLM-Managed Memory; Memory Intelligence features |
| **M9: P4 Complete** | Month 18 | P4 99/99 remaining (100%); GraphRAG Query Engine (P5-trigger); Agentic RAG; Temporal Memory; Lazy GraphRAG (P5-trigger); Peer Selection; Agent Team Templates; ACP Support (2.13); Distributed Team Orchestration; Deterministic Hooks; Asynchronous Execution API; 5-Level Intent Recognition; Simulation Environment; Computer-use (6.27a); Voice Agent Pipeline (11.11); DataAgent; VibeCoding; Multi-Stream Modes; Projection Registry (8.21) + Atomic File Locks (13.20); Canvas Intelligence; Model Service Publishing (6.47) + Quick Wins (6.48); Intent Package Asset (6.49) |
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
Plugin SPI Core (5.5a) → EvaluatorBase (7.2-abc) + ChannelBase (11.1-abc) + AuthProvider (10.3-abc) + NotifierABC (8.6-abc) + i18n SPI (15.1)
Failure Analysis → Meta-Agent Scheduler → Garbage Collector + Drift Detector + Compliance Checker
Model Routing → A/B Testing → Gray Release → Circuit Breaker → Key Encryption → Intelligent Router
Security → Sandbox Executor → Sandbox Pool → Event Store → Tracing → Metrics → NotifierABC
Authentication → Authorization → Multi-Tenant → Tenant Isolation
Multi-Agent → A2A Protocol (2.10) → Signed Agent Cards (2.10a) → Conflict Handling → Skill Registry → Mutual Embedding
MCP Client → MCP Server Mode → MCP Streamable HTTP (5.4b) → MCP Server Registry & Connection Management (5.4c) → MCP Gateway → Plugin System (5.5, via 5.5a) → Tool Permission
Skill Loading → Skill Versioning (5.9d) → Resource Versioning → **1.3.20 Agent Versioning** *(carries the 14.x versioning substrate that 5.9d, 3.5.12, and 7.5 all reference)*
Knowledge Graph Construction (3.5.1) → Graph Database Integration (3.5.2) → Community Detection (3.5.3) *(P5 deferred)*
Memory Isolation (4.6) → Memory Importance Scoring (4.14) → Multi-Signal Fusion Retrieval (4.15)
LLM-Managed Memory (4.16) → Memory Pressure Alert (4.17) → ContextEngine Integration
Task Memory (4.21) → Trajectory Learning → Experience Retrieval
Evaluation → Agentic RL Framework (6.15) → Data Flywheel → Model Optimization
Canvas + Graph DSL → NL2Agent (6.16) → NL2Flow → Workflow Auto-Generation
EventStore → Trace Annotation (6.18) → Evaluation Datasets → Agentic RL
Evaluation → Prompt Self-Optimization (6.19) → ACE/GEPA Algorithm → Auto-Optimized Prompts
Knowledge Graph (3.5.1, P5 deferred) → Ontology Action System (6.20) → Object Actions → Writeback
EventStore → Decision Lineage (6.21, P5 deferred) → Decision Audit → Compliance
6.20 + RAG → OAG (6.22) → RAG + Logic + Actions Closed Loop *(blocked by 6.20's P5 KG dependency)*
Auth Service → Per-Token-Type Auth (11.16) → Two-Tier Identity (11.17) → Fine-Grained Access Control
Canvas → Human Input/Form Node (1.1.24) → Trigger Node (1.1.25) → Event-Driven Workflows
CheckpointStore → Distributed Session State Store (13.4a) ✅ (5/5) → Horizontal Scaling (13.4) → Stateless Multi-Replica
EventStore → Event-Sourced Execution State (1.3.19) → Run Replay (8.20) + Projection Registry (8.21, P4)
EventStore (1.3.19) → HITL durable audit pairs + middleware waterfall events
Pregel + Collaboration Patterns (2.7a ✅) → Dynamic Orchestration (1.3.18) → runtime task DAG → 7th pattern → Advanced Orchestration (1.3.18a, P4: consensus / PlanPatch repair / async steering / plan-freeze replay) + UI companion (P3, follow-up change on pattern-selector-ui / multi-agent-canvas / 8.20)
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
AuthProvider (P3) → SCIM Directory Sync (10.3b)
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
Engine Parity (1.3.21, Sprint 8, 收官 2026-09-09): Declarative Interrupts (① ✅) → Time-Travel Resume (② ✅) → Dynamic Fan-out (③ ✅) → Sprint 9 consumers: What-If Checkpoint Branching (6.26 E5) + executable 8.20 replay + Deep Research (5.11) parallel collection
Knowledge Graph (3.5.1-3.5.3, P5 deferred) → Object CRUD Node (1.1.26) → Ontology-Native Canvas Development
Execution State Visualization (1.1.23) → Side-by-side Chat+Canvas (1.1.27) → Integrated Dev/Test View
Streaming → Asynchronous Execution API (1.3.11) → Long-Running Workflow Support
P3 Observability (8.0-8.8) → Unified Ops Center Dashboard (8.9) → Agent Health Monitoring (8.9a) → Conversation Analytics (8.9b)
P3 Deployment (13.0-13.4) + Data Backup (13.5) + Version Upgrade (13.6) → Environment Management & ALM Pipeline (13.17) → API Management & Developer Portal (13.18)
P3 Cost Dashboard (8.3) → Budget Management & Cost Governance (10.7)
P3 AB Testing (7.4) + P3 Evaluators (7.2) → Testing Center / Sandbox (7.9)
P3 Model Management (6.8-6.13) → Model Catalog (6.44) → Model Lifecycle Manager (6.45) → Model Governance (6.46-P5)
Model Lifecycle Manager (6.45 ✅) → Model Service Publishing (6.47) → published-only application reference surface
Traces/Costs (8.x ✅) → Provider Call-Count Wiring (6.48)
Intent Recognition (6.23) → Intent Package Asset (6.49) → few-shot classification evidence
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
P3 Evaluators (7.2a) → Evaluation Three-Dimension Structuring (OE8) → Reasoning Efficiency Evaluator (OE9, frozen → OTel export)
P3 Online/Offline Eval (7.2c) → Production Online Scoring (OE3) → CI/CD Evaluation Gating (8.10/OE1)
Decision Lineage (6.21, P5 deferred) → Data-to-Decision Traceability (OE4) → Ontology-Level Provenance
Testing Center (7.9) + Regression Test Set (7.6) → CI/CD Evaluation Gating (8.10/OE1) → Deployment Quality Gate
Agent Evaluation (7.2) + Agent Benchmarks (7.8a) → Agent Catalog Governance (8.12/OE7) → Quality-Gated Publishing
Red Teaming (7.10) → Adversarial Test Generation (OE10) → Pre-Publish Robustness Verification
```
