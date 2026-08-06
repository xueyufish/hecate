## Context

Hecate 的 stateful execution 架构当前断裂：

```
POST /v1/chat/completions
  → chat.py → WorkflowExecutionService(port, db)
  → execution_service.py:281  ⚠️  checkpoint_store = InMemoryCheckpointStore()  (每次新建)
  → PregelRuntime(graph, worker, checkpoint_store=↑)
  → _restore_from_checkpoint() → 永远 None (空 store)
  → 每个 superstep save 到 InMemory → 请求结束即丢失
```

**三个独立 ABC，互不连接：**
- `CheckpointStore` (engine) — `save/load/list_checkpoints`；实现：`InMemoryCheckpointStore` (用) + `PostgresCheckpointStore` (已建但未接线)
- `AgentStateStore` (services) — `save/load/list`；实现：仅 `InMemoryStateStore`；chat API 完全不传
- `EventStore` (engine) — `append/get_events/replay/get_version`；仅 `InMemoryEventStore` 在用

这种状态让水平扩展（13.4 + 13.4a）不可能——任何 replica 崩溃都会丢失 session。

**本 change 是 5-change 拆分的第一步（详见 proposal.md）：**
1. **本 change**：engine 层引入 `SessionStateStore` 抽象
2. `session-state-store-redis-pg`：Redis + PostgreSQL + Tiered 实现
3. `session-state-store-wiring`：接入生产（替换 execution_service.py:281）
4. `horizontal-scaling-validation`：K8s + HPA + load test 验证
5. `eventstore-pg-wiring`：EventStore 也接 PostgreSQL

p3-mvp-audit.md 第 342 行已将 13.4 + 13.4a 列为 P0 发布阻塞项。

## Goals / Non-Goals

**Goals：**
- 引入 `SessionState` frozen dataclass，聚合 channel_state + agent_state + event_position + metadata
- 定义 `SessionStateStore` ABC，支持 `(org_id, user_id, session_id)` 三元组 key
- 提供 `InMemorySessionStateStore` 实现（单进程 / 测试用）
- engine 层 zero-external-deps（jsonschema 仍为唯一例外）
- 预留 `MemoryProvider` 扩展点（用于未来长期记忆层，本 change 不实现）
- 设计决策固化：write-through / TTL 默认 7 天 / hash tag `{org_id}`（后续 change 引用）

**Non-Goals：**
- 不实现 Redis / PostgreSQL 后端（Change 2 范围）
- 不修改现有 `CheckpointStore` / `EventStore` / `AgentStateStore` 的代码（保持独立抽象）
- 不接入生产路径（Change 3 范围）
- 不实现长期记忆 / MemoryProvider（独立 feature）
- 不实现 Redis Cluster hash tag（Change 2 时再决定）

## Decisions

### Decision 1: SessionState 是不可变 frozen dataclass（Pydantic v2 frozen）

**Why：** 简化并发模型。多个 superstep 并发写时，frozen 强制返回新实例，避免就地修改的竞态。AgentScope 2.0 和 LangGraph 都用 immutable 快照。

**替代方案：** mutable dict
- **拒绝理由：** 难以追踪修改来源，难以做 transactional snapshot，OpenHands `__setattr__` 自动持久化的复杂经验表明 mutable state 是陷阱。

### Decision 2: SessionState 聚合三套 store 的内容（但 ABC 不合并）

```
SessionState（统一数据快照）
├── channel_state: dict          # 原 CheckpointStore 的 channel_state
├── agent_state: dict            # 原 AgentStateStore 的所有字段（含 conversation buffer）
├── event_position: int          # EventStore 消费位置
└── metadata: dict               # superstep, started_at, interrupt, ...
```

**Why：** 数据层统一（一个完整 snapshot 描述一次 agent run），抽象层独立（CheckpointStore 和 EventStore 各自保留 ABC）。

