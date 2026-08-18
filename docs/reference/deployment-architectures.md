# Deployment Architectures

Reference topologies for deploying Hecate — from a single host to a multi-region Kubernetes cluster. Each pattern includes a component diagram, sizing guidance, and when to choose it.

> For step-by-step deployment commands, see [Deploy to Production](../how-to/deploy-production.md). This page covers the *architecture patterns* — which components go where, how they connect, and how to scale them.

---

## Component map

Every Hecate deployment is assembled from the same components. The topology determines how many instances of each run and where they live:

```
                    ┌─────────────┐
                    │  Client(s)  │
                    └──────┬──────┘
                           │ HTTPS
                    ┌──────▼──────┐
                    │ Reverse Proxy│  (nginx / Caddy / ALB)
                    │   TLS term   │
                    └──────┬──────┘
                           │ HTTP
              ┌────────────▼────────────┐
              │     Hecate App (×N)     │  stateless API servers
              │  uvicorn / gunicorn     │
              └─┬───────┬───────┬───────┘
                │       │       │
    ┌───────────▼──┐ ┌──▼───┐ ┌▼──────────┐
    │ PostgreSQL   │ │Redis │ │  Qdrant   │
    │  (primary)   │ │(state│ │ (vectors) │
    │              │ │cache)│ │           │
    └──────┬───────┘ └──────┘ └───────────┘
           │ optional replica
    ┌──────▼───────┐
    │ PostgreSQL   │
    │  (replica)   │
    └──────────────┘

    ┌──────────────┐         ┌──────────────┐
    │    MinIO     │         │   Temporal   │
    │ (S3 storage) │         │  (optional)  │
    └──────────────┘         └──────────────┘
```

**Stateful**: PostgreSQL, Qdrant, MinIO, Temporal. **Stateless**: Hecate app, Redis (cache only). The app is the only component that scales horizontally without coordination.

---

## Pattern 1: Single-host Docker Compose

```
┌─────────────────── Single Host / VM ───────────────────┐
│                                                         │
│  nginx ── hecate (×1) ── postgres                       │
│              │                  qdrant                  │
│              │                  minio                   │
│              └─────────────── temporal (optional)       │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

| Aspect | Spec |
|--------|------|
| **Best for** | Small teams, evaluation, ≤ 100 RPS |
| **App instances** | 1 |
| **HA** | None — single point of failure per component |
| **Recovery** | Backup + restore ([runbook](../operations/backup-restore.md)) |

**Sizing**: 4 CPU cores, 8 GB RAM, 100 GB SSD. PostgreSQL and Qdrant are the main memory consumers.

**When to use**: Internal tool, proof of concept, team of < 20 users. The reference `docker/docker-compose.yml` deploys this pattern out of the box.

---

## Pattern 2: Blue-green Docker Compose

```
┌─────────────── Docker Compose Host(s) ─────────────────┐
│                                                         │
│  nginx ──┬── hecate-blue  (active)  ──┐                │
│          │                             ├── postgres     │
│          └── hecate-green (standby)  ──┘    qdrant      │
│                                            minio        │
│           Redis (session state)           temporal      │
└─────────────────────────────────────────────────────────┘
```

| Aspect | Spec |
|--------|------|
| **Best for** | Zero-downtime deploys, instant rollback |
| **App instances** | 2 (one active, one standby) |
| **HA** | App-level only (blue-green switch). Data stores still single-instance. |
| **Session state** | Redis (`SESSION_STATE_STORE_BACKEND=redis`) — so both instances see the same state |

**Key requirement**: Switch `SESSION_STATE_STORE_BACKEND` from `memory` to `redis` so both app instances share session state. Without Redis, a blue-green switch loses in-flight sessions.

**When to use**: Production team that needs zero-downtime deploys but doesn't need Kubernetes-level complexity. See [Deploy to Production — Blue-green](../how-to/deploy-production.md#blue-green-deployment-zero-downtime).

---

## Pattern 3: Kubernetes cluster

```
┌─────────────────────── K8s Cluster ────────────────────┐
│                                                         │
│  Ingress Controller                                     │
│       │                                                 │
│  ┌────▼─────┐  Hecate Deployment (replicas: 3+)        │
│  │ Service  │──┤ pod-1 ├──┤ pod-2 ├──┤ pod-3 ├──       │
│  └──────────┘                                          │
│       │                                                 │
│  ┌────▼──────────────────────────────────────────┐     │
│  │  StatefulSet / Managed Service                 │     │
│  │  ├── PostgreSQL (primary + read replica)      │     │
│  │  ├── Qdrant (cluster mode)                    │     │
│  │  ├── MinIO (distributed)                      │     │
│  │  └── Temporal (cluster, optional)             │     │
│  └───────────────────────────────────────────────┘     │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐                    │
│  │ Redis Stateful│  │ Migrations   │                    │
│  │   Set         │  │ Init Container│                    │
│  └──────────────┘  └──────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

