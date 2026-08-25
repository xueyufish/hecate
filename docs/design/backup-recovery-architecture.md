# Backup & Recovery Architecture

Deep-dive design document for Hecate's backup and disaster recovery system. For operational recipes, see [Backup Runbook](../operations/backup-restore.md). For the cross-cutting deployment context, see [Reference Architectures](reference-architectures.md).

This document is for **SREs and compliance officers** designing backup policies, RPO/RTO targets, and disaster recovery procedures for Hecate deployments.

> **Implementation status (recent)**: Backup & Recovery is delivered as feature **13.5 Data Backup & Recovery (P3 ✅)** in [feature-catalog.md](../../features/feature-catalog.md). The `hecate-migrate` CLI binary and the preflight / health-check three-endpoint pattern (`/health/live`, `/health/ready`, `/health/startup`) are part of **13.6 Version Upgrade (P3 ✅, shipped recently)** — see ADR-016 for the Platform SPI that hosts the migrate command, and the 13.6 catalog entry for the graceful-shutdown + feature-flag two-tier system + `hecate flag-audit --check` tool that this document presupposes.

---

## Why backups matter for an agent platform

Agent platforms have **more state than typical web apps**. A single Hecate deployment contains:

- **Agent definitions** (configuration, persona, model settings)
- **Knowledge bases** (uploaded documents, embeddings, citations)
- **Conversations** (chat history, message turns)
- **Execution event logs** (per-session state — the source of truth for resume; see [Log-as-Truth, ADR-030](adr/030-event-sourced-execution-state.md))
- **Checkpoints** (materialized caches of execution state — rebuildable from the event log, but included for fast recovery)
- **Tool definitions** (custom tools, MCP servers, plugins)
- **User / workspace data** (RBAC, quotas, settings)
- **Audit logs** (compliance evidence)
- **Observability data** (traces, metrics, logs)

Losing any of these to disk failure, accidental deletion, or ransomware means losing user trust. Backups are **not optional** in production.

---

## What gets backed up

Hecate uses a **scope-based** backup model. Each backup specifies one or more scopes:

| Scope | What it contains | Backing store |
|---|---|---|
| **PG** | PostgreSQL — agents, KBs, conversations, execution event logs, checkpoints, audit, observability metadata | PostgreSQL `pg_dump` |
| **QDRANT** | Vector embeddings, payload indexes | Qdrant snapshot API |
| **MINIO** | Uploaded documents, generated artifacts | MinIO / S3 bucket copy |
| **FS** | Filesystem state — plugin packages, configuration overrides | `tar` or `rsync` |
| **ALL** | All of the above (default for full backups) | Composite |

Most backups are `ALL` (full). Incremental backups can target a single scope (e.g., only `PG` for hourly event-log backups). Note: the event log is the source of truth — a backed-up event log is fully sufficient to rebuild any session's state; checkpoints only speed that rebuild up.

---

## Backup types

Hecate supports three backup strategies:

| Type | What it captures | Use case |
|---|---|---|
| **FULL** | Complete snapshot of all scopes | Daily / weekly |
| **INCREMENTAL** | Only changes since last backup | Hourly during business hours |
| **WAL** | Postgres Write-Ahead Log only | Continuous archiving for PITR |

**Default schedule**:

```
00:00 daily   → FULL backup
00:00, 06:00, 12:00, 18:00  → INCREMENTAL (PG only)
continuously  → WAL archive (PG)
```

WAL archiving enables **Point-in-Time Recovery (PITR)**: restore from yesterday's full + replay WAL up to any second.

---

## Storage backends

Backups can land in any of four storage types (`src/hecate/services/backup/`):

| Backend | Module | Use case |
|---|---|---|
| **MinIO** | `minio_backup.py` + `minio_storage.py` | Self-hosted, on-prem |
| **S3** | `s3_storage.py` | AWS / cloud |
| **Filesystem** | `fs_backup.py` | Air-gapped, NFS-mounted |
| **GCS / Azure Blob** | (via S3-compatible adapter) | Multi-cloud |

Each storage backend exposes the same interface:

