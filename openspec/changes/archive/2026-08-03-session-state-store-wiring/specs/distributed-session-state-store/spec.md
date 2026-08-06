## ADDED Requirements

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