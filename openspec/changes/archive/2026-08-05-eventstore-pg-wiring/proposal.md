## Why

`EventStore` ABC 与 `InMemoryEventStore` 已在 `engine/eventstore.py` 中存在，引擎层接入也由 `2026-06-06-wire-eventstore` 完成——`PregelRuntime(event_store=...)` 和 `Worker(event_store=...)` 已能发出 `NODE_START` / `NODE_END` / `LLM_REQUEST` / `LLM_RESPONSE` / `TOOL_CALL` / `TOOL_RESULT` 等事件。然而 services 层 `WorkflowExecutionService` 从未给 `PregelRuntime` 传入 `event_store`，导致**生产路径从未记录过任何执行事件**——审计日志、时间旅行调试、跨请求 resume 都缺失事件流这一半。

同时，`SessionState.event_position` 字段注释为"EventStore consumption position, used for event replay when restoring"，但**当前无人保证它与 `EventStore.get_version(session_id)` 一致**——`SessionState` snapshot 与 `EventStore` event log 之间的不变式悬空。这是 13.4a 路线图（Change 1-4 已交付 SessionStateStore 链路）后必须补齐的最后一块。

## What Changes

**services 层（本次新增）：**

- `PostgresEventStore` 实现，落表到 `events`（append-only，PK `(session_id, version)` + `org_id, user_id` 运维列 + `event_type, node_id, superstep, payload JSONB, trace_id, created_at` + 复合索引 `(org_id, user_id, created_at)`）
- `acquire_event_lock` 可选锁接口（InMemory 默认 no-op；`PostgresEventStore` 用 `SELECT MAX(version)+1 FOR UPDATE` 串行化天然避免并发版本冲突，不需显式 acquire）
- `create_event_store(settings) -> EventStore` factory，按 `EVENT_STORE_BACKEND` setting 选择后端（`"memory"` 默认向后兼容 / `"postgres"`）
- `get_event_store()` FastAPI 依赖函数，读 `app.state.event_store` 单例，fallback 到 factory
- `main.py` lifespan 初始化 `app.state.event_store` 单例 + INFO 日志输出活跃 backend
- `chat.py` 在 `WorkflowExecutionService(...)` 构造点传 `event_store=Depends(get_event_store)`
- `WorkflowExecutionService.__init__` 新增可选参数 `event_store: EventStore | None = None`，透传给内部 `PregelRuntime`
- `core/config.py` 新增 settings：`EVENT_STORE_BACKEND`（默认 `"memory"`）、`EVENT_STORE_PG_TABLE`（默认 `"events"`）
- `alembic` 新增 migration 创建 `events` 表与索引
- `_sync_event_position(state, event_store)` 函数：在 `_persist_session_state` 末尾调用一次，把 `state.event_position` 设为 `await event_store.get_version(session_id)`，补齐 snapshot ↔ event log 不变式

**显式不做（deferred）：**

- ❌ 不扩展 engine 层 `EventStore` ABC（保留 `session_id` 单键——由 services 层 PG 表补充 `org_id, user_id` 运维列）
- ❌ 不引入 Redis event backend（业界调研显示 Redis 适合 snapshot 缓存不适合 event log range query）
- ❌ 不引入 Kafka / Temporal / Restate 式外部 event log 系统（Hecate 当前规模不需要）
- ❌ 不实现 retention / TTL 自动清理（schema 预留 `created_at` 列，TTL 策略留给后续 ops change）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `eventstore`: 新增 services 层 PG 实现、factory、FastAPI DI 接入、`WorkflowExecutionService` 透传、`SessionState.event_position` 同步契约。原 engine 层 ABC + `InMemoryEventStore` + `PregelRuntime` 接入 requirement 不变。

## Impact

**新增文件：**

- `src/hecate/services/event_state/__init__.py`（新模块，对标 `services/session_state/`）
- `src/hecate/services/event_state/postgres_store.py`（`PostgresEventStore`）
- `src/hecate/services/event_state/factory.py`（`create_event_store`）
- `src/hecate/services/event_state/models.py`（`EventModel` ORM）
- `src/hecate/core/deps_event_store.py`（FastAPI 依赖）
- `alembic/versions/<rev>_add_events_table.py`
- `tests/test_services/test_event_state/test_postgres_store.py`
- `tests/test_services/test_event_state/test_factory.py`
- `tests/test_core/test_deps_event_store.py`
- `tests/test_api/test_chat_event_store_wiring.py`

**修改文件：**

- `src/hecate/services/workflow/execution_service.py`（构造函数加 `event_store` 参数；`PregelRuntime(event_store=...)` 透传；`_persist_session_state` 末尾调 `_sync_event_position`）
- `src/hecate/main.py`（lifespan 初始化 `app.state.event_store`）
- `src/hecate/api/v1/chat.py`（`WorkflowExecutionService(...)` 传 `event_store=Depends(get_event_store)`）
- `src/hecate/core/config.py`（新增 `EVENT_STORE_BACKEND` / `EVENT_STORE_PG_TABLE`）
- `pyproject.toml`（无新增依赖，复用既有 SQLAlchemy + asyncpg）

**向后兼容：**

- `EVENT_STORE_BACKEND="memory"`（默认）保留 Change 4 前所有行为：`InMemoryEventStore` + events 不持久化
- 既有 23 个 `test_execution_service.py` 测试零修改通过（不传 `event_store` 时 `self._event_store is None`，`PregelRuntime` 不记录事件，与现状一致）
