# ADR-021: Ops Center Architecture

> **Status**: Proposed
> **Date**: 2026-07-01

## Context

Hecate's operational capabilities are currently distributed across independent features: Observability (8.0-8.8), Evaluation (7.0-7.8), Deployment (13.0-13.6), and Audit (8.7). While each provides individual value, operators lack a unified administrative interface to monitor, manage, and govern the entire platform. Competitive analysis of Palantir AIP Control Panel, Salesforce Agentforce Studio, Microsoft Copilot Studio, and Dify revealed that all mature Agent platforms converge on a centralized Ops Center pattern.

The gap analysis identified 12 gaps (O1-O12) across the operational stack:

| Gap | Description | Priority |
|-----|-------------|----------|
| O1 | Unified Ops Center Dashboard (8.9) | P4 |
| O2 | Agent Health Monitoring Dashboard (8.9a) | P4 |
| O3 | Environment Management & ALM Pipeline (13.17) | P4 |
| O4 | Conversation Analytics & Quality Scoring (8.9b) | P4 |
| O5 | Testing Center / Sandbox (7.9) | P4 |
| O6 | Budget Management & Cost Governance (10.7) | P4 |
| O7 | Compliance & Audit Center (9.9) | P5 |
| O8-O12 | Various enhancements (dashboard builder, incident management, model management, API management, backup UI) | P3-P5 |

These features span 4 architecture layers and require decisions on integration strategy, UI composition, and data source unification.

## Decision

Establish the **Ops Center** as a **composition layer** that aggregates data from existing backend services into a unified administrative interface. The Ops Center is NOT a new microservice but a **presentation and aggregation layer** that:

1. **Composes** existing backends (Observability, Evaluation, Deployment, Security, Model Hub, Cost)
2. **Unifies** navigation and role-based access through a single admin console
3. **Extends** existing APIs with aggregation endpoints where needed
4. **Maintains** separation of concerns — each backend retains its own data store and processing logic

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Ops Center (Admin Console)                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ Dashboard │ │  Health  │ │  Eval    │ │  Deploy  │ │ Compliance│  │
│  │ (8.9)    │ │ (8.9a)   │ │ (7.9)    │ │ (13.17)  │ │ (9.9)    │  │
│  ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤ ├──────────┤  │
│  │ Analytics │ │  Budget  │ │  API Mgmt│ │  Audit   │ │ Environ. │  │
│  │ (8.9b)   │ │ (10.7)   │ │ (13.18)  │ │ (8.7)    │ │ (13.17)  │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
│                          │ aggregation & routing                    │
│                          ▼                                          │
│               ┌──────────────────────┐                              │
│               │  Ops Center Gateway  │                              │
│               │  (aggregation API)   │                              │
│               └──────┬───────┬───────┘                              │
└──────────────────────┼───────┼──────────────────────────────────────┘
                       │       │
           ┌───────────┘       └───────────┐
           ▼                               ▼
