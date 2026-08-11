# Health Checks Runbook

How to verify that a Hecate instance is alive, ready to serve traffic, and fully started. Use these endpoints for container probes, load-balancer health checks, and incident triage.

All endpoints are **unauthenticated** and return JSON unless noted. They are defined directly on the application in `src/hecate/main.py`.

---

## The three Kubernetes-style probes

Hecate exposes separate liveness, readiness, and startup probes so an orchestrator can distinguish "the process is up" from "the process can serve requests" from "the process has finished booting".

| Probe | Path | Checks | Returns 503 when |
|-------|------|--------|------------------|
| **Liveness** | `GET /health/live` | Process is alive (no external checks) | Never (200 if the process responds at all) |
| **Readiness** | `GET /health/ready` | Not draining + DB + Redis (if configured) + Qdrant (if configured) | Any check fails, or the instance is draining |
| **Startup** | `GET /health/startup` | Lifespan initialization has completed | Still booting (seeding tools, warming pools, starting background services) |

### Liveness — `GET /health/live`

```bash
curl http://localhost:8000/health/live
```

```json
{"status": "alive"}
```

Use this for the **liveness probe** in Kubernetes / Docker. It only confirms the event loop is responsive — it does not check dependencies, so a flapping database will not get the pod killed.

### Readiness — `GET /health/ready`

```bash
curl http://localhost:8000/health/ready
```

When healthy:

```json
{"status": "ready", "checks": {"draining": true, "database": true, "redis": true, "qdrant": true}}
```

When a dependency is down, the response is **HTTP 503** with the list of failed checks:

```json
{"status": "not_ready", "checks": {"draining": true, "database": false, "redis": true, "qdrant": true}, "failed": ["database"]}
```

The readiness probe runs real checks against dependencies:

- **`draining`** — the `SHOULD_ACCEPT_TRAFFIC` flag. After the process receives `SIGTERM`, this flips to `false` so the load balancer stops sending new requests while in-flight work drains (up to 30 s).
- **`database`** — executes `SELECT 1` against the application's session factory.
- **`redis`** — sends `PING`, but only if the session-state store is configured to use Redis. Skipped (returns `true`) when Redis is not in use.
- **`qdrant`** — calls `get_collections()`, but only if a Qdrant client is configured. Skipped when Qdrant is not in use.

Use this for the **readiness probe** and for load-balancer health checks. A 503 means the instance should be pulled from rotation.

### Startup — `GET /health/startup`

```bash
curl http://localhost:8000/health/startup
```

```json
{"status": "started", "startup_complete": true}
```

During boot (seeding built-in tools, registering plugins, warming the sandbox pool, starting the audit writer and SIEM collector), the response is **HTTP 503**:

```json
{"status": "starting", "startup_complete": false}
```

Use this for the **startup probe** so an orchestrator does not route traffic to an instance whose lifespan handler has not finished.

---

## Build info — `GET /version`

```bash
curl http://localhost:8000/version
```

```json
{
  "version": "0.1.0",
  "commit": "f0ceed6",
  "alembic_head": "3da681b...",
  "python": "3.12.4",
  "build_date": "2026-08-11T00:00:00Z"
}
```

Useful for confirming which build and migration head a running instance is on. `commit` and `build_date` come from the `GIT_COMMIT` and `BUILD_DATE` environment variables (set by your CI); `alembic_head` is read from `alembic.ini` at request time.

---

## Prometheus metrics — `GET /metrics`

```bash
curl http://localhost:8000/metrics
```

Returns metrics in **Prometheus text exposition format** (`text/plain`). The payload is produced by `MetricsCollector.export_prometheus()`. Scrape this endpoint with Prometheus and use it for dashboards and alerting. For the full observability stack (tracing, metrics, logs), see [Monitor with OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md).

---

## Recommended probe configuration

### Kubernetes

```yaml
livenessProbe:
  httpGet: { path: /health/live, port: 8000 }
  periodSeconds: 10
  failureThreshold: 3
readinessProbe:
  httpGet: { path: /health/ready, port: 8000 }
  periodSeconds: 5
  failureThreshold: 2
startupProbe:
  httpGet: { path: /health/startup, port: 8000 }
  periodSeconds: 10
  failureThreshold: 30   # allow up to 5 minutes for first boot
```

### Docker Compose healthcheck

```yaml
services:
  hecate:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health/ready"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 60s
```

### Load balancer

Point the backend health check at `/health/ready`. A 200 means the instance should receive traffic; a 503 means route around it. Do not use `/health/live` for load-balancer checks — it will keep sending traffic to an instance whose database is down.

---

## Graceful shutdown

On `SIGTERM`, the process:

1. Flips `SHOULD_ACCEPT_TRAFFIC = false` → `/health/ready` starts returning 503 (load balancer drains).
2. Waits up to 30 s (configurable via `shutdown_drain_timeout`) for in-flight requests to finish.
3. Stops the sandbox pool, SIEM collector, tool-decision service, audit writer, and monitoring service.
4. Disposes the database connection pool.

This is why the readiness probe checks the `draining` flag — it lets you drain cleanly before the process exits.

---

## Troubleshooting

### `/health/ready` returns 503 with `failed: ["database"]`

The application cannot reach PostgreSQL. Check that the `postgres` container is healthy and that `DATABASE_URL` points at it. See [Quickstart troubleshooting](../getting-started/quickstart.md#troubleshooting).

### `/health/startup` stays at 503 for a long time

The lifespan handler is stuck — most often on `seed_builtin_tools` or plugin discovery. Check the application logs for the exception that is blocking startup. A slow first boot is normal on cold infrastructure; a boot that never completes indicates a blocking error.

### `/health/ready` returns 200 but chat requests fail

Readiness only checks DB, Redis, and Qdrant connectivity — it does not verify LLM provider keys. A 200 readiness response means the *platform* is up, not that every *provider* is reachable. Check `.env` for the relevant provider key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.).

---

## See also

- [Monitor with OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md) — tracing, metrics, and structured logging
- [Rollback Runbook](rollback.md) — what to do when a deployment breaks
- [Environment Variables](../reference/env-vars.md) — `TRACING_ENABLED`, `SANDBOX_POOL_ENABLED`, and other operational settings
