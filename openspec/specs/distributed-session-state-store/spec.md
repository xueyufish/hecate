# distributed-session-state-store Specification

## Purpose
分布式会话状态存储：为跨请求、跨副本的 Agent 会话提供可持久化的状态快照（channel state + agent state + event position + metadata）。支持 `memory` / `redis` / `postgres` / `tiered` 四种 backend，通过 FastAPI DI 注入生产 chat 请求路径，操作员可用环境变量切换 backend 而无需代码改动。
## Requirements
### Requirement: SessionState frozen dataclass aggregates per-session execution state
The engine SHALL define a `SessionState` Pydantic v2 frozen model in `src/hecate/engine/session_state.py` that aggregates the complete per-session state at a checkpoint boundary, including channel state, agent state, event position, and metadata.

The model SHALL have fields:
- `channel_state: dict[str, Any]` — full snapshot of all PregelRuntime channel values (subset of existing `CheckpointStore` `channel_state`)
- `agent_state: dict[str, Any]` — agent working state snapshot (covers existing `AgentState` fields: `summary`, `context`, `permission_context`, `tool_context`, `task_context`, `environment_root`, `metadata`)
- `event_position: int` — current EventStore consumption position (monotonically increasing per session, used for replay)
- `metadata: dict[str, Any]` — checkpoint metadata (e.g., `superstep`, `started_at`, `interrupted`, `interrupt_value`)

The model SHALL be frozen (immutable) so concurrent superstep snapshots cannot mutate each other's state. Any mutation SHALL return a new `SessionState` instance via Pydantic `model_copy(update=...)`.

#### Scenario: SessionState is frozen and rejects in-place mutation
- **WHEN** code attempts to assign `state.channel_state = {"x": 1}` on an existing `SessionState` instance
- **THEN** Pydantic SHALL raise `ValidationError` (frozen field assignment)

#### Scenario: SessionState can produce a copy with one updated field
- **WHEN** `state.model_copy(update={"event_position": 5})` is called
- **THEN** a new `SessionState` instance SHALL be returned with `event_position=5` and all other fields copied from the original

#### Scenario: SessionState serializes to and from JSON without loss
- **WHEN** a `SessionState` is serialized via `model_dump_json()` then deserialized via `SessionState.model_validate_json()`
- **THEN** the resulting instance SHALL be equal (same field values) to the original

### Requirement: SessionStateStore ABC defines save/load/list interface with tenant-scoped keys
The engine SHALL define `SessionStateStore` as an abstract base class in `src/hecate/engine/session_state.py` with methods:

- `async save(org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, state: SessionState) -> None`
- `async load(org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionState | None`
- `async list_recent(org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10) -> list[SessionSummary]`

The ABC SHALL accept `(org_id, user_id, session_id)` as the three-part key for every method to enforce multi-tenant isolation at the type level. Implementations SHALL NOT accept a single `session_id` parameter without the org/user dimensions.

`SessionSummary` SHALL be a Pydantic v2 frozen model with fields: `session_id: uuid.UUID`, `org_id: uuid.UUID`, `user_id: uuid.UUID`, `updated_at: datetime`, `superstep: int | None`.

#### Scenario: Save then load returns equal SessionState
- **WHEN** a `SessionState` is saved via `await store.save(org_id, user_id, session_id, state)` then loaded via `await store.load(org_id, user_id, session_id)`
- **THEN** the loaded state SHALL be equal (same field values) to the saved state

#### Scenario: Load returns None for unknown session
- **WHEN** `await store.load(org_id, user_id, session_id)` is called for a session that was never saved
- **THEN** the method SHALL return `None`

#### Scenario: list_recent returns at most limit summaries ordered by updated_at descending
- **WHEN** `await store.list_recent(org_id, user_id, limit=5)` is called and 8 sessions exist for `(org_id, user_id)`
- **THEN** the method SHALL return 5 summaries ordered by `updated_at` descending (most recent first)

#### Scenario: list_recent filters by org_id to enforce tenant isolation
- **WHEN** sessions exist for `(org_a, user_1)` and `(org_b, user_1)` with the same `user_id` but different `org_id`
- **THEN** `list_recent(org_a, user_id=user_1)` SHALL return only sessions for `org_a`, not `org_b`

#### Scenario: list_recent filters by user_id to enforce user isolation
- **WHEN** sessions exist for `(org_1, user_a)` and `(org_1, user_b)`
- **THEN** `list_recent(org_1, user_id=user_a)` SHALL return only sessions for `user_a`, not `user_b`

### Requirement: InMemorySessionStateStore implementation supports single-process use and tests
The engine SHALL provide `InMemorySessionStateStore` in `src/hecate/engine/session_state.py` implementing `SessionStateStore` for single-process use and unit testing.

Storage SHALL be a nested dict `_storage: dict[uuid.UUID, dict[uuid.UUID, dict[uuid.UUID, tuple[SessionState, datetime]]]]` keyed by `(org_id, user_id, session_id)`.

The implementation SHALL also maintain `_updated_at_index: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, datetime]]` for `list_recent` ordering without scanning all sessions.

#### Scenario: InMemorySessionStateStore save stores in nested dict
- **WHEN** `await store.save(org_id, user_id, session_id, state)` is called
- **THEN** the state SHALL be stored at `_storage[org_id][user_id][session_id]` with `updated_at` set to current UTC time

#### Scenario: InMemorySessionStateStore load returns None for unknown org_id
- **WHEN** `await store.load(org_id, user_id, session_id)` is called for an org_id that has no entries
- **THEN** the method SHALL return `None`

#### Scenario: InMemorySessionStateStore load returns None for known org_id but unknown session_id
- **WHEN** `await store.load(org_id, user_id, session_id)` is called where `(org_id, user_id)` has entries but `session_id` does not
- **THEN** the method SHALL return `None`

#### Scenario: InMemorySessionStateStore list_recent returns most recent first across all org_ids for a user
- **WHEN** sessions are saved at different times for the same `(org_id, user_id)` and `list_recent` is called
- **THEN** the result SHALL be ordered by `updated_at` descending (most recent first)

### Requirement: SessionStateStore raises NotFoundError-like exception for unknown session
The engine SHALL define `SessionNotFoundError(ValueError)` exception in `src/hecate/engine/session_state.py` that implementations MAY raise for not-found cases.

