# How to Deploy to Production

> Run Hecate in production with Docker Compose, scale horizontally with Redis, deploy zero-downtime with blue-green, and operate backup/restore with PITR.

This guide assumes you have already completed the [Quickstart](../getting-started/quickstart.md) locally. Production is about three things: **reliability** (no single point of failure), **observability** (know what's happening), and **recoverability** (roll back or restore when things go wrong).

---

## Choose your deployment topology

| Topology | Best for | Complexity |
|----------|----------|------------|
| **Single-host Docker Compose** | Small teams, evaluation, ≤ 100 RPS | Low |
| **Blue-green Docker Compose** | Zero-downtime deploys, instant rollback | Medium |
| **Kubernetes** | High-scale multi-replica, multi-region | High |

Hecate does **not** ship a Helm chart — the Docker image is plain Python 3.12-slim, and you bring your own orchestration. See [Kubernetes deployment](#kubernetes-deployment) for guidelines.

---

## Single-host Docker Compose

The reference deployment is `docker/docker-compose.yml` — six services wired together:

```
postgres (primary database)
qdrant    (vector store)
minio     (S3-compatible object storage)
temporal  (durable workflow execution)
temporal-ui (workflow inspection UI)
hecate-migrate (one-shot DB migrations)
hecate    (the application)
```

### Step 1 — Production `.env`

Copy `.env.example` and change every default. **Never** ship default credentials.

```dotenv
# .env (production)

# --- Secrets (rotate via your secret manager) ---
HECATE_API_KEYS=                       # comma-separated; one per client
POSTGRES_PASSWORD=                     # strong random
MINIO_ACCESS_KEY=                      # strong random
MINIO_SECRET_KEY=                      # strong random
FERNET_KEY=                            # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
JWT_SECRET=                            # for SSO/JWT auth

# --- LLM provider keys ---
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# --- Database ---
DATABASE_URL=postgresql+asyncpg://hecate:${POSTGRES_PASSWORD}@postgres:5432/hecate

# --- Object storage ---
MINIO_URL=minio:9000
MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY}
MINIO_SECRET_KEY=${MINIO_SECRET_KEY}
MINIO_BUCKET=hecate

# --- Vector store ---
VECTOR_STORE_TYPE=qdrant
QDRANT_URL=http://qdrant:6333

# --- Production tuning ---
LLM_GUARD_ENABLED=true
RATE_LIMIT_RPM=120
TRACING_ENABLED=true

# --- Optional: search provider for web_search tool ---
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=tvly-...
```

### Step 2 — Start the stack

```bash
docker compose -f docker/docker-compose.yml up -d
```

Watch the migration container complete successfully before the app starts:

```bash
docker compose -f docker/docker-compose.yml ps
```

`hecate-migrate` should show `Exited (0)` (success), and `hecate` should show `(healthy)`.

### Step 3 — Verify health

```bash
curl http://localhost:8000/health/live
```

Returns `{"status": "ok"}` when the application is ready to serve requests.

### Step 4 — Put a reverse proxy in front

Never expose port 8000 directly to the internet. Use nginx, Caddy, or a cloud load balancer with TLS termination. Minimum requirements:

```nginx
# /etc/nginx/conf.d/hecate.conf
upstream hecate_backend {
    server 127.0.0.1:8000;
}

server {
    listen 443 ssl http2;
    server_name hecate.example.com;

    ssl_certificate     /etc/letsencrypt/live/hecate.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/hecate.example.com/privkey.pem;

    client_max_body_size 100M;  # document uploads

    location / {
        proxy_pass http://hecate_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE streaming (chat completions)
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

---

## Blue-green deployment (zero-downtime)

For zero-downtime deploys and instant rollback, Hecate ships a blue-green Compose setup. Both `hecate-blue` and `hecate-green` run simultaneously; nginx routes traffic to the active one. Switch active color with a single command.

### Start the blue-green stack

```bash
docker compose -f docker/docker-compose.blue-green.yml up -d
```

This brings up:

```
postgres + redis + qdrant + minio + hecate-migrate
hecate-blue    (port 8000)
hecate-green   (port 8001, inactive)
nginx          (port 80, routes to active)
```

The blue instance is active by default (`ACTIVE_COLOR=blue`).

### Deploy a new version

```bash
# 1. Pull/build the new image
git pull
docker compose -f docker/docker-compose.blue-green.yml build

# 2. Start the inactive color with the new version
docker compose -f docker/docker-compose.blue-green.yml up -d hecate-green

# 3. Wait for green to be healthy
docker compose -f docker/docker-compose.blue-green.yml ps hecate-green

# 4. Switch traffic
./deploy/scripts/blue-green-switch.sh active green
```

Effect time: **< 2 seconds** (nginx hot-reload, no dropped connections if WebSockets are kept alive).

### Roll back

```bash
./deploy/scripts/blue-green-switch.sh rollback
```

Effect time: **< 2 seconds** — traffic goes back to the previous color.

### Check which instance is active

```bash
./deploy/scripts/blue-green-switch.sh status
```

See [Rollback Runbook](../operations/rollback.md) for the full decision tree covering feature flags, code rollback, and database rollback paths.

---

## Horizontal scaling

Hecate supports multiple replicas behind a load balancer. Two things change from single-instance:

1. **Session state** must be in Redis (not in-memory per replica), so any replica can handle any request.
2. **Migrations** must run exactly once (the `hecate-migrate` init container already handles this).

### Configure Redis session state

```dotenv
# .env (multi-replica)
SESSION_STATE_STORE_BACKEND=redis
SESSION_STATE_REDIS_URL=redis://redis:6379/0
SESSION_STATE_KEY_PREFIX=hecate:state:
SESSION_STATE_TTL_DAYS=7
```

Add Redis to your Compose file or K8s deployment. The `docker-compose.blue-green.yml` template already includes it.

### Run multiple app replicas

In Compose:

```yaml
services:
  hecate:
    build: .
    deploy:
      replicas: 3
    # ... env_file, depends_on unchanged
```

In K8s, set `replicas: 3` on your Deployment. Use a `ClusterIP` Service for internal traffic and an `Ingress` for external.

> **Sticky sessions are NOT required.** The session ID is passed in the chat request body (`session_id`), and any replica can resolve it via the shared Redis state.

---

## Kubernetes deployment

Hecate does not ship a Helm chart. The recommended approach:

1. **Build a container image** from the included `Dockerfile`:

```bash
docker build -t hecate:1.2.3 .
docker push registry.example.com/hecate:1.2.3
```

2. **Run Postgres, Qdrant, MinIO, Redis** as managed services or in-cluster StatefulSets. For production, prefer managed services (RDS, ElastiCache, etc.) to avoid operating your own database.

3. **Run Hecate as a Deployment** with `replicas: N`:

```yaml
# hecate-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hecate
spec:
  replicas: 3
  selector:
    matchLabels: { app: hecate }
  template:
    metadata:
      labels: { app: hecate }
    spec:
      initContainers:
        - name: migrate
          image: registry.example.com/hecate:1.2.3
          command: ["hecate-migrate"]
          envFrom: [{ secretRef: { name: hecate-env } }]
      containers:
        - name: hecate
          image: registry.example.com/hecate:1.2.3
          command: ["uvicorn", "hecate.main:app", "--host", "0.0.0.0", "--port", "8000"]
          ports: [{ containerPort: 8000 }]
          envFrom: [{ secretRef: { name: hecate-env } }]
          readinessProbe:
            httpGet: { path: /health/live, port: 8000 }
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /health/live, port: 8000 }
            periodSeconds: 30
```

4. **Expose via Service + Ingress** with TLS termination at the ingress controller.

5. **Run migrations as a Job or init container** (shown above). `hecate-migrate` exits 0 on success; the Deployment only starts after it succeeds.

---

## Backup and recovery

Hecate ships a complete backup system covering **PostgreSQL**, **Qdrant**, **MinIO**, and the local **filesystem** (agent environments, uploaded files).

### Backup scopes

| Scope | What it captures | When to use |
|-------|------------------|-------------|
| `all` | Everything (default) | Daily full backup |
| `pg` | PostgreSQL database | Frequent metadata snapshots |
| `qdrant` | Vector store embeddings | Before/after embedding model changes |
| `minio` | Object storage (uploaded docs) | Before bulk deletion |
| `fs` | Local filesystem (agent envs) | Less common; filesystem is ephemeral |

### Create a backup

**CLI:**

```bash
hecate backup create --scope all
hecate backup create --scope pg
hecate backup create --scope pg,qdrant,minio   # combine scopes
```

**API:**

```bash
curl -X POST http://localhost:8000/api/backups \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{"scope": "all"}'
```

Backups are stored in the configured S3-compatible storage (default: MinIO bucket `hecate-backups`). Each backup records `id`, `started_at`, `scope`, `status`, `size_bytes`, and `error_message`.

### List and verify

```bash
# List recent backups
hecate backup list --limit 20

# Verify a specific backup's integrity
hecate backup verify <backup-uuid>
```

Verification reads the backup and compares row counts against the live database. Mismatches indicate corruption or schema drift since the backup was taken.

### Schedule automatic backups

Enable the APScheduler-based backup scheduler (optional dependency group `[scheduling]`):

```bash
uv pip install -e ".[scheduling]"
```

Configure via env vars:

```dotenv
BACKUP_SCHEDULE_ENABLED=true
BACKUP_SCHEDULE_CRON=0 2 * * *        # daily at 02:00 UTC
BACKUP_SCHEDULE_SCOPE=all
BACKUP_SCHEDULE_RETENTION_DAYS=30
```

Backups older than the retention window are auto-cleaned.

### Restore from backup

**CLI:**

```bash
hecate backup restore <backup-uuid> \
  --scope all \
  --conflict fail \
  --yes
```

**API:**

```bash
curl -X POST http://localhost:8000/api/restore \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "backup_id": "uuid",
    "scope": "all",
    "conflict": "fail",
    "confirm": true
  }'
