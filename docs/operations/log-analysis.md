# Log Analysis Runbook

How to read, search, and correlate Hecate's logs and traces during incident triage. This runbook covers the log sources that exist today, the event-style logging convention used in the application, and how to bridge logs to OpenTelemetry traces.

---

## Log sources

Hecate does not ship a custom JSON log formatter. Application logs go through Python's stdlib `logging` and are emitted to stdout by uvicorn. In a containerized deployment, stdout is captured by your container runtime or log driver — there is no log file inside the container to tail.

| Source | What it emits | Where it goes |
|--------|---------------|---------------|
| **uvicorn access log** | One line per HTTP request (method, path, status, duration) | stdout |
| **uvicorn error log** | Unhandled exceptions, startup/shutdown events | stdout |
| **Application loggers** (`logging.getLogger(__name__)`) | Event-style messages from every module | stdout |
| **Audit middleware** | Every HTTP request captured as a structured audit event | PostgreSQL (async batch writer), not stdout |
| **OpenTelemetry spans** | Distributed traces across LLM calls, tool executions, supersteps | DB export (optional) + console |

To follow logs from a running server:

```bash
# Foreground uvicorn — logs stream to the terminal directly
uvicorn hecate.main:app --reload

# Docker Compose
docker compose -f docker/docker-compose.yml logs -f hecate

# Kubernetes
kubectl logs -f deployment/hecate -n hecate
```

---

## Log level

There is no `LOG_LEVEL` setting in Hecate's configuration. The level is controlled by **uvicorn's `--log-level` flag**:

```bash
uvicorn hecate.main:app --log-level debug    # debug | info | warning | error | critical
```

For production, `info` (the default) is appropriate. Drop to `debug` only for active troubleshooting — it is verbose.

---

## The event-style logging convention

Application code uses an **event-name-first** style: the message is a short snake_case event identifier, and structured context is passed via the `extra` kwarg or lazy `%s` interpolation. This makes events greppable even in plain-text form.

Examples from the application source:

```python
logger.info("sigterm_received", extra={"signum": signum})
logger.warning("drain_timeout", extra={"active": ACTIVE_REQUESTS, "timeout": timeout})
logger.warning("readiness_db_check_failed", exc_info=exc)
logger.info("EventStore backend=%s", settings.EVENT_STORE_BACKEND)
logger.info("Plugin discovery: %d discovered, %d registered, %d errors", ...)
```

When triaging, search for the event name:

```bash
# Find all graceful-shutdown events
docker compose logs hecate | grep "sigterm_received"

# Find every readiness-check failure (with traceback)
docker compose logs hecate | grep "readiness_.*_check_failed"

# Find startup problems
docker compose logs hecate | grep -E "Plugin discovery|EventStore backend|Sandbox container pool"
```

---

## Key events to know

| Event name | Meaning | Severity |
|-----------|---------|----------|
| `sigterm_received` | Process received SIGTERM, beginning graceful drain | info |
| `drain_timeout` | In-flight requests did not finish within the drain window | warning |
| `readiness_db_check_failed` | Readiness probe could not reach PostgreSQL | warning |
| `readiness_redis_check_failed` | Readiness probe could not reach Redis | warning |
| `readiness_qdrant_check_failed` | Readiness probe could not reach Qdrant | warning |
| `Unhandled exception on <METHOD> <path>` | A request raised an unhandled exception (returned 500) | error |

---

## Correlating logs with traces

Because stdout logs are plain text, the reliable way to follow a single request across components is **OpenTelemetry tracing**, not log grep. Tracing is enabled by default (`TRACING_ENABLED = true`) and enriches every span with request-scoped headers.

### Request-scoped span attributes

The OTel attribute middleware (`_OTelAttributeMiddleware` in `main.py`) stamps the current span with these headers when present:

| Request header | Span attribute |
|----------------|----------------|
| `X-Agent-ID` | `agent.id` |
| `X-Session-ID` | `session.id` |
| `X-User-ID` | `user.id` |

If you send these headers from your client (or inject them at your ingress), every span for that request — the HTTP handler, the LLM call, the tool execution, the superstep — carries the same agent/session/user identifiers. This is how you reconstruct a multi-superstep workflow run.

### Trace export

By default, spans are exported to the console via `BatchSpanProcessor(ConsoleSpanExporter())`. For production, enable **DB export** so traces are queryable:

```dotenv
TRACING_ENABLED=true
TRACE_DB_EXPORT_ENABLED=true
```

With DB export on, the `HecateTraceSpanProcessor` writes spans to PostgreSQL, where you can query them via the `/api/traces` endpoint or directly. See the [Traces](../reference/rest-api.md#traces-and-monitoring) route group.

### Inspecting a trace

1. Capture the `trace_id` (from a response header, a client log, or by querying `/api/traces` for a given session/agent).
2. Query the trace — all spans with that `trace_id` form the full request timeline, including LLM latency, tool-execution time, and superstep barriers.
3. Cross-reference: the same `trace_id` lets you find the matching audit records and application-log timestamps.

---

## Metrics (when logs aren't enough)

For aggregate behavior — request rates, error rates, LLM-call latency distributions — use the Prometheus endpoint rather than logs:

```bash
curl http://localhost:8000/metrics
```

Scrape it with Prometheus and build dashboards. The full observability stack (Prometheus + Grafana + OTel collector) is covered in [Monitor with OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md).

---

## Audit trail (structured, queryable)

For compliance and forensic "who did what" analysis, the audit trail is the authoritative source — not stdout logs. The `AuditMiddleware` captures every HTTP request and writes it asynchronously to PostgreSQL via a batch writer (started in the lifespan handler). Query the audit data via the `/api/audit` endpoint.

Audit records include the agent, session, user, action, and timestamp — the same context the [guardrail hooks](../concepts/guardrails.md#from-hook-events-to-the-siem-pipeline) use. For security-event analysis (PII masking decisions, tool-access allow/deny, content-filter blocks), query the SIEM pipeline outputs instead.

---

## Troubleshooting

### Logs are too verbose in production

Run uvicorn at `info` (default), not `debug`. The application's own events are emitted at info/warning level; `debug` adds per-request SQLAlchemy and uvicorn internals.

### A request returned 500 but I see nothing in the logs

Unhandled exceptions are caught by the global exception handler and logged as `Unhandled exception on <METHOD> <path>` at error level. If you genuinely see nothing, the request may have been handled and returned a structured error by a router-level handler — check the HTTP status and the response body's `error.code` field.

### Trace export is not working

Confirm `TRACING_ENABLED=true`. If `TRACE_DB_EXPORT_ENABLED=true` but traces are not appearing in the DB, the background consumer for `HecateTraceSpanProcessor` may have failed to start — check startup logs for errors from the span processor.

---

## See also

- [Monitor with OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md) — the full observability deployment guide
- [Health Checks Runbook](health-checks.md) — liveness, readiness, and startup probes
- [Guardrails and Hooks](../concepts/guardrails.md#from-hook-events-to-the-siem-pipeline) — how security events flow to the SIEM pipeline
- [Environment Variables](../reference/env-vars.md) — `TRACING_ENABLED`, `TRACE_DB_EXPORT_ENABLED`, `SIEM_ENABLED`