| Aspect | Spec |
|--------|------|
| **Best for** | High-scale, multi-replica, multi-region |
| **App instances** | 3+ (horizontal autoscaling) |
| **HA** | Full — app, database, vector store |
| **Session state** | Redis StatefulSet (required) |

**Managed services**: Use cloud-managed PostgreSQL (RDS, Cloud SQL, Aurora), object storage (S3 instead of MinIO), and optionally managed Redis (ElastiCache). Qdrant can run as a StatefulSet or use Qdrant Cloud.

**Migration**: Run `hecate-migrate` as a Kubernetes Job or init container — it exits 0 on success, blocking the Deployment from starting until the schema is ready.

**When to use**: Production at scale, multi-tenant SaaS, regulated environments requiring HA. See [Deploy to Production — Kubernetes](../how-to/deploy-production.md#kubernetes-deployment).

---

## Sizing guidelines

| Component | Small (≤100 RPS) | Medium (≤1K RPS) | Large (1K+ RPS) |
|-----------|-----------------|------------------|-----------------|
| **Hecate app** | 1 replica, 2 CPU, 4 GB | 3 replicas, 4 CPU, 8 GB each | 5+ replicas, 4 CPU, 8 GB each |
| **PostgreSQL** | 2 CPU, 4 GB, 50 GB | 4 CPU, 16 GB, 200 GB + replica | 8+ CPU, 32+ GB, 500+ GB + replicas |
| **Qdrant** | 1 CPU, 2 GB, 20 GB | 2 CPU, 8 GB, 100 GB | 4+ CPU, 16+ GB, cluster mode |
| **Redis** | — (use memory) | 1 CPU, 2 GB | 2 CPU, 4 GB + persistence |
| **MinIO** | 1 CPU, 1 GB, 50 GB | 2 CPU, 4 GB, 200 GB | 4 CPU, 8 GB, distributed |
| **Total host** | 4 CPU, 8 GB | 16+ CPU, 40+ GB | 32+ CPU, 80+ GB |

> These are starting points. Actual sizing depends on: conversation length, knowledge base size, concurrent sessions, and LLM call frequency. Monitor [agent health](../how-to/monitor-opentelemetry.md#part-5--agent-health-monitoring) and scale up the bottleneck component first.

---

## Stateful vs. stateless scaling

| Component | Scaling model | Why |
|-----------|--------------|-----|
| **Hecate app** | Horizontal (add replicas) | Stateless — all state is in PostgreSQL/Redis |
| **Redis** | Vertical or primary-replica | Session state cache; loss is tolerable (sessions resume from the event log — caches are rebuildable) |
| **PostgreSQL** | Vertical + read replicas | Primary holds all writes; replicas serve reads |
| **Qdrant** | Cluster mode (sharding) | Vector indices are partitioned across nodes |
| **MinIO** | Distributed mode (erasure coding) | S3-compatible; scales by adding nodes |
| **Temporal** | Cluster (matching + history + worker) | Optional; only for durable distributed workflows |

**The app is always the easiest to scale** — it's stateless. PostgreSQL and Qdrant are the bottlenecks at scale; invest in managed services or clustering for those first.

---

## Decision matrix

| If you need... | Choose | Key requirement |
|----------------|--------|-----------------|
| Evaluate Hecate locally | Pattern 1 (Docker Compose) | `docker compose up -d` |
| Zero-downtime deploys without K8s | Pattern 2 (Blue-green) | Redis for session state |
| Horizontal autoscaling + HA | Pattern 3 (Kubernetes) | Managed PostgreSQL + Redis |
| Multi-region active-active | Pattern 3 per region + cross-region DB replication | Read-from-nearest, write-to-primary |
| Compliance / air-gapped | Pattern 1 or 3, no external API calls | Local models via Ollama; block egress |

---

## Further reading

- [Deploy to Production](../how-to/deploy-production.md) — step-by-step Docker Compose, blue-green, and Kubernetes instructions
- [Security Hardening](../how-to/security-hardening.md) — production security checklist
- [Health Checks](../operations/health-checks.md) — probe configuration for each topology
- [Backup and Restore](../operations/backup-restore.md) — backup scopes and PITR
- [Rollback Runbook](../operations/rollback.md) — four rollback paths for bad deploys
- [Monitor with OpenTelemetry](../how-to/monitor-opentelemetry.md) — tracing and metrics for each topology
