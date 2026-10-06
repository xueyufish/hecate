# Spec Delta

## ADDED Requirements

### Requirement: 平台中断尝试优先恢复原执行会话

平台 durable dispatch 对含受保护动作的中断尝试 SHALL 先检查原 engine 会话的可恢复状态(平台会话/检查点存储中该会话的状态可装载);可恢复时 SHALL 沿用原 Run 与原 engine 会话重新执行——执行事实由会话延续与动作台账仲裁(已决动作回填真实结果,未决动作停止),MUST NOT 为恢复创建新 Run。会话状态不可装载时 SHALL 保持既有 `reconciliation_required` 保守行为。恢复执行的业务副作用 SHALL 以实际调用计数验证不重做已决动作。

#### Scenario: 会话可恢复的中断尝试沿用原 Run

- **WHEN** 平台任务的中断尝试在其 engine 会话上有可装载的会话状态,重新派发
- **THEN** 沿用原 Run 与原 engine 会话执行,动作台账仲裁,已决动作零重复调用

#### Scenario: 会话不可恢复的中断尝试保持保守待对账

- **WHEN** 中断尝试的 engine 会话状态不可装载且含受保护动作
- **THEN** 任务保持 `reconciliation_required`,不以新 Run 盲目重放