**替代方案：** 把 CheckpointStore / EventStore / AgentStateStore 完全合并成一个 `SessionStateStore` ABC
- **拒绝理由：** EventStore 的 append / replay / get_version 等事件级操作天然和"全状态快照"不匹配。强行统一会让事件级操作变成完整 snapshot 的写入——浪费 100x IO（每次 emit 一个 CHANNEL_WRITE 事件就要写整个 channel_state）。

**为什么 event_position 进 SessionState 但 EventStore ABC 独立：** SessionState 是某次 superstep 边界的"快照"，包含快照点处的 event_position；EventStore 单独负责事件追加和回放。两个抽象互补不冲突（Dify issue #25244 教训：event_position 不共享会导致多副本回放错乱，所以必须进 SessionState）。

### Decision 3: Key 设计 `(org_id, user_id, session_id)`

**Why：**
- AgentScope 2.0 precedent：`(userId, sessionId)` 复合 key 是业界标准
- Hecate AuthContext 已有 `org_id`（= tenant）+ `user_id` + `workspace_id`
- 三元组天然防止跨租户读

**替代方案：** 仅用 `session_id`
- **拒绝理由：** 没有 org_id 维度，无法做 Redis ACL、限流、租户隔离

**Redis Cluster hash tag：** Change 2 时决定是否用 `{org_id}` 做 hash tag 保证同租户数据落同 slot。本 change 只设计 key schema，不实现。

### Decision 4: 完整快照 vs 增量 diff

**Why：** write-through（PG 同步 + Redis 同步）配合完整快照（每次 save 是整个 SessionState）：
- LangGraph superstep 增量：复杂、IO 放大、不利于 debugging
- AgentScope call-boundary 整快照：简单、强一致、易 replay

**Hecate 折中：**
- PregelRuntime 在 superstep 边界 save channel_state（增量到 memory 但持久化时打包成完整 SessionState 快照）
- AgentState 在 call 边界整快照写入
- EventStore position 跟随 channel_state

**为什么不是纯 call-boundary 整快照：** PregelRuntime 是 superstep 模型（ADR-005 progressive worker pool），中间需要 checkpoint 支持中断恢复。如果只在 call 边界快照，中断恢复只能从上一次 call 开始——无法恢复 superstep 中断点。

### Decision 5: TTL 默认 7 天 idle，可配置

**Why：**
- AgentCore microVM session: 15min idle / 8h max（不直接适用，microVM 是计算资源）
- openJiuwen: Redis checkpointer 无明确 TTL，靠 store release 机制
- Hecate conversational session: 用户可能几天后再回来续聊

**默认 7 天 idle**（可通过 `BACKUP_RETENTION_DAILY` 类配置项扩展），活跃 session 不淘汰。

### Decision 6: 写策略 write-through（不是 write-behind）

**Why：**
- 业界主流：LangGraph / Dify / ADK 都是 write-through
- Write-behind 在 Redis 故障时仍然成功，但 PG 异步可能丢失
- PG 是真相源：故障时正确行为是报错，不是静默成功

**Redis 故障容忍：** Redis 写入失败时记录 warning 但不阻塞（PG 已成功，session 仍可用；下次 read 时 cache miss 自动从 PG 加载）。
**PG 故障处理：** 必须报错返回 5xx——业务失败，正确行为。

### Decision 7: SessionStateStore 抽象 vs MemoryProvider 扩展点分离

**Why：**
- SessionStateStore 处理短期 session state（per-session 快照）
- MemoryProvider 处理长期记忆（跨 session 的 user profile、semantic facts）
- openJiuwen L0-L3 分层记忆是 MemoryProvider 的实现细节

**本 change 只做 SessionStateStore；MemoryProvider 抽象留 TODO 给独立 feature。**

## Risks / Trade-offs

**Risk：** SessionState 每次保存是完整快照，状态大时（如 conversation buffer 几十 MB）写入放大
**Mitigation：**
- Change 2 Redis tiered store 用 pipelining / compression
- 后续可加"channel_state 和 agent_state 分开存储"（参考 OpenHands base_state + events 分层）

