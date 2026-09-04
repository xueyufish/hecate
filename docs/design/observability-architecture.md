# Observability Architecture

> **P3 close-out note (2026-08-22)**: The Ops Center console shipped its first wave on 2026-08-22. Specifically:
> - **8.9 Unified Dashboard** ✅ Shipped P3 (per [feature-catalog.md](../../features/feature-catalog.md) § Ops Center)
> - **8.9a Health Monitoring** ✅ Shipped P3
> - **8.9b Conversation Analytics** ✅ Shipped P3
> - **8.9c Tool Execution Analytics** ✅ Shipped P3 (this P3 close-out item was not in some prior roadmap drafts; confirm against catalog line 477)
> - **8.20 Execution Replay** ✅ Phase 1 Shipped P3 ([ADR-030](adr/030-event-sourced-execution-state.md) Log-as-Truth substrate)
>
> All Wave-1 items above are documented inline below. **Defer to P4**: 7.2b-e / 7.3 / 7.4 / 7.4a / 7.5 Evaluation Suite (full), 8.10 CI/CD Eval Gating, 8.12 Agent Catalog Governance.

---

Deep-dive design document for Hecate's observability stack: traces, metrics, logs, and audit. For the operational recipe, see [Configure OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md). For the broader Ops Center context, see [Ops Center Design](ops-center-design.md).

This document is for **operators** and **SREs** deploying Hecate in production — what to monitor, how to integrate with your stack, and what the four signals (trace / metric / log / audit) actually mean in Hecate.

---

## The four signals

Hecate produces four distinct observability signals. They are not interchangeable — each answers a different question:

| Signal | Answers | Storage | Cardinality | Retention |
|---|---|---|---|---|
| **Trace** | "What happened in this specific request, and where did it slow down?" | TimescaleDB / OpenTelemetry Collector / LangFuse | High (per-request spans) | 7-30 days |
| **Metric** | "How many / how fast / how much, aggregated over time?" | Prometheus / TimescaleDB | Low (counters, gauges, histograms) | 90+ days |
| **Log** | "What happened, in human-readable form, with full context?" | Loki / Elasticsearch / stdout | Medium | 7-30 days |
| **Audit** | "Who did what, when, from where, to what resource?" | Postgres `audit_logs` + SIEM | High (every action) | 1-7 years (compliance) |

Hecate's architecture separates these by **purpose** and **storage backend** so each can be tuned independently for cost, retention, and access pattern.

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Hecate Engine                                 │
│                                                                     │
│   TraceManager  ──on_trace_start──┐                                │
│   MetricsStore  ──increment──────┤                                │
│   StructuredLog ──emit────────────┤                                │
│   AuditWriter   ──batch───────────┤                                │
└─────────────────────────┬─────────┴───────────────────────────────┘
                          │
       ┌──────────────────┼──────────────────┬──────────────────┐
       ▼                  ▼                  ▼                  ▼
  ┌─────────┐       ┌─────────┐        ┌──────────┐       ┌────────────┐
  │ Trace   │       │ Metric  │        │   Log    │       │   Audit    │
  │ backend │       │ backend │        │ backend  │       │  backend   │
  │         │       │         │        │          │       │            │
  │ OTel    │       │Prometheus│       │  Loki /  │       │ Postgres + │
  │ LangFuse│       │Timescale│        │  stdout  │       │ MinIO/S3 + │
  │ etc.    │       │         │        │          │       │   SIEM     │
  └─────────┘       └─────────┘        └──────────┘       └────────────┘