┌──────────────────┐          ┌──────────────────┐
│  Observability   │          │  Backend         │
│  Service (8.x)   │          │  Services        │
│  - Tracing       │          │  - Evaluators    │
│  - Monitoring    │          │  - Deployments   │
│  - Alerting      │          │  - Budget/Cost   │
│  - Audit         │          │  - Compliance    │
│  - Cost/Tracing  │          │  - API Keys      │
│  DB: TimescaleDB │          │  DB: PostgreSQL  │
└──────────────────┘          └──────────────────┘
```

### Component Responsibilities

| Component | Role | Data Source |
|-----------|------|-------------|
| Ops Center Gateway | Aggregation API, RBAC filtering, viewport data assembly | Proxies to backend services |
| Dashboard (8.9) | Landing page with configurable widget layout | Aggregate from all services |
| Health Monitoring (8.9a) | Per-agent health metrics, fleet overview | Observability Service |
| Conversation Analytics (8.9b) | Session trends, quality scoring, RCA | Observability + Evaluation |
| Testing Center (7.9) | Test suite management, batch runs, regression detection | Evaluation Service |
| Budget Management (10.7) | Spending limits, forecasting, chargeback | Cost Service |
| Environment Management (13.17) | Environment lifecycle, promotion workflows | Deployment Service |
| API Management (13.18) | API keys, usage analytics, dev portal | Management Service |
| Compliance Center (9.9) | Compliance posture, policy UI, audit log viewer | Security + Audit |

### Integration Pattern

Each Ops Center component follows a consistent integration pattern:

1. **Backend service** owns the data and business logic (existing services remain unchanged)
2. **Aggregation API** (Ops Center Gateway) provides composite queries across multiple services
3. **Frontend component** renders the UI, calling the Gateway for data
4. **Role-based access** (10.2 RBAC) applies at both the Gateway and UI layer

## Rationale

### Why Not a Monolithic Service

- **Separation of concerns preserved**: Observability, evaluation, deployment, and compliance have fundamentally different data models and scaling characteristics
- **Independent evolution**: Each domain can be developed and deployed independently
- **Existing investment leveraged**: The P3 observability infrastructure (8.1-8.7), evaluation engine (7.1-7.8), and deployment features (13.0-13.6) already exist — Ops Center composes them
- **Failure isolation**: A bug in the testing center doesn't affect agent health monitoring

### Why Composition Layer Instead of Dashboard-as-a-Service

- **Latency**: Direct backend service calls avoid unnecessary network hops
- **Consistency**: The Gateway can enforce uniform pagination, filtering, and RBAC across all views
- **Simplicity**: Each component is a thin composition layer, not a service with its own data store
- **Flexibility**: New components can be added without infrastructure changes

### Enhancement Strategy (O8-O12)

Enhancements are implemented as **component additions** within existing features rather than separate features:

- **O8 (Custom Dashboard Builder)**: Widget registry within the Dashboard component — users select from available metric widgets
- **O9 (Incident Management Console)**: Alerting UI surface within the existing Alerting (8.6) feature
- **O10 (Model Management Console)**: Admin UI for model provider management within Model Hub (6.x)
- **O11 (API Management Portal)**: Developer portal as part of 13.18
- **O12 (Backup & Restore UI)**: Admin UI for backup configuration as part of 13.5

### Dependency Chain

```
P3 Observability (8.0-8.8) ─────────────────────────┐
P3 Cost Dashboard (8.3) ────┐                       │
P3 Alerting (8.6) ──────────┤                       │
P3 Audit Logs (8.7) ────────┤ → Unified Dashboard (8.9) → Health Monitoring (8.9a)
P3 Evaluation (7.1-7.4) ────┤                       │
P3 Deployment (13.0-13.4) ──┘                       └→ Conversation Analytics (8.9b)
P3 Data Backup (13.5) ──────┐
P3 Version Upgrade (13.6) ──┤ → Environment Mgmt (13.17) → API Management (13.18)
P3 Canary (13.1a) ──────────┘
P3 A/B Testing (7.4) ───────→ Testing Center (7.9)
P3 Cost Dashboard (8.3) ────→ Budget Management (10.7)
P4 Compliance Framework (9.6) → Compliance Center (9.9)
```

## Consequences

- **P3 Sprint 5 dependency**: 8.9, 8.9a, 8.9b, 7.9, 10.7, 13.17, 13.18 all depend on P3 observability and deployment features being complete
- **8 new features, 4 enhancements**: Ops Center adds 8 new feature IDs (O1-O7 + O11 as 13.18) and 4 enhancement notes (O8, O9, O10, O12)
- **No new microservices**: All Ops Center features are implemented as frontend components + aggregation API layer
- **RBAC integration required**: Ops Center views must respect the existing RBAC (10.2) — users see only what their role permits
- **P4 count increases from 78 to 85**: 7 P4 features (8.9, 8.9a, 8.9b, 7.9, 10.7, 13.17, 13.18), 1 P5 feature (9.9)
- **Feature 13.18 (API Management) is new**: Previously P3 API Open Platform (14.1) had no developer portal — this fills that gap
- **Design document**: See `docs/design/ops-center-design.md` for detailed L2 architecture description
