# Operations

Runbooks for production incidents and routine operations. Each runbook is a step-by-step procedure for a specific scenario — pick the one that matches your situation.

For deployment, backup, monitoring, and scaling recipes, see the [how-to guides](../how-to/). For breaking schema changes between versions, see the [migrations](../migrations/) section.

## Runbooks

- **[Health Checks](health-checks.md)** — the liveness, readiness, and startup probes (`/health/live`, `/health/ready`, `/health/startup`), the `/version` build-info endpoint, and the `/metrics` Prometheus scrape. Includes Kubernetes/Docker probe configuration and graceful-shutdown behavior.
- **[Backup and Restore](backup-restore.md)** — the `/api/system` backup and restore API: create, list, verify, and restore backups across PostgreSQL, Qdrant, MinIO, and the filesystem, with point-in-time recovery and conflict policies.
- **[Log Analysis](log-analysis.md)** — log sources, the event-style logging convention, log-level control, and how to correlate stdout logs with OpenTelemetry traces and the audit trail.
- **[Rollback Runbook](rollback.md)** — decision tree and procedures for four rollback paths: code revert, database downgrade (Alembic), feature-flag toggle, and blue-green switch. Includes timing and irreversibility constraints for contract migrations.
- **[Upgrade Guide](upgrade-guide.md)** — end-to-end platform upgrade procedure: pre-upgrade checks, running migrations via `hecate-migrate`, expand-contract deployment sequence, feature-flag gated rollout, post-upgrade verification, and rollback decision matrix.

## Related guides

- **[Deploy to production](../how-to/deploy-production.md)** — Docker Compose, Kubernetes, horizontal scaling, and backup/restore with PITR.
- **[Monitor with OpenTelemetry and Prometheus](../how-to/monitor-opentelemetry.md)** — tracing, metrics, structured logging, and health probes.
- **[Expand-Contract Migration Guide](../migrations/expand-contract-guide.md)** — the pattern Hecate uses for all schema changes; required reading before rolling back a migration.
