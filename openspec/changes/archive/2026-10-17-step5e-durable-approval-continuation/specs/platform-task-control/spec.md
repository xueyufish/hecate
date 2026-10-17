# Spec Delta — platform-task-control

## MODIFIED Requirements

### Requirement: 任务等待人工输入或审批为持久等待并可恢复

任务控制面 SHALL 支持进入 `waiting_input`/`waiting_approval` 的持久等待:等待状态与输入/审批契约引用、单次消费回调 token、期限一并持久化;`waiting_input` 经 `provide_input` 命令凭有效 token 唤醒,输入随任务状态持久化并在重派时回放;`waiting_approval` 经 `resume` 命令凭有效 token 与匹配等待上下文的持久审批决定唤醒——仍 pending(或无匹配绑定)时拒绝唤醒且不消费 token,任务保持等待;决定为 rejected 时消费 token 并转 `approval_rejected` 失败终态。token 已消费、已过期、任务已终态或输入与等待契约不符 SHALL 拒绝并记录;进程重启后等待任务保持等待状态且可被唤醒。审批的策略判定、参数绑定校验与职责分离归授权域(step7),本能力只保证等待的持久化、真实回执与恢复;持久审批等待的进入(类型化等待信号与停车上下文)由 `execution-approval-binding` 的对应 requirement 约束。

#### Scenario: 等待跨重启

- **WHEN** 任务等待输入期间平台进程重启
- **THEN** 任务保持 `waiting_input`,有效 token 唤醒后按持久化输入重放派发

#### Scenario: token 单次消费

- **WHEN** 同一回调 token 被重复使用
- **THEN** 第二次唤醒被拒绝并留下拒绝记录,任务状态不被二次改写

#### Scenario: 迟到输入拒绝

- **WHEN** 输入在任务到达终态后到达
- **THEN** 唤醒被拒绝并记录,不复活已终态任务

#### Scenario: 审批等待无决定不消费 token

- **WHEN** `waiting_approval` 任务的审批绑定仍为 pending 时发起 `resume`
- **THEN** 唤醒被拒绝并记录,token 未消费,任务保持 `waiting_approval`

#### Scenario: 拒绝决定转终态

- **WHEN** `waiting_approval` 任务的审批绑定判定为 rejected 时发起 `resume`
- **THEN** token 被消费,任务转失败终态并携带 `approval_rejected` 类别,受保护动作零执行
