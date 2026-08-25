# Reference Architectures

Architectural patterns for deploying Hecate at scale. This is the **"what to choose and why"** document. For concrete configs and step-by-step commands, see [Deployment Architectures Reference](../reference/deployment-architectures.md).

This document is for **architects and SREs** planning a deployment — choosing topology, sizing components, planning for failure, and matching deployment to operational capability.

---

## Three things to decide

A Hecate deployment is determined by three independent choices:

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. Application topology                                            │
│     Single host │ Blue-green │ Kubernetes │ Multi-region           │
│                                                                     │
│  2. Stateful component selection                                    │
│     Postgres vs SQLite                                              │
│     Qdrant vs Chroma vs pgvector                                     │
│     Redis vs in-memory session state                                │
│     MinIO vs S3 vs filesystem                                       │
│     Temporal vs in-engine checkpointing                             │
│                                                                     │
│  3. Operational model                                               │
│     Self-managed │ Managed services │ Hybrid │ Air-gapped          │
└─────────────────────────────────────────────────────────────────────┘
```

Each axis has trade-offs. The right combination depends on **scale**, **availability requirement**, **data residency**, and **team operational capability**.

---

## Application topology

### Decision tree

```
How many concurrent users?
│
├── < 20          → Single host (Pattern 1)
├── 20–200        → Blue-green (Pattern 2) or single-host K8s
├── 200–2000      → Kubernetes (Pattern 3)
└── > 2000        → Multi-region K8s (Pattern 3 + cross-region DB)

What HA do you need?
│
├── None (dev/test)           → Single host
├── App only (data stores ok) → Blue-green
└── Full                      → Kubernetes with managed DBs

What's your ops team?
│
├── 1–2 engineers              → Single host or managed cloud
├── 3–10 engineers             → Blue-green or K8s
└── 10+ engineers              → Full K8s with platform team
```

### Pattern comparison

| Pattern | App scaling | HA | Operational complexity | When to use |
|---|---|---|---|---|
| **Single host** | 1 replica | None | Trivial (1 docker compose) | Dev, eval, small team |
| **Blue-green** | 2 replicas | App only | Low (just 2 hosts) | Small production |
| **Kubernetes** | 3+ replicas | Full | High (K8s expertise) | Production at scale |
| **Multi-region** | 3+/region | Full + DR | Very high | Global SaaS, regulated |

The right starting point for **most teams** is **Blue-green** — it gives you zero-downtime deploys without the K8s complexity tax.

---

## Stateful component selection

### Database

| Engine | Pros | Cons | When to choose |
|---|---|---|---|
| **PostgreSQL 16** | Battle-tested, full SQL, pgvector extension, mature tooling | Operationally complex (backups, replication) | Production |
| **SQLite** | Zero ops, file-based | Single writer, no HA, no pgvector | Dev / test only |

**Rule**: use SQLite for dev/test, Postgres for production. Don't try to scale SQLite.

For PostgreSQL HA:

| Setup | RPO | RTO | Cost | Notes |
|---|---|---|---|---|
| Single primary | Last backup | Hours | Low | Acceptable for small production |
| Streaming replica (async) | Seconds | Minutes | Medium | Good default |
| Synchronous replica | Zero (per commit) | Minutes | Higher (latency) | Financial-grade only |
| Patroni / PgBouncer HA | Seconds | Seconds | Higher | Production-grade |

### Vector store

| Engine | Pros | Cons | When to choose |
|---|---|---|---|
| **Qdrant** | Purpose-built for vectors, hybrid search, scales to billions | Another service to operate | Production with > 100K vectors |
| **Chroma** | Simple, in-process option | Limited scalability, dev-focused | Dev / test only |
| **pgvector** | No extra service (PG extension), transactional with PG data | Limited ANN performance | Small production (≤ 1M vectors) |

**Rule**: Qdrant is the default. Use Chroma only for testing. Use pgvector only if you have a hard "no extra services" constraint.

### Session state cache

| Backend | Pros | Cons | When to choose |
|---|---|---|---|
| **In-memory** (`memory`) | Zero ops, fast | Lost on restart, single-instance only | Dev / test |
| **Redis** | Durable, multi-instance, mature | Extra service to operate | Production |
| **Redis Cluster** | Sharded + replicated | More ops complexity | Large production (>100K sessions) |

For multi-replica deployments, **session state MUST be in Redis** (or another shared store). Otherwise a session started on pod-1 dies when the request lands on pod-2.

### Object storage

| Backend | Pros | Cons | When to choose |
|---|---|---|---|
| **MinIO** (self-hosted) | S3-compatible, runs anywhere | Single-host = single point of failure | On-prem / air-gapped |
| **MinIO Distributed** | Erasure-coded, horizontally scalable | More complexity | Production on-prem |
| **S3 / GCS / Azure Blob** | Infinite scale, 11 nines durability | Vendor lock-in, network egress | Cloud production |
| **Local filesystem** | Zero ops | Not shared across instances | Single-host only |

Hecate's Storage SPI supports all four. Use MinIO Distributed or S3 for production.

### Durable execution engine

| Engine | Pros | Cons | When to choose |
|---|---|---|---|
| **Built-in checkpoints** (Postgres) | Zero extra services | Limited to Hecate execution | Default |
| **Temporal** | Distributed workflow orchestration, retries, signals | Heavy, another service | Multi-day workflows, saga patterns |

Most workloads don't need Temporal. Hecate's built-in checkpointing covers the common case.

---

## Scaling characteristics

### Bottleneck identification

Each component has a different bottleneck. **Find yours before scaling**:

| Component | First bottleneck | Signal |
|---|---|---|
| **Hecate app** | LLM API rate limit (not CPU) | `hecate_llm_requests_total{status="429"}` increasing |
| **PostgreSQL** | Write throughput / connection count | `pg_stat_activity.waiting > 5` |
| **Qdrant** | RAM for index | OOM errors / slow searches |
| **Redis** | Memory | `used_memory > maxmemory` evictions |
| **MinIO** | Disk I/O | High `s3_requests_5xx` rate |

### When to scale horizontally

| Component | Scale up (vertical) | Scale out (horizontal) |
|---|---|---|
| Hecate app | Cheap, do it first | Add replicas when CPU > 70% across N nodes |
| PostgreSQL | Pin to fast disk, more RAM | Read replicas; write sharding is hard |
| Qdrant | More RAM (index fits) | Cluster mode (sharding) at >10M vectors |
| Redis | More RAM | Cluster mode at >100K sessions |
| MinIO | Faster disks | Distributed mode (more nodes) |

**Rule**: scale UP first (cheaper, simpler), scale OUT only when vertical scale is exhausted.

### Horizontal pod autoscaling (K8s)

```yaml
# HPA example
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: hecate
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: hecate
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Pods
    pods:
      metric:
        name: hecate_active_sessions
      target:
        type: AverageValue
        averageValue: "100"