```python
class BackupStorage(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes) -> None: ...
    
    @abstractmethod
    async def get(self, key: str) -> bytes: ...
    
    @abstractmethod
    async def list(self, prefix: str) -> list[str]: ...
    
    @abstractmethod
    async def delete(self, key: str) -> None: ...
```

Backups are stored with content addressing (`checksum: sha256`) — re-running the same backup is idempotent.

---

## Backup record model

Every backup creates a `BackupRecord` row in Postgres (`src/hecate/models/backup.py`):

```python
class BackupRecordModel(BaseModel):
    """Persistent record of a backup execution."""
    
    backup_type: str        # "full" / "incremental" / "wal"
    scope: str              # "all" / "pg" / "qdrant" / "minio" / "fs"
    status: str             # "pending" / "running" / "completed" / "failed" / "partial"
    storage_type: str       # "minio" / "s3" / "fs" / "gcs"
    storage_path: str       # URI where the backup is stored
    
    size_bytes: int | None  # After completion
    checksum: str | None    # SHA256 of the backup content (after completion)
    
    started_at: datetime    # Set when status → running
    completed_at: datetime | None  # Set when status → completed / failed / partial
    error_message: str | None      # Set on failure
```

This makes backups queryable (`SELECT * FROM backup_records WHERE workspace_id = X ORDER BY started_at DESC`).

---

## Backup API

Backups are managed via the management API (`src/hecate/api/system/backup.py`):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/backups` | Trigger a backup |
| `GET` | `/api/backups` | List backups |
| `GET` | `/api/backups/{id}` | Get backup details |
| `POST` | `/api/backups/{id}/verify` | Verify backup integrity |
| `POST` | `/api/restore` | Restore from a backup |

### Trigger a backup

```bash
curl -X POST http://localhost:8000/api/backups \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "backup_type": "full",
    "scope": "all",
    "storage_type": "minio",
    "storage_path": "s3://backups-bucket/daily/2026-08-11/",
    "retention_days": 90
  }'
```

Returns:

```json
{
  "id": "bk_a1b2c3d4...",
  "status": "pending",
  "started_at": "2026-08-11T00:00:00Z"
}
```

The API returns immediately — the backup runs asynchronously. Poll `GET /api/backups/{id}` for status.

### Restore from backup

```bash
curl -X POST http://localhost:8000/api/restore \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "backup_id": "bk_a1b2c3d4...",
    "target_time": "2026-08-10T14:23:00Z",
    "conflict_policy": "overwrite"  // or "skip" or "fail"
  }'
```

`target_time` enables PITR — restore to a specific moment. Without it, restores the full backup as-is.

---

## Scheduler

The scheduler (`src/hecate/services/backup/scheduler.py`) runs as a background process:

```python
class BackupScheduler:
    """Cron-style backup scheduler."""
    
    async def start(self) -> None:
        # Load schedule from config
        # Register with event loop
        ...
    
    async def schedule_full_backup(self, cron: str) -> None:
        # "0 0 * * *" → daily at midnight
        ...
    
    async def schedule_incremental(self, cron: str, scope: str) -> None:
        # "0 */6 * * *" → every 6 hours
        ...
```

Schedule is configured via `BACKUP_SCHEDULE_FULL`, `BACKUP_SCHEDULE_INCREMENTAL` env vars (cron expressions).

---

## Verification

A backup that doesn't restore is worse than no backup. Hecate includes `verification.py` that runs every backup through a **restore dry-run**:

```python
# src/hecate/services/backup/verification.py
async def verify_backup(backup_id: UUID) -> VerificationResult:
    """Restore the backup to a temporary location and validate."""
    
    backup = await load_backup(backup_id)
    
    # 1. Checksum matches
    assert backup.checksum == sha256(download(backup.storage_path))
    
    # 2. Restore to scratch database
    scratch_db = await restore_to_scratch(backup)
    
    # 3. Schema is valid
    assert all_tables_present(scratch_db)
    
    # 4. Row counts reasonable
    counts = await count_rows(scratch_db)
    assert counts["agents"] > 0 or counts["knowledge_bases"] > 0
    
    # 5. Cleanup scratch
    await drop_database(scratch_db)
    
    return VerificationResult(
        backup_id=backup_id,
        verified_at=now(),
        passed=True,
    )
