# How to Monitor with OpenTelemetry and Prometheus

> Make Hecate observable in production — distributed tracing via OpenTelemetry, Prometheus metrics, structured logs, Kubernetes-style health probes, and trace storage for post-hoc debugging.

Hecate ships a complete observability stack:

| Signal | Where it goes | What you get |
|--------|---------------|--------------|
| **Distributed tracing** | OpenTelemetry SDK → stdout + Postgres | Every HTTP request, LLM call, tool execution, Pregel superstep |
| **Metrics** | In-process collector → `/metrics` in Prometheus text format | Request count, error rate, latency, token usage, cost |
| **Structured logs** | Python `logging` → stdout (JSON via `StructuredLogger`) | Machine-parseable logs for ELK/Loki |
| **Health probes** | `/health/live`, `/health/ready`, `/health/startup` | Kubernetes-style probes for liveness/readiness/startup |

---

## Install the observability dependencies

The OpenTelemetry SDK and FastAPI auto-instrumentation are part of the `[observability]` extra group:

```bash
source .venv/bin/activate
uv pip install -e ".[observability]"
```

This installs:

- `opentelemetry-api` / `opentelemetry-sdk`
- `opentelemetry-instrumentation-fastapi`
- `aiosmtplib` (for alert email channels)

Without these, tracing still works through Hecate's built-in DB span processor, but OTel SDK export to external collectors is disabled.

---

## Part 1 — Tracing

### Step 1 — Enable tracing

Tracing is **on by default**. Verify with `.env`:

```dotenv
# .env
TRACING_ENABLED=true

# Also write traces to the Postgres traces table for UI/API inspection
TRACE_DB_EXPORT_ENABLED=true
TRACE_DB_QUEUE_MAX_SIZE=10000
TRACE_DB_FLUSH_INTERVAL=5
```

The DB export lets you query traces via the API:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/api/traces?limit=20"
```

Each trace returns spans organized as a tree (LLM call → tool call → tool result → LLM synthesis) with timing, input/output, and metadata for `session_id`, `agent_id`, and `user_id`.

### Step 2 — Export to an OTel collector

The Hecate process uses `ConsoleSpanExporter` by default (spans go to stdout in OTel format). To ship them to a real backend — Tempo, Jaeger, Honeycomb, Datadog — set the standard OpenTelemetry environment variables. Hecate reads them through the OTel SDK auto-instrumentation.

```dotenv
# .env
# OTLP endpoint (HTTP or gRPC)
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc

# Resource attributes — identify this service in your tracing backend
OTEL_SERVICE_NAME=hecate
OTEL_RESOURCE_ATTRIBUTES=service.namespace=hecate,deployment.environment=production
```

> **`OTEL_EXPORTER_OTLP_*` variables are honored by the OpenTelemetry SDK itself** — Hecate doesn't parse them. They work because the SDK registers them at startup.

Common backend configurations:

| Backend | Endpoint | Protocol |
|---------|----------|----------|
| **Tempo (Grafana)** | `http://tempo:4317` | gRPC |
| **Jaeger** | `http://jaeger:4317` | gRPC (with OTLP receiver enabled) |
| **Honeycomb** | `https://api.honeycomb.io` | HTTP (`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`) |
| **Datadog** | `http://datadog-agent:4317` | gRPC (with OTLP ingest enabled) |
| **New Relic** | `https://otlp.nr-data.net:4317` | gRPC |

### Step 3 — Hecate-specific span attributes

Hecate's `_OTelAttributeMiddleware` enriches every HTTP request span with:

| Header | Span attribute |
|--------|---------------|
| `X-Agent-ID` | `agent.id` |
| `X-Session-ID` | `session.id` |
| `X-User-ID` | `user.id` |

Set these headers in API calls to make traces filterable in your backend:

```bash
curl -X POST http://localhost:8000/v1/agents/a1b2c3d4-.../chat/completions \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "X-Agent-ID: a1b2c3d4-..." \
  -H "X-Session-ID: 550e8400-..." \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hi"}]}'
```

---

## Part 2 — Metrics

### Step 1 — Scrape the Prometheus endpoint

Hecate exposes Prometheus-format metrics at `/metrics` (no auth required):

```bash
curl http://localhost:8000/metrics
```

The endpoint returns metrics like:

```
# HELP requests_GET_/api/agents Total requests to /api/agents
# TYPE requests_GET_/api/agents counter
requests_GET_/api/agents 42
# HELP errors_GET_/api/agents Total errors to /api/agents
# TYPE errors_GET_/api/agents counter
errors_GET_/api/agents 1
# HELP token_usage_input_tokens_total Total input tokens
# TYPE token_usage_input_tokens_total counter
token_usage_input_tokens_total 15420
# HELP token_usage_output_tokens_total Total output tokens
# TYPE token_usage_output_tokens_total counter
token_usage_output_tokens_total 8921
```

