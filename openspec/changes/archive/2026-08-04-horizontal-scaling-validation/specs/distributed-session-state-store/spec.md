## ADDED Requirements

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
- **THEN** line 473-474 的 `if self._state_store and agent_state:` 分支被移除

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

### Requirement: RedisSessionStateStore SHALL 用 SET NX EX + Lua release 实现锁

`src/hecate/services/session_state/redis_store.py` 的 `RedisSessionStateStore.acquire_session_lock` SHALL：

1. 生成 owner UUID（每次调用唯一）
2. 执行 `SET lock:{org}:{user}:{session} {owner_uuid} NX PX {timeout_ms}`
3. SET 成功则进入临界区；失败则 retry（jitter 20-150ms，最多 3 次），仍失败抛 `SessionStateConflictError`
4. 退出临界区时执行 Lua 脚本：`if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) else return 0 end`——确保只删自己的锁

锁 key SHALL 使用与 session data 相同的 `{org_id}` hash tag，保证 Redis Cluster 下两者在同一 slot。

#### Scenario: SET NX 成功获取锁

- **WHEN** `acquire_session_lock(org, user, session)` 调用且 lock key 不存在
- **THEN** Redis 执行 `SET lock:{org}:{user}:{session} {uuid} NX PX 30000` 返回 OK
- **THEN** 临界区执行
- **THEN** 退出时 Lua 脚本删除 key（验证 owner UUID 匹配）

#### Scenario: SET NX 失败触发 retry

- **WHEN** `acquire_session_lock` 调用且 lock key 已被其他 owner 持有
- **THEN** retry 3 次，每次前 `asyncio.sleep(random.uniform(0.020, 0.150))`
- **THEN** 3 次都失败则抛 `SessionStateConflictError`

#### Scenario: Lua release 防止误删他人锁

- **WHEN** lock 已超时，其他 client 获取了新锁
- **THEN** 原 client 的 Lua release 检查 owner UUID 不匹配，返回 0，不删除

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

Redis 锁失败时（Redis 不可用），Tiered SHALL fallback 到只用 PG 行锁（swallow Redis 异常并 log warning）。

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

`_persist_session_state` SHALL NOT 在锁失败或 save 失败时 fallback 到 legacy `self._state_store.save`——line 389-391 的 fallback 分支 SHALL 被移除（保留 fallback 会造成双 store 状态分裂）。

`_persist_session_state` SHALL 在 SessionStateStore 异常时 swallow 并 log warning（best-effort 持久化，不阻塞主请求），但**仅在锁未持有**的情况下——锁持有期间 save 失败应 propagate（说明是真实数据问题，不是竞态）。

#### Scenario: 锁获取 retry 3 次后成功

- **WHEN** 首次 `acquire_session_lock` 失败但 retry 1-2 次内成功
- **THEN** 进入临界区执行 save，无异常抛出

#### Scenario: 锁 retry 3 次全失败抛 SessionStateConflictError

- **WHEN** retry 3 次后仍无法获取锁
- **THEN** `SessionStateConflictError` 抛出，消息含 `(org_id, user_id, session_id)`
- **THEN** 异常 propagate 到 chat handler，返回 409 Conflict（或 chat endpoint 的等价错误响应）

#### Scenario: 移除 legacy fallback 不影响既有测试

- **WHEN** 既有 23 个 `test_execution_service.py` 测试运行（不传 `checkpoint_store`）
- **THEN** `_persist_session_state` 因 `self._checkpoint_store is None` 直接 return（不进入锁/save 分支）
- **THEN** 不调用 `self._state_store.save`（原本就走这个分支的测试需要更新——但既有测试都是 mock，不受影响）

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