The default `load()` contract SHALL return `None` for not-found (per the `load` scenario above), but implementations MAY raise `SessionNotFoundError` instead when callers explicitly want exception-based error handling. Both behaviors SHALL be supported.

#### Scenario: Default load returns None on unknown session
- **WHEN** `load` is called for an unknown session on a standard implementation (e.g., `InMemorySessionStateStore`)
- **THEN** `None` SHALL be returned, not an exception

#### Scenario: Implementations may raise SessionNotFoundError for not-found
- **WHEN** an implementation chooses exception-based error handling and `load` is called for an unknown session
- **THEN** `SessionNotFoundError` SHALL be raised with a message including the `(org_id, user_id, session_id)` triple for diagnostics

### Requirement: SessionStateStore implementations must serialize SessionState via standard Pydantic v2 JSON
The engine SHALL require all `SessionStateStore` implementations to serialize `SessionState` via `SessionState.model_dump_json()` for storage and deserialize via `SessionState.model_validate_json()` for retrieval.

This requirement SHALL apply to all current and future implementations (`InMemorySessionStateStore`, future `RedisSessionStateStore`, `PostgresSessionStateStore`, etc.). Implementations SHALL NOT use `pickle`, `repr()`, or custom binary formats.

#### Scenario: InMemorySessionStateStore serializes via model_dump_json
- **WHEN** `InMemorySessionStateStore.save` stores a `SessionState`
- **THEN** the in-memory representation SHALL be the JSON string from `state.model_dump_json()`, not the raw Pydantic model

#### Scenario: Future Redis/Postgres implementations must use JSON serialization
- **WHEN** future implementations persist `SessionState` to any backend (Redis, PostgreSQL, etc.)
- **THEN** they SHALL use `SessionState.model_dump_json()` and `SessionState.model_validate_json()` exclusively

### Requirement: SessionState dataclass validates required field types at construction
The engine SHALL require `SessionState` to validate that:
- `channel_state` is a dict (not None, not other type)
- `agent_state` is a dict (not None, not other type)
- `event_position` is a non-negative int
- `metadata` is a dict (not None, not other type)

Default values SHALL be provided for optional convenience: `event_position=0`, `metadata={}`.

#### Scenario: SessionState rejects negative event_position
- **WHEN** `SessionState(channel_state={}, agent_state={}, event_position=-1, metadata={})` is constructed
- **THEN** Pydantic SHALL raise `ValidationError` because `event_position` must be non-negative

#### Scenario: SessionState accepts default values
- **WHEN** `SessionState(channel_state={}, agent_state={})` is constructed
- **THEN** `event_position` SHALL default to `0` and `metadata` SHALL default to `{}`

### Requirement: SessionStateStore extension point reserved for future MemoryProvider integration
The engine SHALL reserve a comment-documented extension point in `src/hecate/engine/session_state.py` for future integration with a `MemoryProvider` abstraction (long-term memory layer for cross-session user profiles, semantic facts, etc.).

The extension point SHALL NOT be implemented in this change. It SHALL be a single comment block referencing the planned future feature and explaining why the current `SessionStateStore` does not handle long-term memory.

#### Scenario: MemoryProvider extension point is documented but not implemented
- **WHEN** a developer reads `src/hecate/engine/session_state.py`
- **THEN** they SHALL find a comment block explaining the future `MemoryProvider` integration point and the rationale for separating short-term session state from long-term memory

### Requirement: SessionStateStore ABC methods are all async
The engine SHALL require all `SessionStateStore` ABC methods (`save`, `load`, `list_recent`) to be async functions. Synchronous implementations SHALL NOT be permitted at the type level.

#### Scenario: ABC methods declared async
- **WHEN** `SessionStateStore.save` is inspected
- **THEN** it SHALL be declared with `async def` signature

#### Scenario: Concurrent load calls do not block each other
- **WHEN** two `asyncio.create_task(store.load(...))` calls are issued concurrently for different session_ids
- **THEN** both SHALL complete concurrently without serializing on each other (async semantics)

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

### Requirement: WorkflowExecutionService 接受可选 SessionStateStore 参数
services 层 `WorkflowExecutionService`（`src/hecate/services/workflow/execution_service.py`）的构造函数 SHALL 接受可选参数 `checkpoint_store: SessionStateStore | None = None`。

当参数为 `None` 时，service SHALL per-request 创建 `InMemoryCheckpointStore()`（engine 层 ABC，**不是** `SessionStateStore`）用于 `PregelRuntime` 的中间 superstep 回滚需求，与既有默认行为一致。当参数为 `None` 时 service 继续工作但**无**跨请求持久化能力（旧单请求模式）。

当参数被提供时，service SHALL 用 `self._checkpoint_store` 作为跨请求 `SessionStateStore`，用于 `SessionState` 的 save 和 load 操作。

#### Scenario: 默认构造函数保留既有行为
- **WHEN** `WorkflowExecutionService` 构造时不传 `checkpoint_store`（与现有 23 个测试一致）
- **THEN** 构造函数接受调用无错误
- **THEN** `self._checkpoint_store is None`
- **THEN** execute() per-request 创建 `InMemoryCheckpointStore()`，与既有行为一致

#### Scenario: wired 构造函数存储 store
- **WHEN** `WorkflowExecutionService` 构造时传 `checkpoint_store=<SessionStateStore>`
- **THEN** `self._checkpoint_store is <SessionStateStore>`
- **THEN** execute() 使用 `self._checkpoint_store` 进行 save/load，代替 per-request in-memory 模式

### Requirement: WorkflowExecutionService 通过 SessionStateStore 持久化 AgentState
services 层 `WorkflowExecutionService.execute` 方法 SHALL 通过写入一个 `SessionState`（其 `agent_state` 字段为 `AgentState.model_dump(mode="json")` 的结果）来持久化每会话状态。

保存流程 SHALL 替换既有的对 `self._state_store`（deprecated `AgentStateStore`）的写操作。具体来说，`execution_service.py` line 310-313 和 392-393（流式后和执行后的 `agent_state.save(...)` 调用）的两次写 SHALL 合并为一次 `SessionStateStore.save(...)` 调用，将 `channel_state` + `agent_state` + `event_position` + `metadata` 打包成单个快照。

