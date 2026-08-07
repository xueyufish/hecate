## 1. Engine 层 acquire_event_lock 接口

- [x] 1.1 在 `src/hecate/engine/eventstore.py` 新增 `EventVersionConflictError(Exception)` 异常（消息含 `session_id`）
- [x] 1.2 在 `EventStore` ABC 新增 `acquire_event_lock` async context manager 方法（`@asynccontextmanager`，默认 no-op yield）
- [x] 1.3 `InMemoryEventStore` 继承 default（不覆盖）
- [x] 1.4 单元测试：`tests/test_engine/test_eventstore.py` 新增 `test_acquire_event_lock_default_is_noop`

## 2. EventModel ORM + Alembic migration

- [x] 2.1 创建 `src/hecate/services/event_state/__init__.py`（空模块导出）
- [x] 2.2 创建 `src/hecate/services/event_state/models.py` 定义 `EventModel`：复合主键 `(session_id, version)`、`id UUID`（业务键）、`org_id/user_id`（运维列 nullable）、`superstep, event_type, node_id, trace_id`、`payload JSON`、`created_at TIMESTAMPTZ DEFAULT now()`
- [x] 2.3 创建索引 `idx_events_session_version` on `(session_id, version)`（主键已覆盖，显式声明）
- [x] 2.4 创建索引 `idx_events_org_user_created` on `(org_id, user_id, created_at)`
- [x] 2.5 创建 `alembic/versions/<rev>_add_events_table.py` migration（upgrade 创建表+索引，downgrade 删除）
- [x] 2.6 单元测试：`tests/test_services/test_event_state/test_models.py` 验证 EventModel round-trip JSONB payload

## 3. PostgresEventStore 实现

- [x] 3.1 创建 `src/hecate/services/event_state/postgres_store.py` 定义 `PostgresEventStore(EventStore)`
- [x] 3.2 构造函数接受 `async_session_factory` + 可选 `tenant_context_provider: Callable[[], tuple[UUID, UUID]] | None`
- [x] 3.3 `append(event)`：事务内 `SELECT COALESCE(MAX(version), 0) + 1 FROM events WHERE session_id=? FOR UPDATE` → 赋值 version → `INSERT ... ON CONFLICT DO NOTHING`；冲突时重试 1 次后抛 `EventVersionConflictError`
- [x] 3.4 `append` 调用 `tenant_context_provider`（如提供）填充 `org_id`/`user_id`；provider 为 None 时写 None
- [x] 3.5 `get_events(session_id, from_version=0)`：`SELECT ... WHERE session_id=? AND version>=? ORDER BY version ASC`，反序列化为 `Event`
- [x] 3.6 `get_version(session_id)`：`SELECT MAX(version) FROM events WHERE session_id=?`，无行返回 0
- [x] 3.7 `replay(session_id, from_version=0)`：复用 `get_events`，按 version 升序 yield
- [x] 3.8 `acquire_event_lock` 继承 default no-op（不覆盖）
- [x] 3.9 单元测试：`tests/test_services/test_event_state/test_postgres_store.py` —— append+get_events round-trip、version 过滤、get_version 无事件返回 0、payload JSONB 查询、并发 append 串行化（mock session_factory 验证 FOR UPDATE 调用）、tenant_context_provider 填充列

## 4. Settings + Factory

- [x] 4.1 在 `src/hecate/core/config.py` 新增 `EVENT_STORE_BACKEND: str = "memory"` 与 `EVENT_STORE_PG_TABLE: str = "events"`
- [x] 4.2 创建 `src/hecate/services/event_state/factory.py` 提供 `create_event_store(settings) -> EventStore`
- [x] 4.3 factory 支持 `"memory"`（返回 `InMemoryEventStore()`）与 `"postgres"`（返回 `PostgresEventStore(async_session_factory, tenant_context_provider)`）
- [x] 4.4 未知 backend 抛 `ValueError` 含支持值列表
- [x] 4.5 factory 内构造 `tenant_context_provider` closure（从请求 contextvar 读取 `(org_id, user_id)`；如尚无 contextvar，本次新增 `core/request_context.py` 暴露 `get_tenant_context()`）
- [x] 4.6 单元测试：`tests/test_services/test_event_state/test_factory.py` —— memory 默认、postgres 返回正确类型、未知 backend 抛 ValueError

## 5. FastAPI 依赖 + lifespan + chat.py wiring

