## Why

Change 1（`session-state-store-abstraction`，2026-08-01 archived）在 engine 层定义了 `SessionStateStore` ABC；Change 2（`session-state-store-redis-pg`，2026-08-02 archived）实现了三种生产后端（Redis / PostgreSQL / Tiered）以及按 settings 选择后端的 factory。**但生产请求路径仍未接通**：`src/hecate/services/workflow/execution_service.py:281` 硬编码 `checkpoint_store = InMemoryCheckpointStore()`（每次请求新建），`src/hecate/api/v1/chat.py:214` 构造 `WorkflowExecutionService` 时也没传新的 store。

结果：即便配置 `SESSION_STATE_STORE_BACKEND=tiered`，每次 chat 请求仍走 per-request 空 in-memory store。Channel values、agent state、event position 在请求结束时丢失。13.4a 的 5-change 路线图（engine abstraction → 后端实现 → wiring → 验证 → EventStore-PG）卡在第 2 步。

本 change 把 `SessionStateStore` 接入生产请求路径，且零破坏性：可选构造参数、FastAPI `app.state` 单例、默认 backend 保持 `memory`，现有测试和单实例部署不受影响。本 change 完成后，配置 `SESSION_STATE_STORE_BACKEND=tiered` 真正能做到 Redis 缓存 + PostgreSQL 持久化的跨请求 session state——为 Change 4 多副本水平扩展铺路。

## What Changes

- 修改 `src/hecate/services/workflow/execution_service.py` 构造函数：新增可选参数 `checkpoint_store: SessionStateStore | None = None`（替换 line 281 隐式的"总是 in-memory"模式）
- 修改 `src/hecate/services/workflow/execution_service.py` execute()：使用 `self._checkpoint_store` 代替 per-request `InMemoryCheckpointStore()`
- 修改 `src/hecate/services/workflow/execution_service.py` execute()：将 `AgentState` 序列化进 `SessionState.agent_state`（`model_dump`）并通过 wired `SessionStateStore` 持久化。旧 `self._state_store`（AgentStateStore）降级为 deprecated——本 change 内仅在加载路径上读取，后续清理 change 删除
- 新增 `src/hecate/core/deps_state_store.py`：FastAPI `get_session_state_store` 依赖函数，从 `app.state.session_state_store` 读取（启动时设置一次）
- 修改 `src/hecate/main.py`：lifespan 中初始化 `app.state.session_state_store = create_session_state_store(settings)` 并记录当前 backend 的日志（Change 2 已加 import，Change 3 补 lifespan 初始化）
- 修改 `src/hecate/api/v1/chat.py:214`：通过 `checkpoint_store=Depends(get_session_state_store)` 把 store 传给 `WorkflowExecutionService`
- 新增 `tests/test_services/test_workflow/test_execution_service_wiring.py`：集成测试，验证 chat 路径使用配置的 `SessionStateStore`（memory 默认 backend，加上 mock tiered backend + fakeredis）
- **不需要数据迁移**：默认 backend 是 `memory`，现有测试和单实例部署行为不变

## Capabilities

### Modified Capabilities
- `distributed-session-state-store`：新增 ADDED Requirements 覆盖生产接线（FastAPI DI 单例、`app.state` 注入、`WorkflowExecutionService` 可选构造参数、deprecated `AgentStateStore` 参数、通过 `SessionState.agent_state` 持久化 `AgentState`）

## Impact

- **修改文件**：`src/hecate/services/workflow/execution_service.py`（构造函数 + line 281 + 3 个调用点）、`src/hecate/api/v1/chat.py:214`、`src/hecate/main.py` lifespan、`openspec/specs/distributed-session-state-store/spec.md`（delta）
- **新增文件**：`src/hecate/core/deps_state_store.py`（约 30 行）、`tests/test_services/test_workflow/test_execution_service_wiring.py`（约 120 行）
- **无新运行时依赖**：所有改动使用现有模块（`hecate.services.session_state`、`hecate.core.config`、FastAPI `app.state`）
- **向后兼容**：默认 `SESSION_STATE_STORE_BACKEND="memory"` 产出 `InMemorySessionStateStore`，其行为与既有 per-request `InMemoryCheckpointStore()` 对单实例部署字节级等价
- **测试兼容**：现有 23 个 `WorkflowExecutionService` 测试继续通过——新参数 `checkpoint_store` 可选，缺省 `None`，构造函数在省略该参数时保留既有行为
- **文档同步**：归档时需把 13.4a 进度从 2/4 更新到 3/4（feature-catalog / roadmap / p3-mvp-audit）
- **CI 不变**：无新依赖，无 schema 变更，无测试基础设施变更