加载流程（line 215-218 的 `self._state_store.load(...)`）SHALL 替换为 `SessionStateStore.load(...)` 后接 `AgentState.model_validate(state.agent_state)` 以重建类型化 agent state。

`PregelRuntime` 内部使用的 engine 层 `CheckpointStore`（line 281: `checkpoint_store = InMemoryCheckpointStore()`）SHALL 保持不变——它服务 `PregelRuntime` 的中间 superstep 回滚需求，与跨请求持久化无关。

#### Scenario: execute 保存带合并 agent_state 的 SessionState
- **WHEN** execute() 完成且 `self._checkpoint_store` 已提供
- **THEN** `self._checkpoint_store.save(org_id, user_id, session_id, state)` 被调用恰好一次
- **THEN** 保存的 `state.agent_state` 是匹配 `AgentState.model_dump(mode="json")` 的 JSON 序列化 dict——所有字段都在（`summary`、`context`、`permission_context`、`tool_context`、`task_context`、`environment_root`、`metadata`）

#### Scenario: execute 加载 SessionState 并重建 AgentState
- **WHEN** execute() 以已知 `(org_id, user_id, session_id)` 三元组开始且 `self._checkpoint_store` 已提供
- **THEN** `state = await self._checkpoint_store.load(org_id, user_id, session_id)` 被调用
- **THEN** 如果 `state is not None`，`agent_state = AgentState.model_validate(state.agent_state)` 重建类型化模型
- **THEN** 如果 `state is None` 或 `model_validate` 抛 `ValidationError`，新建 `AgentState(session_id=session_id, agent_id=agent_id)` 并 log warning

#### Scenario: engine 层 CheckpointStore 保持 per-request in-memory
- **WHEN** execute() 运行（无论 `self._checkpoint_store` 是否提供）
- **THEN** 构造 `checkpoint_store = InMemoryCheckpointStore()` 给 `PregelRuntime` 的代码行不变
- **THEN** `runtime_checkpoint_store` 是 engine 层 ABC，`self._checkpoint_store`（如设）是 services 层 ABC——两者在一次 execute() 调用中共存

### Requirement: get_session_state_store FastAPI 依赖
FastAPI 依赖函数 `get_session_state_store` SHALL 定义在 `src/hecate/core/deps_state_store.py`。

依赖 SHALL 读 `request.app.state.session_state_store`。如果该属性未设置（例如绕过 FastAPI lifespan 的测试），依赖 SHALL fallback 到用 `create_session_state_store(settings)` 构造一个新 store，其中 `settings` 是 `hecate.core.config` 的模块级单例。

依赖 SHALL 返回一个 `SessionStateStore` 实例。绕过 lifespan 的测试和代码路径 SHALL 观察到 fallback 行为（`SESSION_STATE_STORE_BACKEND="memory"` 时是全新的 `InMemorySessionStateStore`）。

#### Scenario: 依赖读 app.state 单例
- **WHEN** `get_session_state_store` 在 FastAPI 请求内被调用
- **THEN** 它返回 `request.app.state.session_state_store`（lifespan 中初始化的单例）

#### Scenario: 依赖在 app.state 未设置时 fallback 到 factory
- **WHEN** `get_session_state_store` 在 FastAPI lifespan 外部被调用（测试、脚本）
- **THEN** 它返回 `create_session_state_store(settings)`——一个新 store，当前 `settings.SESSION_STATE_STORE_BACKEND` 决定实现

#### Scenario: 依赖始终返回可用的 SessionStateStore
- **WHEN** `get_session_state_store` 在任何条件下被调用
- **THEN** 返回的对象是 `SessionStateStore` 的子类，可立即用于 save/load/list_recent

### Requirement: main.py lifespan 初始化 app.state 单例
`src/hecate/main.py` 应用 lifespan SHALL 在启动过程中初始化 `app.state.session_state_store` 恰好一次，使用 `hecate.services.session_state` 中的 `create_session_state_store(settings)`。

初始化 SHALL 在 `Base.metadata.create_all`（或等价 migration）运行之后、任何请求处理器被调用之前发生。单例 MUST 在 worker 内的请求间持续存在，但 MAY 在多 worker 部署中每个 worker 进程重新创建（每个 worker 拥有自己的 store 实例和自己的连接池）。

INFO 级别的日志行 SHALL 记录从 `settings.SESSION_STATE_STORE_BACKEND` 选择的活跃 backend。日志行 MUST 包含字面量的 backend 字符串，让操作员能在运行时确认正确 backend。（Change 2 已加类似日志行——本 requirement 确认并固定格式。）

#### Scenario: lifespan 在服务请求前设置 app.state.session_state_store
- **WHEN** FastAPI 应用启动
- **THEN** 在第一个请求被服务前，`app.state.session_state_store` 非 None，是 `SessionStateStore` 实例

#### Scenario: 启动时输出 backend 日志行
- **WHEN** 应用启动
- **THEN** 日志行 `"SessionStateStore backend=<value>"` 出现，其中 `<value>` 匹配 `settings.SESSION_STATE_STORE_BACKEND`
- **THEN** 操作员能从容器日志确认运行时 backend

#### Scenario: 多 worker 部署中的 per-worker 隔离
- **WHEN** 应用在 N workers 的 gunicorn/uvicorn 下运行
- **THEN** 每个 worker 进程有自己的 `app.state.session_state_store` 实例和自己的 Redis 连接池（无 cross-worker 共享）
- **THEN** 行为在 OpenSpec 中记录，让操作员理解 per-worker 隔离模型

### Requirement: chat.py 使用 Depends 注入 SessionStateStore
`src/hecate/api/v1/chat.py` 中 line 214 的 chat endpoint（`WorkflowExecutionService(...)` 构造点）SHALL 传 `checkpoint_store=Depends(get_session_state_store)` 让 FastAPI 把单例注入每个 chat 请求。

chat endpoint SHALL NOT 在不带 `checkpoint_store` 的情况下构造 `WorkflowExecutionService`。旧模式 `WorkflowExecutionService(port=port, db=db)`（不带 `checkpoint_store`）保留给显式 opt-out 跨请求持久化的测试和直接 service 调用方。

