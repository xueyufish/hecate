# tool-recovery Specification

## Purpose

区分"状态可回放"与"外部动作可恢复"：为工具执行建立回执语义（稳定调用 ID、副作用分类、结果状态），使崩溃恢复能够安全决定重试、跳过或进入人工核对；并让未实现的 Temporal 分发选项显式失败而非静默回退。

## Requirements

### Requirement: 工具执行回执与稳定调用 ID

每次工具执行 SHALL 由服务端分配稳定的 `execution_id`（UUID，独立于 LLM 生成的 `tool_call_id`），并 SHALL 记录于该次执行的 `TOOL_CALL` 与 `TOOL_RESULT` 事件 payload。`TOOL_CALL` 事件 SHALL 在工具执行开始前落盘并充当该次执行的领取记录；`TOOL_CALL` 落盘后、对应 `TOOL_RESULT` 落盘之前的窗口 SHALL 被恢复流程视为 `claimed` 态，SHALL NOT 被视为"从未执行"。`TOOL_CALL` 与 `TOOL_RESULT` payload SHALL 记录一致的 `arguments_digest`（工具参数经 canonical JSON 序列化后的 SHA-256 摘要）。`TOOL_RESULT` 事件 SHALL 携带结果状态（`succeeded` / `failed` / `unknown`）与副作用分类；工具执行抛出异常时 SHALL 补写 `TOOL_RESULT` 事件：结果不可判定（超时、连接中断等）时状态为 `unknown`，其余异常状态为 `failed`。

#### Scenario: 成功执行有完整回执

- **WHEN** 一次工具执行成功返回
- **THEN** 事件日志中 `TOOL_CALL` 与 `TOOL_RESULT(status=succeeded)` 携带相同的 `execution_id` 与副作用分类

#### Scenario: 异常执行补写失败回执

- **WHEN** 工具执行抛出异常
- **THEN** 事件日志中出现与该次 `TOOL_CALL` 配对的 `TOOL_RESULT`：超时/连接类异常状态为 `unknown`，其余为 `failed`

#### Scenario: LLM 调用 ID 缺失时仍有稳定标识

- **WHEN** LLM 生成的 `tool_call_id` 为空串
- **THEN** 该次执行仍获得服务端分配的 `execution_id`，事件配对不受影响

#### Scenario: 未决 TOOL_CALL 表示领取态

- **WHEN** 执行在 `TOOL_CALL` 落盘后、`TOOL_RESULT` 落盘前中断
- **THEN** 事件日志保留该 `TOOL_CALL`，恢复流程将该执行判定为 `claimed`，而非"从未执行"

#### Scenario: 回执记录参数摘要

- **WHEN** 一次工具执行落盘 `TOOL_CALL` 与 `TOOL_RESULT`
- **THEN** 两个事件的 payload 携带一致的 `arguments_digest`

### Requirement: 副作用分类与重试判定

系统 SHALL 维护工具副作用分类注册表（`readonly` / `idempotent_write` / `non_idempotent_write` / `external_side_effect` / `unknown`）：内置工具静态映射，未登记工具（含自定义工具）默认 `unknown`。自动重跑判定 SHALL 满足：终态回执为 `succeeded` 的执行 SHALL NOT 重新执行；`failed` 回执仅允许 `readonly` 与 `idempotent_write` 类自动重跑——异常类型不能证明非幂等副作用未发生，`non_idempotent_write`、`external_side_effect` 与 `unknown` 类在 `failed` 回执下 SHALL 进入人工核对而非自动重跑；回执状态为 `unknown` 的调用 SHALL NOT 自动重跑。自动重跑的唯一依据是明确幂等保证或已完成对账，不得以回执缺失或异常类型推断"未生效"。

#### Scenario: 只读工具自动重试

- **WHEN** `readonly` 类工具的回执状态为 `failed`，或其执行中断且无 `succeeded` 回执
- **THEN** 恢复流程可自动重试该调用

#### Scenario: 非幂等写仅在确认未生效时重试

- **WHEN** `non_idempotent_write`、`external_side_effect` 或 `unknown` 类工具的回执状态为 `failed`
- **THEN** 该调用不凭回执自动重跑，标记人工核对；仅在明确幂等保证或已完成对账确认未生效后方可重试

