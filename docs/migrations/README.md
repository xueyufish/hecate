# Migrations

Guides for database schema migrations, including breaking changes and the expand-contract pattern used across Hecate upgrades.

## Guides

- **[Migrating from AgentStateStore to SessionStateStore](agent-state-store.md)** — breaking change introduced in 13.4a-6 (Aug 2026). Covers what changed, how to migrate existing data, and how to verify the migration.
- **[Expand-Contract Migration Guide](expand-contract-guide.md)** — the pattern Hecate uses for all schema changes: add the new column (expand), backfill data, switch reads/writes, drop the old column (contract). Required reading before writing Alembic migrations.
