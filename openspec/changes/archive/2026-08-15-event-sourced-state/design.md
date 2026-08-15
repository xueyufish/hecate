# Design: event-sourced-state

> 特性 ID：**1.3.19 Event-Sourced Execution State（Log-as-Truth）**
> 探索记录：2026-08-15 深度探索会话（代码实证 + dsh/deer-flow 源码分析 + Temporal/Dify/EventStoreDB/Kafka/OpenAI/AgentCore retention 调研）

## Context

### 现状三层断层（代码实证）

| # | 事实 | 位置 |
|---|---|---|
| 1 | 状态真相在 ChannelManager（内存），每 superstep 全量快照进 checkpoint（O(N²)） | `pregel.py:407-412` |
| 2 | `CHANNEL_WRITE` 事件只记通道名不记值，日志无法重建状态；读侧消费方为零 | `pregel.py:399`；grep 证实 `get_events`/`replay` 无生产调用者 |
| 3 | 生产 chat 路径用 `InMemoryCheckpointStore`（用完即弃）；`PostgresCheckpointStore` 零接线 | `execution_service.py:338` |
| 4 | resume 端点为存根：只翻转 status，`resume_value` 丢弃 | `api/management/sessions.py:134-181` |
| 5 | `SessionState.channel_state` 恒为空 dict；`event_position` 每 turn 同步但从未被消费 | `execution_service.py:530` |
| 6 | chat API 三条分叉：A（agent tools 直驱，绕过 Pregel）、B（kb/增强，过引擎）、C（纯文本直通） | `api/v1/chat.py:250/307/407` |
| 7 | 子图复用父 session_id，事件归属歧义 | `subgraph.py:59`、`agent_worker.py:142` |
| 8 | 13.4a 已铺好三块基石：events 表 `(session_id, version)` PK + JSONB payload + 租户列；SessionStateStore（Redis/PG/Tiered）；`SessionState.event_position` | `event_state/models.py`、`engine/session_state.py` |

### 参考

- **dsh**（deepseek-harness 源码分析）：session log = only source of truth、`deriveMessages()` 投影、deep-freeze 请求、fail-closed 审计对、伴随式 invariant 模块、`output-retention` 有界保留器（head/tail 字节预算 + `omittedBytes`）。
- **Temporal**：retention 仅对 closed 执行起算（1–90d，cloud 默认 30d）；单执行硬上限 51,200 事件 / 50MB（warn 10,240 / 10MB）；continue-as-new 锚定式历史轮转；归档到 blob。
- **Dify**（反面教材）：默认无限期 → 370 万行 → 查询 5–60s；补救性清理又引发表锁/时区/级联 bug —— 证明 retention 必须与状态模型同步设计。
- **EventStoreDB**：`$maxCount`/`$maxAge`/`TruncateBefore` 流元数据 + scavenge 两阶段回收（远期参考）。
- **Kafka compaction**：按 key 保最新值、tail 压缩、tombstone 保留期（远期参考）。
- **Bedrock AgentCore**：1GB/session + 14d；**OpenAI**：遥测 30d / 应用状态 until deleted 分离。

## Goals / Non-Goals

**Goals:**

1. EventStore 升格为执行状态唯一真相："model-visible ⟺ logged"（Phase 1 出处级可验证）。
2. 恢复路径从全量快照注入改为 缓存 + tail 重放；checkpoint 降级为纯优化。
3. HITL 跨进程 resume 真正可用（本 change 的端到端验收载体）。
4. 存储从 O(N²) 降为近线性（delta 存储 + 有界载荷 + 会话级 TTL）。
5. 热路径（LLMWorker/ToolWorker 读取、superstep 循环语义）零行为变化，1713 测试基线最大程度保留。

**Non-Goals:**

