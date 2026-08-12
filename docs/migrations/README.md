# Migrations

Guides for database schema migrations, including breaking changes and the expand-contract pattern used across Hecate upgrades.

## Version-specific guides

- **[Migrating from 0.1.x (Alpha) to 0.2.x (Beta)](v0.1-to-v0.2.md)** — the version upgrade guide for Alpha → Beta: configuration renames, env var changes, plugin manifest updates, database migrations, and rollback procedure. Required reading before deploying Beta.

## Pattern and historical guides

- **[Migrating from AgentStateStore to SessionStateStore](agent-state-store.md)** — breaking change introduced in 13.4a-6 (Aug 2026). Covers what changed, how to migrate existing data, and how to verify the migration.
- **[Expand-Contract Migration Guide](expand-contract-guide.md)** — the pattern Hecate uses for all schema changes: add the new column (expand), backfill data, switch reads/writes, drop the old column (contract). Required reading before writing Alembic migrations.
