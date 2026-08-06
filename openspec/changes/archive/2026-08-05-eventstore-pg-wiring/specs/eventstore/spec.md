## ADDED Requirements

### Requirement: PostgresEventStore persists EventStore events to PostgreSQL via ORM

services 层 SHALL 提供 `PostgresEventStore`（`src/hecate/services/event_state/postgres_store.py`）实现 engine-layer `EventStore` ABC，把 `Event` 记录以 append-only 方式落表到 `events` 表。

实现 SHALL 复用既有 `async_session_factory`（来自 `hecate.core.database`），与 `PostgresSessionStateStore` 共享连接池与 PG 方言处理。

序列化 SHALL 把 `Event` 的 `payload` 字段存为 PostgreSQL `JSONB`（便于查询）；其它字段（`session_id`, `superstep`, `event_type`, `node_id`, `trace_id`, `version`, `id`）存为独立强类型列。整体 row 不 SHALL 使用 `pickle` 或自定义二进制格式。

`append` 方法 SHALL 在事务内为 event 赋值单调递增的 `version`——具体机制为 `SELECT COALESCE(MAX(version), 0) + 1 FROM events WHERE session_id = ? FOR UPDATE`，`FOR UPDATE` 锁定现有行集（如有）以串行化同 session 的并发 append。`INSERT ... ON CONFLICT DO NOTHING` 保证 `(session_id, version)` 唯一性，冲突时（极罕见的 race）SHALL 重试或抛 `EventVersionConflictError`。

`get_events` SHALL 用 `SELECT ... WHERE session_id = ? AND version >= ? ORDER BY version ASC` 返回结果，反序列化为 `Event` 实例。

`get_version` SHALL 用 `SELECT MAX(version) FROM events WHERE session_id = ?`，无行时返回 `0`。

`replay` SHALL 复用 `get_events` 实现，按 version 升序 yield 每个 `Event`。

#### Scenario: append 后能按 version 升序读回

- **WHEN** 对同一 `session_id` 连续 `append` 3 个 Event，然后调 `get_events(session_id)`
- **THEN** 返回 list 长度为 3，versions 为 `[1, 2, 3]`，顺序与 append 一致

#### Scenario: get_events 支持版本过滤

- **WHEN** 10 个 Event 已 append，调用 `get_events(session_id, from_version=7)`
- **THEN** 返回 versions 为 `[7, 8, 9, 10]` 的 4 个 Event

#### Scenario: 同 session 并发 append 自然串行化

- **WHEN** 两个并发 `append` 同一 `session_id` 同时到达
- **THEN** PG `SELECT ... FOR UPDATE` 让第二个等待第一个事务提交
- **THEN** 两个 Event 各得唯一 version（无版本冲突）

#### Scenario: get_version 无事件返回 0

- **WHEN** 对一个从未写过 Event 的 `session_id` 调用 `get_version(session_id)`
- **THEN** 返回 `0`

#### Scenario: payload 用 JSONB 便于查询

- **WHEN** `Event(payload={"tool": "search", "latency_ms": 42})` 被 append
- **THEN** 落表后 `payload` 列是 JSONB
- **THEN** `SELECT * FROM events WHERE payload->>'tool' = 'search'` 能命中该行

### Requirement: EventModel ORM 映射 events 表

services 层 SHALL 在 `src/hecate/services/event_state/models.py` 提供 `EventModel` SQLAlchemy ORM 类，映射到 `events` 表，字段如下：

- `id: UUID`（主键之一，对应 `Event.id`）
- `session_id: UUID`（主键之一，外键关联 `sessions` 概念——但 Hecate 无 sessions 表，故仅作索引列）
- `version: INTEGER`（主键之一，与 `session_id` 组成复合主键 `(session_id, version)`）
- `superstep: INTEGER`
- `event_type: STRING`（对应 `EventType` 枚举值字符串）
- `node_id: STRING NULLABLE`
- `trace_id: STRING NULLABLE`
- `payload: JSON`（SQLAlchemy 通用 JSON 类型；PG 方言下落为 JSONB）
- `created_at: TIMESTAMPTZ`，`server_default=func.now()`

复合主键 SHALL 为 `(session_id, version)`——`(session_id, version)` 全局唯一（同 session 内 version 单调），`id` UUID 仅作业务标识（用于跨 session 唯一引用），不参与主键。

SHALL 额外创建两个索引：

- `idx_events_session_version` on `(session_id, version)`——主键已覆盖，但显式声明以支持 `get_events` range scan
- `idx_events_org_user_created` on `(org_id, user_id, created_at)`——运维查询（GDPR 删除、按租户清理、磁盘计量）使用

`org_id` 与 `user_id` SHALL 作为**运维列**存在（非主键），用于按租户 / 用户做批量删除与查询，但不参与 `EventStore` ABC 的方法签名（ABC 保持单键 `session_id`）。

