# ADR-022: Model Hub Enhancement Architecture

> **Status**: Proposed
> **Date**: 2026-07-01

## Context

Hecate's Model Hub delivers strong LLM integration (100+ providers, intelligent routing, circuit breaker, A/B testing, gray release) and basic provider management (CRUD, multi-auth, key encryption, model classification). However, competitive analysis against Vertex AI Model Garden, IBM watsonx, Dify, Portkey, and Palantir Foundry revealed 8 gaps in the management and governance layer:

| Gap | Description | Type | Priority |
|-----|-------------|------|----------|
| G1 | **Model Catalog** — browseable/searchable catalog, capability badges, provider comparison | New Feature | P4 (6.44) |
| G2 | **Model Lifecycle Manager** — versioned registry, staging channels, deprecation scheduling | New Feature | P4 (6.45) |
| G3 | **Model Governance** — approval workflows, risk scoring, compliance reporting | New Feature | P5 (6.46) |
| G4 | **Model Monitoring Dashboard** — drift detection, quality regression, health scoring | O10 Enhancement | P4 |
| G5 | **Managed Model Deployment** — end-to-end deployment workflow for self-hosted models | 6.5 Enhancement | P4 |
| G6 | **Multi-Modal Model Classification** — Image/Video/Audio/Code model type support | 6.11 Enhancement | P4 |
| G7 | **Fine-Tuning Pipeline** — dataset → training → evaluation → deployment | 6.6 Enhancement | P4 |
| G8 | **Model Cost Management** — per-model budgets, anomaly detection, chargeback | 6.4 Enhancement | P4 |

These gaps span two architectural layers:
1. **Data/Metadata layer** — New models and schemas for catalog entries, lifecycle states, governance policies, monitoring metrics, deployment configs, fine-tuning jobs, cost budgets
2. **UI/Presentation layer** — New admin views within the Ops Center pattern (Model Management Console O10)

## Decision

### 1. Model Catalog (G1/6.44) — Metadata-Driven Discovery

Build the Model Catalog as a **metadata layer on top of existing provider models**. No new runtime infrastructure — the catalog is a read-optimized index that aggregates model metadata from:

- **ModelProviderModel** (existing) — provider name, auth config, status
- **ModelRegistryModel** (existing) — model_id, model_type, capabilities, version
- **New: CatalogEntryModel** — curated metadata: display name, description, capability badges (token window, pricing tier, supported modalities), provider comparison data, documentation links, activation status

The Catalog is populated via:
1. **Built-in seed data** — Hecate ships with a curated catalog of 200+ models across 50+ providers
2. **Provider discovery** — On provider registration, fetch available models via provider API (OpenAI `GET /models`, etc.)
3. **Custom entries** — Admin can add private models via the UI

**Design principle**: Catalog is read-optimized. All writes go through the existing provider/model CRUD APIs. The catalog index is rebuilt asynchronously on provider changes.

### 2. Model Lifecycle Manager (G2/6.45) — State Machine

Model lifecycle is a **state machine** applied to ModelRegistryModel:

```
registered → staged(dev) → staged(staging) → approved → deployed
                                                                  ↓
                                                             deprecated
                                                                  ↓
                                                             sunset (auto-removal)
```

Each transition is gated by:
- **Staging approval** — Manual or policy-based (e.g., "all tests pass")
- **Deprecation scheduling** — Admin sets a date; system sends notifications at T-30d, T-7d, T-1d
- **Rollback** — `deprecated → approved` or `approved → staged` via re-promotion

The Lifecycle Manager is implemented as a **service** (`services/model/lifecycle.py`) with:
- `LifecycleState` enum (REGISTERED, STAGED, APPROVED, DEPLOYED, DEPRECATED, SUNSET)
- `promote(model_id, target_state, actor, reason)` — the single mutation entry point
- `schedule_deprecation(model_id, sunset_date, notify_channels)` — timed deprecation
- `get_lifecycle_history(model_id)` — full audit trail

### 3. Model Governance (G3/6.46) — Policy Enforcement Layer

Model Governance is a **policy enforcement layer** that wraps the Lifecycle Manager with:

