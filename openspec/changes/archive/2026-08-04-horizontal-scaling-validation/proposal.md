## Why

Change 3（`session-state-store-wiring`，2026-08-03 archived）把 `SessionStateStore` 接入生产 chat 请求路径，但代码 review 与业界对比调研（覆盖 LangGraph / AgentScope / Bedrock AgentCore / Salesforce BYOP / Restate / Hermes / Temporal / Ably 等 20 个项目）暴露**三个真实问题**：

1. **流式保存路径漏接（bug）**：`execution_service.py:473-474` 的 `_stream_execute` 仍只走 deprecated `AgentStateStore`，未调用 Change 3 新增的 `_persist_session_state`。配置 `SESSION_STATE_STORE_BACKEND=tiered` 时，流式 chat 在生产环境（`state_store=None`）下**完全不持久化**——session state 永远不写 Redis/PG。
2. **并发写竞态无防护**：同一 `(org_id, user_id, session_id)` 三元组被两个并发请求持有时，两次 `SessionStateStore.save` 会 last-write-wins，丢失前一次的 agent_state。LangGraph production guide、BSWEN 实战、Kleppmann 文章都把这一点列为生产必踩坑。
3. **性能与可观测性黑盒**：配置 tiered backend 后延迟增加多少、save 失败率多少、Redis 命中率多少——全部未知。BYOP（5ms 平台开销）和 LangGraph（checkpoint write latency 为 4 个必看指标之一）都把这些作为生产就绪门槛，我们当前没有。

本 change 修复以上三个问题，**让 Change 3 的 wiring 真正达到生产可用**，为 13.4a 的 5-change 路线图收尾（后续 Change 5 EventStore-PG 后整个 13.4a 完成）。

## What Changes

- **修复流式保存 bug**：`_stream_execute` 接入 `_persist_session_state`，stream 正常结束 atomic save；客户端断开/超时时 best-effort save 当前 agent_state（参考 Ably "durable sessions" 概念 + BYOP session-end atomic 模式）
- **新增并发写竞态防护**：在 `SessionStateStore` 接口层加 `acquire_session_lock` / `release_session_lock` 可选方法（default no-op）；`RedisSessionStateStore` 用 `SET NX EX` + Lua release + owner UUID；`PostgresSessionStateStore` 用 `SELECT ... FOR UPDATE` 在事务内自然串行化；`TieredSessionStateStore` 用 Redis 锁 + PG 幂等 key 双保险
- **新增 jitter retry 失败策略**：锁获取失败时 retry 3 次（20-150ms 随机 jitter，借鉴 Hermes Agent 实践），仍失败则抛 `SessionStateConflictError` 并 fail-fast——**不走** legacy `_state_store` fallback（保留 fallback 是 bug）
- **新增性能基准测试**：`tests/test_services/test_session_state/test_perf.py`，对比 wired vs unwired 的 save/load latency（p50/p95/p99），用 fakeredis 不依赖外部基础设施；目标：tiered backend 平台开销 ≤ 10ms p95
- **新增 OTel observability 钩子**：`_persist_session_state` 加 OTel span + 结构化日志（save 次数、失败次数、latency、backend 类型），接入现有 `hecate.observability`
- **显式文档化 checkpoint 频率决策**：在 spec 中明确"services-layer SessionStateStore 每请求 1 次 atomic save，**不**做 per-superstep checkpoint"——这是经过 LangGraph production 教训（"78% reduction in checkpoint writes"）对比后的有意决策
- **不动** `AgentStateStore`（deprecated 参数 `state_store`）——保留双写期作为安全网，待 Change 5 EventStore 跑通后 Change 6 再做 cleanup（参考 Claude Code dual-write 模式 + AgentScope major 版本节奏）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `distributed-session-state-store`：新增并发锁、流式保存修复、性能基准、observability、checkpoint 频率决策 5 类 ADDED Requirements

## Impact

- **修改文件**：
  - `src/hecate/services/workflow/execution_service.py`（`_stream_execute` 接入 `_persist_session_state` + 移除 line 473-474 legacy 分支 + `_persist_session_state` 加锁调用 + OTel span）
  - `src/hecate/engine/session_state.py`（`SessionStateStore` ABC 加 `acquire_session_lock` / `release_session_lock` 默认 no-op 方法 + `SessionStateConflictError` 异常）
  - `src/hecate/services/session_state/redis_store.py`（实现 Redis SETNX 锁 + Lua release）
  - `src/hecate/services/session_state/postgres_store.py`（实现 PG `SELECT FOR UPDATE` 行锁）
  - `src/hecate/services/session_state/tiered_store.py`（Redis 锁 + PG 幂等 key）
  - `src/hecate/services/session_state/factory.py`（无变化，锁方法由各实现提供）
- **新增文件**：
  - `tests/test_services/test_session_state/test_concurrency_lock.py`（锁正确性测试）
  - `tests/test_services/test_session_state/test_perf.py`（性能基准）
  - `tests/test_services/test_workflow/test_streaming_save.py`（流式保存 bug 回归测试）
- **无新运行时依赖**：所有改动使用现有模块（`redis.asyncio`、`sqlalchemy.ext.asyncio`、OpenTelemetry）
- **向后兼容**：锁方法在 ABC 上是 default no-op，`InMemorySessionStateStore` 不需改；既有 23 个 execution_service 测试零修改通过
- **CI 不变**：无新依赖、无 schema 变更、无测试基础设施变更
- **文档同步**：归档时把 13.4a 进度从 3/4 更新到 4/4（feature-catalog / roadmap / p3-mvp-audit）
