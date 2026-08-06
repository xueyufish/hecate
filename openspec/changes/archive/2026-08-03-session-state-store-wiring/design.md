## Context

Change 1（`session-state-store-abstraction`，2026-08-01 archived）引入 engine 层 `SessionStateStore` ABC 与 `InMemorySessionStateStore`。Change 2（`session-state-store-redis-pg`，2026-08-02 archived）新增三个生产实现（`RedisSessionStateStore`、`PostgresSessionStateStore`、`TieredSessionStateStore`）以及按 `SESSION_STATE_STORE_BACKEND` 选择后端的 factory。

**但生产请求路径仍硬编码为 in-memory。** 两个文件阻止 wiring：

```python
# src/hecate/services/workflow/execution_service.py:281
checkpoint_store = InMemoryCheckpointStore()       # 每请求新建，请求结束即死

# src/hecate/api/v1/chat.py:214
exec_service = WorkflowExecutionService(port=port, db=db)   # 没传 checkpoint_store
```

现在在生产环境设置 `SESSION_STATE_STORE_BACKEND=tiered` **完全无效**——请求路径根本不调用 factory。13.4a 的 5-change 路线图卡在第 2 步。

**两个现有状态抽象需要协调**：

| 抽象 | 层 | 用途 | 实现 | 使用位置 |
|------|----|----|------|---------|
| `CheckpointStore`（engine） | engine | PregelRuntime channel 快照 | `InMemoryCheckpointStore`（生产代码）、`PostgresCheckpointStore`（已建，未接线） | `execution_service.py:281` |
| `AgentStateStore`（services） | services | 每会话工作状态（对话缓冲区、权限上下文等） | 仅 `InMemoryStateStore` | `execution_service.py:216,310,392` |
| `SessionStateStore`（engine） | engine | **统一**：`channel_state` + `agent_state` + `event_position` + `metadata` | 4 个实现（Change 2） | 生产路径未使用 |

计划：用 `SessionStateStore` 取代 `CheckpointStore` 与 `AgentStateStore` 作为生产边界。Change 3 接入新抽象；后续清理 change 删除旧代码。

## Goals / Non-Goals

**Goals：**
- 通过可选构造参数（缺省 `None`）把 `SessionStateStore` 接入 `WorkflowExecutionService`（向后兼容）
- 用 `self._checkpoint_store` 替换 `execution_service.py:281` 的 per-request `InMemoryCheckpointStore()`
- 把 `AgentState` 序列化进 `SessionState.agent_state`（`model_dump(mode="json")`），让单一 store 承载 channel + agent + event position + metadata
- FastAPI `app.state.session_state_store` 单例在 `main.py` lifespan 中初始化（避免 per-request 创建 Redis 连接池）
- `chat.py:214` 用 `Depends(get_session_state_store)` 注入
- `get_session_state_store` 在 `app.state` 未设置时 fallback 到 `create_session_state_store(settings)`（防御性，支持脱离 lifespan 的测试）
- 集成测试验证 chat 路径使用配置的 store

**Non-Goals：**
- **不删除** `CheckpointStore` 与 `AgentStateStore`——本 change 仅降级，后续清理 change（Change 4 验证后再做）
- **无数据迁移**：默认 backend `memory`，单实例部署行为字节级不变
- **不改** engine 层 `CheckpointStore` ABC——`execution_service.py` 在 `PregelRuntime` 内构造的 engine-layer `InMemoryCheckpointStore()` 仍保留（这是 PregelRuntime 内部中间回滚用，与 wired services-layer `SessionStateStore` 共存）
- **不做 Change 5（EventStore-PG）工作**——本 change 只动 chat 路径的 session/state 管道；EventStore 仍是 per-request `InMemoryEventStore`

## Decisions

### 决策 1：双 store 在 execution_service.py 内共存