`created_at` 列 SHALL 预留用于未来 retention / TTL 策略（本次 Change 不实现清理逻辑）。

#### Scenario: EventModel round-trips via JSONB payload

- **WHEN** 一个 `EventModel` 实例以 `payload={"key": "value"}` 持久化后读回
- **THEN** `payload` 字段反序列化为原始 dict

#### Scenario: 复合主键防止同 session version 冲突

- **WHEN** 试图插入 `(session_id=X, version=5)` 两次
- **THEN** 第二次 INSERT 因主键冲突失败（或被 `ON CONFLICT DO NOTHING` 静默忽略）

### Requirement: PostgresEventStore 接受 tenant 运维列但 ABC 方法保持单键

`PostgresEventStore.__init__` SHALL 接受可选的 `tenant_context_provider: Callable[[], tuple[uuid.UUID, uuid.UUID]] | None = None` 参数。当 provider 被提供时，每次 `append` SHALL 调用 provider 获取 `(org_id, user_id)` 并写入对应列。

当 provider 为 `None` 时，`org_id` / `user_id` 列 SHALL 写入 `None`（向后兼容测试场景；生产部署 SHALL 通过 DI 提供 provider）。

ABC 方法（`append`, `get_events`, `replay`, `get_version`）SHALL 保持 `session_id` 单键签名不变——租户隔离由 PG schema 层的列 + 索引承担，不污染 engine-layer 接口。

#### Scenario: 生产路径 tenant 列被填充

- **WHEN** `WorkflowExecutionService` 构造 `PostgresEventStore` 时通过 DI 注入了 `tenant_context_provider`
- **THEN** 每次 `append` 后 `events` 表行的 `org_id`, `user_id` 列非空
- **THEN** 后续可按 `(org_id, user_id)` 查询/清理该租户的所有事件

#### Scenario: 测试路径 tenant 列允许为空

- **WHEN** 单元测试直接 `PostgresEventStore(async_session_factory)` 构造，不传 provider
- **THEN** `append` 正常工作，`org_id` / `user_id` 列为 `None`
- **THEN** ABC 方法行为与生产一致

#### Scenario: ABC 方法签名保持单键

- **WHEN** 调用 `PostgresEventStore.append(event)` 或 `get_events(session_id, from_version)`
- **THEN** 方法签名与 engine-layer `EventStore` ABC 完全一致（不要求 org_id/user_id 参数）

### Requirement: WorkflowExecutionService 构造接受可选 event_store

services 层 `WorkflowExecutionService`（`src/hecate/services/workflow/execution_service.py`）的构造函数 SHALL 接受可选参数 `event_store: EventStore | None = None`。

当 `event_store` 为 `None` 时（默认），`PregelRuntime(event_store=None)` 不记录事件，行为与 Change 5 前完全一致——既有测试零修改通过。

当 `event_store` 被提供时，service SHALL 用 `self._event_store` 传给内部 `PregelRuntime`，使引擎层在执行节点 / 调用 LLM / 调用工具时记录对应 Event。

#### Scenario: 默认构造保留既有行为

- **WHEN** `WorkflowExecutionService` 构造时不传 `event_store`
- **THEN** `self._event_store is None`
- **THEN** `PregelRuntime` 被构造时 `event_store=None`，与现状一致
- **THEN** 既有 23 个 `test_execution_service.py` 测试零修改通过

#### Scenario: wired 构造函数透传给 PregelRuntime

- **WHEN** `WorkflowExecutionService(event_store=<EventStore>)` 构造
- **THEN** `self._event_store is <EventStore>`
- **THEN** 内部 `PregelRuntime(event_store=self._event_store)` 被调用
- **THEN** 执行期间 `NODE_START` / `NODE_END` / `LLM_REQUEST` / `LLM_RESPONSE` / `TOOL_CALL` / `TOOL_RESULT` 等 Event 被 append 到该 store

### Requirement: _sync_event_position 在 persist 末尾同步 event_position

`WorkflowExecutionService._persist_session_state` SHALL 在写入 `SessionState` 前，调用一次内部函数 `_sync_event_position(state, event_store)`：

```python
async def _sync_event_position(state: SessionState, event_store: EventStore | None) -> SessionState:
    if event_store is None:
        return state
    pos = await event_store.get_version(state.session_id)  # 通过 metadata 取 session_id
    return state.model_copy(update={"event_position": pos})
```

此函数 SHALL 在 `_persist_session_state` 的 save 调用之前、acquire_session_lock 之后被 await 一次，确保落库的 `SessionState.event_position` 反映此刻 `EventStore` 的真实 version。

当 `event_store is None` 时 SHALL 直接 return 原 state（向后兼容）。

