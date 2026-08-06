## ADDED Requirements

### Requirement: RedisSessionStateStore persists SessionState to Redis
The services layer SHALL provide `RedisSessionStateStore` in `src/hecate/services/session_state/redis_store.py` implementing `SessionStateStore` for Redis-backed hot-path persistence.

The implementation SHALL use `redis.asyncio` (redis-py >= 5.0) for asynchronous I/O. The connection SHALL be lazily initialized on first use via `redis.asyncio.from_url(redis_url, decode_responses=True)`. The store SHALL accept a `redis_url: str` constructor parameter and MAY accept an optional `key_prefix: str` (default `"hecate:state:"`) and `ttl_seconds: int | None` parameter (default = configured `SESSION_STATE_TTL_DAYS * 86400`).

Serialization SHALL use `SessionState.model_dump_json()` for writes and `SessionState.model_validate_json()` for reads, per the engine-layer ABC contract.

The Redis key SHALL be `{key_prefix}{org_id}:{user_id}:{session_id}` where `{org_id}` is the Redis Cluster hash tag ensuring same-tenant data lands on the same slot.

#### Scenario: RedisSessionStateStore save writes to Redis with TTL
- **WHEN** `save(org_id, user_id, session_id, state)` is called and Redis is reachable
- **THEN** the implementation SHALL execute `SET <key> <json> EX <ttl_seconds>` and return successfully

#### Scenario: RedisSessionStateStore save fails gracefully when Redis is unavailable
- **WHEN** `save` is called and the Redis connection raises any exception
- **THEN** the implementation SHALL log a warning, swallow the exception, and return without raising

#### Scenario: RedisSessionStateStore load returns None for unknown session
- **WHEN** `load` is called for a session_id that has no key in Redis
- **THEN** the implementation SHALL return `None`

#### Scenario: RedisSessionStateStore load returns deserialized state for known session
- **WHEN** `load` is called for a known session and Redis returns the JSON string
- **THEN** the implementation SHALL deserialize via `SessionState.model_validate_json()` and return the parsed instance

#### Scenario: RedisSessionStateStore load propagates exceptions when Redis fails mid-read
- **WHEN** `load` is called and the Redis connection raises after the key was confirmed missing (network blip)
- **THEN** the implementation SHALL propagate the exception (caller decides whether to fall back to PG; the single-backend `RedisSessionStateStore` has no fallback)

#### Scenario: RedisSessionStateStore list_recent scans only same-tenant keys
- **WHEN** `list_recent(org_id, user_id, limit=10)` is called
- **THEN** the implementation SHALL use `SCAN MATCH <key_prefix>{org_id}:*` to enumerate keys, filter by `{user_id}:{session_id}` substring, deserialize each, sort by `updated_at` descending, and return the first `limit` `SessionSummary` entries

### Requirement: PostgresSessionStateStore persists SessionState to PostgreSQL via ORM
The services layer SHALL provide `PostgresSessionStateStore` in `src/hecate/services/session_state/postgres_store.py` implementing `SessionStateStore` for PostgreSQL-backed durable persistence.

The implementation SHALL use the existing async SQLAlchemy `async_session_factory` (from `hecate.core.database`) and the new `SessionStateModel` ORM class. The store SHALL accept an `async_session_factory` constructor parameter.

Serialization SHALL use `SessionState.model_dump_json()` / `model_validate_json()`. The `state` column SHALL be the PostgreSQL `JSONB` type for queryability.

The table SHALL be named `session_states` (configurable via `SESSION_STATE_PG_TABLE` setting) with composite primary key `(org_id, user_id, session_id)` and a secondary index `(org_id, user_id, updated_at DESC)` to support `list_recent` ordering.

#### Scenario: PostgresSessionStateStore save upserts via INSERT ON CONFLICT
- **WHEN** `save(org_id, user_id, session_id, state)` is called and a row with the same `(org_id, user_id, session_id)` already exists
- **THEN** the implementation SHALL execute an UPSERT (INSERT ON CONFLICT DO UPDATE) replacing the `state` JSONB and refreshing `updated_at`

#### Scenario: PostgresSessionStateStore load returns None for unknown session
- **WHEN** `load` is called for a session_id that has no row in `session_states`
- **THEN** the implementation SHALL return `None`

#### Scenario: PostgresSessionStateStore load returns deserialized state for known session
- **WHEN** `load` is called for a known session
- **THEN** the implementation SHALL deserialize via `SessionState.model_validate_json()` and return the parsed instance

#### Scenario: PostgresSessionStateStore list_recent orders by updated_at DESC
- **WHEN** `list_recent(org_id, user_id, limit=10)` is called
- **THEN** the implementation SHALL execute `SELECT ... WHERE org_id=? AND user_id=? ORDER BY updated_at DESC LIMIT ?` and return `SessionSummary` entries

#### Scenario: PostgresSessionStateStore save propagates database exceptions
- **WHEN** `save` is called and the underlying SQLAlchemy session raises any exception (connection lost, constraint violation)
- **THEN** the implementation SHALL propagate the exception to the caller (PG is the source of truth — failures must be loud)