```

Verification is **automatic** after every backup by default. If verification fails, the backup is marked `partial` and the failure is alerted.

---

## Recovery procedures

### Scenario 1: Single component failure (e.g., Postgres corrupt)

```bash
# 1. Stop Hecate app
docker compose stop api

# 2. Restore Postgres from latest full + WAL replay
hecate-migrate restore \
  --backup-id bk_20260811_full \
  --target-time "2026-08-11T14:23:00Z" \
  --component pg

# 3. Start Hecate app
docker compose start api

# 4. Verify
hecate health
```

### Scenario 2: Complete loss (server destroyed)

```bash
# 1. Provision new server(s) from infrastructure-as-code
terraform apply

# 2. Restore all scopes
hecate-migrate restore \
  --backup-id bk_20260811_full \
  --component all

# 3. Run migrations (if version mismatch)
hecate-migrate upgrade head

# 4. Start services (13.6 — `hecate-migrate` is an independent binary that runs *before* container startup; see ADR-016 Platform SPI + 13.6 Version Upgrade entry in feature-catalog)
docker compose up -d

# 5. Verify
hecate health
hecate preflight
```

### Scenario 3: Accidental deletion (e.g., wrong workspace removed)

```bash
# 1. Find backup before the deletion
hecate backup list --before "2026-08-11T14:00:00Z"

# 2. Restore that specific workspace only
hecate-migrate restore \
  --backup-id bk_20260810_full \
  --target-time "2026-08-10T13:00:00Z" \
  --component pg \
  --workspace-id ws_deleted_workspace \
  --conflict-policy skip
```

---

## RPO / RTO targets

Hecate's default backup policy achieves:

| Metric | Default | Configurable |
|---|---|---|
| **RPO** (Recovery Point Objective) | 6 hours (last incremental) | Continuous (with WAL archive) |
| **RTO** (Recovery Time Objective) | 1-4 hours (full restore) | <30 min (incremental + WAL replay) |
| **Retention** | 90 days (configurable) | Unlimited (with cold storage tiering) |

### Improving RPO

| Target | Configuration |
|---|---|
| 24 hours | Daily FULL only |
| **6 hours** (default) | Daily FULL + 6-hourly INCREMENTAL |
| 1 hour | Hourly INCREMENTAL |
| Continuous (PITR) | Daily FULL + continuous WAL archive |

### Improving RTO

| Target | Configuration |
|---|---|
| Days | Restore from cold storage; manual process |
| **Hours** (default) | Restore from warm storage; semi-automated |
| Minutes | Hot standby + automated failover (e.g., Patroni for Postgres) |
| Seconds | Multi-region active-active (P5) |

---

## Cross-region replication

For multi-region deployments (P5+), backups should be replicated:

```
Region A (primary)                    Region B (DR)
┌────────────────────┐               ┌────────────────────┐
│  Hecate app        │               │  Hecate app        │
│  Postgres primary  │ ──replica──▶ │  Postgres replica  │
│  Qdrant primary    │ ──snapshot──▶│  Qdrant replica    │
│  MinIO bucket      │ ──replicate─▶│  MinIO bucket      │
└────────────────────┘               └────────────────────┘
         │                                       │
         └─────────────┬─────────────────────────┘
                       ▼
              ┌─────────────────────┐
              │  Cold storage       │
              │  (S3 Glacier / etc) │
              └─────────────────────┘