#### Scenario: 提供 event_store 时 event_position 被同步

- **WHEN** `_persist_session_state(state, ...)` 被调用，且 `self._event_store is not None`，且当前 EventStore 已有 7 个事件
- **THEN** 写入 `SessionStateStore` 的 `state.event_position == 7`

#### Scenario: 不提供 event_store 时 event_position 保持原值

- **WHEN** `_persist_session_state(state, ...)` 被调用，且 `self._event_store is None`
- **THEN** state 不被修改，原 `event_position` 值（可能是 0 或默认）保留
- **THEN** 既有测试零修改通过

#### Scenario: event_position 同步在锁持有期间

- **WHEN** `_persist_session_state` 进入临界区（持有 `acquire_session_lock`）
- **THEN** `_sync_event_position` 在 save 调用之前被 await
- **THEN** save 写入的 state 已包含更新后的 event_position

### Requirement: create_event_store factory 按 setting 选择后端

services 层 SHALL 在 `src/hecate/services/event_state/factory.py` 提供 `create_event_store(settings) -> EventStore`，根据 `settings.EVENT_STORE_BACKEND` 返回实现：

- `"memory"`（默认）→ `InMemoryEventStore()`（来自 `hecate.engine.eventstore`，保持向后兼容）
- `"postgres"` → `PostgresEventStore(async_session_factory=..., tenant_context_provider=...)`

未知值 SHALL 抛 `ValueError`，消息列出支持的 backend。

`tenant_context_provider` SHALL 在 factory 内构造一个 closure，从当前请求的 contextvar 读取 `(org_id, user_id)`——具体 contextvar 由 `core/config.py` 或 `core/request_context.py` 提供（如尚无则本次新增）。

#### Scenario: factory memory 默认向后兼容

- **WHEN** `settings.EVENT_STORE_BACKEND == "memory"`（默认）
- **THEN** `create_event_store(settings)` 返回 `InMemoryEventStore` 实例
- **THEN** 生产部署不设环境变量时行为与 Change 5 前一致

#### Scenario: factory postgres 返回 PG 实现

- **WHEN** `settings.EVENT_STORE_BACKEND == "postgres"`
- **THEN** factory 返回 `PostgresSessionStateStore(async_session_factory=..., tenant_context_provider=...)`

#### Scenario: factory 未知 backend 抛 ValueError

- **WHEN** `settings.EVENT_STORE_BACKEND == "kafka"`（未知）
- **THEN** factory 抛 `ValueError`，消息含 `"memory"` 与 `"postgres"`

### Requirement: get_event_store FastAPI 依赖函数

`src/hecate/core/deps_event_store.py` SHALL 提供 `get_event_store(request: Request) -> EventStore` 依赖函数：

- 优先读 `request.app.state.event_store`（lifespan 初始化的单例）
- 如未设置（绕过 lifespan 的测试场景），fallback 调 `create_event_store(settings)` 构造新 store
- SHALL 返回 `EventStore` 实例

依赖 SHALL 与 `get_session_state_store` 形态对齐（Change 3 模式）。

#### Scenario: 依赖读 app.state 单例

- **WHEN** `get_event_store` 在 FastAPI 请求内被调用，且 lifespan 已初始化 `app.state.event_store`
- **THEN** 返回 `app.state.event_store`（lifespan 中的单例）

#### Scenario: 依赖 fallback 到 factory

- **WHEN** `get_event_store` 在 lifespan 外被调用（测试、脚本）
- **THEN** 返回 `create_event_store(settings)`，当前 setting 决定实现

### Requirement: main.py lifespan 初始化 app.state.event_store

`src/hecate/main.py` 应用 lifespan SHALL 在启动时初始化 `app.state.event_store` 恰好一次，调 `create_session_state_store` 的姊妹函数 `create_event_store(settings)`。

初始化 SHALL 在 `Base.metadata.create_all` 之后、任何请求处理器之前发生。

INFO 级别日志 SHALL 记录 `"EventStore backend=<value>"`，让操作员能在容器日志确认活跃 backend。

多 worker 部署中每个 worker 进程 SHALL 拥有自己的 `app.state.event_store` 实例与连接池（per-worker 隔离，与 SessionStateStore 一致）。

#### Scenario: lifespan 设置 app.state.event_store

- **WHEN** FastAPI 应用启动
- **THEN** 在第一个请求被服务前，`app.state.event_store` 非 None，是 `EventStore` 实例

#### Scenario: 启动日志输出 backend

- **WHEN** 应用启动
- **THEN** 日志行 `"EventStore backend=<value>"` 出现，`<value>` 匹配 `settings.EVENT_STORE_BACKEND`

### Requirement: chat.py 使用 Depends 注入 event_store

