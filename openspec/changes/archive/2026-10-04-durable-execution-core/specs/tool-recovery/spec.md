# Spec Delta

## MODIFIED Requirements

### Requirement: 恢复流程消费工具回执

执行恢复遇到此前已派发的工具调用时,SHALL 按 `execution_id` 将其判定为四态之一并据此动作:`never_started`(无 `TOOL_CALL`)按新调用正常领取执行;`claimed`(最新 `TOOL_CALL` 已落盘、无配对 `TOOL_RESULT`)下仅 `readonly` 类可自动重跑,所有写类别 SHALL NOT 自动重跑并标记人工核对;`outcome_unknown`(回执状态 `unknown`)不自动重跑,标记人工核对;`store_unavailable`(事件存储读取失败)下仅 `readonly` 类放行,写操作 SHALL 安全停止并标记,SHALL NOT 将存储不可用降级解释为"从未执行"。回执为 `succeeded` 的调用 SHALL NOT 重新执行,SHALL 依次尝试:从通道历史回填该 `tool_call_id` 的真实结果;通道历史不可得且配置了持久 Action 台账钩子时,回填台账落盘的真实结果内容;两者皆不可得时返回显式待对账标记(附已记录的 `result_digest`),SHALL NOT 以占位文本冒充恢复结果。

#### Scenario: 已成功执行不重放

- **WHEN** 恢复执行遇到回执为 `succeeded` 的工具调用,且通道历史包含该 `tool_call_id` 的结果消息
- **THEN** 该调用不重新执行,通道回填该真实结果

#### Scenario: 台账落盘结果跨进程回填

- **WHEN** 恢复执行遇到回执为 `succeeded` 的工具调用,通道历史无对应结果消息,但持久台账记录了该动作的真实结果内容
- **THEN** 以台账真实内容回填,不重新执行,不返回占位文本

#### Scenario: 成功回执但结果内容不可得时显式待对账

- **WHEN** 恢复执行遇到回执为 `succeeded` 的工具调用,通道历史与持久台账均无结果内容
- **THEN** 返回显式待对账标记并附 `result_digest`,不重新执行,不返回占位文本

#### Scenario: 不可判定结果进入人工核对

- **WHEN** 恢复执行遇到回执状态为 `unknown` 的外部副作用类工具调用
- **THEN** 该调用不自动重新执行,并被标记为人工核对状态

#### Scenario: 无回执的安全类调用按判定重试

- **WHEN** 恢复执行遇到 `readonly` 类调用,其无 `succeeded` 回执(含 `claimed` 态与事件日志无记录两种情形)且事件存储可用
- **THEN** 按 `should_auto_retry` 判定自动重跑,并沿用同一 `execution_id`

#### Scenario: claimed 态写操作安全停止

- **WHEN** 恢复执行发现任意写类别(含 `idempotent_write`)执行处于 `claimed` 态
- **THEN** 该调用不自动重跑,标记人工核对

#### Scenario: 存储不可用时写操作安全停止

- **WHEN** 恢复查询事件存储失败
- **THEN** `readonly` 类调用放行,写操作停止并标记人工核对,不当作"从未执行"

## ADDED Requirements

### Requirement: 持久 Action 台账钩子关联 TOOL_CALL/TOOL_RESULT

内核 SHALL 提供可选的 `ActionLedgerHook` 扩展点(具名消费者:独立宿主适配器):配置时,ToolWorker SHALL 在业务分发前将动作意图(动作键、工具名、参数摘要、副作用类别、session/execution/tool_call 关联)持久化到台账并原子领取——并发派发至多一个领取者,未领取到者按台账判定处置;台账判定 MUST 与事件存储判定共用同一决策函数,任一权威来源给出 claimed/unknown/store_unavailable 或摘要冲突时按其保守结论处置。执行结束(成功或失败)SHALL 向台账回执真实 outcome(含结果内容,由适配者落盘内容/摘要/引用);回执落盘失败 MUST NOT 撤销已发生副作用,台账停留领取态使后续恢复 fail-closed。钩子未配置时,ToolWorker 行为与既有 EventStore 路径完全一致。

#### Scenario: 钩子镜像领取与回执

- **WHEN** 配置台账钩子的 ToolWorker 执行一次工具调用
- **THEN** 台账按序记录意图、领取、outcome(含真实结果),关联列可对应该 session 的 TOOL_CALL/TOOL_RESULT 事件

#### Scenario: 台账权威拒绝重复派发

- **WHEN** 事件存储为空(如重启后)但台账记录该动作已领取且类别为写
- **THEN** 派发被安全停止并按决策表返回待核对结果,不产生第二次业务调用

#### Scenario: 回执落盘失败保持待对账

- **WHEN** 工具执行成功但台账 outcome 写入失败
- **THEN** 结果仍按通道流转,台账停留领取态;后续恢复按 claimed 判定安全停止,不自动重跑

#### Scenario: 未配置钩子行为不变

- **WHEN** ToolWorker 未注入 action_hook
- **THEN** 恢复与回执仅依赖事件存储,既有全部测试行为不变
