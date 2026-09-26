# tool-recovery Specification

## Purpose

区分"状态可回放"与"外部动作可恢复"：为工具执行建立回执语义（稳定调用 ID、副作用分类、结果状态），使崩溃恢复能够安全决定重试、跳过或进入人工核对；并让未实现的 Temporal 分发选项显式失败而非静默回退。

## Requirements

### Requirement: 工具执行回执与稳定调用 ID

每次工具执行 SHALL 由服务端分配稳定的 `execution_id`（UUID，独立于 LLM 生成的 `tool_call_id`），并 SHALL 记录于该次执行的 `TOOL_CALL` 与 `TOOL_RESULT` 事件 payload。`TOOL_RESULT` 事件 SHALL 携带结果状态（`succeeded` / `failed` / `unknown`）与副作用分类；工具执行抛出异常时 SHALL 补写 `TOOL_RESULT(status=failed)` 事件——事件日志中 SHALL NOT 存在没有配对 `TOOL_RESULT` 的未决 `TOOL_CALL`（`unknown` 状态除外）。结果不可判定（超时、连接中断等）时 SHALL 写入 `TOOL_RESULT(status=unknown)`。

#### Scenario: 成功执行有完整回执

- **WHEN** 一次工具执行成功返回
- **THEN** 事件日志中 `TOOL_CALL` 与 `TOOL_RESULT(status=succeeded)` 携带相同的 `execution_id` 与副作用分类

#### Scenario: 异常执行补写失败回执

- **WHEN** 工具执行抛出异常
- **THEN** 事件日志中出现 `TOOL_RESULT(status=failed)`，与该次 `TOOL_CALL` 的 `execution_id` 配对

#### Scenario: LLM 调用 ID 缺失时仍有稳定标识

- **WHEN** LLM 生成的 `tool_call_id` 为空串
- **THEN** 该次执行仍获得服务端分配的 `execution_id`，事件配对不受影响

### Requirement: 副作用分类与重试判定

系统 SHALL 维护工具副作用分类注册表（`readonly` / `idempotent_write` / `non_idempotent_write` / `external_side_effect` / `unknown`）：内置工具静态映射，未登记工具（含自定义工具）默认 `unknown`。恢复与重试判定 SHALL 依据回执：`readonly` 与 `idempotent_write` 在前次尝试无 `succeeded` 回执时可自动重试；`non_idempotent_write` 与 `external_side_effect` 仅在前次回执为 `failed`（确认未生效）时重试；`unknown` 或 `status=unknown` 的调用 SHALL NOT 自动重试，SHALL 进入人工核对状态（事件标记，不静默重发）。

#### Scenario: 只读工具自动重试

- **WHEN** `readonly` 类工具执行中断且无 `succeeded` 回执
- **THEN** 恢复流程自动重试该调用

#### Scenario: 非幂等写仅在确认未生效时重试

- **WHEN** `non_idempotent_write` 类工具的回执为 `failed`
- **THEN** 该调用可重试；回执为 `unknown` 时不可自动重试并标记人工核对

#### Scenario: 未登记工具按最保守策略处理

- **WHEN** 执行未在注册表中登记的自定义工具
- **THEN** 其副作用分类为 `unknown`，中断后不自动重试

### Requirement: 未实现的 Temporal 分发选项显式失败

`TemporalWorkerPool` 在分布式分发未实现期间 SHALL 在 `dispatch` 时抛出明确异常（含指引信息），SHALL NOT 静默回退为本地直接执行。Temporal worker 启动脚本在未注册任何 Activity 时 SHALL 拒绝启动。功能目录对 Temporal 集成限制的注明 SHALL 保留。

#### Scenario: 选择 Temporal 分发时显式报错

- **WHEN** 代码路径显式构造并使用 `TemporalWorkerPool` 分发节点
- **THEN** 抛出含"分布式分发未实现"指引的异常，本地不执行该节点

#### Scenario: 空 Activity 的 worker 拒绝启动

- **WHEN** 运行 Temporal worker 启动脚本且注册的 Activities 为空
- **THEN** 启动失败并输出原因，不进入空转轮询

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