### Step 2 — Configure Prometheus scraping

Add to `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: hecate
    metrics_path: /metrics
    static_configs:
      - targets: ['hecate:8000']
        labels:
          service: hecate
          env: production
```

### Step 3 — Long-term metrics storage (optional)

The default `MetricsStore` is in-process memory. For multi-replica deployments or long retention, switch to TimescaleDB:

```dotenv
# .env
METRICS_STORE_TYPE=timescale

# Required when METRICS_STORE_TYPE=timescale
TIMESCALE_DSN=postgresql+asyncpg://metrics:metrics@timescale:5432/metrics
```

> The `[mysql]` / `[scheduling]` extras may also be required depending on which storage backend you choose.

### Step 4 — Live monitoring dashboard (WebSocket)

Hecate pushes live metrics over WebSocket for the operator dashboard:

```
WS /api/ws/monitoring
```

The push interval is configurable:

```dotenv
METRICS_PUSH_INTERVAL=5  # seconds between WebSocket pushes
```

The buffer caps memory usage under load:

```dotenv
MAX_METRICS_BUFFER_SIZE=100000
```

---

## Part 3 — Structured logging

### What Hecate logs

Hecate emits Python `logging` records to stdout. The default formatter is plain text, but `StructuredLogger` (used internally for trace/audit events) emits JSON:

```json
{
  "timestamp": "2026-01-15T10:30:00.123Z",
  "level": "INFO",
  "message": "agent_chat completed",
  "session_id": "550e8400-...",
  "agent_id": "a1b2c3d4-...",
  "user_id": "00000000-...",
  "duration_ms": 1234
}
```

### Make ALL logs JSON

By default only `StructuredLogger` instances emit JSON. To convert the entire log stream to JSON, configure Python's root logger:

```python
# In a custom app entrypoint or plugin
import logging
import sys
# The dedicated StructuredLogger shim was retired in PR3a; trace context now
# rides on OTel span attributes via packages/hecate-ops/src/hecate_ops/span_adapter.py

root = logging.getLogger()
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(message)s"))
root.addHandler(handler)
root.setLevel(logging.INFO)
```

