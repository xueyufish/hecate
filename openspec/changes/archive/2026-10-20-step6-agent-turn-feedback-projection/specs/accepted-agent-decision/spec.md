# Spec Delta

## ADDED Requirements

### Requirement: 已完成动作回合投影为下一轮消息输入

系统 SHALL 提供从一次完整成功的动作回合(`AgentTurnOutcome`)到下一轮模型输入消息列表的纯投影转换:按已提交决策的动作顺序重建一条 assistant `tool_calls` 消息(工具名与参数取自已提交决策事实,`tool_call_id` SHALL 复用 `tool_call_id_for_action` 派生的稳定身份),随后按同一顺序为每个动作附加一条工具结果消息。工具结果 SHALL 只取自 `AgentTurnOutcome` 中已经过治理的结果(`AcceptedActionOutcome.result`,即执行器返回的治理后消息),SHALL NOT 读取台账原文、重新执行工具或对结果内容做猜测性解析;首跑与恢复的结果内容允许存在既有序列化差异,投影 SHALL 保留实际受治理内容,SHALL NOT 声称与首跑消息逐字一致。

转换前 SHALL 复用既有决策记录校验，确认支持的版本及合法非空动作集合，验证接受标记存在且内容摘要与当前记录一致。模型消息的 `function.arguments` SHALL 为已接受参数的 JSON 字符串编码，原记录参数 SHALL 保持不变。基础消息 SHALL 为有限 JSON 对象列表，succeeded 结果携带真值 `is_error` 时 SHALL 作为矛盾结果拒绝。

转换前 SHALL 校验并显式拒绝以下情形,SHALL NOT 做隐式转换或降级:`is_final_answer` 为真的最终回答回合;任一动作状态非 `succeeded`(含 `failed`/`withheld`/`stopped`);`unprocessed_action_keys` 非空的部分完成回合;投影结果与已提交决策的动作数量或顺序不符;动作的 `execution_id` 与 `execution_id_for_action` 派生值不符;结果消息形状畸形或其内嵌 `tool_call_id` 与派生 `tool_call_id` 不一致;派生调用 ID 重复。assistant 消息文本 SHALL 取自已提交决策的 `accepted_output` 文本字段,历史 v1 动作决策不含该字段时 SHALL 回退为空串,SHALL NOT 据此拒绝或猜测解析其他字段。投影 SHALL 深拷贝消息与嵌套参数:原 `AgentTurnOutcome`、调用方传入消息与既有决策记录 SHALL NOT 被修改。

#### Scenario: 多动作回合重建配对消息

- **WHEN** 一个包含两个已成功动作的回合被投影
- **THEN** 产出一条 assistant 消息携带两个按已接受顺序排列的 `tool_calls` 条目,其后跟两条同序工具结果消息,每对 `tool_call_id` 与 `tool_call_id_for_action` 派生值一致

#### Scenario: 只使用治理后的结果

- **WHEN** 首跑被 PostTool 治理屏蔽的动作回合被投影
- **THEN** 工具结果消息携带治理后占位内容,台账或执行层保存的原始内容不出现在投影中

#### Scenario: 非成功回合显式拒绝

- **WHEN** 回合中任一动作状态为 `failed`、`withheld` 或 `stopped`,或 `unprocessed_action_keys` 非空
- **THEN** 投影抛出显式校验错误,不产出任何消息,不以部分结果继续

#### Scenario: 最终回答回合不转换

- **WHEN** `is_final_answer` 为真的回合被传入投影
- **THEN** 投影抛出显式校验错误,最终回答不被隐式转换为继续执行的动作输入

#### Scenario: 关联身份不符显式拒绝

- **WHEN** 某动作结果的 `execution_id` 或结果消息内嵌 `tool_call_id` 与派生稳定身份不一致
- **THEN** 投影抛出显式校验错误,不产出消息

#### Scenario: 投影不修改输入

- **WHEN** 投影执行完成
- **THEN** 原 `AgentTurnOutcome`、传入的基础消息与已提交决策记录的全部嵌套内容与投影前逐字段一致

#### Scenario: 历史 v1 决策缺文本回退

- **WHEN** 已提交动作决策的 `accepted_output` 不含文本字段
- **THEN** assistant 消息文本为空串,投影正常产出,不报错、不猜测解析其他字段

### Requirement: 端口响应方提供显式回合续接入口

`PortDecisionResponder` SHALL 提供显式消费投影结果的构造入口:以投影产出的消息列表为基础、叠加宿主提供的新一轮输入与引用,构造与既有构造方式等价的冻结快照响应方。既有构造方式与 `respond` 语义 SHALL 保持不变;续接入口 SHALL 复用同一输入冻结、引用校验与工具快照校验,SHALL NOT 引入自动循环、继续位置存储或等待唤醒。宿主串联两次回合时,第二次模型调用 SHALL 实际收到第一次回合投影出的工具调用与治理后工具结果消息。

#### Scenario: 续接构造冻结投影消息

- **WHEN** 以投影消息列表经续接入口构造响应方,并以匹配的新引用发起第二次决策
- **THEN** 第二次模型调用收到的消息包含投影出的 assistant `tool_calls` 消息与全部工具结果消息,且构造后修改调用方持有的消息列表不影响冻结快照

#### Scenario: 引用不匹配拒绝

- **WHEN** 第二次决策的 `input_refs` 与续接构造时冻结的引用不一致
- **THEN** 响应方按既有语义拒绝,不发起模型调用

#### Scenario: 既有构造方式不变

- **WHEN** 以既有构造方式创建响应方并调用 `respond`
- **THEN** 行为与续接入口引入前完全一致

#### Scenario: 未提交或损坏决策不能成为反馈输入

- **WHEN** 决策缺少接受标记、摘要不匹配、版本不支持，或动作集合不满足记录契约
- **THEN** 投影显式拒绝，不把其参数或结果构造成模型历史

#### Scenario: 工具参数使用模型端口消息格式

- **WHEN** 已接受动作参数包含嵌套对象、布尔值、null 或非 ASCII 字符
- **THEN** assistant tool_calls 的 function.arguments 是可解码为原参数对象的 JSON 字符串，原决策仍保存原 dict 参数

#### Scenario: 错误标记不能被投影抹去

- **WHEN** 动作声称 succeeded 而结果消息带有真值 is_error
- **THEN** 投影显式拒绝，不通过丢弃错误标记将其转换为成功模型输入

#### Scenario: 真实恢复后的治理结果可继续反馈

- **WHEN** 已完成动作回合经 AgentTurn 原入口查找原决策并由账本回填，输出治理返回 BLOCK 或 SANITIZE
- **THEN** 恢复不重新调用模型或业务工具，投影保留治理后文本，下一回合实际收到配对的工具调用和治理后结果
