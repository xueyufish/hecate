# Spec Delta

## ADDED Requirements

### Requirement: 持久等待绑定契约并可跨重启保持

durable profile 的宿主 SHALL 支持任务进入持久等待:manifest 声明的 `approval_required` 工具在动作台账记录意图后不执行业务调用,业务 API 返回输入契约请求(`input_required`)时不产生业务副作用,两种路径都将任务持久迁移到 `waiting_approval`/`waiting_input`。等待记录 SHALL 绑定原 Task/Run、工具与参数摘要、审批或输入契约引用、一次性 `wait_token` 与期限,并与状态迁移同事务提交。等待中的任务 MUST NOT 占用执行槽或被自动重驱动;进程强制终止并重启后等待任务 SHALL 保持等待并可被合法唤醒。非 durable 的 preview profile MUST 继续拒绝 `approval_required` 工具——无持久化就没有可靠等待。

#### Scenario: 审批工具进入持久等待且不产生副作用

- **WHEN** durable 宿主执行包含 `approval_required` 工具的任务
- **THEN** 动作意图已记录但业务 API 无调用,任务持久进入 `waiting_approval`,等待记录含工具/参数摘要、契约引用、一次性 token 与期限,重启后仍为等待态

#### Scenario: 输入契约请求进入持久等待

- **WHEN** 业务派发返回 `input_required` 与契约引用
- **THEN** 任务持久进入 `waiting_input`,无第二次业务调用,等待记录可经授权查询

### Requirement: 一次性唤醒原子应用且拒绝显式可查

宿主 SHALL 提供授权的唤醒命令入口(`provide_input`/`resume`,经服务端验证身份):命令按 `command_id` 幂等记录并出具独立回执。合法唤醒 SHALL 在单事务内校验命令期限与任务绑定、等待记录的 token 匹配/未消费/未过期,然后消费 token、合并提供的输入到任务输入、迁移 `waiting_*→queued` 并重绑新 attempt Run、命令置 `applied` 并发 `command_state` 事件;后续由串行调度以新 attempt Run 执行。过期命令、过期等待、token 不匹配或重复唤醒 SHALL 得到显式 `rejected` 回执(或既有回执的幂等重放),MUST NOT 派发任何工具、不产生业务副作用。

#### Scenario: 合法唤醒合并输入并以新 Run 执行

- **WHEN** 持有有效 `wait_token` 的调用方提交 `provide_input` 命令与输入载荷
- **THEN** token 被一次性消费,输入并入任务输入,任务以新 attempt Run 被串行执行一次,命令回执为 `applied`

#### Scenario: 过期与重复唤醒不派发工具

- **WHEN** 超过命令或等待期限后唤醒,或同一 `command_id` 重复提交
- **THEN** 得到显式 `rejected` 回执或幂等重放的原回执,工具零派发,业务副作用计数不变

#### Scenario: 错误 token 的唤醒被拒绝

- **WHEN** 唤醒命令携带与等待记录不匹配的 `wait_token`
- **THEN** 命令置 `rejected` 并留原因,等待记录不被消费,任务保持等待