### Requirement: TieredSessionStateStore composes Redis cache and Postgres durable store
The services layer SHALL provide `TieredSessionStateStore` in `src/hecate/services/session_state/tiered_store.py` implementing `SessionStateStore` for the production-recommended write-through + read-through + Redis-failure-degraded mode.

The implementation SHALL accept both a `redis_store: RedisSessionStateStore` and a `postgres_store: PostgresSessionStateStore` constructor parameter. The tiered store SHALL coordinate the two backends per the write-through and read-through protocols in this spec.

#### Scenario: TieredSessionStateStore save writes to Redis then Postgres (write-through)
- **WHEN** `save` is called and both backends are healthy
- **THEN** the implementation SHALL first call `redis_store.save()` then `postgres_store.save()`, in that order

#### Scenario: TieredSessionStateStore save continues when Redis fails but propagates Postgres failure
- **WHEN** `save` is called and `redis_store.save()` raises an exception (Redis unavailable)
- **THEN** the implementation SHALL log a warning, swallow the Redis exception, proceed to call `postgres_store.save()`, and propagate any exception from `postgres_store.save()`

#### Scenario: TieredSessionStateStore load hits Redis and returns immediately
- **WHEN** `load` is called and Redis returns the session JSON
- **THEN** the implementation SHALL deserialize and return without touching Postgres

#### Scenario: TieredSessionStateStore load falls back to Postgres on Redis miss and warms cache
- **WHEN** `load` is called and Redis returns no value (cache miss)
- **THEN** the implementation SHALL call `postgres_store.load()`, and if it returns a `SessionState`, SHALL call `redis_store.save()` to warm the cache before returning the state

#### Scenario: TieredSessionStateStore load falls back to Postgres on Redis failure
- **WHEN** `load` is called and `redis_store.load()` raises an exception (Redis unavailable)
- **THEN** the implementation SHALL log a warning, swallow the Redis exception, and call `postgres_store.load()` instead

#### Scenario: TieredSessionStateStore list_recent prefers Redis when available
- **WHEN** `list_recent` is called and Redis is reachable
- **THEN** the implementation SHALL delegate to `redis_store.list_recent()`

#### Scenario: TieredSessionStateStore list_recent falls back to Postgres on Redis failure
- **WHEN** `list_recent` is called and `redis_store.list_recent()` raises an exception
- **THEN** the implementation SHALL log a warning and delegate to `postgres_store.list_recent()`

### Requirement: SessionStateStoreFactory selects backend from settings
The services layer SHALL provide `create_session_state_store(settings) -> SessionStateStore` in `src/hecate/services/session_state/factory.py` that selects an implementation based on the `SESSION_STATE_STORE_BACKEND` setting value.

Supported backend values:
- `"memory"` — returns a new `InMemorySessionStateStore` (default, preserves backward compatibility)
- `"redis"` — returns a configured `RedisSessionStateStore` using `SESSION_STATE_REDIS_URL`
- `"postgres"` — returns a configured `PostgresSessionStateStore` using the shared `async_session_factory`
- `"tiered"` — returns a `TieredSessionStateStore` composing both Redis and Postgres

Unknown values SHALL raise `ValueError` at factory call time.

#### Scenario: factory returns InMemorySessionStateStore for memory backend
- **WHEN** `settings.SESSION_STATE_STORE_BACKEND == "memory"`
- **THEN** the factory SHALL return `InMemorySessionStateStore()`

#### Scenario: factory returns RedisSessionStateStore for redis backend
- **WHEN** `settings.SESSION_STATE_STORE_BACKEND == "redis"` and `settings.SESSION_STATE_REDIS_URL` is set
- **THEN** the factory SHALL return `RedisSessionStateStore(redis_url=..., key_prefix=..., ttl_seconds=...)`

#### Scenario: factory returns PostgresSessionStateStore for postgres backend
- **WHEN** `settings.SESSION_STATE_STORE_BACKEND == "postgres"`
- **THEN** the factory SHALL return `PostgresSessionStateStore(async_session_factory=...)`

#### Scenario: factory returns TieredSessionStateStore for tiered backend
- **WHEN** `settings.SESSION_STATE_STORE_BACKEND == "tiered"` and both Redis URL and PG factory are configured
- **THEN** the factory SHALL return `TieredSessionStateStore(redis_store=..., postgres_store=...)`

#### Scenario: factory raises ValueError for unknown backend
- **WHEN** `settings.SESSION_STATE_STORE_BACKEND == "elasticsearch"` (unknown)
- **THEN** the factory SHALL raise `ValueError` with a message listing supported values

### Requirement: Settings expose session-state-store configuration
The core configuration module (`src/hecate/core/config.py`) SHALL expose the following settings:

- `SESSION_STATE_STORE_BACKEND: str = "memory"` — one of `"memory"`, `"redis"`, `"postgres"`, `"tiered"`
- `SESSION_STATE_TTL_DAYS: int = 7` — idle TTL applied to both Redis `EX` and PG query filter
- `SESSION_STATE_REDIS_URL: str = ""` — Redis connection URL (e.g., `"redis://localhost:6379/0"`)
- `SESSION_STATE_PG_TABLE: str = "session_states"` — PG table name
- `SESSION_STATE_KEY_PREFIX: str = "hecate:state:"` — Redis key prefix for multi-app isolation