`PregelRuntime` 接受 engine 层 `CheckpointStore`（与 `SessionStateStore` 是不同 ABC）。`execution_service.py:281` 给 `PregelRuntime` 构造这个 engine 层 `InMemoryCheckpointStore()` per-request。这是正确的，**保持不变**——engine 层 `CheckpointStore` 服务 `PregelRuntime` 的中间 superstep 回滚需求，而 wired services 层 `SessionStateStore` 持有跨请求的持久化快照。

具体模式：

```python
# PregelRuntime 用的 per-request engine 层 store：
runtime_checkpoint_store = InMemoryCheckpointStore()   # engine ABC，保持
# wired services 层 store，跨请求持久化：
session_state_store: SessionStateStore = self._checkpoint_store   # services ABC
```

wired `SessionStateStore` 在 `runtime_checkpoint_store` 保存每个 superstep 快照后接收最终 `SessionState`。这与 ADR-020 一致：`RedisAgentStateStore` 层叠在 `CheckpointStore` 之上，而非替换。

### 决策 2：AgentState 合并进 SessionState.agent_state

`AgentStateStore.load()` 当前返回 `AgentState | None`（Pydantic 模型，类型化字段）。`SessionState.agent_state` 是 `dict[str, Any]`。**保留两种类型但在边界处序列化**：

```python
# 加载路径：
state = await session_state_store.load(org_id, user_id, session_id)
agent_state = AgentState.model_validate(state.agent_state) if state else None

# 保存路径：
new_state = state.model_copy(update={"agent_state": agent_state.model_dump(mode="json")})
await session_state_store.save(org_id, user_id, session_id, new_state)
```

**理由**：本 change 是桥接——`SessionState` 内的 `agent_state` dict 承载与独立 `AgentState` 模型完全相同的字段。加载只是 `model_validate`。运行时类型校验的损失可接受，因为：
- `SessionState` 故意设计为不透明快照（Change 1 设计——`agent_state` 是 `dict[str, Any]`）
- 校验发生在 `execution_service.py` 的加载边界，不在每个 store 调用
- 后续 Change 4（验证）确认合并边界无数据丢失

### 决策 3：app.state 单例 + Depends 注入

```python
# main.py lifespan（在已有 plugin discovery 之后）：
app.state.session_state_store = create_session_state_store(settings)

# src/hecate/core/deps_state_store.py：
def get_session_state_store(request: Request) -> SessionStateStore:
    store = getattr(request.app.state, "session_state_store", None)
    if store is None:
        # 防御性 fallback，支持脱离 lifespan 的测试和代码路径
        from hecate.core.config import settings
        from hecate.services.session_state import create_session_state_store
        return create_session_state_store(settings)
    return store

# src/hecate/api/v1/chat.py：
exec_service = WorkflowExecutionService(
    port=port, db=db,
    checkpoint_store=Annotated[SessionStateStore, Depends(get_session_state_store)],
)
```

**为什么用单例**：`RedisSessionStateStore._redis` 在 `self` 上 lazy 缓存 client，但每个请求新建 `RedisSessionStateStore` 都会创建新的 `redis.asyncio.from_url` 连接池→ 高负载下连接泄漏。单例 `app.state` 复用连接池。

**为什么 fallback 到 factory**：不经过 FastAPI app 的测试（如 `tests/test_services/test_workflow/test_execution_service.py`）直接构造 `WorkflowExecutionService`，没有 `Request`。fallback 确保这些测试仍能工作——从默认 `memory` backend 拿到全新 in-memory store。

### 决策 4：可选构造参数 + None fallback

```python
# src/hecate/services/workflow/execution_service.py
def __init__(
    self,
    port,
    db,
    suggestion_service=None,
    pre_llm_hook=None, post_llm_hook=None,
    environment_manager=None,
    state_store: AgentStateStore | None = None,   # deprecated
    checkpoint_store: SessionStateStore | None = None,   # 新增，缺省 = InMemoryCheckpointStore
) -> None:
    ...
    self._checkpoint_store = checkpoint_store
```

当 `self._checkpoint_store is None` 且旧 `_state_store` 也是 `None`，保留既有 per-request `InMemoryCheckpointStore()` 模式（零行为变化，默认 `memory` backend）。