- 路径 A/C 的调用入日志（显式边界：日志覆盖范围 = Pregel 执行路径；代码留 TODO 指针）。
- Conversation/Message 双重记账统一（A2 挂账）。
- 1.3.4 fail-closed 审批对、1.3.5i E3 瀑布中间件、9.4 content-aware gating、8.20 Replay UI、8.21 Projection Registry（全部是下游消费方）。
- 日志截断/压缩、archive 归档实现、Redis-backed EventStore、chat 服务端历史。

## Decisions

> 完整决策过程见探索会话；此处为定案与理由。

### D1 事件语法：双层

- **通道 delta 层**（通用）：`CHANNEL_WRITE {channel, value, seq}` 携带**裁决后**完整值；`CHANNEL_WRITE_REJECTED`（仅审计，fold 跳过）；`EVICTION`（事实记录）。
- **会话语义层**：`STEP_END`（提交点）、`TURN_START/END`、`TOOL_CALL/TOOL_RESULT` 配对、`INTERRUPT`/`RESUME` 携带完整 payload（含 `interrupt_value`）、`SUBGRAPH_START/END {child_session_id}`。
- `LLM_REQUEST` 记录**完整冻结请求**（截断后）：8.20 回放直接可用；hash-only 的"可重新推导"依赖 config 版本 + offloading 文件副作用，链条脆弱（已否决）。

### D2 通道入日志规则：LogPolicy 黑名单

默认入日志（宁多勿漏：忘配 = 多记而非状态丢失），排除三类集中注册于 `engine/logpolicy.py`：① ephemeral（`_fanout__*`、`_resume_value`）；② 可重建控制通道（`_` / `sys.` 前缀，恢复时由 execution_service 重新注入 —— 与今天每 turn 注入行为一致）；③ 已有专属持久化（`_agent_state`，活对象不可序列化，SessionStateStore.agent_state 已存）。`_route` 显式豁免入日志（条件路由是 fold 正确性的一部分）。等价断言只覆盖非排除通道。

### D3 状态模型：投影缓存 + 三重强制机制 + 三层读取

> 探索期称"三颗牙齿"（"invariant with teeth"—— 不变式若无可执行机制即为愿望）：以下三项把 "model-visible ⟺ logged" 从声明式升级为 enforced by construction + verified by assertion。

- **ChannelManager = 日志的内存投影读缓存**，热路径零改动；`derive_messages()` = state fold 的 messages 通道视图，服务温/冷路径。
- **机制 1 — Write-Ahead Logging（WAL 序 + 提交点）**：superstep 结果收集 → 冲突裁决 → **批量 append（单事务、批内保序）** → apply 到通道；append 失败则不 apply。`STEP_END`/`INTERRUPT` = commit point（提交点）；尾部有 `CHANNEL_WRITE` 无提交点 = torn write（撕裂），恢复回退到上一完整 step，即 crash recovery with atomic commit semantics（与今天每步原子 checkpoint 语义等价，无新风险；连带闭合崩溃窗口 C5 与 append 吞吐 N1 —— 每步 1 次 DB 往返而非每节点）。
- **机制 2 — 单一状态转移函数（event applier 复用，防 projection drift）**：fold 与 ChannelManager 写入共用 `ChannelBehavior.write()`，保证 deterministic replay；**fold 输入为裁决后事实**（不重新裁决冲突），EVICTION 作为事实事件参与。
- **机制 3 — 运行时不变式断言 + fail-stop（snapshot validation）**：恢复 / interrupt / 物化时校验 `projection ≡ snapshot`；违背 = **fail-stop**（分歧是 bug 信号，缓存可弃、日志为准；不做热修复 —— 运行中热换会掩盖 bug 且与 worker 手中深拷贝产生对象身份混乱，let-it-crash 哲学）。
- **物化缓存瘦身**：只存 `channel_state + log_version`（复用 `SessionState.event_position` 字段，不加新字段）；superstep / interrupted_node / interrupt_value / route 全部由日志推导（`Event.superstep` 已存在；`pending_writes` 死字段从签名移除）。
- **Phase 1 不变式为出处级**：冻结请求中每条消息可在日志中找到产生事件；完整重建等式（`LLM_REQUEST = assembly(log, config)`）留给 8.20 Phase 2 版本绑定。