**Risk：** SessionState 把 agent_state 统一进 engine 层，但 agent_state 概念上属 services 层
**Mitigation：**
- SessionState 是 dataclass（聚合数据），不是 ABC（聚合行为）
- agent_state 字段保留为 `dict[str, Any]`，由 services 层 AgentState 业务对象负责读写
- engine 层不感知 AgentState 业务语义——只负责 snapshot 它

**Risk：** CheckpointStore 在 Change 1 中未被废弃，3 套 ABC 并存可能长期分裂
**Mitigation：**
- Change 3（wiring）会把 `execution_service.py:281` 改为用 SessionStateStore
- Change 3 完成后 CheckpointStore / AgentStateStore 标记 deprecated
- 删除需要单独小 change（清理性 refactor）

**Risk：** SessionStateStore 与 EventStore 的 event_position 字段可能成为耦合点
**Mitigation：**
- event_position 在 SessionState 是 `int`（snapshot 时的位置），EventStore 仍负责追加和回放
- 写入时由 caller 保证一致性（先写 EventStore，再写 SessionState 包含 event_position）

## Migration Plan

本 change 是纯新增，无破坏性：
1. 新增 `src/hecate/engine/session_state.py`（约 200 行）
2. 新增 `tests/test_engine/test_session_state.py`（约 200 行）
3. CI 全过即可，无数据库 migration、无 API 变更
4. 部署：直接合入 main，pre-commit hook 验证 ruff + mypy + pytest

## Open Questions

1. **Redis Cluster hash tag 是否必要？** Change 2 时确认。如果用 {org_id} 做 hash tag，需要 Change 2 在 key 设计里写明。
2. **SessionState 序列化格式：** 当前设计用 Pydantic `model_dump_json()`。是否需要 msgpack 或 protobuf？Change 2 评估（msgpack 二进制更紧凑但不可读）。
3. **agent_state 进 SessionState 是否会让单 session 体积过大？** 需要 Change 2 实测 benchmark。如果过大，考虑分层（base_state + events，对标 OpenHands）。

## Reference：业界调研结论（已固化到本 design）

| 项目 | 关键借鉴 |
|------|---------|
| LangGraph Redis Checkpoint Saver | Cache miss 回退 PG 模式；TTL 可配 |
| AgentScope 2.0 | `(userId, sessionId)` 复合 key；call-boundary 整快照；强制 production 必须 distributed store |
| Amazon Bedrock AgentCore | microVM stickiness（不适用自建）；5 种长期记忆策略（独立 feature）；15min idle / 8h max |
| Dify | PostgreSQL 是 workflow_run 真相源；HA 下 UUIDv7 + retry 解决冲突（issue #25244） |
| Google ADK / Vertex AI | Session = events + state + state_delta；`append_event` 触发持久化 |
| Claude Code | local-first + SessionStore mirror（adapter 模式） |
| OpenAI Codex | rollout JSONL + SQLite state.db 双层 |
| OpenHands SDK | base_state.json + events/ 分层；自动 `__setattr__` 持久化 |
| Palantir Foundry | 事件追加 + 周期快照；乐观并发 vs 线性化权衡（Redis 天然线性化） |
| IBM watsonx Orchestrate | "Externalize state" 普世原则；多区域 active + global LB（无 stickiness） |
| Salesforce Agentforce | Parse 边界（before/after_reasoning）确定性逻辑；variable 默认安全状态 |
| openJiuwen | 直接 Redis checkpointer；L0-L3 分层记忆 + dreaming 后台提取 |
| Huawei Versatile | 仅营销材料，无技术细节可借鉴 |

ADR-020 `docs/design/adr/020-async-execution-distributed-state.md` 是本 change 的设计起点，13.4a 的"SessionStateStore + Redis + PG"设计正是 ADR-020 中描述的 `SessionStateStore` ABC + `InMemorySessionStateStore` + `RedisSessionStateStore` 三层结构。