当 `self._checkpoint_store` 提供，engine 层 `InMemoryCheckpointStore()` 仍 per-request 构造（PregelRuntime 需要），但 wired services 层 `SessionStateStore` 处理跨请求持久化。

### 决策 5：AgentStateStore 保留但降级

`self._state_store`（AgentStateStore）保留在构造函数中作为 deprecated 可选参数。**仅加载路径用它做向后兼容**（line 218 既有代码路径）。line 310/393 的保存路径被替换为 `SessionStateStore.save`——不再双写。

```python
# 保存路径（替换 line 310-313 和 392-393）：
new_session_state = SessionState(
    channel_state=self._channel_manager.snapshot(),
    agent_state=agent_state.model_dump(mode="json"),
    event_position=...,   # 来自 event_store.get_version()
    metadata={"superstep": ..., "started_at": ..., ...},
)
await self._checkpoint_store.save(org_id, user_id, session_id, new_session_state)
```

**理由**：`AgentStateStore` 在 7 个文件外部 `execution_service.py` 被引用（主要在 `services/state/` 模块和测试中）。单独的清理 change（Change 4 后）会删除它；本 change 只重新路由保存路径使行为可观测正确。

### 决策 6：默认 backend "memory" 保持所有现有行为

Settings 默认是 `SESSION_STATE_STORE_BACKEND="memory"`（Change 2 设置）。该默认下，`create_session_state_store(settings)` 返回 `InMemorySessionStateStore`，其行为对单实例部署与之前的 per-request `InMemoryCheckpointStore()` 模式字节级等价。不显式配置 Redis/PostgreSQL 的用户没有任何生产变化。

**迁移风险：零。** 操作员通过环境变量显式 opt-in。

## Risks / Trade-offs

**[Risk]** 双抽象窗口：`execution_service.py` 从 `_state_store`（旧 AgentStateStore）读 agent_state，但写 `_checkpoint_store`（新 SessionStateStore）。如果部署同时启用两者，`save` 到 SessionStateStore 与 `load` 从 AgentStateStore 返回不同数据。
**Mitigation**：chat.py 传 `checkpoint_store=Depends(get_session_state_store)` 且**不传** `state_store`。`_state_store` 缺省 `None`，旧加载路径跳过，只跑新路径。旧 `state_store` 参数仅对显式使用的测试保留。

**[Risk]** `AgentState.model_validate(state.agent_state)` 在加载边界可能因 schema drift 失败。生产响应会 500。
**Mitigation**：用 try/except 包裹 validate——`ValidationError` 时 log warning 并 fallback 新建 `AgentState()`。会话丢失旧状态但请求完成（优雅降级）。

**[Risk]** `app.state.session_state_store` 在启动时全局修改。多 worker 部署（gunicorn/uvicorn workers）每个 worker 有自己的副本。**不是 bug**——每个 worker 拥有自己的 store 实例与自己的 Redis 连接池，正是我们想要的。但操作员必须理解某个 worker 中的配置错误不会自动传播。

**[Risk]** 现有 23 个 `WorkflowExecutionService` 构造函数测试传 `port=port` 不带 `state_store` 或 `checkpoint_store`。本 change 后行为相同（默认 in-memory）。变更后跑测试套件验证无需测试编辑。

**[Risk]** Redis 连接池耗尽——如果未来 bug 导致 `RedisSessionStateStore._redis` 永不关闭。Mitigation 延后——生产监控（cache hit rate、connection count）是 Change 4 范围。

## Migration Plan

**不需要生产数据迁移。**

部署：
1. Merge 本 PR——默认 backend 是 `memory`，行为不变
2. 操作员通过设置 `SESSION_STATE_STORE_BACKEND=tiered`（或 `redis`/`postgres`）opt-in 到 Redis/PG
3. 无数据迁移：升级时刻的现有 in-memory session state 丢失（与今天一样——per-request 临时）

回滚：revert merge。无持久状态被写入。