```

Replication is provided by the underlying storage engines:

- **Postgres**: streaming replication (built-in) or logical replication (for selective)
- **Qdrant**: built-in replication in cluster mode
- **MinIO**: bucket replication (built-in)
- **WAL archive**: continuous copy to remote storage

Hecate itself is stateless — just deploy additional replicas in the DR region pointing at the replicated data layer.

---

## Compliance considerations

### What auditors want to see

| Requirement | How Hecate addresses it |
|---|---|
| **Backups are encrypted at rest** | Storage backend encryption (MinIO SSE, S3 SSE-KMS) |
| **Backups are encrypted in transit** | TLS for all backup operations |
| **Restore tested periodically** | Automatic verification + manual quarterly drills |
| **Retention matches policy** | Configurable per backup (e.g., `retention_days: 2555` for 7 years) |
| **Tamper-evident** | SHA256 checksum per backup; WAL archive is append-only |
| **Geographic separation** | Multi-region storage configuration |
| **Access controlled** | Backup operations require `ADMIN` role; storage uses IAM / access keys |

### Retention policies

Default retention is **90 days**. Override per backup:

```python
# Common retention patterns
retention_days = 30      # Dev / test
retention_days = 90      # Production default
retention_days = 365     # Compliance (1 year)
retention_days = 2555    # HIPAA / financial (7 years)
```

Expired backups are deleted by a background cleanup process. **Deletion is logged in the audit trail** for compliance.

---

## Disaster recovery drills

A backup that has never been restored is a backup that won't work when you need it.

**Recommended drill cadence**:

| Drill type | Frequency | Duration | RTO target |
|---|---|---|---|
| **Restore to scratch DB** (automated) | Daily (after every backup) | Seconds | N/A |
| **Single-workspace restore** | Monthly | <30 min | <30 min |
| **Full restore to new environment** | Quarterly | 1-4 hours | Document actual |
| **Multi-region failover** | Annually | Hours | Document actual |

Drill results are recorded as audit events (`recovery_drill.completed`) for compliance evidence.

---

## Cost optimization

Backups are expensive at scale. Hecate's design reduces cost by:

1. **Incremental-first** — daily FULL is the base; incrementals layer on top
2. **Tiered storage** — hot (S3 Standard) for recent, cold (Glacier) for old
3. **Compression** — pg_dump uses gzip by default; MinIO / S3 server-side compression
4. **Deduplication** — content-addressed storage means identical backups don't multiply
5. **Selective scope** — only back up what changed

Default: 30 days hot + 90 days cold + indefinite archive for compliance-tagged backups.

---

## What's NOT implemented

| Feature | Target |
|---|---|
| **Multi-region active-active backup** | [post-1.0] |
| **Per-tenant encryption keys** for backups | [1.x] |
| **Continuous WAL archive to S3** | [1.0] |
| **Backup scheduling UI** in canvas | [1.x] |
| **Cross-platform restore** (Windows → Linux) | Not planned — recommend matching OS |

---

## Implementation references

- `src/hecate/models/backup.py` — BackupRecordModel + enums
- `src/hecate/services/backup/factory.py` — backup backend factory
- `src/hecate/services/backup/pg_backup.py` — PostgreSQL backup (`pg_dump`)
- `src/hecate/services/backup/qdrant_backup.py` — Qdrant snapshot
- `src/hecate/services/backup/minio_backup.py` — MinIO backup
- `src/hecate/services/backup/fs_backup.py` — filesystem backup
- `src/hecate/services/backup/storage.py` — BackupStorage ABC
- `src/hecate/services/backup/minio_storage.py` — MinIO storage backend
- `src/hecate/services/backup/s3_storage.py` — S3 storage backend
- `src/hecate/services/backup/orchestrator.py` — backup orchestration
- `src/hecate/services/backup/scheduler.py` — cron-style scheduler
- `src/hecate/services/backup/verification.py` — backup verification (restore dry-run)
- `src/hecate/services/backup/restore.py` — restore from backup
- `src/hecate/api/system/backup.py` — Management API
- `src/hecate/cli/backup_cli.py` — `hecate-migrate backup ...` commands

## Related documents

- Backup & Recovery Architecture — current implementation
- [Reference Architectures](reference-architectures.md) — where backups fit in deployment topology
- [Observability Architecture](observability-architecture.md) — what to monitor about backups
- [Multi-Tenancy Architecture](multi-tenancy-architecture.md) — per-tenant backup considerations
- [Security Architecture](security-architecture.md) — encryption at rest, audit trail