`WorkflowExecutionService` 上的 deprecated `state_store` 参数 SHALL NOT 由 chat.py 传入——只使用 `checkpoint_store`。这避免设计 risk 章节描述的双写窗口。

#### Scenario: chat endpoint 传 Depends 注入的 store
- **WHEN** 一个 chat completion 请求进入 `chat.py`
- **THEN** `WorkflowExecutionService` 构造时带 `checkpoint_store=<FastAPI 注入的 SessionStateStore>`
- **THEN** `state_store` 参数不传（省略或为 None）

#### Scenario: chat endpoint 在请求间共享单例
- **WHEN** 多个 chat 请求命中同一个 worker
- **THEN** 所有请求使用同一个 `app.state.session_state_store` 实例（worker 单例）
- **THEN** 单例的连接池在请求间复用（无 per-request 池创建）

### Requirement: AgentStateStore 参数被降级
`WorkflowExecutionService.__init__` 上的 `state_store: AgentStateStore | None` 参数 SHALL 通过 Python docstring 降级标记（`.. deprecated::`）。

参数 SHALL 继续工作以保持向后兼容（既有测试使用它），但新代码路径 SHALL NOT 传它。参数计划在后续清理 change 中删除（Change 4 `horizontal-scaling-validation` 确认 `SessionStateStore` 路径的生产行为后）。

#### Scenario: deprecated 参数仍为测试工作
- **WHEN** 既有测试构造 `WorkflowExecutionService(port=port, state_store=mock_state_store)`
- **THEN** 构造函数接受调用
- **THEN** `self._state_store = mock_state_store`
- **THEN** docstring 含降级标记

#### Scenario: deprecated 参数不在生产路径中使用
- **WHEN** `chat.py` 构造 `WorkflowExecutionService`
- **THEN** `state_store` 参数省略
- **THEN** `self._state_store` 为 `None`，旧加载路径跳过

### Requirement: 默认 memory backend 向后兼容
当 `settings.SESSION_STATE_STORE_BACKEND` 是 `"memory"`（Change 2 设置的默认值），wired `SessionStateStore` SHALL 是 `InMemorySessionStateStore`。从不设置环境变量的单实例部署 SHALL 观察到与 Change 3 前完全一致的行为：跨请求 state 在请求结束时丢失。

不需要数据迁移，已有测试 SHALL NOT 被修改。所有 23 个既有 `WorkflowExecutionService` 测试 SHALL 零编辑通过。

#### Scenario: 默认 backend 产出 in-memory 单例
- **WHEN** `settings.SESSION_STATE_STORE_BACKEND == "memory"`
- **THEN** `create_session_state_store(settings)` 返回 `InMemorySessionStateStore`
- **THEN** lifespan 设置 `app.state.session_state_store = <InMemorySessionStateStore>`
- **THEN** 跨请求持久化不可用（单请求模式，与 Change 3 前一致）

#### Scenario: 既有 23 个测试零修改通过
- **WHEN** 既有测试套件 `tests/test_services/test_workflow/test_execution_service.py` 运行
- **THEN** 所有 23 个测试零编辑通过

### Requirement: 操作员 opt-in 到分布式 backend
操作员 SHALL 能通过环境变量设置 `SESSION_STATE_STORE_BACKEND` 为 `"redis"`、`"postgres"` 或 `"tiered"` 把默认 `memory` backend 切换为分布式 backend。不需要代码改动——factory 统一处理四种 backend。

设置 `SESSION_STATE_STORE_BACKEND=tiered` 后，chat 路径 SHALL 使用 tiered Redis + PostgreSQL backing store，让跨请求和跨副本的 session 持久化生效。

#### Scenario: 设置环境变量后 tiered backend 生效
- **WHEN** 操作员设置 `SESSION_STATE_STORE_BACKEND=tiered` 并重启服务
- **THEN** `create_session_state_store(settings)` 返回 `TieredSessionStateStore`
- **THEN** `app.state.session_state_store` 是 tiered store
- **THEN** 跨请求和跨副本 session state 通过 Redis + PostgreSQL 持久化

#### Scenario: redis-only 和 postgres-only backend 也生效
- **WHEN** 操作员设置 `SESSION_STATE_STORE_BACKEND=redis` 或 `=postgres`
- **THEN** 对应的单 backend 实现被使用
- **THEN** chat 请求使用配置的 backend，无代码改动

### Requirement: 流式执行路径 SHALL 通过 SessionStateStore 持久化 agent_state
services 层 `WorkflowExecutionService._stream_execute`（`src/hecate/services/workflow/execution_service.py`）SHALL 在 stream 正常结束后调用 `_persist_session_state` 一次，将 `agent_state.model_dump(mode="json")` 写入 wired `SessionStateStore`。

stream 异常断开（client disconnect / timeout / 未捕获异常）时，`_stream_execute` SHALL 在异常重抛前 best-effort 调用 `_persist_session_state`。best-effort 调用的失败 SHALL 被 swallow 并 log warning——in-memory `agent_state` 在当前请求内仍有效。

`_stream_execute` SHALL NOT 使用 deprecated `self._state_store`（`AgentStateStore`）作为保存路径——line 473-474 的 legacy 分支 SHALL 被移除。

#### Scenario: stream 正常结束触发 atomic save
- **WHEN** `_stream_execute` 的 generator 正常耗尽（所有 event 已 yield）
- **THEN** `_persist_session_state(agent_state, session_id, agent_id, org_id, user_id)` 被调用恰好一次
- **THEN** 写入的 `SessionState.agent_state` 匹配 `agent_state.model_dump(mode="json")`

#### Scenario: stream 异常断开触发 best-effort save
- **WHEN** `_stream_execute` 因 client disconnect / timeout / 异常中断
- **THEN** 在异常重抛前，best-effort 调用 `_persist_session_state`
- **THEN** best-effort 调用失败时 swallow 并 log warning，原异常重抛

#### Scenario: 流式路径不再走 legacy AgentStateStore
- **WHEN** `_stream_execute` 执行（无论 `self._checkpoint_store` 是否提供）
- **THEN** `self._state_store.save(...)` 不被调用
- **THEN** `if self._state_store and agent_state:` 分支被移除