```

---

## Trace architecture

Hecate's tracing pipeline lives in `packages/hecate-ops/src/hecate_ops/`:

```
packages/hecate-ops/src/hecate_ops/
├── otel_setup.py          # configure_tracing() — OTel SDK setup, OTLP exporter
├── span_processor.py      # OTel SpanProcessor that bridges OTel → Hecate TraceModel
├── span_adapter.py        # create_otel_span / end_otel_span + span registry
├── metrics.py             # Prometheus collector + dataclasses
├── metrics_storage.py     # metrics persistence
├── monitoring.py          # MonitoringService + MetricsStore
└── timescale_metrics_store.py  # TimescaleDB store
```

The earlier in-repo trace-manager layer (`OpsTraceManager` / `TraceProvider`
ABC / structured logger) was retired in PR3a — spans now flow through the
OTel SDK directly, exported via OTLP (or any backend an OTLP exporter can
reach, e.g. LangFuse, Jaeger, an OTel Collector).

### The bridge: OTel ↔ Hecate

Hecate is **OTel-native**: every span emitted in the engine goes through the OTel SDK, then a custom `SpanProcessor` (`packages/hecate-ops/src/hecate_ops/span_processor.py`) bridges OTel spans into Hecate's internal `TraceModel`. This gives you:

- **Local query**: Hecate can answer "show me all spans for session X" without an external backend
- **External export**: Simultaneously forward spans to LangFuse, OTel Collector, Jaeger, etc.
- **Span type inference**: Hecate classifies span names (e.g., `hecate.runtime.pregel.superstep` → type=`superstep`) for better filtering in UIs

### Span pipeline lifecycle

The retired `OpsTraceManager` queue/dispatch layer is replaced by the OTel
SDK's own `TracerProvider` + `BatchSpanProcessor` (wired in
`packages/hecate-ops/src/hecate_ops/otel_setup.py::configure_tracing`):

1. Engine and API code create spans via the OTel API (`create_otel_span` /
   `end_otel_span` in `span_adapter.py` wrap a registry keyed by span id)
2. The SDK's batch processor buffers spans and exports them over OTLP
3. `span_processor.py` simultaneously bridges each span into Hecate's
   internal `TraceModel` for local query

The batch processor prevents slow exporters from blocking the engine. If an
export fails, the span is dropped by the SDK without failing the request.

### Backends

Spans export over OTLP to any compatible backend:

- **OTel Collector / Jaeger / Tempo** — native OTLP destinations
- **LangFuse** — popular LLM observability platform, reached via OTLP
- **No export** — when tracing is disabled, `configure_tracing` returns
  `None` and spans become no-ops

New destinations are added at the exporter level (OTLP endpoint config), not
via in-process provider plugins.

### Span hierarchy in Hecate

Each chat completion produces a span tree:

```
Trace (root)
├── Span: hecate.api.chat_completions        (HTTP entry)
│   ├── Span: hecate.auth.authenticate        (AuthN/AuthZ)
│   ├── Span: hecate.runtime.pregel.invoke    (Engine entry)
│   │   ├── Span: hecate.runtime.superstep.1   (Pregel superstep 1)
│   │   │   ├── Span: hecate.runtime.llm_invoke
│   │   │   ├── Span: hecate.runtime.tool_use
│   │   │   └── Span: hecate.guardrail.pre_llm
│   │   ├── Span: hecate.runtime.superstep.2   (Pregel superstep 2)
│   │   └── Span: hecate.runtime.checkpoint.save
│   └── Span: hecate.api.serialize_response
```

This hierarchy lets you answer:
- "What's the slowest part of an agent run?" → look at child span durations
- "Did the guardrail block this request?" → check for `guardrail.block` events
- "Which LLM call used the most tokens?" → look at `llm_invoke.token_count` attribute

---

## Metric architecture

Hecate's metrics implementation is in `packages/hecate-ops/src/hecate_ops/`:

- `metrics.py` — Prometheus-compatible collector
- `monitoring.py` — MonitoringService with snapshots
- `timescale_metrics_store.py` — TimescaleDB-backed production store

### Metric types

Hecate emits the four standard Prometheus metric types:

| Type | Example | Use |
|---|---|---|
| **Counter** | `hecate_llm_tokens_total{provider, model}` | Cumulative counts (always increases) |
| **Gauge** | `hecate_active_sessions` | Point-in-time values (goes up and down) |
| **Histogram** | `hecate_request_duration_seconds{endpoint}` | Distribution (compute p50, p95, p99) |
| **Summary** | `hecate_guardrail_block_rate` | Pre-aggregated stats |

### Built-in metrics

Hecate emits ~30 standard metrics out of the box:

```
# Request metrics
hecate_requests_total{endpoint, method, status_code}
hecate_request_duration_seconds{endpoint, method}

