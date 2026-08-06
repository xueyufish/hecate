## Context

13.4a 路线图（Change 1-4）已交付 `SessionStateStore`（snapshot 层）的 engine 抽象 + Redis/PG/Tiered 实现 + 生产 wiring + 水平扩展 validation。但 `EventStore`（event log 层）只完成了 engine 抽象（`2026-06-02-eventstore-interface`）和 PregelRuntime/Worker 接入（`2026-06-06-wire-eventstore`）——services 层从未给 `PregelRuntime` 传过 `event_store`，导致**生产路径无任何执行事件被记录**。

同时，`SessionState.event_position` 字段（注释为"EventStore consumption position, used for replay when restoring"）当前是悬空字段——没有任何代码保证它等于 `EventStore.get_version(session_id)`。这是 snapshot ↔ event log 双轨设计中缺失的最后一块胶水。

**业界对照**（基于 15 个项目调研：Huawei AgentArts / openJiuwen / AgentScope Java 2.0 / IBM watsonx / Google Gemini Enterprise / Salesforce Agentforce / Amazon Bedrock AgentCore / OpenClaw / Hermes Agent / Claude Code / OpenAI Codex / Dify / DeerFlow / LangGraph Postgres / Temporal / Restate）：

- **企业级多租户平台**（AgentScope / Bedrock / Dify / Agentforce）全部以 `(tenant/org, user, session)` 三元组或其变体为主键——Hecate 现状 `(org_id, user_id, session_id)` 对齐
- **PG 是企业级 event log 主选后端**（LangGraph `PostgresSaver` / Dify SQLAlchemy / DeerFlow DatabaseConfig）——Redis 适合 snapshot 缓存不适合 event log range query
- **append-only + 单调 version + replay** 是 event sourcing 主流形态（LangGraph `checkpoint_writes` / Dify `WorkflowNodeExecutionModel` / DeerFlow `RunEventStore` / Temporal history tree / Restate Bifrost）
- **retention 业界无银弹**——LangGraph / Dify / DeerFlow 都未实现自动清理，本次对齐"deferred"

## Goals / Non-Goals

**Goals:**

- 让生产路径首次记录执行事件（NODE_START/NODE_END/LLM_*/TOOL_*）
- 补齐 `SessionState.event_position` ↔ `EventStore.get_version()` 同步契约
- 复用 Change 1-4 的 factory + DI + lifespan + chat.py Depends 模式（最小认知开销）
- 保持 100% 向后兼容（`EVENT_STORE_BACKEND="memory"` 默认，既有测试零修改通过）

**Non-Goals:**

- ❌ 不扩展 engine EventStore ABC 为租户感知（保留单键 `session_id`，租户列在 PG schema 层）
- ❌ 不引入 Redis event backend（Redis 不适合 range query）
- ❌ 不引入 Kafka / Temporal / Restate 式外部 event log
- ❌ 不实现 retention / TTL / 分区（schema 预留 `created_at` 列即可）
- ❌ 不删除 InMemoryEventStore（仍是 default backend）
- ❌ 不重写 PregelRuntime 的事件发射逻辑（`wire-eventstore` 已交付，本次只补 services 层 wiring）

## Decisions

### 决策 1：租户键——ABC 保持单键，PG schema 补运维列（方案 C）

**选择**：engine `EventStore` ABC 保持 `session_id` 单键签名，`PostgresEventStore` 在 PG 表中增加 `org_id` / `user_id` 运维列（非主键），通过 `tenant_context_provider` 在 `append` 时填充。

**备选方案**：

| 方案 | 评估 | 决定 |
|---|---|---|
| A. 扩展 ABC 加 org/user 到 Event + ABC | 破坏性变更，需重做 `wire-eventstore`（`PregelRuntime` / `Worker` 全改） | ❌ scope 过大 |
| B. ABC 不动，PG 表单键 session_id | 失去租户运维能力（GDPR 删除、按租户清理、磁盘计量） | ❌ 企业级不可接受 |
| **C. ABC 不动，PG 表加 org/user 运维列 + provider 注入** | 无 engine 改动，租户列就位，ABC 方法签名干净 | ✅ 采纳 |
| D. services 层新 ABC `TenantAwareEventStore` + adapter | 两个 ABC 维护负担 | ❌ 过度设计 |

**业界对照**：AgentScope `(userId, sessionId)` 进 Redis key 或 MySQL PK；Bedrock AgentCore 用 5 层 namespace（global/strategy/tenant/user/session）；Dify `(tenant_id, app_id, workflow_run_id)` 进每个 key。Hecate `(org_id, user_id, session_id)` 与 AgentScope / Bedrock 对齐。

`session_id` 是 UUID（全局唯一），作 PK 足够保证隔离；`org_id`/`user_id` 列是**运维列**（operational columns），不是 security boundary——security boundary 仍由应用层 RBAC + 多租户 model 层（`AgentModel.org_id` 等）承担。