### Requirement: SessionStateStore SHALL 提供可选的 session 锁接口
engine 层 `SessionStateStore` ABC（`src/hecate/engine/session_state.py`）SHALL 新增 `acquire_session_lock` 异步上下文管理器方法，签名为：

```python
@asynccontextmanager
async def acquire_session_lock(
    self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, *, timeout_ms: int = 30000
) -> AsyncGenerator[None, None]: ...
```

默认实现 SHALL 是 no-op（直接 yield），让 `InMemorySessionStateStore` 等单进程实现无需覆盖。

锁获取失败 SHALL 抛 `SessionStateConflictError(SessionStateError)`（新增异常，继承 `Exception`），消息包含 `(org_id, user_id, session_id)` 三元组用于诊断。

#### Scenario: 默认实现是 no-op
- **WHEN** `InMemorySessionStateStore.acquire_session_lock(org, user, session)` 被调用
- **THEN** 直接 yield，不抛异常，不阻塞

#### Scenario: 锁获取失败抛 SessionStateConflictError
- **WHEN** 并发请求竞争同一 `(org_id, user_id, session_id)` 且 retry 次数耗尽
- **THEN** `SessionStateConflictError` 被抛出，消息含三元组

### Requirement: RedisSessionStateStore SHALL 用 SET NX + 原子 owner 校验 release 实现锁
`src/hecate/services/session_state/redis_store.py` 的 `RedisSessionStateStore.acquire_session_lock` SHALL：

1. 生成 owner UUID（每次调用唯一）
2. 执行 `SET lock:{org}:{user}:{session} {owner_uuid} NX PX {timeout_ms}`
3. SET 成功则进入临界区；失败则 retry（jitter 20-150ms，最多 3 次），仍失败抛 `SessionStateConflictError`
4. 退出临界区时执行 owner 校验的 release——仅当 key 的当前值等于本 owner UUID 时才删除，确保不会误删其他 client 的锁

锁 key SHALL 使用与 session data 相同的 `{org_id}` hash tag，保证 Redis Cluster 下两者在同一 slot。

#### Scenario: SET NX 成功获取锁
- **WHEN** `acquire_session_lock(org, user, session)` 调用且 lock key 不存在
- **THEN** Redis 执行 `SET lock:{org}:{user}:{session} {uuid} NX PX 30000` 返回 OK
- **THEN** 临界区执行
- **THEN** 退出时基于 owner 校验删除 key（验证 owner UUID 匹配）

#### Scenario: SET NX 失败触发 retry
- **WHEN** `acquire_session_lock` 调用且 lock key 已被其他 owner 持有
- **THEN** retry 3 次，每次前 `asyncio.sleep(random.uniform(0.020, 0.150))`
- **THEN** 3 次都失败则抛 `SessionStateConflictError`

#### Scenario: owner 校验 release 防止误删他人锁
- **WHEN** lock 已超时，其他 client 获取了新锁
- **THEN** 原 client 的 release 检查 owner UUID 不匹配，返回 0，不删除

### Requirement: PostgresSessionStateStore SHALL 用 SELECT FOR UPDATE 在事务内串行化
`src/hecate/services/session_state/postgres_store.py` 的 `PostgresSessionStateStore` SHALL 在 `save` 方法的事务内用 `SELECT ... FOR UPDATE` 锁定 `(org_id, user_id, session_id)` 对应行（如存在），自然串行化并发写。

`acquire_session_lock` 方法 SHALL 是 no-op（继承 default）—— PG 行锁在 save 事务内自动生效，不需要显式 acquire。

#### Scenario: 并发 save 自然串行化
- **WHEN** 两个并发 `save(org, user, session, state)` 请求到达
- **THEN** PG 行锁让第二个等待第一个事务提交
- **THEN** 第二个 save 在第一个 commit 后执行，覆盖前一个（last-write-wins 在 PG 隔离级别内）

#### Scenario: 新 session 无行可锁直接 INSERT
- **WHEN** `save` 调用且 `(org, user, session)` 不存在
- **THEN** `SELECT FOR UPDATE` 返回空，执行 INSERT ON CONFLICT DO UPDATE

### Requirement: TieredSessionStateStore SHALL 用 Redis 锁 + PG 行锁双保险
`src/hecate/services/session_state/tiered_store.py` 的 `TieredSessionStateStore.acquire_session_lock` SHALL 委托给 `redis_store.acquire_session_lock`（Redis SETNX 锁）。`save` 方法 SHALL 内部调用 `postgres_store.save`（自动带 PG 行锁）。

Redis 锁失败时（Redis 不可用），Tiered SHALL fallback 到只用 PG 行锁（记录 Redis 异常并 log warning）。

#### Scenario: Redis 锁 + PG 行锁同时生效
- **WHEN** 并发 `save` 到 Tiered backend 且 Redis/PG 都健康
- **THEN** Redis SETNX 让第二个请求 retry
- **THEN** 即便 Redis 锁失效（failover），PG 行锁兜底串行化

#### Scenario: Redis 故障时降级到 PG 单锁
- **WHEN** `acquire_session_lock` 调用且 Redis 不可用
- **THEN** swallow Redis 异常并 log warning
- **THEN** 继续进入临界区，依赖 PG 行锁保证一致性

### Requirement: _persist_session_state SHALL 用 jitter retry + fail-fast 策略
`WorkflowExecutionService._persist_session_state`（`src/hecate/services/workflow/execution_service.py`）SHALL 在调用 `save` 前用 `acquire_session_lock` 包裹，retry 3 次（jitter 20-150ms 随机），仍失败抛 `SessionStateConflictError`。

`_persist_session_state` SHALL NOT 在锁失败或 save 失败时 fallback 到 legacy `self._state_store.save`——fallback 分支 SHALL 被移除（保留 fallback 会造成双 store 状态分裂）。

#### Scenario: 锁获取 retry 3 次后成功
- **WHEN** 首次 `acquire_session_lock` 失败但 retry 1-2 次内成功
- **THEN** 进入临界区执行 save，无异常抛出

#### Scenario: 锁 retry 3 次全失败抛 SessionStateConflictError
- **WHEN** retry 3 次后仍无法获取锁
- **THEN** `SessionStateConflictError` 抛出，消息含 `(org_id, user_id, session_id)`
- **THEN** 异常 propagate 到 chat handler，返回 409 Conflict（或 chat endpoint 的等价错误响应）

