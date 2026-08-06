## ADDED Requirements

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