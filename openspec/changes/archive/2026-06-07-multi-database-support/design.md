## Context

Hecate uses a single `DATABASE_URL` (defaulting to `postgresql+asyncpg://`) configured in `core/config.py`. The async engine and session factory are created once at module import time in `core/database.py`. All 16 ORM models inherit from `BaseModel` (defined in `models/base.py`) which provides `id`, `created_at`, `updated_at`, and `deleted_at` columns.

The current soft-delete pattern uses `deleted_at IS NULL` as both a deletion flag and an audit timestamp. This forces 10 partial indexes using PostgreSQL-specific `postgresql_where=` syntax. The test suite runs on SQLite via `conftest.py` with `sqlite+aiosqlite://`, confirming that the ORM layer is already mostly portable — the partial indexes are silently ignored on SQLite.

17 services inject `AsyncSession` via FastAPI's `Depends(get_db)` and apply `WHERE deleted_at IS NULL` filters in their queries.

## Goals / Non-Goals

**Goals:**
- Support PostgreSQL, MySQL, and SQLite as deploy-time database choices (one backend per deployment)
- Separate deletion state (`deleted: bool`) from audit timestamp (`deleted_at: datetime`)
- Replace all `postgresql_where=` partial indexes with portable composite indexes
- All existing tests continue to pass on SQLite; new PostgreSQL CI validation added
- Zero API-level breaking changes — soft-delete behavior remains transparent to API consumers

**Non-Goals:**
- Simultaneous multi-database connections (sharding, read replicas)
- Automatic database provisioning or schema management beyond Alembic
- Oracle, SQL Server, or other database backends
- Changes to the engine layer — this is purely a `core/` + `models/` + `services/` change

## Decisions

### D1: `deleted: bool` field instead of reusing `deleted_at` for state

**Decision**: Add `deleted: Mapped[bool]` to `BaseModel` with `default=False`. Retain `deleted_at` as a pure audit timestamp.

**Rationale**: The `deleted` field represents "current state" (is this row deleted?), while `deleted_at` represents "when did deletion happen" (audit). These are distinct concerns. A boolean field enables clean composite indexes like `Index("idx_name", "name", "deleted")` that work identically across PostgreSQL, MySQL, and SQLite.

**Alternatives considered**:
- `CHAR(1)` with N/Y values — more human-readable but adds string comparison overhead and loses SQLAlchemy `Boolean` type safety
- Sentinel value for `deleted_at` (epoch 0 instead of NULL) — changes NULL semantics, less intuitive

### D2: Composite indexes `(column, deleted)` instead of dialect-aware partial indexes

**Decision**: Replace all `Index("name", col, postgresql_where=BaseModel.deleted_at.is_(None))` with `Index("name", col, "deleted")`.

**Rationale**: Composite indexes on `(col, deleted)` produce identical uniqueness semantics across all three databases. Since `deleted` is a boolean, the index cardinality is low (2 values) and the combined index is efficient. No runtime dialect detection needed.

**What this changes**:
```
Before: Index("idx_agents_workspace", "workspace_id", postgresql_where=deleted_at IS NULL)
After:  Index("idx_agents_workspace", "workspace_id", "deleted")
```
For unique indexes, `(name, deleted)` allows at most one active row with the same `name` and `deleted=False`, plus any number of deleted rows (`deleted=True`). Since multiple deleted rows with the same name can coexist, the composite index provides the same practical uniqueness guarantee as the partial index.

**Edge case**: Two deleted rows with identical `(name, deleted=True)` would violate a unique composite index. The solution is to use **non-unique** composite indexes for most cases, and only add `deleted` to unique indexes where the business rule requires it. For unique constraints like tool names per workspace, the index becomes `Index("name", "workspace_id", "deleted", unique=True)` — this means only one `(workspace_id, name, deleted=False)` row is allowed, and deleted rows use their `deleted_at` timestamp to differentiate (but `deleted` alone can't distinguish two deleted rows with the same name).

**Resolution**: For unique indexes, append `deleted_at` to make the tuple fully unique:
```
Index("name", "workspace_id", "deleted", "deleted_at", unique=True)
```
This works because `deleted_at` is NULL for active rows (all active rows share the same NULL) and unique timestamps for deleted rows. Active uniqueness is guaranteed by `(workspace_id, name, False, NULL)`, and deleted rows are differentiated by their timestamps.

Wait — NULL values in unique indexes are treated differently across databases. PostgreSQL allows multiple NULLs in unique indexes, MySQL allows multiple NULLs (since 5.1), and SQLite allows multiple NULLs. So `(workspace_id, name, deleted, deleted_at)` with `deleted_at=NULL` for active rows and unique timestamps for deleted rows is actually **portable and correct**.

### D3: Dialect detection from DATABASE_URL

**Decision**: Parse the database driver from `DATABASE_URL` string to determine dialect. No separate `DB_TYPE` config variable.

**Rationale**: The URL scheme already encodes the dialect (`postgresql+asyncpg`, `mysql+aiomysql`, `sqlite+aiosqlite`). A separate config variable would be redundant and could drift out of sync.

### D4: Engine creation refactored into a factory function

**Decision**: Replace module-level `engine = create_async_engine(settings.DATABASE_URL, ...)` with a `create_engine_from_url()` function that applies dialect-specific defaults (pool config for PG/MySQL, no pool for SQLite).

**Rationale**: SQLite does not support connection pooling (in-memory database has exactly one connection). PostgreSQL and MySQL benefit from `pool_size`/`max_overflow`. The factory encapsulates this variation.

### D5: Migration strategy — additive migration

**Decision**: Add a single new Alembic migration that (1) adds `deleted` column with `server_default="0"`, (2) backfills from `deleted_at`, (3) drops old partial indexes, (4) creates new composite indexes.

**Rationale**: Rewriting the migration chain would break existing deployments. An additive migration preserves the full history and allows zero-downtime upgrades.

## Risks / Trade-offs

- **Composite indexes are slightly larger than partial indexes** → The `deleted` boolean adds 1 byte per index entry. For Hecate's data volumes this is negligible.
- **Unique composite index with `deleted_at` NULL** → All three databases (PG, MySQL, SQLite) treat NULLs as distinct in unique indexes, so multiple active rows with the same `(name, workspace_id, False, NULL)` would NOT violate uniqueness. However, the `(name, workspace_id, deleted=False)` uniqueness is already enforced by the `deleted` boolean — there can only be one active row per name. The `deleted_at` column only serves to differentiate deleted rows from each other. → **Mitigation**: For non-unique indexes (most cases), `deleted` alone is sufficient. For unique indexes, use `(columns..., deleted, deleted_at)` composite.
- **Existing deployments require migration** → The Alembic migration must be run before deploying the new code. Old code will ignore the new `deleted` column. → **Mitigation**: Add `deleted` with `server_default=False` so old rows work immediately.
- **Service layer changes are widespread (17 files)** → Mechanical find-and-replace, but must be thorough. → **Mitigation**: Use AST-based search to find all `deleted_at.is_(None)` and `deleted_at == None` patterns.
