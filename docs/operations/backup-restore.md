# Backup and Restore Runbook

How to back up and restore Hecate's data stores through the built-in Backup & Recovery API. The API coordinates backups across PostgreSQL, Qdrant, MinIO, and the filesystem, tracks each backup as a `BackupRecord`, and supports point-in-time restore with conflict policies.

All endpoints live under `/api/system` and are intended for Platform Admin use. Endpoints are defined in `src/hecate/api/system/backup.py`; orchestration logic lives in `hecate.ops.backup`.

---

## What gets backed up

Hecate stores data across four backends. A backup can cover all of them or any subset:

| Scope code | Backend | Contents |
|-----------|---------|----------|
| `pg` | PostgreSQL | Agents, workflows, sessions, execution event logs, checkpoint caches, knowledge-base metadata, audit records, budgets, feature flags |
| `qdrant` | Qdrant | Vector embeddings for knowledge bases (RAG retrieval index) |
| `minio` | MinIO | Uploaded source documents, object attachments |
| `fs` | Filesystem | AgentEnvironment working files, offloaded context |
| `all` | All of the above | Full snapshot (default) |

---

## Create a backup

### `POST /api/system/backups`

Create a new backup. The request body takes a single `scope` field:

```bash
curl -X POST http://localhost:8000/api/system/backups \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"scope": "all"}'
```

The response is a `BackupRecord` with the backup's `id`, `status`, `scope`, timestamps, and storage location. `scope` accepts `all` (default), `pg`, `qdrant`, `minio`, or `fs`.

For a PostgreSQL-only backup:

```bash
curl -X POST http://localhost:8000/api/system/backups \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"scope": "pg"}'
```

---

## List and inspect backups

### `GET /api/system/backups`

List backup records, optionally filtered by status, capped at 200:

```bash
curl "http://localhost:8000/api/system/backups?status=completed&limit=50" \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

### `GET /api/system/backups/{backup_id}`

Get the full record for one backup:

```bash
curl http://localhost:8000/api/system/backups/$BACKUP_ID \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

### `POST /api/system/backups/{backup_id}/verify`

Trigger verification of a backup's integrity (checks the artifact is readable and complete):

```bash
curl -X POST http://localhost:8000/api/system/backups/$BACKUP_ID/verify \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

Verification should be part of every backup schedule — an unverified backup is not known to be restorable.

---

## Restore

### `POST /api/system/restore`

Restore data from a backup. **Restores are gated behind `confirm: true`** — the API rejects the request with HTTP 400 if it is missing.

```bash
curl -X POST http://localhost:8000/api/system/restore \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "backup_id": "'"$BACKUP_ID"'",
    "scope": "all",
    "conflict": "fail",
    "confirm": true
  }'
```

Request fields:

| Field | Type | Purpose |
|-------|------|---------|
| `backup_id` | UUID | The backup to restore from (required) |
| `scope` | `all` \| `pg` \| `qdrant` \| `minio` \| `fs` | Which backends to restore (default `all`) |
| `workspace_id` | UUID \| null | Restrict restore to a single workspace (optional) |
| `conflict` | `replace` \| `merge` \| `fail` | How to handle conflicting existing data (default `fail`) |
| `confirm` | bool | Must be `true`; otherwise the request is rejected |
| `pitr_timestamp` | ISO 8601 \| null | Point-in-time target for PostgreSQL restore (optional) |

Conflict policies:

- **`fail`** (default) — abort the restore if any conflict is found. Safest; use when you expect the target to be empty or the backup to be authoritative.
- **`replace`** — overwrite existing data with the backup's contents.
- **`merge`** — keep existing data and add only what is missing.

The response is a `RestoreResponse` with `status`, `details`, and an optional `error`:

```json
{"status": "completed", "details": {"pg": {"rows_restored": 12345}}, "error": null}
```

---

## Recommended backup schedule

Run a full (`scope: "all"`) backup daily and verify it immediately:

```bash
# 1. Create
BACKUP_ID=$(curl -s -X POST http://localhost:8000/api/system/backups \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"scope": "all"}' | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 2. Verify
curl -X POST "http://localhost:8000/api/system/backups/$BACKUP_ID/verify" \
  -H "Authorization: Bearer $HECATE_API_KEY"
```

Schedule this via cron, a Kubernetes `CronJob`, or your platform's scheduler. Retain backups per your organization's data-retention policy, and periodically test a restore into a staging environment — a backup that has never been restored is an assumption, not a guarantee.

---

## Point-in-time recovery (PITR)

For PostgreSQL, pass `pitr_timestamp` to restore the database to a specific moment rather than the backup's snapshot time. This requires PostgreSQL WAL archiving to be enabled on the database server (outside Hecate). Use PITR when a bad change was committed after the backup and you want to rewind to just before it.

```bash
curl -X POST http://localhost:8000/api/system/restore \
  -H "Authorization: Bearer $HECATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "backup_id": "'"$BACKUP_ID"'",
    "scope": "pg",
    "pitr_timestamp": "2026-08-11T03:00:00Z",
    "conflict": "replace",
    "confirm": true
  }'
```

> **Caution** — PITR for `pg` rewinds the *entire* database, not a single workspace. For workspace-scoped restore, use `workspace_id` without `pitr_timestamp`.

---

## Restore decision tree

```
What went wrong?
│
├── Single workspace corrupted
│   → scope: "all", workspace_id: <id>, conflict: "replace"
│
├── Bad migration / schema change
│   → See Rollback Runbook Path 2 first (alembic downgrade is faster)
│   → If downgrade is irreversible, restore scope: "pg"
│
├── Bad data committed after the backup
│   → scope: "pg", pitr_timestamp: <moment before the bad change>, conflict: "replace"
│
└── Catastrophic loss (whole cluster)
    → scope: "all", conflict: "fail" (into a clean target)
```

---

## See also

- [Rollback Runbook](rollback.md) — code-level and feature-flag rollback (often faster than a full restore)
- [Deploy to production — Backup and recovery](../how-to/deploy-production.md#backup-and-recovery) — operational backup strategy in the deployment guide
- [Migrating from AgentStateStore](../migrations/agent-state-store.md) — breaking schema changes that may affect restore compatibility