#### Scenario: Default settings preserve existing single-process behavior
- **WHEN** no settings are explicitly configured
- **THEN** `SESSION_STATE_STORE_BACKEND == "memory"` and `create_session_state_store(settings)` SHALL return `InMemorySessionStateStore`

#### Scenario: Tiered deployment sets all required settings
- **WHEN** an operator configures `SESSION_STATE_STORE_BACKEND=tiered`, `SESSION_STATE_REDIS_URL=redis://prod:6379/0`, and the standard PG `DATABASE_URL`
- **THEN** the factory SHALL construct a fully-functional `TieredSessionStateStore`

### Requirement: SessionStateModel ORM persists SessionState to PostgreSQL
The services layer SHALL provide `SessionStateModel` in `src/hecate/services/session_state/models.py` as a SQLAlchemy ORM model mapping to the `session_states` table with:

- Composite primary key `(org_id, user_id, session_id)` (UUID type)
- `state` column of JSONB-compatible type (SQLAlchemy `JSON`) — stored as `SessionState.model_dump_json()`
- `updated_at` column of `TIMESTAMPTZ` with `server_default=func.now()`
- `superstep` column of `INTEGER NULL`
- Secondary index `idx_session_states_org_user_updated` on `(org_id, user_id, updated_at DESC)`

A corresponding Alembic migration SHALL be added to create the table and index.

#### Scenario: SessionStateModel round-trips state via JSONB
- **WHEN** a `SessionStateModel` instance is constructed with `state={"key": "value"}` and persisted via the session factory
- **THEN** reading the row SHALL yield a `state` field deserializable by `SessionState.model_validate_json()`

### Requirement: pyproject.toml declares redis optional dependency
`pyproject.toml` SHALL declare a new `[redis]` optional-dependency group containing:
- `redis>=5.0,<6.0` (asynchronous Redis client)
- `fakeredis>=2.20` (in-memory Redis mock for unit tests)

This group SHALL NOT be in the `[dev]` group, as production deployments that choose not to use Redis (backend = memory or postgres) SHALL NOT be forced to install it.

#### Scenario: pip install ".[redis]" installs both packages
- **WHEN** `pip install -e ".[redis]"` is run
- **THEN** both `redis` and `fakeredis` SHALL be available for import

#### Scenario: pip install ".[dev]" without redis still works
- **WHEN** `pip install -e ".[dev]"` is run on a system without Redis installed
- **THEN** `SESSION_STATE_STORE_BACKEND="memory"` SHALL work because `redis` is lazy-imported inside `redis_store.py`

### Requirement: RedisSessionStateStore is single-slot only in Cluster mode
In Redis Cluster mode, `RedisSessionStateStore` SHALL guarantee that all keys for a given `(org_id, user_id)` land on the same slot via the `{org_id}` hash-tag pattern in the key.

`list_recent(org_id, user_id, ...)` SHALL only return sessions within the same `org_id` because Redis Cluster's `SCAN` cannot span slots. Cross-org listing is NOT supported.

#### Scenario: Same-tenant keys land on same slot via hash tag
- **WHEN** keys are constructed as `hecate:state:{org_id}:{user_id}:{session_id}`
- **THEN** Redis Cluster SHALL route all keys for the same `org_id` to the same slot, regardless of `user_id` or `session_id`

#### Scenario: list_recent returns only sessions within the same org_id
- **WHEN** keys exist for `{org_a}:{user_1}:{session_1}` and `{org_a}:{user_2}:{session_2}` and `{org_b}:{user_3}:{session_3}`
- **THEN** `list_recent(org_a, user_id=user_1)` SHALL return only sessions matching `{org_a}:{user_1}:*`

### Requirement: Integration tests use testcontainers for real Redis and Postgres
The test suite SHALL include integration tests under `tests/test_services/test_session_state/test_integration_*.py` that exercise real Redis and PostgreSQL via testcontainers.

Integration tests SHALL be marked with `@pytest.mark.integration` and skipped by default. They SHALL run only when the `--integration` pytest flag is provided or when the `RUN_INTEGRATION_TESTS=1` environment variable is set.

#### Scenario: Integration test runs against real PostgreSQL via testcontainers
- **WHEN** an integration test is executed with `RUN_INTEGRATION_TESTS=1`
- **THEN** the test SHALL spin up a PostgreSQL container, run the actual SQL queries, and verify behavior against the real database engine

#### Scenario: Integration test runs against real Redis via testcontainers
- **WHEN** an integration test is executed with `RUN_INTEGRATION_TESTS=1`
- **THEN** the test SHALL spin up a Redis container, run SET/GET/EXPIRE commands, and verify behavior against the real Redis server

#### Scenario: Unit tests run without Docker
- **WHEN** unit tests run in the default CI pipeline (no `RUN_INTEGRATION_TESTS=1`)
- **THEN** they SHALL use `fakeredis` for Redis simulation and `unittest.mock` for PG simulation, without requiring Docker