## MODIFIED Requirements

### Requirement: Workflow parent-child callback uses a named adapter and persisted facts

平台 SHALL 提供具名 adapter(`WorkflowChildTaskAdapter`):平台任务的输入载荷可声明确定性子任务步骤序列(`workflow.steps`,非 LLM 决定),dispatcher 经 adapter 逐步执行——每步 MUST 通过真实 `TaskControlService.submit` 提交子任务(系统发起,子任务载荷 MUST 盖章父任务引用与编排元数据),随后父任务 MUST 挂入持久等待,等待契约的 `await_task_ref` 绑定该子任务引用。子任务到达终态时,平台 SHALL 从子任务自身载荷的父引用元数据自动发起经核实的 `submit_workflow_callback`(平台 issuer)——回调 MUST 满足既有核实语义(同 workspace、子任务真实终态与声明一致、契约匹配、一次性 token),父任务被唤醒后子结果摘要 MUST 随 `provide_input` 进入持久输入,下一步 SHALL 仅在上一步核实结果进入后才提交。任一步的子任务失败时,adapter SHALL 按 `on_failure` 声明收敛(`fail`:父任务失败;`await`:父任务转待对账)。父任务等待事实、子任务终态事实与跨进程发出的自动回调 SHALL 各自跨进程重启保持。回调 MUST NOT 消费 token、MUST NOT 改变父任务状态,当:伪造或跨 workspace 的子引用、声明状态与子任务真实终态不符、等待契约不含该子引用。重复回调(同 command_id)SHALL 幂等返回既有回执。

#### Scenario: Real adapter callback wakes the parent after child terminal fact

- **WHEN** a workflow parent waits for a child through the named adapter and the child reaches succeeded
- **THEN** the callback verifies the persisted child terminal fact, consumes the parent wait token once, and requeues the parent with the child result summary

#### Scenario: Cross-workspace or forged child callback is rejected

- **WHEN** a callback references a child from another workspace or a child whose terminal fact does not match the declaration
- **THEN** the command receipt is rejected, the parent wait token is not consumed, and the parent remains waiting

#### Scenario: Declarative steps run in order through real submit and park

- **WHEN** 平台任务输入声明两步确定性子任务步骤并真实派发(无 `_execute` monkeypatch)
- **THEN** 第一步子任务经真实 submit 提交且父任务以 `await_task_ref` 绑定它挂入 `waiting_input`;子任务 succeeded 后自动核实回调唤醒父任务,第二步子任务才被提交;两步的子任务事实与父任务唤醒输入均来自平台持久记录

#### Scenario: Child terminal auto-callback rides platform records without an external caller

- **WHEN** 子任务在某进程到达终态,而父任务等待在另一进程(或父平台进程已重启)
- **THEN** 子任务终态路径从自身载荷的父引用元数据自动发起回调,核实基于双方持久事实,父任务被唤醒且子结果摘要进入其持久输入;HTTP 回调端点对外部调用者行为不变

#### Scenario: Failed step converges per declaration across restarts

- **WHEN** 某步子任务失败(声明 `on_failure=fail`),或父任务等待期间父平台进程重启、子任务在另一进程完成
- **THEN** `fail` 声明下父任务以失败终态收敛且剩余步骤不再提交;重启组合下链条按持久事实收敛,无步骤被重复提交、无业务动作重复发生