```

Scale on **active sessions** (not just CPU) — agent workloads are I/O bound on LLM calls, not CPU bound.

---

## Failure mode analysis

For each component, what's the impact when it fails?

| Failure | Impact | Recovery | Acceptable? |
|---|---|---|---|
| Hecate app (1 replica) | All requests fail | Restart pod | NO for production |
| Hecate app (N replicas) | Reduced capacity, N-1 serving | Load balancer removes bad pod | YES |
| Postgres primary | All writes fail | Promote replica (manual or Patroni) | NO — needs HA |
| Postgres replica | No impact (replica was read-only) | Provision new replica | YES |
| Qdrant node | Search latency increases | Cluster rebalances | YES |
| Redis | Sessions lost (resumable from event log) | Restart Redis (data lost) | TOLERABLE |
| MinIO | File uploads fail | Restart MinIO (data persistent on disk) | YES |
| LLM provider | All chat completions fail | Switch provider via fallback config | YES (built-in) |

**Key insight**: Hecate is **stateless** (state in Postgres/Redis). The app can always be replaced by a fresh replica. The stateful components (Postgres, Qdrant, MinIO) are what need HA.

### Recovery time objectives

| Component | RPO target | RTO target | Cost to achieve |
|---|---|---|---|
| Postgres | 5 min (streaming replica) | 1 min (auto-failover) | Medium |
| Qdrant | 1 hour (daily snapshot) | 1 hour (restore from snapshot) | Low |
| MinIO | Zero (synchronous replication) | 5 min (failover) | Medium |
| Redis | Acceptable data loss | 1 min (restart empty) | Low |
| App | Zero | <30s (k8s recreate pod) | Low |

See [Backup & Recovery Architecture](backup-recovery-architecture.md) for implementation details.

---

## Network architecture

### Internal vs external traffic

```
                       ┌─────────────────────┐
   Internet ──TLS──▶ │   Reverse Proxy      │  nginx, Caddy, ALB
                       │   TLS termination   │  Handles TLS, rate limits, WAF
                       └──────────┬───────────┘
                                  │ HTTP (cleartext, internal)
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
       ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
       │  Hecate     │     │  Hecate     │     │  Hecate     │
       │  pod-1      │     │  pod-2      │     │  pod-N      │
       └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
              │                   │                   │
              └───────────────────┼───────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
       ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
       │ PostgreSQL  │     │   Qdrant    │     │    Redis    │
       └─────────────┘     └─────────────┘     └─────────────┘