- **Approval workflows** — Configurable gates: single approval, multi-party approval, automated policy evaluation
- **Risk scoring** — Per-model risk assessment using configurable metrics: bias scan results, performance benchmarks, provider reliability score, data privacy classification
- **Compliance documentation** — Auto-generated model cards (model name, provider, training data summary, intended use, limitations, bias metrics)

Architecturally, Governance is a **service** (`services/model/governance.py`) that imports LifecycleManager and extends it with policy evaluation hooks. It integrates with:

- **Compliance & Audit Center (9.9)** — Shared policy definitions and audit log
- **Decision Lineage (6.21)** — Full audit trail of model approval decisions

### 4. Model Monitoring Dashboard (G4) — Presentation Enhancement

The Model Monitoring Dashboard is a **presentation-only enhancement** to the Model Management Console (O10). It consumes existing observability data (8.1-8.3) and aggregates it at the model level:

- **Latency/Cost/Error rate trends** — Grouped by model_id from Trace/Span attributes
- **Drift detection alerts** — Threshold-based alerting on metric deviations (e.g., "latency increased 50% compared to 7-day rolling average")
- **Model health scoring** — Composite score from availability (40%), latency (30%), error rate (30%)

No new backend services — all data comes from the existing OpenTelemetry-compatible trace store (TimescaleDB).

### 5. Managed Model Deployment (G5/6.5 Enhancement)

Extend Self-Hosted Inference (6.5) with a **deployment workflow service**:

- **Model artifact registry** — Store model references (HF hub IDs, S3 paths, Docker images)
- **Health check configuration** — Probe endpoint, expected response, interval, failure threshold
- **Auto-scale configuration** — Min/max replicas, CPU/memory target utilization
- **Monitoring integration** — Auto-attach latency/throughput/error rate dashboards

Implementation: New service `services/model/deployment.py` that manages model deployment lifecycle. Uses existing container orchestration (Docker Compose/K8s) for runtime.

### 6. Fine-Tuning Pipeline (G7/6.6 Enhancement)

Extend Model Fine-Tuning (6.6) with a **pipeline management service**:

- **Dataset management** — Upload, version, preview, split (train/val/test)
- **Training job configuration** — Base model, hyperparameters, compute resources
- **Progress monitoring** — Real-time loss/accuracy curves via WebSocket
- **Model evaluation** — Automatic evaluation on held-out test set post-tuning
- **One-click deployment** — Promote tuned model directly to production endpoint

Implementation: New service `services/model/finetuning.py` with async job execution via Celery/Temporal. Supports multi-provider backends (vLLM fine-tuning API, Hugging Face AutoTrain, custom training scripts).

### 7. Multi-Modal Classification (G6/6.11 Enhancement)

Extend `ModelClassification` enum to include:
- `IMAGE_GENERATION` — Stable Diffusion, DALL-E, Midjourney
- `VIDEO_GENERATION` — Sora, Runway, Pika
- `AUDIO_GENERATION` — ElevenLabs, Whisper, MusicGen
- `CODE_GENERATION` — Codex, Code Llama, Qwen Coder

The LLM Service invocation layer is not changed — multi-modal support depends on the provider's existing API capabilities. Classification changes are metadata-only (UI filtering, pricing, routing rules).

### 8. Model Cost Management (G8/6.4 Enhancement)

Extend Cost Tracking (6.4) with per-model and per-workspace budget controls:

- **Model-level budgets** — `ModelCostBudget` model: model_id, workspace_id, monthly_limit_hard, monthly_limit_soft, alert_threshold
- **Anomaly detection** — Statistical deviation detection comparing current spend to rolling 30-day average
- **Chargeback reports** — Per-workspace cost attribution with drill-down to model-level granularity

