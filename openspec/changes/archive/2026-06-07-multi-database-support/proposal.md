## Why

Hecate currently hardcodes PostgreSQL as its sole database backend. The `postgresql_where=` partial indexes in 10 model definitions and 10 migration files make the codebase incompatible with MySQL and SQLite at the DDL level. Meanwhile, the test suite already runs on SQLite (via `sqlite+aiosqlite://`) — proving the ORM models are nearly portable. Removing PostgreSQL-specific assumptions unlocks deploy-time database choice (PostgreSQL / MySQL / SQLite), which is a prerequisite for Multi-Tenant RBAC (Sprint 4) and enterprise deployment flexibility.

Additionally, the current `BaseModel` conflates two distinct semantics in the `deleted_at` column: it serves as both a deletion flag (`IS NULL` = active) and an audit timestamp (when was it deleted). This should be separated into a `deleted: bool` field for state and `deleted_at: datetime` for audit.

## What Changes

- **Add `deleted: bool` field to `BaseModel`** — explicit deletion state flag, defaults to `False`. `deleted_at` remains as an audit timestamp.
- **Replace all `postgresql_where=` partial indexes with portable composite indexes** — e.g., `Index("idx_name", "name", "deleted")` works identically on PostgreSQL, MySQL, and SQLite.
- **Update Service layer queries** — change `WHERE deleted_at IS NULL` to `WHERE deleted = false` across all 17 services.
- **Refactor `database.py` for multi-dialect support** — detect database dialect from `DATABASE_URL` at startup; create engine with dialect-appropriate pool settings.
- **Add Alembic data migration** — backfill `deleted` column from existing `deleted_at` values (NULL → False, non-NULL → True).
- **Add CI test matrix** — run pytest against both SQLite (existing) and PostgreSQL to catch dialect regressions.

## Capabilities

### New Capabilities
- `multi-database`: Deploy-time database backend selection (PostgreSQL, MySQL, SQLite) with automatic dialect detection and portable schema definitions

### Modified Capabilities
- `data-models`: `BaseModel` gains `deleted: bool` field; soft-delete semantics change from `deleted_at IS NULL` to `deleted = false`; all partial indexes replaced with composite indexes
- `core-infrastructure`: `database.py` supports multi-dialect engine creation; `DATABASE_URL` default changes from PostgreSQL-only to dialect-agnostic; `Settings` adds database type validation

## Impact

- **Models**: All 16 `BaseModel` subclasses gain a new `deleted` column (migration required)
- **Indexes**: 10 `postgresql_where=` indexes across 7 model files replaced with portable composite indexes
- **Services**: 17 service files update query filters from `deleted_at IS NULL` to `deleted = false`
- **Migrations**: New Alembic migration to add `deleted` column, backfill data, and recreate indexes
- **Tests**: Existing SQLite tests continue to pass; new PostgreSQL integration test configuration added
- **API**: No API-level changes — soft-delete behavior is transparent to API consumers
- **Dependencies**: `aiomysql` added as optional dependency for MySQL support (new `[mysql]` extra in pyproject.toml)