### 决策 2：append 串行化——PG `SELECT MAX(version)+1 FOR UPDATE`

**选择**：`PostgresEventStore.append` 在事务内执行：

```sql
SELECT COALESCE(MAX(version), 0) + 1 FROM events WHERE session_id = ? FOR UPDATE;
-- 然后用上面返回的 next_version 做 INSERT
```

`FOR UPDATE` 锁定现有行集（如有），让并发 append 自然串行化。`INSERT ... ON CONFLICT (session_id, version) DO NOTHING` 作为最后一道防线。

**备选方案**：

| 方案 | 评估 | 决定 |
|---|---|---|
| **PG `SELECT MAX+1 FOR UPDATE`** | 简单、利用 PG 行锁、无额外组件 | ✅ 采纳 |
| 单独的 `session_version_counters` 表 + `UPDATE ... RETURNING` | 多一张表、多一次写、维护负担 | ❌ 过度 |
| PG advisory lock (`pg_advisory_xact_lock(hashtext(session_id))`) | 需要额外的 lock 释放管理，调试复杂 | ❌ 不必要 |
| Redis 分布式锁（像 SessionStateStore） | 引入 Redis 依赖（本次 PG-only）；event append 频率比 SessionState 高一个数量级，Redis 锁成本不划算 | ❌ scope 蔓延 |
| 序列号列 `BIGSERIAL` 全局自增 | 破坏 `(session_id, version)` 复合主键语义；version 应 per-session 单调 | ❌ 语义错误 |

**业界对照**：
- Temporal 用 history tree + branch 机制（每个 workflow run 独立 tree，node_id 是事件 batch 起始 ID）——比 Hecate 复杂得多，因为 Temporal 支持 reset/fork
- Restate 用 partition-leader 串行化（单 writer per partition）——Hecate 没有分区机制
- LangGraph `checkpoint_writes` 用 `INSERT ... ON CONFLICT DO NOTHING`（task_id+idx 唯一），依赖 Pregel 的 task 调度避免并发——Hecate 类似但更直接

`SELECT MAX+1 FOR UPDATE` 在 Hecate 当前规模（~1000 events/session，并发 ~100 sessions）性能足够；`events` 表按 `(session_id, version)` 主键聚簇，`MAX(version) WHERE session_id=?` 是 index-only scan，延迟 < 1ms。

### 决策 3：retention 完全 deferred，schema 预留扩展点

**选择**：本次不实现 retention / TTL / 分区。`events` 表加 `created_at TIMESTAMPTZ DEFAULT now()` 列与 `(org_id, user_id, created_at)` 索引，但**不引入任何清理逻辑**。spec 中显式标注"deferred"。

**备选方案**：

| 方案 | 评估 | 决定 |
|---|---|---|
| **完全 deferred** | 对齐 LangGraph / Dify / DeerFlow 现状；spec 标注阻止误解 | ✅ 采纳 |
| 加 TTL 列 + cron sweeper | 增加运维负担；Hecate 当前规模未到瓶颈 | ❌ 过早优化 |
| PG 原生 RANGE 分区（按月） | Restate 式 log trim；分区表变更复杂，跨分区查询麻烦 | ❌ 过度工程 |
| Codex 式 Zstd 冷压缩 | Hecate events payload 是 JSONB，PG 已有 TOAST；冷压缩收益小 | ❌ 收益不显著 |

**业界对照**：
- **TTL by session** 最常见（Bedrock 365d / AgentArts 7-365d / Agentforce 24h / Claude Code 30d）
- **Partition drop**（Restate log trim after snapshot）——Hecate 无 snapshot 机制
- **无显式 retention**（LangGraph / Dify / DeerFlow）——Hecate 对齐

未来 retention 实现 SHALL 复用 `created_at` 列与 `(org_id, user_id, created_at)` 索引——schema 已经为那一天做好准备，但不在本次引入复杂度。

### 决策 4：`_sync_event_position` 单独函数，最小触碰 Change 4 代码

**选择**：新增 `_sync_event_position(state, event_store) -> SessionState` 函数，在 `_persist_session_state` 末尾调一次（锁持有期间、save 调用之前）。

```python
async def _sync_event_position(state, event_store):
    if event_store is None:
        return state
    pos = await event_store.get_version(<state.session_id from metadata>)
    return state.model_copy(update={"event_position": pos})
```

**备选方案**：

| 方案 | 评估 | 决定 |
|---|---|---|
| **单独函数 `_sync_event_position`** | 局部改动、单元可测、向后兼容（event_store None 时 no-op） | ✅ 采纳 |
| 在 `_persist_session_state` 内联同步代码 | 改动散落、难单元测试 | ❌ 可读性差 |
| 不做（spec 标注 deferred） | `event_position` 字段继续悬空；resume 时 replay 位置错误 | ❌ 破坏不变式 |