#### Scenario: 幂等写失败后可自动重跑

- **WHEN** `idempotent_write` 类工具的回执状态为 `failed`
- **THEN** 该调用可自动重跑

#### Scenario: 不可判定结果不自动重跑

- **WHEN** 任一类别工具的回执状态为 `unknown`
- **THEN** 该调用不自动重跑，标记人工核对

#### Scenario: 未登记工具按最保守策略处理

- **WHEN** 执行未在注册表中登记的自定义工具
- **THEN** 其副作用分类为 `unknown`，中断后不自动重跑

### Requirement: 未实现的 Temporal 分发选项显式失败

`TemporalWorkerPool` 在分布式分发未实现期间 SHALL 在 `dispatch` 时抛出明确异常（含指引信息），SHALL NOT 静默回退为本地直接执行。Temporal worker 启动脚本在未注册任何 Activity 时 SHALL 拒绝启动。功能目录对 Temporal 集成限制的注明 SHALL 保留。

#### Scenario: 选择 Temporal 分发时显式报错

- **WHEN** 代码路径显式构造并使用 `TemporalWorkerPool` 分发节点
- **THEN** 抛出含"分布式分发未实现"指引的异常，本地不执行该节点

#### Scenario: 空 Activity 的 worker 拒绝启动

- **WHEN** 运行 Temporal worker 启动脚本且注册的 Activities 为空
- **THEN** 启动失败并输出原因，不进入空转轮询

### Requirement: 恢复流程消费工具回执

执行恢复遇到此前已派发的工具调用时,SHALL 按 `execution_id` 将其判定为四态之一并据此动作:`never_started`(无 `TOOL_CALL`)按新调用正常领取执行;`claimed`(最新 `TOOL_CALL` 已落盘、无配对 `TOOL_RESULT`)下仅 `readonly` 类可自动重跑,所有写类别 SHALL NOT 自动重跑并标记人工核对;`outcome_unknown`(回执状态 `unknown`)不自动重跑,标记人工核对;`store_unavailable`(事件存储读取失败)下仅 `readonly` 类放行,写操作 SHALL 安全停止并标记,SHALL NOT 将存储不可用降级解释为"从未执行"。回执为 `succeeded` 的调用 SHALL NOT 重新执行,SHALL 依次尝试:从通道历史回填该 `tool_call_id` 的真实结果;通道历史不可得且配置了持久 Action 台账钩子时,回填台账落盘的真实结果内容;两者皆不可得时返回显式待对账标记(附已记录的 `result_digest`),SHALL NOT 以占位文本冒充恢复结果。

台账来源的回填内容 SHALL 在进入模型或通道前复用与首次执行一致的 PostTool 输出处理:首跑被输出屏蔽或脱敏的内容 SHALL NOT 经任一成功回放出口(恢复预检、锁内复查、台账领取竞争失败后的回填)重新暴露。回放治理是本地输出处理:SHALL NOT 重新执行业务工具、不重复消费审批、不重新认领已成功动作,SHALL NOT 把台账已记录的业务成功改写为失败或触发对账重跑。输出治理在回放路径失败时 SHALL 按既有 unknown/待对账语义 fail closed,SHALL NOT 返回原始内容。通道历史回填的内容为首次执行返回的已治理结果,SHALL 保持既有行为,不二次应用输出处理。

#### Scenario: 已成功执行不重放

- **WHEN** 恢复执行遇到回执为 `succeeded` 的工具调用,且通道历史包含该 `tool_call_id` 的结果消息
- **THEN** 该调用不重新执行,通道回填该真实结果

#### Scenario: 台账落盘结果跨进程回填

- **WHEN** 恢复执行遇到回执为 `succeeded` 的工具调用,通道历史无对应结果消息,但持久台账记录了该动作的真实结果内容
- **THEN** 以台账真实内容回填,不重新执行,不返回占位文本

#### Scenario: 成功回执但结果内容不可得时显式待对账

- **WHEN** 恢复执行遇到回执为 `succeeded` 的工具调用,通道历史与持久台账均无结果内容
- **THEN** 返回显式待对账标记并附 `result_digest`,不重新执行,不返回占位文本

#### Scenario: 台账回填内容先经输出治理

