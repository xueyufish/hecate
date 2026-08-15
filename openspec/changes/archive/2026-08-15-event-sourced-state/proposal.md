# Proposal: event-sourced-state

> 对应特性：**1.3.19 Event-Sourced Execution State（Log-as-Truth）**（roadmap Sprint 7 首位，2026-08-14 re-scope Q4=A 决策）

## Why

Hecate 当前的执行状态持久化存在三层断层（均有代码实证）：

1. **EventStore 仅是观察者**：`CHANNEL_WRITE` 事件只记通道名不记值（`pregel.py:399`），日志无法重建任何状态；读侧消费方为零。
2. **Checkpoint 是全量快照且生产未接线**：每 superstep 全量序列化（O(N²) 存储增长），而生产 chat 路径实际使用 `InMemoryCheckpointStore`（`execution_service.py:338`），`PostgresCheckpointStore` 零接线 —— 进程重启后状态即失。
3. **HITL 跨进程恢复是坏的**：resume 端点只翻转 `session.status` 即返回（`api/management/sessions.py:134-181`），`resume_value` 被丢弃；`SessionState.channel_state` 恒为空 dict（`execution_service.py:530`），13.4a 建好的物化缓存层空转。

与此同时，下游特性 8.20 Run Replay、1.3.4 HITL fail-closed 审计对、1.3.5i E3 瀑布中间件、9.4 content-aware gating、8.21 Projection Registry 全部依赖一份"携带完整值、可重放"的事件日志。本 change 是它们的前置依赖（roadmap 明确 "1.3.19 must land FIRST"）。

## What Changes

- **事件日志升格为唯一真相**：`CHANNEL_WRITE` 携带裁决后完整值；新增边界/语义事件（`STEP_END`、`EVICTION`、`SUBGRAPH_START/END`、`CHANNEL_WRITE_REJECTED`）；`INTERRUPT`/`RESUME` 携带完整 payload；`LLM_REQUEST` 记录完整冻结请求（截断治理）。
- **WAL 写入序 + 提交点**：superstep 结果先批量 append（单事务、批内保序）再 apply 到通道；`STEP_END`/`INTERRUPT` 为提交点，撕裂尾部可检测、恢复时回退到上一完整 step。
- **状态投影与物化缓存**：ChannelManager 保持为日志的内存投影（热路径零改动）；新增 fold 机器（温/冷路径）；`SessionStateStore` 填充 `channel_state` 成为物化缓存，恢复 = 缓存 + tail 重放；物化节奏 = turn 结束 / interrupt / 每 N=10 superstep。
- **Resume API 真实现**：校验依据从 Session 行改为日志推导（尾部未闭合 `INTERRUPT` 即可恢复）；Session 行缺失则懒补建；graph 编译失败即拒绝（fail-fast）。
- **运行时不变式层**：`LogInvariants` 注册表（step 配对、工具调用配对平衡、LLM 请求纯函数出处检查、dispatch 树一致性、投影等价断言），违背即 fail-stop。
- **事件生命周期管理**：会话级 TTL（终态起算、interrupted 豁免）、会话树级联删除（跨 session/conversation 双脊柱）、写入时有界载荷保留器、单会话 warn-only 阈值、策略枚举 `delete|archive`（archive 仅留位不实现）。
- **PostgresCheckpointStore 软废弃**：DeprecationWarning + 文档标注；硬删除与 `checkpoints` 表 drop 留给后续清理 change（沿 13.4a-6/-7 先例）。`CheckpointStore` ABC 保留名字、改造为物化缝（载荷瘦身为 `channel_state + log_version`，移除死字段 `pending_writes`）。
- **兼容**：事件 payload 携带 `log_schema_version`，旧事件视为不可回放前缀；零 alembic 迁移（events 表结构不变）；chat API 无状态契约不变。

### 非目标（显式边界）

- **路径 A/C 不入日志**：agent-tools 直驱工具循环（`_chat_with_tools`）与纯文本直通路径的 LLM/工具调用不进事件日志 —— 8.20 回放覆盖范围 = Pregel 执行路径。代码留 TODO 指针；长期收敛方向（tool-loop 建模为引擎内子图）记档，与 1.3.5i E3 天然拍档。
- 不统一 Conversation/Message 与日志的双重记账（A2 挂账，日志成为权威后该统一更不紧急）。
- 不实现 fail-closed 审批对（1.3.4）、瀑布中间件（1.3.5i E3）、content-aware gating（9.4）、Run Replay UI（8.20）、Projection Registry（8.21）—— 全部是本 change 的下游消费方。
- 不做日志截断/压缩（EventStoreDB TruncateBefore / Kafka compaction 方向记为远期；物化缓存的 `log_version` 锚点已预留语义）。
- 不实现 archive 归档层（仅策略枚举留位）。
- chat 不切换服务端历史（保持客户端传全量 messages 的无状态契约）。

## Capabilities

### New Capabilities

- `execution-state-log`：日志即真相的执行状态模型 —— WAL 写入序与提交点、fold/投影（含 `derive_messages`）、LogPolicy 通道入日志规则、LogInvariants 运行时不变式、恢复语义（缓存 + tail 重放）、Session.status 日志投影化、resume 日志推导校验。
- `event-retention`：事件日志生命周期 —— 终态起算的会话级 TTL、interrupted 豁免、会话树级联删除清单（跨双脊柱）、写入时有界载荷保留器、单会话 warn-only 阈值、`delete|archive` 策略枚举、GDPR 级联。

### Modified Capabilities

- `eventstore`：事件 schema 富化（值级 delta、新事件类型、完整 payload、`log_schema_version` 标记）+ superstep 批量 append 接口；读侧契约不变（本 change 引入第一个读消费者 fold 机器）。
- `pregel-runtime`：WAL 序（append→apply）、checkpoint 降级为物化缓存（节奏与载荷变更）、恢复路径改造（撕裂尾部回退）、边界等价断言 fail-stop。
- `distributed-session-state-store`：`channel_state` 填充语义、`event_position` 作为 log_version 锚点、物化时机（turn/interrupt/N 步）、物化缝经 `CheckpointStore` ABC 以 `tenant_context_provider` 模式落 Store。

## Impact

- **engine/**：`eventstore.py`（schema 常量与批量接口）、`pregel.py`（写路径/恢复路径/物化节奏）、`channel.py`（LogPolicy 查询点）、`checkpoint.py`（签名改造）、`session_state.py`（无 ABC 变更）、新增 `logfold.py` / `logpolicy.py` / `loginvariants.py`（零外部依赖）。
- **services/**：`event_state/postgres_store.py`（批量 append）、`checkpoint_store.py`（软废弃）、`workflow/execution_service.py`（SessionStateMaterializer adapter、channel_state 填充、路径 B 接线）、新增 retention 清扫服务（定时批删 + 游标分页 + dry-run + 指标）。
- **api/**：`management/sessions.py` resume 真实现（API 形状不变）。
- **models/alembic**：零迁移（`log_schema_version` 为 payload 字段；checkpoints 表不动）。
- **tests/**：引擎测试的 checkpoint 断言需随载荷瘦身机械性更新；新增 fold/invariants/retention/恢复四类测试。
- **文档交付物**：ADR-030、`engine-design.md` Checkpoint/Event Store 节改写、`concepts.md` 持久化对象三层分类 + 足迹矩阵、`roadmap.md` Pending Cleanups 增 C2 项。
- **依赖关系**：解锁 8.20 / 1.3.4 / 1.3.5i E3 / 9.4 / 8.21；不阻塞其他在途 change。