- [x] 5.1 创建 `src/hecate/core/deps_event_store.py` 提供 `get_event_store(request: Request) -> EventStore`（优先读 `app.state.event_store`，fallback factory）
- [x] 5.2 在 `src/hecate/main.py` lifespan 初始化 `app.state.event_store = create_event_store(settings)`，在 `Base.metadata.create_all` 之后
- [x] 5.3 lifespan 内 `logger.info("EventStore backend=%s", settings.EVENT_STORE_BACKEND)`
- [x] 5.4 在 `src/hecate/api/v1/chat.py` chat endpoint 构造 `WorkflowExecutionService(...)` 时传 `event_store=Depends(get_event_store)`
- [x] 5.5 单元测试：`tests/test_core/test_deps_event_store.py` —— 读 app.state 单例、fallback factory、返回 EventStore 实例
- [x] 5.6 单元测试：`tests/test_api/test_chat_event_store_wiring.py` —— chat endpoint 构造时传了 event_store、多请求共享单例

## 6. WorkflowExecutionService 接入 + _sync_event_position

- [x] 6.1 在 `src/hecate/services/workflow/execution_service.py` `__init__` 新增 `event_store: EventStore | None = None` 参数，存为 `self._event_store`
- [x] 6.2 `PregelRuntime(...)` 构造时传 `event_store=self._event_store`（None 时 PregelRuntime 不记录事件，与现状一致）
- [x] 6.3 新增 `_sync_event_position(state: SessionState, event_store: EventStore | None) -> SessionState` async 函数（event_store 为 None 时 return state；否则 await `event_store.get_version(session_id)` 并 `state.model_copy(update={"event_position": pos})`）
- [x] 6.4 `_sync_event_position` 从 `state.metadata` 取 `session_id`（或签名加 `session_id` 参数）
- [x] 6.5 在 `_persist_session_state` 末尾（acquire_session_lock 之后、save 之前）调用 `state = await _sync_event_position(state, self._event_store)`
- [x] 6.6 既有 23 个 `test_execution_service.py` 测试零修改通过（不传 event_store 时 `self._event_store is None`，_sync 直接 return state）
- [x] 6.7 单元测试：`tests/test_services/test_workflow/test_execution_service_event_wiring.py` —— 默认构造 event_store is None、wired 构造透传给 PregelRuntime、_sync_event_position 同步 event_position、_sync event_store None 时 no-op

## 7. OTel observability（对齐 Change 4 模式）

- [x] 7.1 在 `PostgresEventStore.append` 创建 `event_store.append` OTel span，属性 `event.session_id, event.event_type, event.version, event.backend="postgres"`
- [x] 7.2 在 `_sync_event_position` 创建 `session_state.sync_event_position` span，属性 `session.id, event.position, event.backend`
- [x] 7.3 OTel 不可用时 fallback 到 `_NullSpan` / `_null_cm`（复用 Change 4 helper）
- [x] 7.4 结构化日志：`logger.info("event_store_append", extra={...})` 与 `logger.info("session_state_sync_event_position", extra={...})`
- [x] 7.5 单元测试：OTel 可选时不抛异常（_NullSpan fallback）

## 8. 集成测试 + 性能基准

- [x] 8.1 `tests/test_services/test_event_state/test_integration_pregel.py` —— 用 stub PregelRuntime 跑 N 个 superstep，验证 EventStore 收到对应 NODE_START/NODE_END 事件、version 单调递增
- [x] 8.2 `tests/test_services/test_event_state/test_integration_wiring.py` —— wired `WorkflowExecutionService`（带 PG mock）跑 execute()，验证事件被 append + SessionState.event_position 同步
- [x] 8.3 `tests/test_services/test_event_state/test_perf.py` —— 标 `@pytest.mark.perf`，参数化对比 InMemory / mock PG 的 append latency（1000 次操作，InMemory p95 < 1ms, mock PG p95 < 10ms）

## 9. 验证

- [x] 9.1 跑 `ruff check src/hecate/services/event_state/ src/hecate/core/deps_event_store.py src/hecate/services/workflow/execution_service.py src/hecate/main.py src/hecate/api/v1/chat.py src/hecate/core/config.py src/hecate/engine/eventstore.py` — All checks passed
- [x] 9.2 跑 `ruff format --check` 上述路径 — formatted
- [x] 9.3 跑 `mypy src/hecate/services/event_state/ src/hecate/services/workflow/execution_service.py src/hecate/engine/eventstore.py src/hecate/core/deps_event_store.py` — Success: no issues found
- [x] 9.4 跑 `python -m pytest tests/test_engine/test_eventstore.py tests/test_services/test_event_state/ tests/test_services/test_workflow/ tests/test_core/test_deps_event_store.py tests/test_api/test_chat_event_store_wiring.py -v` — 所有新测试 + 既有测试全过
- [x] 9.5 跑 `python -m pytest tests/ -q` — 不破坏现有测试（既有 23 个 execution_service 测试零修改通过）
- [x] 9.6 跑 `python -m pytest tests/test_services/test_event_state/test_perf.py -v` — 性能阈值满足
- [x] 9.7 跑完整 `ruff check src/hecate/ tests/` — All checks passed