`src/hecate/api/v1/chat.py` 的 chat endpoint 在构造 `WorkflowExecutionService(...)` 时 SHALL 传 `event_store=Depends(get_event_store)`。

`chat endpoint` SHALL NOT 在不传 `event_store` 的情况下构造 `WorkflowExecutionService`（生产路径必须接入事件持久化）。

旧模式 `WorkflowExecutionService(port=port, db=db)`（不带 `event_store`）保留给显式 opt-out 的测试与直接 service 调用方。

#### Scenario: chat endpoint 注入 event_store

- **WHEN** 一个 chat 请求进入 `chat.py`
- **THEN** `WorkflowExecutionService` 构造时带 `event_store=<FastAPI 注入的 EventStore>`

#### Scenario: 多请求共享 worker 单例

- **WHEN** 多个 chat 请求命中同一 worker
- **THEN** 所有请求复用同一 `app.state.event_store` 实例（per-worker 单例）

### Requirement: EVENT_STORE_BACKEND 与 EVENT_STORE_PG_TABLE settings

`src/hecate/core/config.py` SHALL 暴露以下 settings：

- `EVENT_STORE_BACKEND: str = "memory"`——`"memory"` 或 `"postgres"`
- `EVENT_STORE_PG_TABLE: str = "events"`——PG 表名（允许运维自定义）

默认值 SHALL 保持 Change 5 前行为（`"memory"` 不持久化事件）。

#### Scenario: 默认 setting 保持向后兼容

- **WHEN** 未设环境变量
- **THEN** `settings.EVENT_STORE_BACKEND == "memory"`
- **THEN** `create_event_store(settings)` 返回 `InMemoryEventStore`
- **THEN** 生产路径不持久化事件（与 Change 5 前一致）

#### Scenario: 操作员 opt-in 到 PG backend

- **WHEN** 操作员设置 `EVENT_STORE_BACKEND=postgres` 并重启服务
- **THEN** `create_event_store(settings)` 返回 `PostgresEventStore`
- **THEN** 事件持久化到 PG `events` 表

### Requirement: acquire_event_lock 锁接口（可选，默认 no-op）

engine 层 `EventStore` ABC SHALL 新增可选的 `acquire_event_lock` async context manager 方法（对齐 `SessionStateStore.acquire_session_lock` 形态），签名为：

```python
@asynccontextmanager
async def acquire_event_lock(
    self, session_id: uuid.UUID, *, timeout_ms: int = 30000
) -> AsyncGenerator[None, None]: ...
```

默认实现 SHALL 是 no-op（直接 yield），让 `InMemoryEventStore` 等单进程实现无需覆盖。

`PostgresEventStore` SHALL NOT 覆盖此方法（继承 default no-op）——`append` 内已用 `SELECT MAX(version)+1 FOR UPDATE` 天然串行化同 session 的并发写，不需要额外的显式锁。

本接口存在是为了**形态对齐**与**未来 Redis 实现的扩展点**，本次 Change 不在生产路径调用（`_persist_session_state` 不 acquire event lock——event append 由 PregelRuntime 内部驱动，不与 SessionState save 共享临界区）。

#### Scenario: 默认实现是 no-op

- **WHEN** `InMemoryEventStore.acquire_event_lock(session_id)` 被调用
- **THEN** 直接 yield，不抛异常，不阻塞

#### Scenario: PostgresEventStore 继承 default 不覆盖

- **WHEN** `PostgresEventStore.acquire_event_lock(session_id)` 被调用
- **THEN** 行为与 default no-op 一致（不额外加锁，依赖 append 内的 `FOR UPDATE`）

### Requirement: retention 与 TTL 策略 deferred（schema 预留）

本次 Change SHALL NOT 实现 retention / TTL 自动清理。`events` 表的 `created_at` 列 SHALL 预留用于未来 retention 实现，但本次不添加 cron job / 分区 / TTL sweeper。

本 requirement 显式标注"deferred"以阻止 contributor 误以为本次会做清理——retention 策略留给独立的 ops change（参考 LangGraph / Dify / DeerFlow 都未实现自动 retention 的现状）。

未来 retention 实现 SHALL 复用 `created_at` 列与 `(org_id, user_id, created_at)` 索引。

#### Scenario: events 表有 created_at 列但无清理逻辑

- **WHEN** `events` 表被创建
- **THEN** `created_at TIMESTAMPTZ DEFAULT now()` 列存在
- **THEN** 无 cron job / TTL sweeper / partition drop 逻辑被引入

#### Scenario: 未来 contributor 加 retention 不需改 schema

- **WHEN** 未来 ops change 引入 retention 策略
- **THEN** `created_at` 列与 `(org_id, user_id, created_at)` 索引已就位
- **THEN** 仅需新增 sweeper 逻辑，无需 schema migration
