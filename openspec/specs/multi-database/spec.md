# multi-database Specification

## Purpose
TBD - created by archiving change multi-database-support. Update Purpose after archive.
## Requirements
### Requirement: Deploy-time database backend selection
The system SHALL support PostgreSQL, MySQL, and SQLite as database backends, selected at deploy time via the `DATABASE_URL` environment variable. The database dialect SHALL be automatically detected from the URL scheme.

#### Scenario: PostgreSQL backend
- **WHEN** `DATABASE_URL` is set to `postgresql+asyncpg://user:pass@host:5432/db`
- **THEN** the system SHALL create an async engine with connection pooling (pool_size=20, max_overflow=10)

#### Scenario: MySQL backend
- **WHEN** `DATABASE_URL` is set to `mysql+aiomysql://user:pass@host:3306/db`
- **THEN** the system SHALL create an async engine with connection pooling (pool_size=20, max_overflow=10)

#### Scenario: SQLite backend
- **WHEN** `DATABASE_URL` is set to `sqlite+aiosqlite:///path/to/db.sqlite3` or `sqlite+aiosqlite://` (in-memory)
- **THEN** the system SHALL create an async engine without connection pooling (poolclass=StaticPool for in-memory, or no pool options for file-based)

#### Scenario: Unsupported database URL
- **WHEN** `DATABASE_URL` uses an unsupported scheme (e.g., `oracle+cx_oracle://`)
- **THEN** the system SHALL raise a `ValueError` at startup with a message listing supported backends

### Requirement: Portable schema definitions
All ORM model definitions and Alembic migrations SHALL use only cross-database-compatible SQLAlchemy types and index definitions. No `postgresql_where=`, `postgresql_using=`, or other dialect-specific index kwargs SHALL appear in model or migration code.

#### Scenario: No postgresql_where in models
- **WHEN** the codebase is scanned for `postgresql_where`
- **THEN** zero matches SHALL exist in `src/hecate/models/`

#### Scenario: No postgresql_where in migrations
- **WHEN** the codebase is scanned for `postgresql_where`
- **THEN** zero matches SHALL exist in `alembic/versions/`

#### Scenario: func.now() used for timestamps
- **WHEN** `created_at` or `updated_at` columns are defined
- **THEN** `server_default=func.now()` SHALL be used (SQLAlchemy auto-adapts to CURRENT_TIMESTAMP for SQLite)

### Requirement: MySQL optional dependency
MySQL support SHALL be available via an optional `[mysql]` dependency group in `pyproject.toml`.

#### Scenario: MySQL driver not installed
- **WHEN** `DATABASE_URL` is `mysql+aiomysql://...` and `aiomysql` is not installed
- **THEN** the system SHALL raise a clear import error indicating the `[mysql]` extra must be installed

#### Scenario: MySQL driver installed
- **WHEN** `hecate[mysql]` is installed and `DATABASE_URL` is `mysql+aiomysql://...`
- **THEN** the system SHALL connect and operate normally

