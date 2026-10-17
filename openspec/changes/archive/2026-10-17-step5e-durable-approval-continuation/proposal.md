# Proposal — step5e-durable-approval-continuation

## Why

step5e 已记录同步审批续推与存储重建后的恢复,但演进方案明确"持久审批、实际进程重启"仍开放:平台 Agent 经 durable 任务派发遇到需审批的动作时,当前只能同步询问或 fail-closed 拒绝——没有"停车等待、人类事后批准、(含进程重启后的)续跑"的闭环。`WAITING_APPROVAL` 生命周期与 `resume` 命令、审批绑定账本(pending/approved/rejected 判定与消费分离)均已交付,缺的是把两者接起来的持久等待链路;本 change 补齐这段接线并以真实进程重启验证。

## What Changes

- **引擎侧审批等待信号**:审批回调新增 durable-wait 变体——策略要求审批且任务派发装配启用持久等待时,回调抛出类型化等待信号;信号干净穿过引擎工具循环(不被工具错误处理改写为 `needs_review`,不落终态失败),入口服务映射为停止类别 `waiting_approval`,派发包装层将其转为既有 `TaskWaitingSignalError(RESUME, …)` 停车。
- **停车上下文持久化**:任务进入 `WAITING_APPROVAL` 时,等待契约持久化待执行动作(工具名、参数摘要、主体指纹)与 `approval_ref`,并在审批账本铸造 **pending** 绑定(`ApprovalBinding.judgment=PENDING`,判定与消费分离的既有字段);停车持久化失败 fail-closed(任务失败,不静默继续)。
- **唤醒授权门**:`waiting_approval` 的 `resume` 唤醒在有效 token 之外,必须存在与等待上下文匹配的持久审批决定——账本绑定 judgment=approved 且绑定校验(参数摘要/主体/期限/撤销)通过才消费 token 并重排队;无决定(仍 pending)时拒绝唤醒且**不消费 token**,任务保持等待;决定为 rejected 时消费 token 并将任务转终态 failed(`approval_rejected`),受保护动作零执行;唤醒载荷自报 `approved` 不构成授权。
- **续跑消费与回执**:唤醒经既有原生续跑门在**原 Run/引擎会话**上续跑(续跑门为 `waiting_approval` 停车放行终态拒绝,其余准入——定义摘要、快照可加载——不变);被停动作在派发前经绑定校验(撤销/期限/参数摘要重核)后一次性消费执行,模型收到真实工具回执继续推理;会话键的动作台账恢复保持有效,已完成动作不重复执行,未知写结果进入待对账(复用既有停止语义)。"唤醒即新 attempt"规则继续适用于无引擎状态的等待(workflow 输入等待),审批唤醒不适用——它与台账恢复的会话作用域冲突。
- **真实进程重启证据**:确定性 CI 场景——worker 子进程 A 执行至停车后被真实终止,经审批决定 + `resume` 后,全新子进程 B 从同一 PostgreSQL 认领续跑至终态,副作用计数恰好一次;负例矩阵(重复唤醒、绑定过期、参数变化、撤销、跨 workspace)以新装配对象在同库验证。真实模型变体另存验收记录(凭据缺失时显式跳过,不冒充通过)。
- **默认关闭与边界**:durable-wait 审批回调仅在任务派发装配显式启用;默认 fail-closed 语义与 chat 同步审批路径行为不变。内置后端六方法契约的 `resolve_approval` 维持 `unsupported` 并显式拒绝；持久审批经任务控制面授权后续跑原 Run，不新增后端审批接口。

## Capabilities

### New Capabilities

(无——全部落在既有能力的 requirement 变更上。)

### Modified Capabilities

- `execution-approval-binding`:新增"持久审批等待的进入与唤醒授权"requirement——引擎停车信号与停车上下文持久化(pending 绑定)、唤醒以持久审批决定为授权门、拒绝决定进入任务终态、唤醒载荷自报不授权、停车 attempt 的投影语义(显式 `waiting_approval` 停止类别,不伪装成功)。
- `platform-task-control`:修改"任务等待人工输入或审批为持久等待并可恢复"requirement——`waiting_approval` 的唤醒校验在 token/状态之外增加审批决定门:无决定拒绝且不消费 token,拒绝决定消费 token 并转 `approval_rejected` 终态;其余等待语义(期限、单次 token、重启保持)不变。

## Impact

- **代码**:`packages/hecate-runtime`(类型化审批等待信号及其在工具循环/引擎层的传播契约)、`src/hecate/execution/task_dispatcher.py`(信号→`TaskWaitingSignalError(RESUME)` 转换、停车上下文与 pending 绑定铸造、崩溃窗口回填的类别处理)、`src/hecate/execution/task_control.py`(`_wake_waiting` 的审批决定门与拒绝终态分支)、`src/hecate/execution/entry_service.py`(`waiting_approval` 停止类别映射)、治理装配(pending 绑定的决定路径接线)。
- **测试**:确定性停车/唤醒/负例矩阵(SQLite 与 PG 分层,PG 排除内存回退)、worker 子进程真实重启及消费后领取前崩溃 E2E(PG 专用)、独立真实模型重启记录(保留实际输入/输出及部分失败)、CI PG 作业纳入新模块。已有双路径 rubric 记录保留，不将本场景冒充完整性能/质量比较。
- **文档**:演进方案 step5e 状态行、执行机制矩阵、ADR-036 如实更新;支持边界(重启矩阵归因范围、审批决定的策略判定仍归授权域)显式记录。
- **不变**:chat 同步审批语义、引擎默认装配、内置后端能力声明、workflow 子任务回调语义、独立宿主(standalone runner)审批路径。