# LLM metrics
hecate_llm_requests_total{provider, model, status}
hecate_llm_tokens_total{provider, model, direction}  # direction=input|output
hecate_llm_cost_dollars_total{provider, model}
hecate_llm_duration_seconds{provider, model}

# Agent metrics
hecate_agent_invocations_total{agent_id, status}
hecate_agent_session_duration_seconds{agent_id}
hecate_agent_tool_calls_total{agent_id, tool_name}

# Engine metrics
hecate_engine_supersteps_total{agent_id}
hecate_engine_checkpoint_size_bytes{agent_id}
hecate_engine_active_sessions

# Infrastructure metrics
hecate_db_connections_active
hecate_db_query_duration_seconds{operation}
hecate_redis_connections_active

# Custom metric
hecate_guardrail_blocks_total{guardrail_type, agent_id}
```

The exact list is in `packages/hecate-ops/src/hecate_ops/metrics.py`.

### MetricsStore ABC

```python
# Implied from monitoring.py create_metrics_store()
def create_metrics_store(...) -> MetricsStore: ...
```

Two implementations:

| Implementation | Use case |
|---|---|
| `InMemoryMetricsStore` | Testing, single-node dev |
| `TimescaleMetricsStore` | Production (uses TimescaleDB hypertables for time-window aggregation) |

The TimescaleDB store uses **hypertables** for automatic time-based partitioning. Aggregate queries (e.g., "p95 latency over the last 24h") use TimescaleDB's continuous aggregates for fast retrieval.

### MonitoringService

The `MonitoringService` (in `monitoring.py`) snapshots current metric values and exposes them via the Ops Center API:

```
GET /api/ops/snapshot → metrics_snapshot (current values, last 5min aggregates, top-N slow requests)
```

This is what powers the dashboard's "current state" view.

---

## Log architecture

Hecate uses Python's `logging` module with JSON formatting:

- Log records carry trace context (current trace_id, span_id) via the OTel span attributes; the former dedicated structured-logger shim was retired in PR3a
- Logs are emitted as JSON to stdout by default
- Container runtimes (Docker, Kubernetes) collect stdout; aggregators (Loki, ELK, Datadog) parse JSON

### Log levels and what they mean

| Level | When | Examples |
|---|---|---|
| `DEBUG` | Engine internals | `superstep state transition`, `channel write` |
| `INFO` | Lifecycle events | `agent session started`, `plugin enabled`, `checkpoint saved` |
| `WARNING` | Recoverable issues | `LLM rate limit retry`, `tool timeout, retrying` |
| `ERROR` | Failures (request fails) | `LLM call failed after 3 retries`, `tool execution failed` |
| `CRITICAL` | System-level failure | `database connection lost`, `engine startup failed` |

### Sensitive data handling

Hecate's structured logger redacts:

- API keys (matched against `HECATE_API_KEYS_*` patterns)
- Bearer tokens (anything starting with `Bearer`)
- PII marked by guardrail hooks (only the redacted form is logged, never the original)

Sensitive fields are replaced with `[REDACTED]` before emission. This is enforced at the logger level — no app code can opt out.

---

## Audit architecture

Audit logs are **separate** from application logs because they have different requirements:

| | Application log | Audit log |
|---|---|---|
| **Purpose** | Debugging | Compliance / forensics |
| **Audience** | Engineers | Security, legal, auditors |
| **Retention** | 7-30 days | 1-7 years |
| **Mutability** | Overwritable | Append-only (write-once) |
| **Volume** | High | Medium |
| **Query pattern** | Recent + grep | Long-range + structured |

Audit logs live in:

- `src/hecate/ops/audit/` — write pipeline
  - `writer.py` — `AuditBatchWriter` (queue + batch(50) / 2s flush)
  - `store.py` — `AuditStore` (Postgres backend)
  - `policy.py` — detection rules (BulkDelete / OffHours / UnusualIP)
  - `archiver.py` — moves old logs to MinIO/S3
- `src/hecate/models/audit.py` — ORM models

### What gets audited

Every mutating action through the Management API:

| Event | Captured fields |
|---|---|
| `agent.created` | actor_id, agent_id, workspace_id, timestamp, IP |
| `agent.updated` | actor_id, agent_id, diff, timestamp |
| `agent.deleted` | actor_id, agent_id (soft delete flag) |
| `workflow.imported` | actor_id, workflow_id, size_bytes |
| `kb.document.uploaded` | actor_id, kb_id, document_id, size_bytes |
| `auth.login` | actor_id, auth_method, IP, user_agent |
| `auth.failed` | attempted_user, IP, reason |
| `tool.executed` | actor_id, agent_id, tool_name, args_hash (no raw args) |
| `guardrail.blocked` | actor_id, agent_id, guardrail_type, reason |

### Audit detection rules

The audit pipeline runs **detection rules** on each event before persisting:

```python
# src/hecate/ops/audit/policy.py
class BulkDeleteRule(DetectionRule):
    """Flags bulk delete operations within a time window."""

