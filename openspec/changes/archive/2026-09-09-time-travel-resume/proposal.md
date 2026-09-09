# Proposal: 1.3.21② Time-Travel Resume + update_state

## Why

1.3.21 引擎对齐中,① 声明式中断已交付(暂停/恢复链路端到端可达),但引擎的恢复路径只支持"从最新状态续跑"——历史状态目前仅只读可见(`GET /replay/state?at_version=N` 已能 fold 到任意版本),无法**从**任意历史提交点**继续执行**,也没有**状态修改入口**供 HITL re-plan 与 what-if 场景使用。Sprint 9 消费方被直接阻塞:6.26 E5 what-if checkpoint branching、8.20 从只读回放升级为可执行时间旅行。同时 2026-09-09 行业调研(23 系统,含 LangGraph/Temporal/deepseek-harness/Pi/deer-flow/Claude Code/Manus 及 6 家企业平台)确认:完整时间旅行全部由单用户 CLI/SDK 工具或基础设施实现,**没有任何企业级多租户平台交付该能力**——Hecate 在此有明确的差异化窗口。

## What Changes

- **提交点锚点列举**:`GET /api/sessions/{id}/commit-points`——从事件日志派生可恢复锚点(`STEP_END`/`INTERRUPT`),不引入 checkpoint 历史存储。**对 roadmap 原文"`CheckpointStore.load(checkpoint_id)`"的刻意偏离**:生产 `SessionStateMaterializer` 无历史(`list_checkpoints` 恒空、`load` 伪造 ID),且 ADR-030 已将 checkpoint 降级为可弃缓存;日志本身就是 checkpoint 列表(纯日志家族先例:Temporal 按 event-id、dsh 按 seed 长度、Pi 按 entry id)。
- **Fork 续跑(fork-and-run)**:`POST /api/sessions/{id}/fork`(`at_version` + 可选 `updates`)——在任意提交点创建**新子会话**继续执行,原始会话不可变;子会话日志以单条 `FORK` 快照事件引导(携带 lineage、折叠态、续跑节点集),自包含可恢复。不做同会话破坏性回退、不做 branch-tagged 单日志。
- **状态修改(update_state)**:`POST /api/sessions/{id}/state`——以**追加记录式**事件写入(`CHANNEL_WRITE` + `source="update_state"` + actor),fold 天然吸收、审计免费;用于 HITL 中断态改值后 resume、以及 idle 会话的下一 turn 注入。存在未闭合 TURN(执行中)时拒绝。
- **引擎原语**:`EventType.FORK` 新枚举值;fold 机器增加 FORK 快照水合分支;日志推导续跑节点集(`derive_continuation`);`execute(resume_from=…)` 同会话仅允许 tail(crash-recovery 语义),历史点一律走 fork。
- **副作用重执行如实标注**:fork 重派节点会再次执行工具与外部副作用(fold 只重建通道态),API 文档与 spec 明示,不做副作用回滚。
- **文档修正**:`docs/concepts/sessions.md` 中不存在的 `GET /api/sessions/{id}/checkpoints` 示例更新为真实端点。

非目标(明确不做):同会话 rewind/截断(破坏审计);branch 合并回主线;`as_node`(6.26 要的是 modified state 而非 modified continuation);8.20 回放 UI 按钮(消费方变更);G7 inspector(1.1.23);跨线程 store(4.23);文件系统/工作区快照(Claude Code/Manus 路线,记为 future);策略化权限钩子(auth + workspace 隔离即可)。

## Capabilities

### New Capabilities
- `time-travel-resume`: 用户可见的时间旅行能力——提交点锚点列举、fork-and-run 语义(含 lineage、updates、副作用重执行声明)、update_state 追加式状态修改及其门控。

### Modified Capabilities
- `eventstore`: `EventType` 枚举新增 `FORK` 值(枚举目录 requirement 变更)。
- `execution-state-log`: `FORK` 事件成为提交点类别;fold 语义扩展(FORK 快照水合、INTERRUPT/FORK 对 fold 中立);update_state 写入以带 source 标记的 `CHANNEL_WRITE` 落日志。
- `pregel-runtime`: 恢复路径从"最新检查点 + 尾部 fold"扩展为统一续跑推导(最后一个提交点描述符:INTERRUPT 走 ① phase-aware 逻辑、FORK 走 payload.next_nodes、STEP_END 走 NODE_END 出边并集);`execute(resume_from=V)` 的 tail-only 守卫。

## Impact

- **引擎层** `src/hecate/runtime/`:`eventstore.py`(枚举)、`replay/logfold.py`(FORK 分支)、`pregel.py`(续跑推导、resume_from 守卫、restore 统一化)、`checkpoint.py`(无接口变更)。
- **服务层** `src/hecate/studio/`:fork 会话编排(child SessionModel + lineage metadata + FORK 事件落日志 + 续跑执行)、update_state 事件写入服务(复用 WAL 追加序)。
- **API 层** `src/hecate/studio/api/sessions.py`:三个新端点(commit-points / fork / state),workspace 隔离与 404/409 语义对齐既有端点。
- **下游解锁**:6.26 E5(what-if 并行场景)、8.20(可执行回放)、HITL re-plan 流。
- **不做 schema 迁移**:FORK 是事件载荷内的快照,无新表;子会话复用 `SessionModel.metadata`(JSON)记 lineage。