### D4 LogInvariants 注册表

`engine/loginvariants.py`，dsh 伴随模块模式（各引擎模块自注册检查）：step 配对、工具调用配对平衡、LLM 请求出处、dispatch 树一致性（C1 引用对 + 子日志完整性）、投影等价。检查时机：append 时（廉价局部）、fold 时（关系型）、测试常开。

### D5 迁移：版本标记，无兼容层

事件 payload 携带 `log_schema_version`；旧事件（无值）为不可回放前缀，fold 遇到即停、该会话回退旧恢复路径。零 alembic 迁移。存量回填不成立：旧日志物理上未记值、checkpoint 又从未落库。

### C1 子图归属：嵌套 session

子图独立 session 日志；父日志记 `SUBGRAPH_START/END {child_session_id}` 引用对。父 fold 零过滤天然干净；子图独立可回放（多智能体调试刚需）；dispatch 树 invariant 复用撕裂检测（每个 START 有配对 END + 子日志自身提交点完整）。子会话 events 行的 org/user 列继承父会话；retention 按会话树级联。
（否决 node_id 前缀：字符串魔法 + fold 逐条过滤；否决 scope 字段：流内过滤 + 需发明新机制。）

### C2 PostgresCheckpointStore：软废弃

本 change 加 DeprecationWarning + docstring；硬删除 + `checkpoints` 表 alembic drop 留后续清理 change（沿 13.4a-6/-7 两段式先例）。`CheckpointStore` ABC **保留名字、改造为物化缝**：载荷瘦身为 `channel_state + log_version`；`InMemoryCheckpointStore` 供测试继续使用；生产物化经新 adapter（services 层）以 `tenant_context_provider` 闭包模式（照抄 `PostgresEventStore` 先例）落 SessionStateStore —— 引擎保持租户无感知、单键 `session_id` 契约。

### C4/C6 细粒度语义

- task 模式照常记日志（8.20/调试价值），retention 分层区分（7d vs 30d）。
- MESSAGES 流式 chunk 不入日志；聚合后的 `LLM_RESPONSE` 入。

### B1/N3 retention：会话级 TTL（业界证据修正版）

- **计时起点 = 会话终态**（completed/failed/expired）；**interrupted 豁免**（否则 TTL 会杀掉等待人工审批的 HITL 会话 —— Temporal "closed only" 模式）。
- 默认 conversational 30d / task 7d（30d 为业界共识），org 级可覆盖。
- **写入时有界保留器优先于事后清扫**（dsh `TextRetainer` 模式：head/tail 预算 + `omitted_bytes` + 恢复指引）—— 控增长率比控表大小更重要。
- **删除单位 = 会话（树）**，绝不部分修剪（fold-from-origin 要求前缀完整）；不做分区表。
- 删除机制吸收 Dify 教训：定时批删 + 游标分页 `(created_at, id)` + batch 上限 + dry-run + 低峰执行 + 指标。
- 单会话 warn-only 阈值（对齐 Temporal：~10MB/10k 事件告警，不强制终止）；物化缓存 `log_version` 锚点为远期 TruncateBefore 式截断预留语义。
- 策略枚举 `delete|archive`（archive 仅留位）。

### B2 Resume 真实现（含探索补丁 1）

校验依据**从 Session 行改为日志推导**：`get_events` 尾部存在未闭合 `INTERRUPT` 即可恢复；Session 行缺失则懒补建（`SessionModel` 整体定性为日志投影，status 只能由真实事件置位 —— chat 路径 B 从不创建 Session 行，若照现有端点逻辑实现，chat 会话永远 404，此为探索发现的真实缺口）。恢复流程：加载 SessionState 缓存 → 校验/回退（撕裂检测）→ tail 重放到提交点 → 重建 runtime → 注入 resume_value → 续跑。graph 重新编译失败即拒绝（fail-fast；版本绑定留 8.20 Phase 2）。API 形状不变。