class OffHoursRule(DetectionRule):
    """Flags sensitive operations outside business hours."""

class UnusualIPRule(DetectionRule):
    """Flags actions from unrecognized IP addresses."""
```

When a rule matches, the audit event is tagged with a `SecurityFinding` and forwarded to the SIEM pipeline (`2026-07-26 siem-security-pipeline`).

### Archival and SIEM

After 30 days in Postgres, audit logs are moved to MinIO/S3 by `archiver.py`. The on-disk format is gzip-compressed JSONL with a daily partition.

SIEM integration (`siem-security-pipeline`) ships events to Splunk / Datadog / Elastic in real-time over webhook. See [Security Architecture](security-architecture.md#siem-export-pipeline-87).

---

## Agent health monitoring

Hecate continuously evaluates per-agent health. The classification function is in `src/hecate/ops/ops_center/agent_health.py`:

```python
def _classify_health_status(error_rate: float, p95_latency_ms: float) -> str:
    """Classify agent health: healthy / warning / critical.
    
    Thresholds (configurable via settings):
      AGENT_HEALTH_ERROR_RATE_WARNING     (default: 0.05 = 5%)
      AGENT_HEALTH_ERROR_RATE_CRITICAL    (default: 0.20 = 20%)
      AGENT_HEALTH_LATENCY_WARNING_MS     (default: 5000ms)
      AGENT_HEALTH_LATENCY_CRITICAL_MS    (default: 30000ms)
    """
```

A fleet overview view is available at:

```
GET /api/ops/fleet → list of agents with health status, last error, last success, error rate, p95 latency
```

---

## Conversation analytics

`src/hecate/ops/ops_center/` provides four analytics:

| Module | What it does |
|---|---|
| `conversation_analytics.py` | Aggregate stats per conversation: turn count, tool usage, citation count |
| `conversation_embedding.py` | Embed conversations for similarity search |
| `conversation_cluster_manager.py` | Cluster similar conversations (find common failure patterns) |
| `conversation_topic_matcher.py` | Tag conversations with topic labels |
| `conversation_quality_scorer.py` | LLM-as-judge quality scoring (faithfulness, helpfulness) |

Use these for:

- "Which conversations are most similar to this one that failed?" → clustering + similarity
- "What's the most common failure mode?" → cluster analysis
- "Has quality degraded since the last prompt change?" → quality scorer over time

---

## Operational deployment topologies

### Minimal (single node, dev/test)

```
Hecate ──▶ stdout (JSON logs)
       └─▶ In-memory metrics + traces (lost on restart)
