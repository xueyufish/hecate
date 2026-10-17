# Proposal — step5e-workflow-agent-continuation

## Why

单个平台 Agent 的持久审批续跑已有证据(`step5e-durable-approval-continuation`,#261),但演进方案明确要求验证它与父 Workflow 组合后的行为:"调用自主 Agent 子任务后的产物校验、失败处理、审批/重启与重复回调;父流程不依据 Agent 自称完成直接推进"。当前组合链存在三个已核实的缺口:①子任务装配不传递持久审批配置(`stamp_step_input` 的白名单输入没有 `approval_wait` 通道),子 Agent 的受保护动作无法停车;②子任务以系统身份发起(`user_id=None`),而引擎快照的保存/恢复按用户作用域键控——`user_id=None` 时 tenant 解析返回 None,快照**完全不持久化**,审批唤醒的原生续跑必然被门拒绝;③子任务审批拒批的终态写在 task-control,绕过了 dispatcher 的自动回调链,父流程会等待到超时而非按声明失败策略收敛。

## What Changes

- **审批声明经步骤传递**:workflow 步骤声明新增 `approval_wait`(与 `mode`/`output_schema`/`artifact` 同通道,向后兼容,显式声明才启用);`stamp_step_input` 将其盖章进子任务持久化输入,子任务派发装配据此启用 durable 审批链——不默认全局开启。
- **可信系统任务快照归属**:系统发起的执行(无人类用户)以**已验证的 agent principal** 派生确定性合成租户键(从子任务持久化载荷的 `agent_id` 派生,非请求输入),引擎快照的保存与恢复使用同一键——不伪造人类用户 ID,不放宽归属校验;带人类用户的执行保持现行为不变。
- **拒批收敛父流程**:子任务审批 rejected 的终态显式进入经核实的回调链(与子任务执行失败可区分的来源),父流程按声明的 `on_failure` 策略收敛(`fail` 失败 / `await` 待对账),终态事件经已有 outbox 持久重试覆盖回调失败/进程退出，不再等待到超时;拒批前的重复/迟到/伪造回调行为维持既有幂等语义。
- **父等待语义明确**:子任务停在 `waiting_approval` 期间(非终态)回调不触发,父流程保持持久等待、不提前推进——结构上已成立,本 change 补显式证据与边界。
- **组合重启证据(PG)**:两个确定性窗口进 CI——(a) 子任务停车时真实重启子 worker 进程,审批后续跑、产物校验通过后父流程推进;(b) 父等待中、子执行中分别重启双方进程,任务关联、审批绑定、工具回执与流程位置从 PG 恢复。真实模型变体另存验收记录(凭据缺失显式 skip)。
- **产物校验先行不变**:子任务完成后的推进仍走既有核验(声明产物/output schema,父不依据子任务自称推进);拒批、子任务失败、产物不合格按父流程失败策略路由,与既有 `workflow-agent-subtask` 语义一致。

## Capabilities

### New Capabilities

(无——全部落在既有能力上。)

### Modified Capabilities

- `workflow-agent-subtask`:修改「子步骤声明扩展且向后兼容」——声明字段增加 `approval_wait`(向后兼容,非法值显式拒绝);新增「子任务持久审批与父流程收敛」requirement——审批等待期间父保持等待、拒批经核实回调按失败策略收敛、组合进程重启后关联/审批/回执/流程位置可恢复。
- `execution-approval-binding`:新增「系统发起执行的快照归属」requirement——无人类用户的执行以已验证 agent principal 派生确定性租户键持久化引擎快照,恢复从持久事实推导同一键,不伪造用户、不放宽归属。

## Impact

- **代码**:`src/hecate/execution/workflow_child.py`(步骤声明解析 + `stamp_step_input` 盖章)、`packages/hecate-runtime/src/hecate_runtime/execution_assembly.py`(系统执行的 tenant provider 派生)、`src/hecate/execution/task_dispatcher.py`(恢复侧同键派生)、`src/hecate/execution/task_control.py`(拒批终态进入回调链)、CI PG 作业。
- **测试**:确定性组合套件(SQLite:声明传递/父等待/拒批收敛/伪造回调)、PG 双窗口子进程重启 E2E、真实模型记录;既有 workflow 子任务契约与持久审批套件不改断言全绿。
- **文档**:演进方案 step5e 状态行、执行机制矩阵、ADR-036 边界;feature-catalog/inventory 登记后缀条目。
- **不变**:父流程既有产物校验与回调幂等语义、chat 同步审批、独立 workflow Run 身份(仍开放)、Runner 自主选择、审批 UI。