### B3/N2 物化填充

`_persist_session_state` 写入裁决后 channel_state 投影，存前过有界保留器；缓存不完整时恢复回退 tail 重放（缓存可烂、日志不能烂）。物化时机 = turn 正常结束（现状时机）/ interrupt / 每 N=10 superstep（占位，基准后调）。SessionStateStore 的 ABC 与 Redis/PG/Tiered 实现零改动，只改写入侧内容。

### 补丁 2：retention 级联清单（跨双脊柱）

删除会话树 → events（含子会话）+ SessionState + Session 行；删除 conversation → Conversation + Message + TurnScore + 关联 Evidence/Cluster；GDPR → org/user 列扫全表（含 PIIMapping，与 events 同生共死但访问路径永不合流 —— 日志只存掩码占位符，加密原文仅存 PIIMapping）。

### 持久化对象三层分类（文档基线，写入 concepts.md）

- **骨架**（凡过引擎必有）：Event（真相）、SessionState（缓存）。
- **条件**（显式开关）：Session、Conversation/Message、Evidence、Trace、ToolDecision、ApprovalRecord、PIIMapping、SecurityFinding、TurnScore/Cluster。
- **非会话**（正交）：AuditLog（管理面）、Metric（聚合）、环境文件 offload。
- 8.20 双源边界：EventStore = 发生了什么/顺序/值；TraceModel = 耗时/span 层级 —— 回放 UI 双源 JOIN，两者重叠是正交维度非冗余，不允许"优化"合并。

## Risks / Trade-offs

- [事件载荷放大：LLM_REQUEST 冻结请求 + 值级 delta 使 events 表成为最大表] → 写入时有界保留器 + 会话级 TTL + warn 阈值；总量仍严格小于今天的每步全量快照。
- [WAL 序引入 superstep 关键路径同步 DB 往返（~1-2ms/步）] → 批量 append 已把往返收敛为每步 1 次；批事务异步化（append 不阻塞 apply，仅阻塞提交点判定）留作实现期基准决策。
- [fold 与 ChannelManager 漂移] → 同一 fold 函数 + 边界等价断言 fail-stop + 测试常开 invariants。
- [1713 测试基线受冲击] → 热路径零改动设计使冲击集中在 checkpoint 载荷断言的机械性更新；fold/invariants/retention/恢复新增四类测试。
- [嵌套 session 导致会话增殖、retention 复杂化] → 会话树级联删除为 B1 原生单位；org/user 列继承保证租户查询不变。
- [撕裂尾部在极端场景（append 成功、STEP_END 丢失且无法重试）丢失整个 superstep] → 与今天 checkpoint 每步原子的语义等价，非回归；invariants 在恢复时报告丢弃量。
- [旧会话不可回放] → 物理不可能补值（值从未记录）；版本标记显式化该事实，无消费者受影响（读侧为零已验证）。

## Migration Plan

1. 纯增量上线：新事件带 `log_schema_version: 2`；旧事件前缀不可回放（fallback 旧路径）。
2. 零 alembic（events/checkpoints 表结构均不变）。
3. 回滚 = 关闭物化填充与 resume 新逻辑开关（事件照写，多写无损）。
4. C2 硬删除与 checkpoints 表 drop：后续独立清理 change。

## Open Questions

- N5 验收数值：冷重放 SLA（8.20 目标）、温恢复 SLA、等价断言抽样频率 —— tasks 阶段定基准占位值，实现期校准。
- 批量 append 异步化是否必要 —— 实现期基准测试决定（默认同步，保留异步开关设计）。
- N 值（物化节奏，占位 10）—— 基准后调整，配置化。