```

#### Conflict strategies

| Strategy | Behavior |
|----------|----------|
| `fail` (default) | Abort if target data exists. Safe — forces operator review. |
| `replace` | Overwrite existing data with backup contents. Destructive. |
| `merge` | Combine backup data with current data (workspace-level only). |

#### Point-in-time recovery (PITR)

For PostgreSQL, restore to a specific timestamp instead of the backup time:

```bash
hecate backup restore <backup-uuid> \
  --scope pg \
  --pitrs "2026-01-15T10:30:00" \
  --conflict replace \
  --yes
```

PITR requires PostgreSQL's WAL archiving to be enabled on the database server. Contact your DBA to configure `archive_mode = on` and `wal_level = replica`.

> **Restore is destructive.** Always confirm the backup ID and scope, and prefer `fail` conflict strategy for the first run. See [Rollback Runbook](../operations/rollback.md) for the full incident-response decision tree.

### Cleanup old backups

```bash
hecate backup cleanup --before 2025-12-01
```

---

## Production checklist

Before going live:

- [ ] **Secrets**: every value in `.env` is rotated from defaults; secrets stored in a manager (Vault, AWS Secrets Manager, K8s Secrets)
- [ ] **Database**: `POSTGRES_PASSWORD` is strong; backups scheduled and verified
- [ ] **TLS**: HTTPS terminated at reverse proxy or ingress; HTTP→HTTPS redirect in place
- [ ] **API keys**: `HECATE_API_KEYS` contains strong random keys; default dev key removed
- [ ] **Fernet key**: `FERNET_KEY` set (required for DB-encrypted provider API keys)
- [ ] **Rate limiting**: `RATE_LIMIT_RPM` set to a value that matches your expected QPS
- [ ] **LLM guard**: `LLM_GUARD_ENABLED=true` for production input/output scanning
- [ ] **Health check**: load balancer points to `/health/live`; alerts fire on failure
- [ ] **Logs**: structured JSON logs shipped to your log aggregator
- [ ] **Tracing**: OpenTelemetry exporter configured (Tempo, Jaeger, or vendor)
- [ ] **Backups**: first successful backup verified end-to-end (create → restore → verify)
- [ ] **Disaster recovery**: documented RPO/RTO; restore runbook tested

---

## Observability

### Health endpoints

| Endpoint | Purpose | Use for |
|----------|---------|---------|
| `/health/live` | Process alive | Liveness probe |
| `/health/ready` | DB + dependencies reachable | Readiness probe |
| `/health` | Full health report | Operator dashboards |

### Structured logging

Hecate emits JSON logs to stdout by default. Configure your container runtime to ship them:

```yaml
# docker-compose.yml
services:
  hecate:
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

