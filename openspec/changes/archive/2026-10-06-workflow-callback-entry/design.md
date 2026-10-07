# Design

## Context

父任务可以通过 `TaskWaitingSignalError` 持久 park 在 `waiting_input`,等待契约里带 `await_task_ref`(子任务引用)。唤醒面(`issue_command` + `_wake_waiting`)已有:一次性 wait_token、过期/重复拒绝、幂等 command_id 回执、requeue。缺口是回调的**真实性**:平台不核实 await_task_ref 指向的子任务真实存在于同一 workspace、已终态、其生命周期状态与回调声明一致——等待契约成了"自声明"而非"经核实的外部事实"。

## Goals

- 工作流回调成为经核实的入口:子任务事实(存在、终态、状态一致)由平台自己的 Task/Run 记录核实,回调载荷只是补充;伪造与跨 workspace 引用被显式拒绝。

## Non-Goals

- 确定性工作流图节点消费回调的编排语义(哪个节点在等待、恢复后走哪条边)——留给具体工作流产品切片;本 change 交付的是经核实的回调入口与任务级语义。
- 审批判定(step7)、跨组织联邦(step17)。
- 不引入新 Port/Protocol;全部是 TaskControlService 方法与既有 REST 面的扩展。

## Decisions

1. **回调 = 契约校验 + 既有唤醒应用。**`submit_workflow_callback(workspace_id, task_id, *, command_id, wait_token, child_task_id, declared_state, payload, expires_at)`:(a) 载入父任务等待记录,要求 `contract_ref.await_task_ref` 存在且其 id 与 `child_task_id` 一致——不一致直接 `rejected`(伪引用);(b) 载入子任务:`workspace_id` 必须与父一致(隔离),生命周期必须为终态,且与 `declared_state` 一致——不一致 `rejected`(不消费 token);(c) 校验通过后把子任务结果摘要(终态 + result 摘要)并入唤醒载荷,走既有 `_wake_waiting` 应用(token 消费、requeue、applied 回执)。父子的关联事实来自平台自己的记录,回调声明只做一致性断言。
2. **REST 入口在既有 tasks 面。**`POST /tasks/{task_id}/workflow-callback`,复用 `get_auth_context`(workspace 认证)+ 既有问题文档错误形状;请求模型绑定 command_id/wait_token/child_task_id/declared_state/result(可选)。命令经 `issue_command` 的幂等通道复用(PROVIDE_INPUT + detail_ns.wait_token)——回执语义与既有命令完全一致。
3. **状态映射用平台生命周期词汇。**`declared_state` 限定 `{succeeded, failed, cancelled}`(平台终态集);子任务 `reconciliation_required` 不接受为回调声明(结果未知不能作为工作流继续的事实),此类父任务等待保持,由人工对账。
4. **重启验收复用既有机制。**父任务 WAITING 跨重启(durable 行,Iteration 4/平台一致)、子任务终态事实持久;测试以持久状态模拟进程死亡后经真实入口回调。

## Risks

- 回调入口持有 wait_token 才能唤醒:token 经 get_task_detail 只对任务属主 workspace 可见(既有面),回调再加子任务事实核实,泄露窗口与既有命令面同级。
- 子任务结果载荷摘要进入父任务输入:摘要仅含生命周期状态与既有 result 摘要(平台自有记录),不复制大载荷。