#### Scenario: fallback 移除不影响既有测试
- **WHEN** 既有 `test_execution_service.py` 测试运行（不传 `checkpoint_store`）
- **THEN** `_persist_session_state` 因 `self._checkpoint_store is None` 直接 return（不进入锁/save 分支）
- **THEN** 不调用 `self._state_store.save`

### Requirement: _persist_session_state SHALL 暴露 OTel span 和结构化日志
`WorkflowExecutionService._persist_session_state` SHALL 用 OpenTelemetry tracer 创建名为 `session_state.persist` 的 span，包含属性：
- `session.id`：session_id 字符串
- `session.backend`：`type(self._checkpoint_store).__name__`
- `session.save.success`：布尔
- `session.save.latency_ms`：浮点
- `session.lock.acquired`：布尔（锁是否获取成功）

`_persist_session_state` SHALL 在每次调用后输出结构化日志（`logger.info("session_state_persist", extra={...})`），字段同上。

OTel 集成 SHALL 复用现有 `hecate.observability` 模块，SHALL NOT 引入新的可观测性依赖（如 prometheus_client）。

#### Scenario: 成功 save 产生 success span
- **WHEN** `_persist_session_state` 成功完成
- **THEN** OTel span 属性 `session.save.success=True`
- **THEN** span 属性 `session.save.latency_ms` 反映实际耗时
- **THEN** 结构化日志输出对应字段

#### Scenario: 锁失败产生 failure span
- **WHEN** `SessionStateConflictError` 抛出
- **THEN** span 属性 `session.save.success=False`、`session.lock.acquired=False`
- **THEN** span 通过 `record_exception` 记录异常
- **THEN** 结构化日志输出 `level=warning`

#### Scenario: OTel 可通过配置关闭
- **WHEN** 环境变量 `OTEL_ENABLED=false`（或现有等价配置）
- **THEN** span 不创建（仅有结构化日志）
- **THEN** latency 开销 < 0.5ms

### Requirement: 性能基准测试 SHALL 验证 wired backend 的 latency 阈值
`tests/test_services/test_session_state/test_perf.py` SHALL 包含参数化测试，对比 `InMemorySessionStateStore` / `RedisSessionStateStore`（用 fakeredis）/ `PostgresSessionStateStore`（用 mock）的 save/load latency。

测试 SHALL 测量 1000 次操作，验证以下阈值：

| 指标 | 阈值 |
|------|------|
| InMemory save p95 | < 1ms |
| fakeredis save p95 | < 5ms |
| mock PG save p95 | < 10ms |
| 平台开销（wired - unwired） p95 | < 10ms |

测试 SHALL 使用 `time.monotonic()` 测量，SHALL NOT 依赖外部基础设施（testcontainers 留给 integration 测试）。

#### Scenario: fakeredis save p95 满足 5ms 阈值
- **WHEN** 测试用 fakeredis 跑 1000 次 save
- **THEN** p95 latency < 5ms
- **THEN** p99 latency < 20ms

#### Scenario: InMemory baseline 不引入 > 1ms 开销
- **WHEN** 测试用 InMemorySessionStateStore 跑 1000 次 save
- **THEN** p95 latency < 1ms

### Requirement: services-layer SessionStateStore SHALL NOT 做 per-superstep checkpoint
services 层 `SessionStateStore` SHALL 在每个 chat 请求内执行**恰好 1 次** atomic save（流式路径在 stream-end，非流式路径在 execute-end）。

services-layer SHALL NOT 在 PregelRuntime 的 per-superstep 边界调用 `SessionStateStore.save`——per-superstep 持久化是 engine-layer `CheckpointStore`（`InMemoryCheckpointStore` / `PostgresCheckpointStore`）的职责，用于 PregelRuntime 中间 superstep 回滚。

本 requirement 是经过业界对比的有意决策：
- LangGraph production 教训显示 per-superstep checkpoint 导致 180KB blob / 11s resume / 10GB 表
- BSWEN 实战显示减少 78% checkpoint writes 显著降低 DB 压力
- 我们的 PregelRuntime 内部已有 engine-layer CheckpointStore 处理中间状态

#### Scenario: 每请求恰好 1 次 SessionStateStore.save
- **WHEN** 一个 chat 请求内部 PregelRuntime 跑 N 个 superstep
- **THEN** `SessionStateStore.save` 被调用恰好 1 次（在请求结束）
- **THEN** PregelRuntime 内部的 engine-layer `InMemoryCheckpointStore` 在每个 superstep 后写（与 services-layer 无关）

#### Scenario: contributor 试图加 per-superstep 写会被 spec 阻止
- **WHEN** 未来 contributor 在 design.md 或 code review 中提议 per-superstep services-layer 写
- **THEN** 此 requirement 明确禁止，需先修改 spec

## ADDED Requirements

### Requirement: AgentStateStore ABC is marked deprecated via PEP 562 __deprecated__ module attribute

The engine SHALL mark `hecate.services.state.store` as a deprecated module by setting `__deprecated__ = ("Use hecate.engine.session_state.SessionStateStore instead.",)` at module level. The deprecation message SHALL direct users to `hecate.engine.session_state.SessionStateStore` as the replacement abstraction.

`__deprecated__` SHALL be set in the `store` submodule only (not in `state` submodule, which defines the still-used `AgentState` Pydantic model).

The `AgentStateStore` ABC and `InMemoryStateStore` class docstrings SHALL begin with a `.. deprecated::` Sphinx directive referencing this change and the migration guide at `docs/migrations/agent-state-store.md`.

#### Scenario: import hecate.services.state.store triggers no warning at import time
- **WHEN** a module imports `from hecate.services.state.store import AgentStateStore`
- **THEN** no `DeprecationWarning` is emitted at import time (only on attribute access, per PEP 562 semantics)

#### Scenario: accessing AgentStateStore via the deprecated module triggers DeprecationWarning
- **WHEN** Python evaluates `from hecate.services.state.store import AgentStateStore` and the module is marked with `__deprecated__`
- **THEN** Python SHALL emit `DeprecationWarning` at attribute access time with the configured message
- **THEN** the warning's `stacklevel` SHALL be `1` (PEP 562 default; import site is reported)

