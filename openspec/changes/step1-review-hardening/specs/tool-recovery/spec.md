## MODIFIED Requirements

### Requirement: 恢复流程消费工具回执

执行恢复遇到此前已派发的工具调用时，SHALL 按 `execution_id` 将其判定为四态之一并据此动作：`never_started`（无 `TOOL_CALL`）按新调用正常领取执行；`claimed`（最新 `TOOL_CALL` 已落盘、无配对 `TOOL_RESULT`）下仅 `readonly` 类可自动重跑，所有写类别 SHALL NOT 自动重跑并标记人工核对；`outcome_unknown`（回执状态 `unknown`）不自动重跑，标记人工核对；`store_unavailable`（事件存储读取失败）下仅 `readonly` 类放行，写操作 SHALL 安全停止并标记，SHALL NOT 将存储不可用降级解释为"从未执行"。回执为 `succeeded` 的调用 SHALL NOT 重新执行，SHALL 从通道历史回填该 `tool_call_id` 的真实结果；通道历史中结果不可得时 SHALL 返回显式待对账标记（附已记录的 `result_digest`），SHALL NOT 以占位文本冒充恢复结果。

#### Scenario: 已成功执行不重放

- **WHEN** 恢复执行遇到回执为 `succeeded` 的工具调用，且通道历史包含该 `tool_call_id` 的结果消息
- **THEN** 该调用不重新执行，通道回填该真实结果

#### Scenario: 成功回执但结果内容不可得时显式待对账

- **WHEN** 恢复执行遇到回执为 `succeeded` 的工具调用，但通道历史中无对应结果消息
- **THEN** 返回显式待对账标记并附 `result_digest`，不重新执行，不返回占位文本

#### Scenario: 不可判定结果进入人工核对

- **WHEN** 恢复执行遇到回执状态为 `unknown` 的外部副作用类工具调用
- **THEN** 该调用不自动重新执行，并被标记为人工核对状态

#### Scenario: 无回执的安全类调用按判定重试

- **WHEN** 恢复执行遇到 `readonly` 类调用，其无 `succeeded` 回执（含 `claimed` 态与事件日志无记录两种情形）且事件存储可用
- **THEN** 按 `should_auto_retry` 判定自动重跑，并沿用同一 `execution_id`

#### Scenario: claimed 态写操作安全停止

- **WHEN** 恢复执行发现任意写类别（含 `idempotent_write`）执行处于 `claimed` 态
- **THEN** 该调用不自动重跑，标记人工核对

#### Scenario: 存储不可用时写操作安全停止

- **WHEN** 恢复查询事件存储失败
- **THEN** `readonly` 类调用放行，写操作停止并标记人工核对，不当作"从未执行"

## ADDED Requirements

### Requirement: Recovery identity and attempt ordering
恢复流程 SHALL 同时校验工具名与参数摘要；已有执行记录缺少可校验身份时 SHALL 停止，不得跳过校验。遗留记录保存了参数时 SHALL 以其计算摘要。非法 JSON、非 object 参数及无法进行 JSON 序列化的参数 SHALL 在工具执行前被拒绝。只有明确 failed 状态允许安全类别重试，未识别状态 SHALL 视为 outcome_unknown。重试 SHALL 在锁内持久化新领取；新领取之后无结果 SHALL 为 claimed，不得沿用上一次 failed 状态。

#### Scenario: Same key different tool
- **WHEN** 同一动作键参数相同但工具名发生变化
- **THEN** 返回冲突错误且不执行工具

#### Scenario: Legacy arguments changed
- **WHEN** 遗留领取无摘要但保存了原参数，本次参数不同
- **THEN** 拒绝执行

#### Scenario: Invalid parameters
- **WHEN** 参数为非法 JSON、非 object 或不能 JSON 序列化
- **THEN** 返回参数错误，不调用执行器

#### Scenario: Unknown receipt status
- **WHEN** 回执状态缺失或不是 succeeded/failed/unknown
- **THEN** 判定 outcome_unknown，不自动重试

#### Scenario: Retry claim supersedes failed receipt
- **WHEN** failed 回执后安全重试已领取且尚未生成结果
- **THEN** 查询得到 claimed 状态，其他 worker 不重复派发写操作