```

**Reverse proxy is mandatory** — TLS termination at the proxy, not at the Hecate pod. This avoids certificate management in the application and lets the proxy handle rate limiting and WAF.

### Service-to-service communication

All internal traffic should be on a private network:

- **Docker Compose**: default `hecate-internal` network, expose only port 80/443 to the host
- **Kubernetes**: NetworkPolicies restrict pod-to-pod traffic; service-to-service uses ClusterIP
- **Multi-region**: VPC peering or transit gateway between regions

### Egress

Hecate makes outbound calls to:

- LLM providers (OpenAI, Anthropic, DeepSeek, Qwen, GLM, etc.)
- MCP servers (configurable per agent)
- A2A agents (configurable per agent)
- OTel collectors / LangFuse (if configured)
- SIEM (if configured)

**For air-gapped deployments**, all of these must be replaced with internal equivalents:

| External | Air-gapped equivalent |
|---|---|
| OpenAI / Anthropic / etc. | Ollama / vLLM (local model server) |
| Cloud MCP servers | Local MCP servers (in-process or sidecar) |
| Cloud A2A agents | Internal Hecate instances |
| OTel Collector / LangFuse | Self-hosted OTel Collector + Jaeger/Tempo |
| Cloud SIEM | Self-hosted SIEM (Splunk / Elastic on-prem) |

Set `LLM_PROVIDER_AUDIT_EGRESS_ENABLED=false` in air-gapped mode to prevent any outbound calls.

---

## Operational model

### Self-managed

You operate everything: Hecate, Postgres, Qdrant, MinIO, Redis. Pros: full control, on-prem option. Cons: requires SRE expertise.

### Managed services (hybrid)

You operate Hecate, but cloud vendors operate the data layer:

| Component | Managed alternatives |
|---|---|
| PostgreSQL | AWS RDS, Cloud SQL, Aurora, Azure DB |
| Qdrant | Qdrant Cloud |
| Redis | ElastiCache, Memorystore |
| Object storage | S3, GCS, Azure Blob |
| LLM | OpenAI, Anthropic, etc. |

Pros: less ops burden. Cons: cloud lock-in, egress costs.

### Air-gapped

No internet egress. All components self-hosted. Common in finance, defense, healthcare.

Required:
- Local model server (Ollama, vLLM)
- Internal auth (LDAP, not OAuth)
- On-prem observability stack
- Manual backup process

---

## Sizing calculator

Approximate sizing based on workload:

### Inputs

| Input | Example |
|---|---|
| **Concurrent users** | 100 |
| **Average session length** | 20 messages |
| **Tokens per message** | 1500 (input + output combined) |
| **Daily active users** | 500 |
| **LLM provider** | OpenAI GPT-4o |
| **Vector store size** | 10M chunks |

### Outputs

| Component | Calculation | Recommended |
|---|---|---|
| **App replicas** | `concurrent_users × avg_session_length / avg_session_duration` | 3-5 |
| **Postgres storage** | `(daily_active_users × 100KB + vector_size × 4KB) × 30` | 200 GB |
| **Postgres RAM** | `working_set × 1.5` (typical 16 GB for 200 GB DB) | 16 GB |
| **Qdrant RAM** | `vector_size × 1.2KB` (with HNSW overhead) | 12 GB |
| **Redis RAM** | `concurrent_sessions × 50KB` | 5 GB |
| **Network egress (LLM)** | `daily_active_users × session_length × tokens_per_message` × $0.005/1k | $375/day |

This is approximate. Always load-test with your actual workload.

---

## Anti-patterns

Things to avoid:

| Anti-pattern | Why |
|---|---|
| **Single-host for production** | Single point of failure for every component |
| **SQLite in production** | No HA, no pgvector, single writer |
| **In-memory session state with multiple replicas** | Sessions die when request lands on different pod |
| **No backup** | Lose everything on disk failure |
| **No monitoring** | Blind to failures until user reports |
| **Managed Postgres with no connection pooler** | Connection exhaustion under load |
| **Single A2A agent as critical path** | No fallback if remote agent is down |
| **Hardcoded API keys** | Rotation breaks everything |

---

## Decision matrix summary

| Scenario | Recommended |
|---|---|
| **Solo developer exploring** | Single host + SQLite + Chroma |
| **Team < 50, internal tool** | Single host + Postgres + Qdrant + MinIO |
| **Team 50-500, production** | Blue-green + Postgres primary + replica + Redis |
| **500-5000 users, regulated** | K8s + managed Postgres + Redis + S3 + OTel |
| **5000+ users, global SaaS** | Multi-region K8s + cross-region DB + CDN |
| **Air-gapped, classified** | Self-hosted + Ollama + on-prem SIEM |

---

## References

- [Deployment Architectures Reference](../reference/deployment-architectures.md) — concrete patterns and sizing tables
- [Deploy to Production](../how-to/deploy-production.md) — step-by-step deployment commands
- [Security Hardening](../how-to/security-hardening.md) — production security checklist
- [Backup & Recovery Architecture](backup-recovery-architecture.md) — backup topology
- [Observability Architecture](observability-architecture.md) — what to monitor in each topology
- [Health Checks](../operations/health-checks.md) — probe configuration
- Multi-region active-active is **P5** (per [ADR-025 EF4](../adr/025-enterprise-foundation-enhancement.md) Multi-Region Data Sovereignty, roadmap 13.6 enhancement direction); region-pinned multi-region deployment with explicit data-residency controls ships as the 13.6 *enhancement* of EF4 (post-1.0 RC, before P5 freeze). Active-active cross-region replication is P5
- [ADR-018: Zero Trust Identity](adr/018-zero-trust-identity-architecture.md) — auth architecture