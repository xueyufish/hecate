# Design

## Context

Pregel 恢复机制已完备:`conversational` 模式下每个 superstep 结束保存 checkpoint(channel 全量快照 + log_version 元数据);`execute(resume_value=...)` 走 `_restore_from_checkpoint`——优先 checkpoint cache 热恢复,再事件日志 tail 回放,恢复 superstep 计数后继续。缺口:(1) Runner 的 checkpoint store 是内存态,进程重启即失;(2) Runner 的 session id `uuid5(run_id)` 每次 attempt 不同(重启恢复用同一 run_ref 重放时相同,但 WAITING 唤醒等新 attempt 场景不同);(3) 平台 dispatcher 对含保护动作的中断尝试保守 `reconciliation_required`,不尝试恢复。

动作安全已由动作台账保证:`recovery_decision` 对 succeeded 回填真实结果、outcome_unknown 停待对账、claimed 写类停审——恢复执行落在任意点上都不会重做已决副作用。

## Goals

- 中断任务恢复时从最近 checkpoint 继续原逻辑执行(superstep 粒度),而非全图重放或人工对账;动作台账仲裁在恢复路径上保持不变。

## Non-Goals

- 跨进程工作流回调与确定性工作流节点(step6e)、安装制品进程级矩阵(step6f)、审批判定(step7)。
- 平台 EventStore 折叠恢复(`resume_from` 路径)与 REST 暂停/取消语义——既有机制不动。
- checkpoint 的跨后端迁移、版本化 schema 演进(表按 discardable cache 定位,坏行直接弃用回退 replay)。
- 不新增 Protocol/ABC:`SqlCheckpointStore` 实现 runtime 既有 `CheckpointStore` ABC(第二实现为 InMemory,具名消费者为 runner 与平台 dispatcher)。

## Decisions

1. **checkpoint 是 discardable cache,落宿主/平台自有库。**`SqlCheckpointStore` 写 `checkpoint` 表(session_id、superstep、node_id、channel_state JSON、metadata JSON、created_at),`load` 取最新,`list_checkpoints` 按序;坏行/缺失一律返回 None,恢复回退既有 replay——cache 语义与 ABC 注释一致,不追求 checkpoint 的强一致。宿主表进宿主 durable 库(`create_schema` 注册),平台表进平台库(Alembic 迁移),两侧各有 owner,不共享。
2. **session id 稳定化为任务级。**Runner:`uuid5(NAMESPACE_URL, f"runner-task:{task_id}")`,同一任务所有 attempt 共享恢复会话——attempt 换 run 不换会话,checkpoint 连续性保住;不同任务天然隔离。平台侧 engine session 已稳定(`RunModel.backend_ref.id` 在首次派发固定),无需改动。
3. **恢复入口统一走 `resume_value`。**`resume()`/`resume_managed()`/唤醒重驱动在 durable 下检查该任务会话是否存在 checkpoint(`list_checkpoints` 非空)且上次尝试非正常终态(QUEUED 中断的 RUNNING 尝试)——存在则以 `resume_value=<时间戳标记>` 执行(`_restore_from_checkpoint` 对 resume_value 仅注入 channel,checkpoint 本体来自 store);不存在则保持既有全图 replay。WAITING 唤醒(等待本身是状态机的正常出口,非崩溃)不复用旧 checkpoint——从新 attempt 干净开始,grant 保证审批工具放行。
4. **平台 dispatcher 保守路径收窄为兜底。**平台统一图引擎的会话恢复机制是 `SessionStateStore`(跨调用会话状态),不暴露 pregel 的 `resume_value`——平台侧的"恢复原执行会话"即:中断尝试含受保护动作时,先经共享 session store `load` 原 engine 会话的状态;可装载 → 沿用原 Run 与原 engine session 重新执行(会话延续 + 动作台账 hook 继续仲裁);不可装载或恢复抛错 → 保持 `reconciliation_required`(现状)。正常完成但任务行未收敛的 backfill 路径不动。
5. **恢复后的动作键延续任务级派生。**动作键 `action_key_for(run_id, tool)` 含 run id——恢复执行若仍在同一 attempt(RUNNING 中断恢复),键相同,台账直接回填;若恢复创建了新 attempt(宿主重启后 requeue),新键下 `recovery_decision` 查无历史即重新执行——这是现状语义,本 change 不改键规则(改键属跨 attempt 结果迁移,收益与风险都不属于 checkpoint 切片)。真正防重做的是:**恢复优先在同一 attempt 上继续**(宿主重启恢复 RUNNING 任务不建新 run,沿用原 run_ref),只有 WAITING 唤醒等显式新 attempt 才换键。

## Risks

- channel_state 含业务数据的内存快照落库:与 durable 事件日志同级(宿主自有库,零外发),保留策略沿用宿主库治理;不新增上传路径。
- checkpoint 与台账的一致性窗口:checkpoint 在 superstep 边界、台账在动作边界,恢复可能重放某 superstep 内已 claim 未决的动作——台账 claimed 写类停审语义正好兜住这个窗口,安全方向正确(宁可停审不重做)。
- 恢复执行叠加 `resume_value` 时图内 `_resume_value` channel 语义与 fixed graph 无消费节点——注入无害;后续工作流切片(6e)才有真实消费者。
