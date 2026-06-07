## MODIFIED Requirements

### Requirement: Async database engine with auto-commit session
The `engine` module SHALL create an async SQLAlchemy engine from `DATABASE_URL` using a factory function that applies dialect-appropriate pool settings. PostgreSQL and MySQL SHALL use pool_size=20, max_overflow=10. SQLite SHALL use no connection pooling (StaticPool for in-memory, default for file-based). The `get_db()` FastAPI dependency SHALL remain unchanged — it SHALL auto-commit on success and auto-rollback on error.

#### Scenario: Successful request commits session
- **WHEN** a FastAPI handler completes without exception using `get_db()` dependency
- **THEN** the session SHALL be committed automatically

#### Scenario: Failed request rolls back session
- **WHEN** a FastAPI handler raises an exception
- **THEN** the session SHALL be rolled back and the exception re-raised

#### Scenario: PostgreSQL engine creation
- **WHEN** `DATABASE_URL` starts with `postgresql+asyncpg://`
- **THEN** the engine SHALL be created with `pool_size=20, max_overflow=10`

#### Scenario: MySQL engine creation
- **WHEN** `DATABASE_URL` starts with `mysql+aiomysql://`
- **THEN** the engine SHALL be created with `pool_size=20, max_overflow=10`

#### Scenario: SQLite in-memory engine creation
- **WHEN** `DATABASE_URL` is `sqlite+aiosqlite://`
- **THEN** the engine SHALL be created with `connect_args={"check_same_thread": False}` and `poolclass=StaticPool`

#### Scenario: SQLite file-based engine creation
- **WHEN** `DATABASE_URL` starts with `sqlite+aiosqlite:///` (with a file path)
- **THEN** the engine SHALL be created without connection pool overrides

#### Scenario: Unsupported dialect
- **WHEN** `DATABASE_URL` uses an unsupported scheme
- **THEN** a `ValueError` SHALL be raised at import time listing supported dialects
