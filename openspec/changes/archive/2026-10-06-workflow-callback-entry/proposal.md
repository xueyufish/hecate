# Proposal

## Why

演进方案 step6e(确定性工作流回调)未交付:平台 `TaskControlService` 的唤醒原语(token 消费、幂等回执、requeue)已完整,父子等待在手写 orchestrator 测试中验证过(`contract_ref.await_task_ref`),但唤醒时**不校验回调契约**——任何持有效 token 的调用方可以声称任意子任务结果,平台不核实该子任务真实存在、已终态、其结果与声明一致;也没有具名的工作流回调产品入口(REST 面),跨 workspace 伪造子引用、重复/迟到回调、父子分别重启没有作为入口验收。现有手写 orchestrator 测试不等于工作流产品入口交付。

## What Changes

- **工作流回调契约校验**:`TaskControlService.submit_workflow_callback`——回调绑定父任务等待契约中的 `await_task_ref`,校验子任务存在于同一 workspace、已达终态、其生命周期状态与回调声明一致;不一致(伪造子引用、状态不符、跨 workspace)以显式 `rejected` 回执拒绝且不消费 wait token。
- **具名 REST 回调入口**:`POST /api/tasks/{task_id}/workflow-callback`(workspace 认证面,复用既有会话/API-key 依赖),请求绑定 `command_id`、`wait_token`、`child_task_id`、`declared_state` 与可选结果载荷;经契约校验后走既有 `provide_input` 唤醒应用(一次性 token、合并载荷、requeue)。
- **负例与重启验收**:重复回调(command_id 幂等)、迟到回调(token 已消费/过期)、伪造/跨 workspace 子引用、父任务与子任务分别重启(父等待跨重启保持,子终态事实持久)均在真实入口验收。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `platform-task-control`:新增工作流回调入口——等待契约的子引用经真实终态核实后才唤醒;伪造、跨 workspace、重复、迟到回调显式拒绝且零副作用。

## Impact

- `src/hecate/execution/task_control.py`:`submit_workflow_callback`(契约校验 + 复用 `_wake_waiting` 应用)。
- `src/hecate/channel/api/tasks.py`:回调路由(认证、载荷校验、回执)。
- 测试:契约校验正/负例、入口集成(认证、幂等、迟到)、父子分别重启恢复;既有回归。
- 不扩展:确定性工作流节点消费回调的编排语义、跨组织联邦(step17)、审批判定(step7)。