### Tracing

Set `TRACING_ENABLED=true` and configure an OTLP exporter:

```dotenv
TRACING_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
```

Traces include each Pregel superstep, LLM call, and tool execution.

### Metrics

Metrics are exposed on the default Prometheus port when the `[observability]` dependency group is installed:

```bash
uv pip install -e ".[observability]"
```

---

## Troubleshooting

### `hecate-migrate` fails and the app never starts

The migration container is the gate — if it fails, `hecate` won't start (Compose `depends_on: service_completed_successfully`). Common causes:

- **PostgreSQL not ready**: wait a few seconds and retry. Compose's healthcheck should handle this; on slow hosts, increase `start_period`.
- **Schema conflict**: someone ran migrations manually. Drop the DB volume and restart from a known state (`docker compose down -v`).
- **Bad `.env`**: `DATABASE_URL` doesn't resolve `postgres:5432` — check for typos or missing hostnames.

### Backup verification fails

Row count mismatches mean the backup or the live database changed since the backup was taken. Investigate:

```bash
hecate backup list --status error --limit 10
hecate backup verify <backup-uuid>
```

For deeper diagnostics, restore to a separate database and compare schemas manually.

### Replica sees stale session state

`SESSION_STATE_STORE_BACKEND` is not `redis`. Check:

```bash
docker compose exec hecate env | grep SESSION_STATE
```

It must show `SESSION_STATE_STORE_BACKEND=redis`. Restart replicas after changing.

### Rollback didn't take effect

- **Code rollback**: did you redeploy? `git revert` only changes the repo state.
- **Blue-green rollback**: did you run `blue-green-switch.sh rollback`? Check `status` to confirm.
- **DB migration**: contract migrations are not reversible. Use `expand-contract-guide.md` (in `docs/migrations/`) for safe rollback patterns.

See [Rollback Runbook](../operations/rollback.md) for the full decision tree.

---

## Further reading

- **[Configure LLM providers](configure-llm-providers.md)** — wire up production LLM credentials.
- **[Environment Variables Reference](../reference/env-vars.md)** — every env var, with defaults.
- **[Rollback Runbook](../operations/rollback.md)** — feature flag, code, and database rollback paths.
- **[Expand-Contract Migration Guide](../migrations/expand-contract-guide.md)** — safe schema change patterns.
- **[Operations: Backup & Recovery](../reference/cli.md)** — full `hecate backup` command reference.