Or use a sidecar log formatter like [`python-json-logger`](https://github.com/madzak/python-json-logger) and configure via `LOGGING_CONFIG` in your deployment.

### Ship logs to your aggregator

Configure Docker to use the `json-file` driver with rotation, then have your log shipper tail:

```yaml
# docker-compose.yml
services:
  hecate:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "5"
        tag: "{{.Name}}"
```

For Kubernetes, the default container runtime captures stdout — `kubectl logs` and any sidecar (Fluent Bit, Vector) works out of the box.

---

## Part 4 — Health probes

Hecate exposes three Kubernetes-style health endpoints. None require authentication.

| Endpoint | Purpose | Use for |
|----------|---------|---------|
| `/health/live` | Process is alive (no dependency checks) | Liveness probe — restart pod on failure |
| `/health/ready` | DB + Redis + Qdrant all reachable | Readiness probe — remove from load balancer on failure |
| `/health/startup` | Application lifespan initialization complete | Startup probe — gate readiness/liveness until true |
| `/version` | Build metadata (commit, alembic head, Python version) | Debugging, deploy verification |

### Kubernetes example

```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  periodSeconds: 30
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  periodSeconds: 10
  failureThreshold: 3

startupProbe:
  httpGet:
    path: /health/startup
    port: 8000
  periodSeconds: 5
  failureThreshold: 30  # up to 2.5 minutes for first boot
```

### Docker Compose

```yaml
services:
  hecate:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/live"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
```

---

## Part 5 — Agent health monitoring

Beyond HTTP health, Hecate tracks per-agent health based on error rates and latency:

```dotenv
# .env
AGENT_HEALTH_ERROR_RATE_WARNING=0.05
AGENT_HEALTH_ERROR_RATE_CRITICAL=0.15
AGENT_HEALTH_LATENCY_WARNING_MS=10000
AGENT_HEALTH_LATENCY_CRITICAL_MS=30000
AGENT_HEALTH_SCORE_WEIGHTS={"error_rate": 0.5, "latency": 0.3, "activity": 0.2}
```

Query agent health via the API:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/api/agent-health?agent_id=<agent-uuid>"
```

Returns per-agent score (0.0–1.0) and trend data. Use this to drive alerting or auto-disable of misbehaving agents.

---

## Part 6 — Trace inspection API

For post-hoc debugging without an external tracing backend, query traces directly from the DB:

```bash
# List recent traces
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/api/traces?limit=10"

# Get a specific trace's span tree
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/api/traces/<trace-uuid>"
```

Filter by agent or session:

```bash
curl -H "Authorization: Bearer dev-key-change-me" \
  "http://localhost:8000/api/traces?agent_id=<agent-uuid>&limit=20"
```

Each trace contains the full span tree with timing, inputs, outputs, and metadata. This is invaluable for debugging "why did this chat return a weird response" — you can see every LLM call and tool invocation.

### OTel ↔ Execution Replay correlation

The traces you see in Tempo/Jaeger and the rows in the Execution Replay tab are the **same execution**, viewed from two different angles:

| What you see | Where it comes from |
|---|---|
| Replay timeline segments (`trace_id`) | `Event.trace_id` (correlated via OTel span context when configured, or generated per-invoke otherwise) |
| Replay `trace_enrichment` block (status, usage, `total_latency_ms`, `ttft_ms`, span name) | `TraceModel.metadata_["otel.trace_id"]` → span tree JOIN |
| LLM/tool spans with `ttft_ms`, token counts, `gen_ai.request.model`, `gen_ai.tool.name` | OTel semantic-conventions attributes on `llm:` / `tool:` spans (see [Engine Design §Execution Replay](../design/engine-design.md#recovery-flow-cache--tail-replay)) |

**Two practical implications:**

1. **OTel disabled = silent enrichment gap.** If `TRACING_ENABLED=False` (or OTel SDK is not configured), the replay timeline still works (the engine log is self-sufficient), but the `trace_enrichment` block will be empty. You will see the trace partition, channel writes, and tool/LLM event bodies — just not the latency and token numbers. This is by design: the replay UI degrades gracefully instead of hiding data when OTel is down.

2. **OTel trace_id = replay trace_id.** If you copy a `trace_id` from Tempo and paste it into the API as a search filter, you'll get the matching replay segment back. They share the same 32-hex correlation key.

To go from a Tempo trace to the replay UI in one click, the path is:

```
Tempo trace → copy trace_id → open /ops-center/conversations/<session_id>?trace=<hex> → Execution Replay tab
```

(linking from Tempo to the replay page is a thin client feature; the data model is already in place).

For the full feature walkthrough, see [Debug an agent run with execution replay](replay-debug-guide.md).

---

## Recommended Grafana dashboard panels

A starting point for Grafana panels using the Prometheus metrics:

| Panel | Query (PromQL) | Insight |
|-------|---------------|---------|
| Request rate | `rate(requests_total[5m])` | Traffic per endpoint |
| Error rate | `rate(errors_total[5m]) / rate(requests_total[5m])` | SLO compliance |
| p99 latency | `histogram_quantile(0.99, ...)` | Tail latency for chat completions |
| Token spend | `rate(token_usage_input_tokens_total[1h])` | Cost driver |
| Active sessions | `sessions_active` (if exposed) | Concurrent load |

For tracing, configure your Tempo/Jaeger datasource in Grafana and link traces to logs via `session_id` / `agent_id`.

---

## Troubleshooting

### `/metrics` returns empty or 404

The metrics endpoint is mounted at `/metrics` (not `/api/metrics`) and does not require authentication. If you get 404, check that the Hecate app started successfully — the endpoint is registered at app construction time.

### Traces don't appear in Tempo/Jaeger

Verify connectivity from Hecate to your collector:

```bash
docker compose exec hecate curl -fsS http://tempo:4317 -o /dev/null -w "%{http_code}\n"
```

If that fails, check:
- Network policy / firewall rules between containers
- The collector is configured to receive OTLP (`receivers.otlp.protocols.grpc.endpoint`)
- `OTEL_EXPORTER_OTLP_PROTOCOL` matches what the collector expects (`grpc` is the OTel default)

### Health probe fails intermittently

`/health/ready` checks the database, Redis (if configured), and Qdrant (if configured). If one flapped, the probe returns 503 and Kubernetes pulls the pod from rotation. Tune `failureThreshold` if your dependencies have known transient issues.

### Memory grows unbounded

`MAX_METRICS_BUFFER_SIZE` caps the in-process metric buffer. If the buffer fills faster than `METRICS_PUSH_INTERVAL` can drain it (no WebSocket consumers), oldest entries are dropped. For long-running deployments, switch `METRICS_STORE_TYPE=timescale` and drain via the DB.

### Span attributes are missing in the backend

The middleware enriches spans with `X-Agent-ID` / `X-Session-ID` / `X-User-ID` headers. If you call the API without these, the span has no agent/session context. Either send the headers or query traces via the Hecate trace API (which uses DB-level session/agent fields).

---

## See also

- **[Deploy to Production](deploy-production.md)** — reverse proxy, health checks for load balancers, secrets management.
- **[Environment Variables Reference](../reference/env-vars.md)** — every `TRACING_*`, `METRICS_*`, and `AGENT_HEALTH_*` variable.
- **[OpenTelemetry specification](https://opentelemetry.io/docs/specs/otel/)** — the protocol reference.
- **[Prometheus scrape config](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config)** — configuring the scraper.