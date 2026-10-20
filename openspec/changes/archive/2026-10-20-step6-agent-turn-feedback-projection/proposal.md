# Proposal: step6-agent-turn-feedback-projection

## Why

`AgentTurn` 已能把一次 `(run_ref, decision_step_id)` 处理为 `AgentTurnOutcome`——已接受决策与经过治理的工具结果——但 `PortDecisionResponder` 仍要求宿主自行拼装并冻结下一次模型调用的 `messages` 快照。回合与回合之间缺少一个统一、可校验的"已完成动作回合 → 下一轮模型输入"转换,宿主各自拼消息会绕过校验、错配调用 ID 或泄露未经治理的内容。这是演进方案 step6"完整循环的继续位置"之前的输入衔接切片,为后续有界、可恢复的多轮 Controller 打基础。

## What Changes

- 新增独立反馈投影模块:从全部成功的 `AgentTurnOutcome` 重建一条 assistant `tool_calls` 消息(动作按已接受顺序,参数来自已提交决策),并按原顺序附加各动作的工具结果消息;调用 ID 复用 `tool_call_id_for_action`,执行关联复用 `execution_id_for_action`。
- 只消费 `AgentTurnOutcome` 中已经过治理的结果(`AcceptedActionOutcome.result`,即执行器返回的治理后消息);不读取台账原文、不重新执行工具、不做字符串猜测解析。首跑与恢复的结果内容允许存在既有序列化差异,投影保留实际受治理内容,不宣称逐字一致。
- 复用已接受决策校验，检查接受标记、版本及内容摘要；模型工具调用的 `function.arguments` 编码为 JSON 字符串，原记录的参数保持 dict。
- 显式校验并拒绝:final-answer 回合(`is_final_answer`)、任一非 `succeeded` 状态、`unprocessed_action_keys` 非空、动作数/顺序不符、`execution_id`/`tool_call_id` 关联不符、结果消息形状畸形(含内嵌 `tool_call_id` 不一致)、重复调用 ID，以及 succeeded 却带 is_error 的结果与非 JSON 基础消息。
- 投影深拷贝消息与嵌套参数,不修改原 `AgentTurnOutcome`、原消息或既有决策记录;assistant 消息文本取 `record.accepted_output.get("text")`,v1 动作决策缺文本时回退空串,不猜测解析其他字段。
- `PortDecisionResponder` 增加显式回合续接入口(投影结果 + 宿主提供的新输入/引用 → 冻结构造),保留现有构造方式与全部既有语义;不做自动循环、继续位置存储、等待唤醒、Task 完成判断、入口切流或数据库迁移。
- 测试显式串联两个 `AgentTurn`,断言第二次模型调用实际收到第一次的工具结果。

**范围外(不动)**:多轮循环推进、继续位置持久化、等待/唤醒、完成判断、评测模块、前端、共享 skill 文件、两个在途 step10 change 的文件范围。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `accepted-agent-decision`:新增"已完成动作回合投影为下一轮消息输入"与"端口响应方提供显式回合续接入口"两条要求;不修改既有要求。

## Impact

- 代码:`packages/hecate-runtime/src/hecate_runtime/` 新增反馈投影模块;`port_decision_responder.py` 增加显式消费入口(现有构造与 `respond` 语义不变)。
- 测试:新增 `packages/hecate-runtime/tests/test_agent_turn_feedback_projection.py`(含双 AgentTurn 串联);回归 runtime 包既有测试。
- 规范:`openspec/specs/accepted-agent-decision/spec.md` 经 delta 同步(仅 ADDED)。
- 与在途工作无交集:`step10-compare-view-query-state-hardening`(Compare/前端)与 `step10-evaluation-dataset-version-read-access-boundary`(Dataset 版本 API)均不触及 runtime 包。
