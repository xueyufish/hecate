# Observability

An agent runtime is a long-lived, stateful, non-deterministic system: an LLM chooses a tool, the tool returns data, another LLM call decides what to do with it, a sub-agent picks up a sub-task, and somewhere in the chain a token budget gets exceeded. Without structured telemetry, debugging this is guesswork. With it, every execution is a tree you can inspect, every regression is a metric that moved, and every dollar spent is a row you can account for.

Hecate treats observability as **four signals** — traces, metrics, logs, and audit — emitted from the same execution loop and correlated by shared IDs. Understanding what each signal captures, where it goes, and how to consume it is what lets you operate agents in production rather than just run them locally.

> This article explains *what* Hecate observes and *why*. For the env vars, endpoint paths, and Prometheus/Tempo/Datadog wiring, see the [Monitor with OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md) guide.

---

## The four signals at a glance

| Signal | Captures | Storage | Consumer |
|--------|----------|---------|----------|
| **Traces** | Hierarchical span tree per request (LLM call → tool call → sub-agent) | Postgres `traces` table + OTel export to Tempo/Jaeger/Honeycomb/etc. | Trace inspection API, OTel backend UIs |
| **Metrics** | Numeric aggregates — request count, latency, token usage, cost | In-memory `MetricsCollector` (default) or TimescaleDB | `/metrics` (Prometheus), WebSocket dashboard |
| **Logs** | Discrete application events with structured fields | stdout (JSON via `StructuredLogger`) | ELK, Loki, Fluent Bit, `kubectl logs` |
| **Audit** | Security-relevant decisions — every `Pre/PostLLM/Tool` hook event | PostgreSQL via the [SIEM pipeline](guardrails.md#from-hook-events-to-the-siem-pipeline) | Webhook, Syslog, OCSF exporters |

All four are correlated by `trace_id` and `span_id`. A trace span's `attributes` carry `agent.id`, `session.id`, and `user.id`; the same IDs appear in structured log records and in the audit trail. This is what lets you pivot from a metric spike → to the traces behind it → to the specific LLM call's prompt and response.

---

## Traces

### What gets traced

Every agent execution produces a hierarchical trace following OpenTelemetry conventions:

```
Trace (single request / workflow run)
  └── Span (node execution)
       ├── LLM generation (model call with prompt/response)
       ├── Tool call (tool execution with args/result)
       └── Sub-span (child node, sub-agent, or nested graph)
```

The trace tree is built by `TracingService` (`services/observability/tracing.py`) and `OpsTraceManager` (`services/observability/trace_manager.py`). The OpenTelemetry SDK is auto-instrumented for FastAPI so every HTTP request enters a trace automatically; Hecate adds the agent-execution spans on top.

### Span enrichment

`_OTelAttributeMiddleware` enriches every HTTP request span with three attributes pulled from request headers:

| Header | Span attribute |
|--------|---------------|
| `X-Agent-ID` | `agent.id` |
| `X-Session-ID` | `session.id` |
| `X-User-ID` | `user.id` |

Setting these headers in API calls is what makes traces filterable by agent or session in your OTel backend.

### Persistence

`HecateTraceSpanProcessor` (`services/observability/span_processor.py`) writes spans to the Postgres `traces` table asynchronously via a bounded queue (size: `TRACE_DB_QUEUE_MAX_SIZE`). When the queue fills, new spans are dropped rather than blocking execution — observability never degrades the request path. The DB exporter is gated by `TRACE_DB_EXPORT_ENABLED` and flushes every `TRACE_DB_FLUSH_INTERVAL` seconds.

This dual-path design (Postgres for inspection + OTel export for backends) gives you both in-product trace browsing via `GET /api/traces/<uuid>` and integration with enterprise tracing stacks (Tempo, Jaeger, Honeycomb, Datadog, New Relic) via standard `OTEL_EXPORTER_OTLP_*` env vars.

---

## Metrics

### What gets collected

`MetricsCollector` (`services/observability/metrics.py`) tracks two primary metric groups:

| Group | Class | Examples |
|-------|-------|---------|
| **Request metrics** | `RequestMetrics` | Per-route request count, error count, latency |
| **Token metrics** | `TokenMetrics` | Input tokens, output tokens (per model, per agent) |

Metrics are exposed at **`/metrics`** in Prometheus text format (no auth), ready for scraping:

```
requests_GET_/api/agents 42
errors_GET_/api/agents 1
token_usage_input_tokens_total 15420
token_usage_output_tokens_total 8921
```

### Storage backends

`MonitoringService` (`services/observability/monitoring.py`) selects a `MetricsStore` based on `METRICS_STORE_TYPE`:

| Backend | Class | Use |
|---------|-------|-----|
| In-memory (default) | `MetricsStore` base | Single-replica dev/test |
| TimescaleDB | `TimescaleMetricsStore` (`services/observability/timescale_metrics_store.py`) | Multi-replica production, long retention |

For multi-replica deployments, switch to TimescaleDB via `METRICS_STORE_TYPE=timescale` plus `TIMESCALE_DSN`. The in-memory store does not replicate across processes.

### Live dashboard

Hecate pushes metrics to a WebSocket channel for the operator console at **`/api/ws/monitoring`**. Push cadence is `METRICS_PUSH_INTERVAL` (default 5s); the buffer caps at `MAX_METRICS_BUFFER_SIZE` to bound memory under load.

---

## Logs

Hecate emits Python `logging` records to stdout. Two formatters are in play:

- **Default formatter** — plain text, used by the root logger.
- **`StructuredLogger`** (`services/observability/structured_logger.py`) — JSON with consistent fields (`timestamp`, `level`, `message`, `trace_id`, `span_id`, `session_id`, `agent_id`, `user_id`, `duration_ms`).

Structured records correlate with traces via shared `trace_id` / `span_id` — click a span in your trace UI, grep the same ID in your log aggregator, and you see the same execution from two angles. For production, configure a JSON log shipper (Fluent Bit, Vector) or a sidecar formatter like `python-json-logger` to convert the entire log stream to JSON.

---

## Audit trail

Audit is **separate from logs** — logs capture operational events, audit captures security-relevant decisions. Every [guardrail hook](guardrails.md) execution produces a structured security event that flows through the SIEM pipeline:

```
Hook fires → SecurityEvent → SIEM Collector → exporters
                                              ├── Webhook (Slack, PagerDuty)
                                              ├── Syslog (RFC 5424)
                                              └── OCSF (Open Cybersecurity Schema)
```

The pipeline runs asynchronously from hook execution so enforcement latency is decoupled from export latency. Audit events persist as `ToolDecisionModel` (allow/deny/require_approval) and `SecurityFinding` (long-lived policy-violation findings, queryable via `GET /api/security/findings`).

This is what makes Hecate deployable in regulated environments — every prompt seen by the LLM, every tool invocation, every deny decision is captured with full context and exportable in standard security formats.

---

## Agent health

Beyond HTTP health probes (`/health/live`, `/health/ready`, `/health/startup`), Hecate tracks **per-agent health** computed from rolling error rate and latency:

```dotenv
AGENT_HEALTH_ERROR_RATE_WARNING=0.05      # 5% error rate → warning
AGENT_HEALTH_ERROR_RATE_CRITICAL=0.15     # 15% error rate → critical
AGENT_HEALTH_LATENCY_WARNING_MS=10000
AGENT_HEALTH_LATENCY_CRITICAL_MS=30000
AGENT_HEALTH_SCORE_WEIGHTS={"error_rate": 0.5, "latency": 0.3, "activity": 0.2}
```

Query per-agent health via `GET /api/agent-health?agent_id=<uuid>`. The score (0.0–1.0) and trend feed back into the [Ops Center](../design/ops-center-design.md) fleet view and can drive automated alerting or disabling of misbehaving agents.

---

## Choosing what to consume

| You want to... | Use |
|----------------|-----|
| Debug a single failed request | Trace inspection API: `GET /api/traces/<uuid>` |
| See the span tree of a specific agent's recent runs | `GET /api/traces?agent_id=<uuid>&limit=20` |
| Build a Grafana dashboard of platform health | Scrape `/metrics` with Prometheus |
| Investigate a slow tool call | Filter traces by `tool.name` in your OTel backend |
| Stream live metrics to a wall display | Subscribe to `/api/ws/monitoring` |
| Verify a deploy is healthy | Hit `/health/ready` (DB + Redis + Qdrant reachable) |
| Audit a security-sensitive tool invocation | Query the SIEM pipeline / `SecurityFinding` API |
| Track per-agent regressions over time | Per-agent health scores + [evaluation](evaluation.md) runs |

---

## Further reading

- [Monitor with OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md) — env vars, endpoint configuration, backend wiring
- [Health Checks runbook](../operations/health-checks.md) — probe semantics, `/version`, alerting integration
- [Log Analysis runbook](../operations/log-analysis.md) — log/trace correlation, audit trail queries
- [Guardrails and Hooks](guardrails.md) — what hooks emit security events into the audit trail
- [Agent Evaluation](evaluation.md) — how evaluation runs integrate with the testing center
- [Ops Center Design](../design/ops-center-design.md) — the unified operator console and full observability breakdown
- [ADR-028: Observability & Evaluation Enhancement](../design/adr/028-observability-evaluation-enhancement.md) — enhancement architecture decisions
- [Extension Points](../reference/extension-points.md) — the SPI surface for plugging in custom trace providers and metrics stores
