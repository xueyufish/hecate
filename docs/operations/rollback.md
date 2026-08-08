# Rollback Runbook

## Decision tree

```
What went wrong?
│
├── Feature-level issue (new feature broken)
│   → Path 3: Feature flag rollback (no redeploy)
│
├── Code-level issue (new version broken)
│   ├── Blue-green deployment?
│   │   → Path 4: Blue-green rollback (switch nginx upstream)
│   ├── Single instance?
│   │   → Path 1: Code rollback (git revert + redeploy)
│   │
├── Database-level issue (migration broke data)
│   → Path 2: Database rollback (alembic downgrade)
│         ⚠ Only safe for expand migrations (contract migrations may be irreversible)
```

## Path 1: Code rollback

Fastest path. Works for any deployment model.

```bash
git revert <bad-commit>
git push origin main
# CI/CD rebuilds and deploys automatically
# OR manually:
docker compose pull && docker compose up -d
```

Downtime: same as a normal deploy (5-30s for single instance).

## Path 2: Database rollback

```bash
# Downgrade one revision
hecate-migrate --downgrade 1

# Or downgrade to specific revision
alembic downgrade <revision-id>
```

**Constraints**:
- Expand migrations (add column/table) are always safe to roll back
- Contract migrations (drop column/alter type) may be **irreversible** if new data was written
- Always check `alembic downgrade --sql <target>` output before executing on production

## Path 3: Feature flag rollback

No redeploy required. Takes effect within Redis cache TTL (5s).

```bash
# Disable a feature flag
curl -X PATCH http://localhost:8000/api/feature-flags/ENABLE_NEW_ENGINE \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Or transition to retired (permanently off)
curl -X POST http://localhost:8000/api/feature-flags/ENABLE_NEW_ENGINE/transition \
  -H "Content-Type: application/json" \
  -d '{"status": "retired"}'
```

Effect time: < 5 seconds (Redis cache TTL).

## Path 4: Blue-green rollback

```bash
# Switch traffic back to the previous instance
./deploy/scripts/blue-green-switch.sh rollback

# Check which instance is active
./deploy/scripts/blue-green-switch.sh status
```

Effect time: < 2 seconds (nginx hot reload).
