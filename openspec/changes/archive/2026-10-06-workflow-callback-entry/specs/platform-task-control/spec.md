# Spec Delta

## ADDED Requirements

### Requirement: 工作流回调经子任务事实核实后唤醒

平台 SHALL 提供具名的工作流回调入口:回调绑定父任务等待契约中的子任务引用(`await_task_ref`),平台核实该子任务存在于同一 workspace、已达终态、且其生命周期状态与回调声明一致后,才消费一次性 wait token 并以 `provide_input` 语义唤醒父任务(合并子任务结果摘要与回调载荷)。伪造或跨 workspace 的子引用、声明状态与子任务真实状态不符、等待契约不含该子引用的回调 MUST 得到显式 `rejected` 回执,MUST NOT 消费 token、不产生父任务状态变化。重复回调(同 command_id)SHALL 幂等返回既有回执;迟到回调(token 已消费或过期)SHALL 得到显式拒绝。父任务与子任务的持久事实 SHALL 各自跨进程重启保持——重启后回调入口行为不变。

#### Scenario: 经核实的回调唤醒父任务

- **WHEN** 子任务已 succeeded,回调声明 `declared_state=succeeded` 并携带正确 wait_token
- **THEN** 平台核实子任务事实后消费 token,父任务 requeue 且输入含子任务结果摘要,命令回执 `applied`

#### Scenario: 伪造或跨 workspace 子引用被拒绝

- **WHEN** 回调引用的子任务不存在、属于其他 workspace、或声明状态与子任务真实终态不符
- **THEN** 回执 `rejected` 并留原因,wait token 未消费,父任务保持等待

#### Scenario: 重复与迟到回调不重复唤醒

- **WHEN** 同一 command_id 重复提交,或 token 已消费/过期后回调
- **THEN** 幂等返回既有回执或显式拒绝,父任务不二次 requeue,无重复副作用

#### Scenario: 父子分别重启后回调仍可完成

- **WHEN** 父任务等待期间父平台进程重启、子任务在另一进程完成后,回调到达
- **THEN** 父任务等待事实与子任务终态事实均从持久记录核实,回调按正常语义应用
