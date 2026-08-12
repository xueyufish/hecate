# Observability Architecture

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

Hecate's tracing implementation lives in `src/hecate/services/observability/`:

```
src/hecate/services/observability/
├── tracing.py             # OpenTelemetry setup helpers
├── trace_manager.py       # OpsTraceManager — async queue + background dispatch
├── trace_providers.py     # TraceProvider ABC + LangFuse/OTel implementations
├── span_processor.py      # OTel SpanProcessor that bridges OTel → Hecate TraceModel
└── structured_logger.py   # TraceContext-aware structured logger
```

### The bridge: OTel ↔ Hecate

Hecate is **OTel-native**: every span emitted in the engine goes through the OTel SDK, then a custom `SpanProcessor` (`src/hecate/services/observability/span_processor.py`) bridges OTel spans into Hecate's internal `TraceModel`. This gives you:

- **Local query**: Hecate can answer "show me all spans for session X" without an external backend
- **External export**: Simultaneously forward spans to LangFuse, OTel Collector, Jaeger, etc.
- **Span type inference**: Hecate classifies span names (e.g., `hecate.engine.pregel.superstep` → type=`superstep`) for better filtering in UIs

### TraceManager lifecycle

The `OpsTraceManager` (in `trace_manager.py`) runs as a background async worker:

```python
class OpsTraceManager:
    async def start(self) -> None: ...        # Start background worker
    async def stop(self) -> None: ...         # Graceful shutdown, flush queue
    
    async def on_trace_start(self, trace_data: dict) -> None: ...
    async def on_span_start(self, span_data: dict) -> None: ...
    async def on_span_end(self, span_data: dict) -> None: ...
    async def flush(self) -> None: ...        # Force flush (e.g., before shutdown)
```

Internally:

1. Spans arrive via `on_span_*` callbacks
2. They're queued in an async queue (default capacity 10,000)
3. A background worker consumes the queue and dispatches to each registered `TraceProvider`
4. Each provider forwards to its backend (LangFuse, OTel Collector, etc.)

The queue prevents slow providers from blocking the engine. If a provider fails, the span is logged but doesn't fail the request.

### TraceProvider ABC

```python
# src/hecate/services/observability/trace_providers.py
class TraceProvider(ABC):
    @abstractmethod
    async def on_trace_start(self, data: dict[str, Any]) -> None: ...
    
    @abstractmethod
    async def on_span_start(self, data: dict[str, Any]) -> None: ...
    
    @abstractmethod
    async def on_span_end(self, data: dict[str, Any]) -> None: ...
```

Hecate ships implementations for:

- **OpenTelemetry Collector** (`OTelTraceProvider`) — OTel-native export to any OTel-compatible backend
- **LangFuse** (`LangFuseTraceProvider`) — popular LLM observability platform
- **No-op** (`NullTraceProvider`) — drops all traces; useful for tests

Third-party providers can be added via plugin (see [Extension Architecture](extension-architecture.md)).

### Span hierarchy in Hecate

Each chat completion produces a span tree:

```
Trace (root)
├── Span: hecate.api.chat_completions        (HTTP entry)
│   ├── Span: hecate.auth.authenticate        (AuthN/AuthZ)
│   ├── Span: hecate.engine.pregel.invoke    (Engine entry)
│   │   ├── Span: hecate.engine.superstep.1   (Pregel superstep 1)
│   │   │   ├── Span: hecate.engine.llm_invoke
│   │   │   ├── Span: hecate.engine.tool_use
│   │   │   └── Span: hecate.guardrail.pre_llm
│   │   ├── Span: hecate.engine.superstep.2   (Pregel superstep 2)
│   │   └── Span: hecate.engine.checkpoint.save
│   └── Span: hecate.api.serialize_response
```

This hierarchy lets you answer:
- "What's the slowest part of an agent run?" → look at child span durations
- "Did the guardrail block this request?" → check for `guardrail.block` events
- "Which LLM call used the most tokens?" → look at `llm_invoke.token_count` attribute

---

## Metric architecture

Hecate's metrics implementation is in `src/hecate/services/observability/`:

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

The exact list is in `src/hecate/services/observability/metrics.py`.

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

Hecate uses Python's `logging` module with a structured-logging shim:

- `src/hecate/services/observability/structured_logger.py` — adds TraceContext (current trace_id, span_id) to every log line automatically
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

- `src/hecate/services/audit/` — write pipeline
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
# src/hecate/services/audit/policy.py
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

SIEM integration (`siem-security-pipeline`) ships events to Splunk / Datadog / Elastic in real-time over webhook. See [Security Architecture](security-architecture.md#siem-integration).

---

## Agent health monitoring

Hecate continuously evaluates per-agent health. The classification function is in `src/hecate/services/ops_center/agent_health.py`:

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

`src/hecate/services/ops_center/` provides four analytics:

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

- `src/hecate/services/observability/trace_manager.py` — OpsTraceManager
- `src/hecate/services/observability/trace_providers.py` — TraceProvider ABC
- `src/hecate/services/observability/span_processor.py` — OTel SpanProcessor bridge
- `src/hecate/services/observability/tracing.py` — OTel setup
- `src/hecate/services/observability/metrics.py` — Prometheus collector + dataclasses
- `src/hecate/services/observability/monitoring.py` — MonitoringService + MetricsStore
- `src/hecate/services/observability/timescale_metrics_store.py` — TimescaleDB store
- `src/hecate/services/observability/structured_logger.py` — structured logger
- `src/hecate/services/ops_center/agent_health.py` — health classification
- `src/hecate/services/ops_center/conversation_analytics.py` — analytics
- `src/hecate/services/ops_center/conversation_quality_scorer.py` — quality scorer
- `src/hecate/services/ops_center/conversation_cluster_manager.py` — clustering
- `src/hecate/services/audit/writer.py` — AuditBatchWriter
- `src/hecate/services/audit/store.py` — AuditStore
- `src/hecate/services/audit/policy.py` — detection rules
- `src/hecate/services/audit/archiver.py` — archival
- `src/hecate/services/event_state/postgres_store.py` — OTel integration

## Related documents

- [ADR-021: Ops Center Architecture](adr/021-ops-center-architecture.md) — why Ops Center is its own module
- [ADR-028: Observability & Evaluation Enhancement](adr/028-observability-evaluation-enhancement.md) — recent observability additions
- [How-to: Monitor with OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md) — operational recipe
- [Ops Center Design](ops-center-design.md) — higher-level context
- [Security Architecture](security-architecture.md) — audit + SIEM in security context
- Observability Architecture — current design