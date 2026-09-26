# tool-recovery Delta

## ADDED Requirements

### Requirement: 恢复流程消费工具回执

执行恢复（会话断线重连、中断续跑）遇到此前已派发但结果未落通道的工具调用时，SHALL 按 `execution_id` 查询回执决定动作：回执为 `succeeded` 的调用 SHALL NOT 重新执行，SHALL 以已记录的结果回填通道；回执为 `unknown` 或无回执的调用 SHALL NOT 自动重新执行（外部副作用类），SHALL 标记人工核对状态。`readonly` / `idempotent_write` 类无回执调用的自动重试 SHALL 遵循 `should_auto_retry` 判定。

#### Scenario: 已成功执行不重放

- **WHEN** 恢复执行遇到回执为 `succeeded` 的工具调用
- **THEN** 该调用不重新执行，通道回填已记录的结果

#### Scenario: 不可判定结果进入人工核对

- **WHEN** 恢复执行遇到回执为 `unknown` 的外部副作用类工具调用
- **THEN** 该调用不自动重新执行，并被标记为人工核对状态

#### Scenario: 无回执的安全类调用按判定重试

- **WHEN** 恢复执行遇到无回执的 `readonly` 类工具调用
- **THEN** 按 `should_auto_retry` 判定自动重试