Integrates with Budget Management (10.7) via shared `BudgetPolicy` model — model budgets are a specialization of workspace budgets.

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Model Hub Architecture                           │
│                                                                          │
│  ┌─────────────────────┐  ┌─────────────────────┐                       │
│  │   Existing Services  │  │    New Services      │                       │
│  │                      │  │                      │                       │
│  │  LLM Service (1xx)   │  │  ModelCatalogService │                       │
│  │  ModelRouter         │  │  LifecycleManager    │                       │
│  │  ProviderService     │  │  GovernanceService   │                       │
│  │  CostTracking (6.4)  │  │  DeploymentService   │                       │
│  │  FineTuning (6.6)    │  │  FinetuningPipeline  │                       │
│  └─────────────────────┘  └─────────────────────┘                       │
│           │                          │                                   │
│           ▼                          ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐           │
│  │                    Models Layer                            │           │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │           │
│  │  │Provider   │ │Registry  │ │Catalog   │ │Lifecycle │    │           │
│  │  │Model     │ │Model     │ │EntryModel│ │LogModel  │    │           │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘    │           │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │           │
│  │  │Governance│ │Deploy    │ │Finetuning│ │CostBudget│    │           │
│  │  │PolicyModel│ │ConfigModel│ │JobModel  │ │Model     │    │           │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘    │           │
│  └──────────────────────────────────────────────────────────┘           │
│           │                                                             │
│           ▼                                                             │
│  ┌──────────────────────────────────────────────────────────┐           │
│  │              Model Management Console (O10)               │           │
│  │  ┌──────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐  │           │
│  │  │Catalog│ │Lifecycle│ │Monitor │ │Deploy  │ │Govern  │  │           │
│  │  │Browse │ │Manager │ │Dashboard│ │Workflow│ │Console │  │           │
│  │  └──────┘ └────────┘ └────────┘ └────────┘ └────────┘  │           │
│  └──────────────────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────────────┘
```

## Consequences

### Positive
- **Model Catalog** makes Hecate competitive with Vertex AI Model Garden and Dify for model discovery
- **Lifecycle Manager** addresses enterprise model governance requirements matching IBM watsonx
- **Governance** closes the compliance gap for regulated industries (finance, healthcare)
- All enhancements follow the existing Ops Center composition pattern — no new infrastructure
- **Model Monitoring** reuses existing observability data (Trace/Span) — minimal backend cost
- **Cost Management** shares budget models with Ops Center (10.7) — no duplicate modeling

### Negative
- **Catalog seed data** requires significant manual effort to curate 200+ model entries
- **Lifecycle Manager** introduces state that must be kept in sync with actual provider API state
- **Fine-Tuning Pipeline** depends on external training infrastructure (GPU compute) — platform must handle diverse backends
- **Governance policy evaluation** adds latency to model deployment workflow

### Neutral
- CatalogEntryModel duplicates some metadata from ModelRegistryModel — intentional separation of runtime metadata vs curated catalog metadata
- Monitoring dashboard consumes OTel trace data without adding new instrumentation — data quality depends on existing observability coverage
- Multi-Modal Classification is metadata-only; actual multi-modal invocation depends on LLM provider capabilities

## Alternatives Considered

### 1. Catalog as LiteLLM Proxy Extension
**Rejected** — Catalog metadata (capability badges, pricing tiers, documentation links) does not belong in a routing proxy. Catalogs are a UI/management concern, not a runtime concern.

### 2. Lifecycle as GitOps Workflow
**Rejected** — GitOps adds unnecessary complexity for a system that needs admin UI-driven state changes. A state machine service with REST API is simpler and matches the existing Hecate pattern.

### 3. Governance as Standalone Microservice
**Rejected** — Governance logic is tightly coupled to Lifecycle Manager state transitions. A service that imports LifecycleManager is simpler than a separate microservice with RPC calls.

### 4. Monitoring as New Instrumentation Layer
**Rejected** — Adding model-specific instrumentation duplicates the OpenTelemetry-based observability already in place. Monitoring dashboards should aggregate existing trace data by model_id.

## Related Documents

- **Design document**: See `docs/design/model-hub-design.md` for detailed L2 architecture, component descriptions, data models, and API endpoints
- **Ops Center ADR**: See [ADR-021](021-ops-center-architecture.md) for the composition architecture pattern shared between Ops Center and Model Hub
- **Feature catalog**: Features 6.44 (Model Catalog), 6.45 (Model Lifecycle Manager), 6.46 (Model Governance), and enhancements G4-G8 documented in `docs/features/feature-catalog.md`
