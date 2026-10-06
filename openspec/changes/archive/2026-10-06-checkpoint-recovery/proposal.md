# Proposal

## Why

演进方案 step6d(checkpoint/动作恢复关联)未接通:Pregel 本身每 superstep 保存 checkpoint 且 `resume_value` 恢复路径(cache + 事件日志 tail 回放)完整,但 Runner 使用**内存** checkpoint store(进程重启即丢),session id 由 run id 派生——重启后恢复重驱动是**从头 replay**(fixed graph 重放 + 动作台账逐工具仲裁,已决动作回填真实结果)。这满足幂等安全,但尚未完成的模型尝试会重新走全图;平台侧更保守:中断尝试已有受保护 Action 时直接 `reconciliation_required`,不尝试恢复原逻辑执行。结果:一个执行到第 N 步崩溃的任务,恢复时要么全图重跑(宿主),要么人工对账(平台),业务 App 无法获得"中断后从中断处继续"的可靠长任务语义。

本 change 是迭代 5 的落地切片:把 checkpoint 持久化并接入恢复,让中断尝试从最近 checkpoint 继续原逻辑执行,已领取/已决动作经台账仲裁不重做。

## What Changes

- **宿主 checkpoint 持久化**:durable profile 的执行 checkpoint 写入宿主自有 durable 库(新 `checkpoint` 表,`SqlCheckpointStore`);session id 稳定化为任务级(`uuid5(NAMESPACE_URL, "runner-task:<task_id>")`),同一任务的每次尝试共享恢复会话,恢复时 `resume_value` 从最近 checkpoint 继续。
- **恢复重驱动走 checkpoint**:重启恢复(WAITING 唤醒后的新 attempt、调度器对 RUNNING 中断任务的恢复)优先 `resume_value` 恢复——图从中断 superstep 继续而非全图重放;无 checkpoint 时保持既有 replay 语义(台账仲裁兜底)。
- **平台保守路径收窄**:平台 dispatcher 对"中断尝试含受保护动作"的尝试,在能恢复原执行会话(同一 engine session 的 checkpoint 可用)时以 `resume_value` 恢复原逻辑执行;checkpoint 不可用或恢复失败时保持 `reconciliation_required` 保守行为。
- **台账仲裁不变**:恢复执行的每个动作仍经动作意图/领取/回填——已 `succeeded` 的动作回填真实结果,`outcome_unknown` 仍停待对账,checkpoint 恢复不绕过、也不重做任何已决动作。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `standalone-runner-host`:durable 宿主的 checkpoint 持久化与恢复——中断任务从最近 checkpoint 继续原逻辑执行,台账仲裁兜底不变。
- `platform-task-control`:平台 durable dispatch 的中断尝试在 checkpoint 可用时恢复原执行会话,保守待对账收窄为恢复失败的兜底。

## Impact

- `packages/hecate-durable`:新增 `SqlCheckpointStore`(CheckpointStore ABC 的 SQL 实现,`checkpoint` 表 + `create_schema` 注册)——第三实现(ABC 既有 InMemory 第二实现,且具名消费者为 runner/平台 dispatcher),不引入新 Protocol。
- `packages/hecate-runner`:`engine.py`(durable 下装配 SqlCheckpointStore、session id 任务级稳定化、`resume_managed`/恢复路径传 `resume_value`)、`durable.py`(透传)。
- `src/hecate`:`task_dispatcher.py`(受保护动作中断尝试的 checkpoint 恢复分支)。
- 测试:落盘成功后崩溃→原 superstep 继续、已 succeeded 动作回填不重做(业务 API 计数)、checkpoint 缺失回退 replay、平台恢复分支;既有回归。
- 不扩展:跨进程工作流回调(step6e)、安装制品进程级验收(step6f)、审批判定(step7)。REST 取消/暂停语义、EventStore 折叠恢复平台侧既有路径不动。