#### Scenario: AgentStateStore docstring contains deprecated directive
- **WHEN** a developer reads `AgentStateStore.__doc__` via `help(AgentStateStore)` or introspection
- **THEN** the docstring SHALL contain a `.. deprecated::` directive
- **THEN** the directive's text SHALL reference `hecate.engine.session_state.SessionStateStore` and the migration guide URL

### Requirement: AgentStateStore construction emits DeprecationWarning with stacklevel=2

The `AgentStateStore` ABC and `InMemoryStateStore` class SHALL emit a `DeprecationWarning` when their `__init__` methods are invoked. The warning SHALL be issued from inside `__init__` with `stacklevel=2` so the call site (not the ABC definition) is reported.

The warning message SHALL follow the format `"AgentStateStore is deprecated. Use hecate.engine.session_state.SessionStateStore instead. See docs/migrations/agent-state-store.md for migration."`. The `AgentStateStore` ABC SHALL NOT raise on construction — `__init__` is empty (no-op), so the deprecation is emitted via a class-level `__init_subclass__` hook OR by adding `__init__` to the concrete `InMemoryStateStore` only (the ABC is abstract and cannot be instantiated directly via Python semantics).

The concrete `InMemoryStateStore.__init__` SHALL emit the warning. The ABC `AgentStateStore` SHALL NOT have a callable `__init__`; users who attempt to instantiate the ABC directly SHALL receive Python's standard `TypeError: Can't instantiate abstract class` (no `DeprecationWarning` because the failure precedes construction).

#### Scenario: InMemoryStateStore() construction emits DeprecationWarning
- **WHEN** a caller invokes `InMemoryStateStore()` to construct an in-memory store
- **THEN** Python SHALL emit a `DeprecationWarning`
- **THEN** the warning message SHALL mention `SessionStateStore` as the replacement
- **THEN** the warning's reported source location SHALL be the caller (caller's line, not the class definition), achieved via `warnings.warn(..., stacklevel=2)`

#### Scenario: existing tests that import AgentStateStore do not change behavior under default warning filter
- **WHEN** `tests/test_services/test_state/test_state.py` runs under pytest's default warning filter
- **THEN** `DeprecationWarning` is suppressed by Python's default `default` filter (warnings shown once per source location; pytest's default may be `error` for some configs but `default` is the project baseline per `pyproject.toml`)
- **THEN** all 23 existing `WorkflowExecutionService` tests SHALL pass without modification
- **THEN** the project test command `pytest tests/ -q` SHALL remain green

#### Scenario: explicit warning visibility in CI for deprecation tracking
- **WHEN** an operator runs `python -W default::DeprecationWarning -m pytest tests/`
- **THEN** the warnings from `InMemoryStateStore()` construction SHALL appear in test output
- **THEN** the deprecation count SHALL be deterministic (one per construction site per test run)

### Requirement: WorkflowExecutionService.state_store parameter emits DeprecationWarning at construction

`services/workflow/execution_service.py` `WorkflowExecutionService.__init__` SHALL emit a `DeprecationWarning` when the `state_store` parameter is provided (not `None`). The warning SHALL be emitted with `stacklevel=2` so the caller's source line is reported.

The parameter SHALL remain in the signature (no removal) to preserve backward compatibility with existing 23 tests in `tests/test_services/test_workflow/test_execution_service.py` that use `state_store=mock_state_store`. The docstring SHALL begin with `.. deprecated::` referencing the migration to `checkpoint_store` (the already-recommended path per the existing `distributed-session-state-store` spec line 437).

The `state_store` parameter is `Optional[AgentStateStore]`. When `None` (the default), no warning is emitted (the deprecated path is not entered). When provided, the warning fires and the parameter is stored in `self._state_store` exactly as before — no behavioral change.

The chat.py production path (`/v1/chat/completions`) already does NOT pass `state_store` per the existing spec (line 437), so production traffic will never see this warning. The warning is purely diagnostic for direct callers and tests.

#### Scenario: WorkflowExecutionService(state_store=mock) emits DeprecationWarning
- **WHEN** a caller constructs `WorkflowExecutionService(port=port, state_store=mock_state_store)` with the deprecated parameter
- **THEN** Python SHALL emit a `DeprecationWarning` with message mentioning `checkpoint_store` (the replacement) and the migration guide
- **THEN** the `state_store` SHALL be stored in `self._state_store` (no behavioral change)
- **THEN** `self._state_store is not None` (the legacy load path remains functional for backward compat)

#### Scenario: WorkflowExecutionService() default construction emits no warning
- **WHEN** a caller constructs `WorkflowExecutionService(port=port)` without `state_store`
- **THEN** no `DeprecationWarning` is emitted (the deprecated path is not entered)
- **THEN** `self._state_store is None` (the legacy path is skipped; only the new `checkpoint_store` path is active when one is wired)

#### Scenario: chat.py production path emits no deprecation warning
- **WHEN** `src/hecate/api/v1/chat.py` constructs `WorkflowExecutionService(port=port, db=db, event_store=..., checkpoint_store=...)` for an incoming chat request
- **THEN** no `state_store` parameter is passed
- **THEN** no `DeprecationWarning` is emitted
- **THEN** the chat response is identical to pre-deprecation behavior

#### Scenario: existing 23 execution_service tests continue to pass
- **WHEN** `pytest tests/test_services/test_workflow/test_execution_service.py -q` runs
- **THEN** all 23 existing tests pass
- **THEN** tests that pass `state_store=mock_state_store` continue to work, with `DeprecationWarning` suppressed by default pytest filter
- **THEN** no test assertion or mock is broken by the deprecation warning

### Requirement: User migration documentation is provided at docs/migrations/agent-state-store.md

`docs/migrations/agent-state-store.md` SHALL exist as a Markdown guide for users migrating from `AgentStateStore` to `SessionStateStore`. The guide SHALL contain:

1. **Why deprecated** (1 paragraph): explain that `SessionStateStore` is the production-ready replacement with multi-tenant keying, multi-backend support, and lock semantics.
2. **Migration mapping** (1 table): for each `AgentStateStore` method (`save`, `load`, `delete`, `list_sessions`), map to the `SessionStateStore` equivalent (`save`, `load`, `list_recent`). Note that `delete` is intentionally not in `SessionStateStore` (TTL handles expiration; if a hard delete is needed, document the workaround using `redis.delete(key)` for the Redis backend or `DELETE FROM session_states WHERE ...` for the PG backend).
3. **Key migration** (1 example): show that the key changes from `(agent_id, session_id)` to `(org_id, user_id, session_id)`. Document how to obtain the three-tuple from the current request context.
4. **Code example** (1 Python snippet): before/after — show `await state_store.save(agent_id, session_id, AgentState(...))` and the equivalent `await session_state_store.save(org_id, user_id, session_id, SessionState(agent_state=agent_state.model_dump(mode='json')))`.
5. **Configuration** (1 snippet): show that `SessionStateStore` requires `SESSION_STATE_STORE_BACKEND` env var to select backend (`memory` / `redis` / `postgres` / `tiered`).
6. **Support window** (1 paragraph): the deprecation is announced in the next release; hard removal (`13.4a-7`) is planned for at least 1 release cycle later. The deprecation period SHALL be at least one minor version (e.g., 0.x → 0.x+1).

#### Scenario: docs/migrations/agent-state-store.md exists and contains all required sections
- **WHEN** a user navigates to `docs/migrations/agent-state-store.md` in the repository
- **THEN** the file SHALL exist and contain the 6 sections listed above
- **THEN** the "Why deprecated" section SHALL reference `SessionStateStore` and the `distributed-session-state-store` OpenSpec capability
- **THEN** the migration mapping table SHALL map all 4 `AgentStateStore` methods to their `SessionStateStore` equivalents (or note absence for `delete`)
- **THEN** the code example SHALL show before/after Python snippets that an import-and-replace reader can follow
- **THEN** the support window SHALL mention `13.4a-7` as the planned hard-removal change

#### Scenario: docs file is linked from deprecation warnings
- **WHEN** a `DeprecationWarning` from `AgentStateStore` or `WorkflowExecutionService(state_store=...)` is rendered
- **THEN** the warning message SHALL include the phrase `See docs/migrations/agent-state-store.md for migration`
- **THEN** the user can `Ctrl+Click` or follow the path in the warning text to find the guide

### Requirement: feature-catalog documents the deprecation status transition

`docs/features/feature-catalog.md` line 403 (the `13.4a | Distributed Session State Store (Redis)` row) SHALL be updated to:

1. Mark the row's deprecation state in the description (e.g., add a sentence: "AgentStateStore ABC deprecated in `13.4a-6`; hard removal planned in `13.4a-7` (next minor version)").
2. Remove or mark the existing `**NOT DONE**: Change 6 AgentStateStore removal pending` line as resolved: `**RESOLVED (deprecation)**: Change 6 deprecation implemented in `13.4a-6` (Aug 2026); hard removal in `13.4a-7` (≥ next minor).`
3. Reference the migration guide: `See docs/migrations/agent-state-store.md`.

`docs/features/roadmap.md` line 458 (the `13.4a` row) SHALL add a `Deprecated:` annotation linking to the migration guide. The status marker `✅ (5/5)` SHALL remain (the implementation is complete; deprecation is operational hygiene).

The `13.4a-7` follow-up change SHALL be added to the roadmap under a new "Pending cleanups" section: "AgentStateStore hard removal (`13.4a-7`) — scheduled ≥ 1 minor version after `13.4a-6`."

#### Scenario: feature-catalog line 403 references the deprecation
- **WHEN** a reader opens `docs/features/feature-catalog.md` and locates the `13.4a` row
- **THEN** the row description SHALL mention `13.4a-6` (deprecation) and `13.4a-7` (hard removal)
- **THEN** the existing `**NOT DONE**: Change 6 ... pending` line SHALL be removed or replaced with a `**RESOLVED (deprecation)**` line
- **THEN** the row SHALL link to `docs/migrations/agent-state-store.md`

#### Scenario: roadmap adds a 13.4a-7 follow-up entry
- **WHEN** a reader opens `docs/features/roadmap.md` and looks for `13.4a`
- **THEN** the `13.4a` row SHALL mention deprecation in the description
- **THEN** a new entry `13.4a-7 | AgentStateStore hard removal` SHALL appear in a `Pending cleanups` or equivalent section, with note that it is scheduled ≥ 1 minor version after `13.4a-6`

### Requirement: 13.4a-7 hard removal is scheduled but not implemented by this change

This change (`13.4a-6`) SHALL implement deprecation only — it SHALL NOT delete `AgentStateStore`, `InMemoryStateStore`, the `state_store` parameter on `WorkflowExecutionService`, or any related files. A follow-up change `13.4a-7` SHALL be the separate OpenSpec change that performs the hard removal.

`13.4a-7` SHALL be scheduled to ship at least one minor version after `13.4a-6` (e.g., if `13.4a-6` ships in v0.20.x, `13.4a-7` targets ≥ v0.21.0). The exact release target SHALL be decided when `13.4a-7` is proposed; this change does not pre-commit a release date.

`13.4a-7` is OUT OF SCOPE for this change. It SHALL be tracked as a follow-up item in the roadmap (per the previous requirement) and SHALL have its own OpenSpec proposal when proposed.

#### Scenario: this change does not delete AgentStateStore files
- **WHEN** the implementation of `13.4a-6` is complete
- **THEN** `src/hecate/services/state/store.py` SHALL still exist
- **THEN** `src/hecate/services/state/state.py` SHALL still exist
- **THEN** `src/hecate/services/state/__init__.py` SHALL still export `AgentStateStore` and `InMemoryStateStore`
- **THEN** the `state_store` parameter on `WorkflowExecutionService.__init__` SHALL still exist (only the `DeprecationWarning` is added)
- **THEN** `git grep "AgentStateStore" src/` SHALL return non-empty results

#### Scenario: 13.4a-7 follow-up is documented but not implemented
- **WHEN** a reader looks at the OpenSpec changes directory
- **THEN** `openspec/changes/deprecate-agent-state-store/` (this change) exists with full artifacts
- **THEN** `openspec/changes/remove-agent-state-store/` (the `13.4a-7` follow-up) does NOT exist (it is for the future, not this change)
- **THEN** `docs/features/roadmap.md` mentions `13.4a-7` in a "Pending cleanups" or equivalent section