**业界对照**：
- LangGraph 用 `channel_versions` + `pending_sends` 在 checkpoint 内同步追踪事件位置——比 Hecate 复杂
- Gemini ADK `state_delta` 在 tool call 后 atomic 写——Hecate 简化版（仅在 persist 时同步一次，而非 per-tool-call）
- Codex `RolloutRecorder` 用 `SessionMeta` + `TurnContext` item 在 rollout 内追踪——Hecate 借鉴但简化

`SessionState.event_position` 是**最终一致**（eventual consistency），不是强一致——它在每次 `_persist_session_state`（chat 请求结束时）被同步一次，中间 PregelRuntime 产生的事件不影响当前请求的 in-memory state。这是合理折衷：事件流是审计/调试用途，不是实时控制信号。

### 决策 5：不引入 Redis event backend

**选择**：本次只做 `memory`（默认）和 `postgres` backend，不做 Redis event store。

**理由**：
- Redis 不适合 event log——`get_events(session_id, from_version)` 需要 range query（ZRANGE 或 sorted set），延迟比 PG `SELECT ... WHERE session_id=? AND version>=? ORDER BY version` 高（PG 主键聚簇 index-only scan）
- Bedrock AgentCore Memory 把 short-term memory（事件）放自己的托管服务；AgentScope 默认 Redis 但那是 snapshot 不是 event log
- Hecate Change 1-4 的 Redis tiered 模式是为 snapshot 缓存（write-through read-through），event log 没有缓存语义（每次 append 都需持久化）
- 引入 Redis event backend 会增加：序列化协议、一致性窗口、TTL 管理三层复杂度——本次 scope 不允许

**未来扩展点**：`acquire_event_lock` 接口为未来 Redis 分布式锁实现预留（本次 PG 继承 default no-op）。

### 决策 6：`EventModel.id` UUID 不参与主键

**选择**：`events` 表复合主键 `(session_id, version)`，`id` UUID 仅作业务标识（用于跨 session 全局唯一引用 Event），不参与主键。

**理由**：
- `(session_id, version)` 已全局唯一（同 session 内 version 单调）
- `id` UUID 是 `Event` dataclass 字段，用于跨 session 引用（trace 关联、跨 session 查询）
- 把 `id` 加进主键会让 `(session_id, version, id)` 三列主键，增加索引大小无收益
- LangGraph `checkpoint_writes` 也是 `(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)` 复合主键，没有单独 UUID 主键

## Risks / Trade-offs

### [Risk] `SELECT MAX(version)+1 FOR UPDATE` 在高并发同 session 写时形成 convoy

→ **Mitigation**：Hecate 当前规模（~100 events/session, ~100 并发 session）不会触发；同 session 的并发 append 罕见（一个 session 通常由一个 PregelRuntime 实例独占）。如果未来出现瓶颈，可加 Redis 分布式锁（`acquire_event_lock` 接口已预留）或迁移到 PG advisory lock。

### [Risk] `events` 表无 retention 无限增长

→ **Mitigation**：spec 显式 deferred；schema 预留 `created_at` 列与 `(org_id, user_id, created_at)` 索引；未来 ops change 引入 TTL sweeper 不需 schema migration。LangGraph / Dify / DeerFlow 也都是这个状态。

### [Risk] `tenant_context_provider` 是 leaky abstraction

→ **Mitigation**：provider 是 `Callable[[], tuple[UUID, UUID]]`——简单 closure，从 contextvar 读取 `(org_id, user_id)`。这是 Hecate 已有的请求上下文模式（chat.py 已能拿到 org_id/user_id），不是新发明。provider 为 `None` 时 `org_id`/`user_id` 列写 `None`，向后兼容测试。

### [Risk] `_sync_event_position` 是最终一致而非强一致

→ **Mitigation**：这是有意折衷。`event_position` 在 `_persist_session_state` 时同步一次（chat 请求结束），中间事件不影响当前请求的 in-memory state。如果未来需要强一致（例如实时 replay 监控），可在 PregelRuntime `_record_event` 后立即更新 contextvar，但这会引入性能开销——本次不做。

### [Risk] ABC 单键 `session_id` 与多租户 PG schema 的阻抗失配

→ **Mitigation**：`session_id` 是 UUID 全局唯一，作 PK 足够隔离；`org_id`/`user_id` 列是运维列不是 security boundary。security boundary 仍由应用层 RBAC + 多租户 model 层承担（与 `AgentModel.org_id` 等既有模式一致）。业界（LangGraph thread_id 单键、Temporal namespace_id 分层）证明单键 ABC + 多租户存储层是常见且可行的模式。

### [Trade-off] 不引入 Redis event backend 牺牲了低延迟读

→ **接受**：event log 是审计/调试用途，不是热路径（hot path 是 SessionStateStore snapshot）。PG `SELECT WHERE session_id=? AND version>=?` index-only scan 延迟 < 1ms，对审计场景足够。如果未来需要 sub-ms 热路径读，可加 Redis read-through cache（不影响 ABC）。