- **WHEN** 首跑将原始结果写入台账后 PostTool 治理返回 BLOCK,此后恢复经台账回填该成功结果
- **THEN** 回放消息为治理占位(与首跑同构),原始内容不出现在消息中,业务工具未被重新执行,台账 outcome 保持 `succeeded`

#### Scenario: SANITIZE 回放与首跑治理一致

- **WHEN** 台账记录原始内容,恢复回放时 PostTool 链对同一内容返回 SANITIZE 及 `modified_data`
- **THEN** 回放消息使用脱敏后的内容,不重新执行业务工具

#### Scenario: 回放治理失败不泄露原文

- **WHEN** 成功回放出口的输出处理抛出异常
- **THEN** 按既有 unknown/待对账语义安全停止,原始内容不出现在任何返回给模型或通道的消息中

#### Scenario: 通道历史回填保持已治理内容

- **WHEN** 通道历史存在该调用的首跑结果消息(含 BLOCK 占位)
- **THEN** 回填行为与现状一致,不对其二次应用输出处理

#### Scenario: 屏蔽回放不改写业务结果

- **WHEN** 回放输出治理返回 BLOCK 占位
- **THEN** 台账 outcome 保持 `succeeded`,该调用不被标记为执行失败,不进入对账重跑

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
### Requirement: 领取原子性与参数摘要冲突

同一 `execution_id` 的恢复状态查询与领取（`TOOL_CALL` 落盘）SHALL 在事件存储的会话级事件锁内完成，使并发派发（重复 worker、同会话并发重放）下至多一个执行者完成首次领取；未领取到首次执行权的执行者 SHALL 按其观测到的状态处置，且 SHALL NOT 因锁等待超时或失败而对写操作自动重跑。恢复时若本次派发的 `arguments_digest` 与该 `execution_id` 已记录的摘要不一致，SHALL 返回显式冲突错误且不执行该调用，不区分工具副作用类别。事件存储的每个生产实现 SHALL 通过契约测试证明上述查询与领取语义。

#### Scenario: 并发领取至多一次执行

- **WHEN** 两个执行者并发对同一处于 `never_started` 态的动作键执行恢复
- **THEN** 仅一个执行者领取并执行工具，另一个观测到 `claimed` 态并按类别处置

#### Scenario: 同键参数变化冲突拒绝

- **WHEN** 恢复派发的 `arguments_digest` 与该 `execution_id` 已记录的摘要不一致
- **THEN** 返回冲突错误且不执行该调用，无论工具副作用类别

#### Scenario: 领取语义进入事件存储契约

- **WHEN** 运行 EventStore 契约测试
- **THEN** InMemory 与 Postgres 实现均通过恢复状态查询与领取原子性用例

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

### Requirement: 持久 Action 台账钩子关联 TOOL_CALL/TOOL_RESULT

内核 SHALL 提供可选的 `ActionLedgerHook` 扩展点(具名消费者:独立宿主适配器):配置时,ToolWorker SHALL 在业务分发前将动作意图(动作键、工具名、参数摘要、副作用类别、session/execution/tool_call 关联)持久化到台账并原子领取——并发派发至多一个领取者,未领取到者按台账判定处置;台账判定 MUST 与事件存储判定共用同一决策函数,任一权威来源给出 claimed/unknown/store_unavailable 或摘要冲突时按其保守结论处置。执行结束(成功或失败)SHALL 向台账回执真实 outcome(含结果内容,由适配者落盘内容/摘要/引用);回执落盘失败 MUST NOT 撤销已发生副作用,台账停留领取态使后续恢复 fail-closed。钩子未配置时,ToolWorker 行为与既有 EventStore 路径完全一致。

台账落盘的原始结果内容是恢复与对账的持久事实,其落盘顺序(业务分发后、PostTool 输出治理前)SHALL 保持不变;进入模型或通道的可见结果由恢复出口的输出治理决定,二者 SHALL 保持可区分。回放治理以台账记录的内容为输入,SHALL NOT 假设其为原始结构化对象。

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

#### Scenario: 台账原文与模型可见内容可区分

- **WHEN** 首跑 PostTool 对结果执行 SANITIZE 后恢复经台账回填
- **THEN** 台账保留原始内容作为持久事实,恢复消息呈现治理后内容;两者不一致是预期行为,不视为数据损坏