```

Sufficient for local dev. No infrastructure needed.

### Standard (production single-region)

```
Hecate ─┬─▶ OTel Collector ─┬─▶ Jaeger / Tempo (traces)
        ├─▶ Prometheus push gateway (metrics)
        ├─▶ Loki / Fluent Bit (logs)
        └─▶ Postgres + MinIO/S3 (audit)
```

The OTel Collector is the single integration point — any OTel-compatible backend works (Jaeger, Tempo, Honeycomb, Datadog, etc.).

### Enterprise (multi-region, SIEM)

```
Hecate ─┬─▶ OTel Collector (per region) ─▶ central tracing backend
        ├─▶ Prometheus federation
        ├─▶ SIEM webhook (Splunk / Elastic / Datadog)
        └─▶ Compliance archive (S3 Glacier)
```

Audit logs are streamed to SIEM in real-time and archived to cold storage for compliance retention.

---

## Metrics naming conventions

When adding custom metrics (via plugins or custom code), follow these rules:

1. **Prefix**: `hecate_` (reserved for Hecate core) or `<your_plugin>_` (for plugins)
2. **Unit suffix**: `_seconds`, `_bytes`, `_total`, `_count`, `_dollars`, `_ratio`
3. **Labels**: use snake_case; avoid high-cardinality labels (e.g., don't use `user_id` as a label)
4. **Buckets** (for histograms): use the Prometheus default buckets for latency (`0.005, 0.01, 0.025, ..., 10`)

Example:
```python
# Good
metrics.counter("my_plugin_requests_total", labels={"plugin": "my_plugin", "status": "ok"})
metrics.histogram("my_plugin_request_duration_seconds", labels={"plugin": "my_plugin"}, buckets=(0.01, 0.1, 1, 10))

# Bad (high-cardinality label)
metrics.counter("my_plugin_requests_total", labels={"user_id": str(user_id)})
```

---

## Cost optimization

Observability is expensive at scale. Hecate's design reduces cost by:

1. **Sampling** — production deployments can configure head-based sampling (e.g., 10% of requests traced fully; 100% of errors)
2. **Tiered retention** — hot data in TimescaleDB (30 days), cold in MinIO/S3 (1+ year)
3. **Async queue** — span emission doesn't block request paths
4. **Provider failure isolation** — one slow provider doesn't cascade

Default settings are conservative; tune for your scale.

---

## Implementation references

- `packages/hecate-ops/src/hecate_ops/span_processor.py` — OTel SpanProcessor bridge
- `packages/hecate-ops/src/hecate_ops/span_adapter.py` — create/end span + registry
- `packages/hecate-ops/src/hecate_ops/otel_setup.py` — OTel setup
- `packages/hecate-ops/src/hecate_ops/metrics.py` — Prometheus collector + dataclasses
- `packages/hecate-ops/src/hecate_ops/monitoring.py` — MonitoringService + MetricsStore
- `packages/hecate-ops/src/hecate_ops/timescale_metrics_store.py` — TimescaleDB store
- `src/hecate/ops/ops_center/agent_health.py` — health classification
- `src/hecate/ops/ops_center/conversation_analytics.py` — analytics
- `src/hecate/ops/ops_center/conversation_quality_scorer.py` — quality scorer
- `src/hecate/ops/ops_center/conversation_cluster_manager.py` — clustering
- `src/hecate/ops/audit/writer.py` — AuditBatchWriter
- `src/hecate/ops/audit/store.py` — AuditStore
- `src/hecate/ops/audit/policy.py` — detection rules
- `src/hecate/ops/audit/archiver.py` — archival
- `src/hecate/studio/event_state/postgres_store.py` — OTel integration

## Related documents

- [ADR-021: Ops Center Architecture](adr/021-ops-center-architecture.md) — why Ops Center is its own module
- [ADR-028: Observability & Evaluation Enhancement](adr/028-observability-evaluation-enhancement.md) — recent observability additions
- [How-to: Monitor with OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md) — operational recipe
- [Ops Center Design](ops-center-design.md) — higher-level context
- [Security Architecture](security-architecture.md) — audit + SIEM in security context
- Observability Architecture — current design