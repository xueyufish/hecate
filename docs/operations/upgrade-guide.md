# Upgrade Guide

How to safely upgrade Hecate across versions — pre-upgrade checks, running migrations, feature-flag gated rollouts, and rollback if something goes wrong.

> For the schema migration *pattern* (expand-contract), see the [Expand-Contract Migration Guide](../migrations/expand-contract-guide.md). For specific breaking changes, see [Migrations](../migrations/). This page covers the end-to-end upgrade *procedure*.

---

## Pre-upgrade checklist

### 1. Read the release notes

Check the version's changelog for:
- **Breaking changes** — renamed env vars, removed endpoints, changed defaults
- **New dependencies** — optional groups that may need installing (e.g., `[rag]`, `[temporal]`)
- **Minimum version bumps** — Python, PostgreSQL, Docker

### 2. Snapshot the current state

```bash
# Record current versions
curl http://localhost:8000/version

# Record current Alembic head
alembic current

# Create a pre-upgrade backup
hecate backup create --scope all --description "pre-upgrade snapshot"
```

### 3. Verify the backup

```bash
hecate backup verify <backup-uuid>
```

A backup that fails verification is useless — fix it before proceeding.

### 4. Check for pending migrations

```bash
# Pull the new image / checkout the new version first, then:
alembic heads    # what the new version expects
alembic current  # where your database is now
```

If `alembic current` ≠ `alembic heads`, you need to run migrations after upgrading the code.

---

## Running migrations

### The `hecate-migrate` tool

Hecate ships a standalone migration runner designed for init containers:

```bash
hecate-migrate          # runs alembic upgrade head
hecate-migrate --help   # all options
```

In Docker Compose, the `hecate-migrate` service runs as an init container — it exits `0` on success before the app starts:

```yaml
services:
  hecate-migrate:
    image: hecate:latest
    command: hecate-migrate
    restart: "no"           # don't restart on success
  
  hecate:
    depends_on:
      hecate-migrate:
        condition: service_completed_successfully
```

In Kubernetes, run migrations as a Job or init container:

```yaml
initContainers:
  - name: migrate
    image: hecate:latest
    command: ["hecate-migrate"]
```

### Expand-contract deployments

Hecate uses the [expand-contract pattern](../migrations/expand-contract-guide.md) for schema changes. This means migrations come in two phases:

```
Day 0: Deploy new code + run expand migrations
       ├── hecate-migrate (runs only additive DDL)
       └── Both old and new code work with the expanded schema

Day 1+: Run contract migrations (after all instances upgraded)
       ├── hecate-migrate (runs destructive DDL)
       └── Old column/table now removed
```

For multi-replica deployments, **never run contract migrations before all replicas are on the new code**. The expand phase is safe to run while old code is still live; the contract phase is not.

### `lock_timeout` safety

Every migration connection sets `SET lock_timeout = '2s'` (configurable via `ALEMBIC_LOCK_TIMEOUT`). If a DDL statement blocks on a long-running transaction for more than 2 seconds, the migration fails fast instead of stalling the entire database. This prevents migration-induced outages on busy tables.

---

## Feature-flag gated rollout

For risky upgrades, use feature flags to gradually enable new behavior:

```bash
# Create a flag (default: off)
curl -X POST http://localhost:8000/api/feature-flags \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "key": "new_context_engine_v2",
    "enabled": false,
    "description": "Toggle for the v2 context engine"
  }'

# Enable for a subset of agents first
curl -X PATCH http://localhost:8000/api/feature-flags/new_context_engine_v2 \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"enabled": true, "target_agent_ids": ["<test-agent-id>"]}'

# Roll out to all agents after validation
curl -X PATCH http://localhost:8000/api/feature-flags/new_context_engine_v2 \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"enabled": true}'
```

Feature flags are the fastest rollback path — disable the flag and the old behavior resumes immediately, without a redeploy. See the [Rollback Runbook](rollback.md#path-3-feature-flag-rollback).

---

## Post-upgrade verification

### 1. Health probes

```bash
curl http://localhost:8000/health/live    # process alive
curl http://localhost:8000/health/ready   # DB + Redis + Qdrant reachable
curl http://localhost:8000/version        # confirm new version
```

### 2. Smoke test

Send a test chat request:

```bash
curl -X POST http://localhost:8000/v1/agents/<test-agent-id>/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "messages": [{"role": "user", "content": "Hello, are you working?"]
  }'
```

### 3. Check traces for errors

```bash
curl "http://localhost:8000/api/traces?limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

Look for spans with `status=ERROR` or high `duration_ms`.

### 4. Verify agent health scores

```bash
curl "http://localhost:8000/api/agent-health" \
  -H "Authorization: Bearer $TOKEN"
```

If scores drop after upgrade, investigate before serving production traffic.

---

## Rollback if upgrade fails

Four rollback paths, ordered by speed:

| Path | Speed | What it does | When to use |
|------|-------|-------------|-------------|
| **Feature flag** | Instant | Disable the flag; old behavior resumes | New feature causes issues |
| **Code rollback** | Minutes | Redeploy previous image; DB schema is forward-compatible (expand phase only) | Bug in new code |
| **Schema rollback** | Minutes | `alembic downgrade -1` (only safe for expand revisions) | Bad migration |
| **Backup restore** | Minutes–hours | Restore from pre-upgrade backup | Catastrophic failure |

**Critical rule**: Contract migrations are **irreversible**. If you've already run contract migrations (dropped columns/tables), code rollback alone won't work — you need a backup restore. This is why the expand-contract pattern separates the two phases by at least one deploy.

For the full procedure, see the [Rollback Runbook](rollback.md).

---

## Common upgrade scenarios

### Scenario: Adding a new optional dependency

```bash
# New version requires the [rag] extra for BGE-M3 embeddings
uv pip install -e ".[rag]"
# Restart the server
```

### Scenario: Renamed environment variable

```bash
# Old: PERSISTENT_TOPIC_ENABLED (removed in v2)
# New: persistence is now per-channel in the graph DSL
# Fix: remove the old env var from .env; no migration needed
```

### Scenario: Database schema change

```bash
# The new version adds a column (expand migration):
hecate-migrate
# Verify:
alembic current

# Both old and new code work now. After all replicas upgraded,
# the next release's contract migration will clean up old columns.
```

### Scenario: Breaking change (e.g., AgentStateStore → SessionStateStore)

Follow the specific migration guide in [`docs/migrations/`](../migrations/). Breaking changes have dedicated guides with step-by-step data migration instructions.

---

## Further reading

- [Expand-Contract Migration Guide](../migrations/expand-contract-guide.md) — the schema migration pattern
- [Rollback Runbook](rollback.md) — four rollback paths in detail
- [Backup and Restore](backup-restore.md) — pre-upgrade backup procedure
- [Health Checks](health-checks.md) — post-upgrade verification probes
- [Environment Variables](../reference/env-vars.md) — all configuration variables
