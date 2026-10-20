# Tasks: step6-agent-turn-feedback-projection

## 1. 投影模块(`packages/hecate-runtime/src/hecate_runtime/turn_projection.py`,新增)

- [x] 1.1 `project_turn_messages(outcome, base_messages=None)`:校验先行(final-answer、数量/顺序、非 succeeded、`unprocessed_action_keys`、`execution_id` 关联、结果形状与内嵌 `tool_call_id`、重复 ID),`DecisionValidationError` 区分原因;随后重建 assistant `tool_calls` 消息(`tool_call_id_for_action` 派生,参数取已提交决策并编码为模型端口需要的 JSON 字符串，原 dict 不变)与同序工具结果消息(`.result` 的治理后 `content` 原样保留);输出深拷贝、输入不可变。验证:`pytest packages/hecate-runtime/tests/test_agent_turn_feedback_projection.py -q`(先落最小用例)。
- [x] 1.2 assistant 文本回退:`record.accepted_output` 无文本字段(v1 动作决策)时 content 空串,不报错、不猜解析其他字段。验证:对应用例通过。

## 2. 续接入口(`packages/hecate-runtime/src/hecate_runtime/port_decision_responder.py`)

- [x] 2.1 新增 `for_turn_messages` 类方法:显式消费投影消息,经既有冻结/引用/工具快照校验构造响应方;`__init__` 与 `respond` 零变化。验证:既有 responder 测试全绿 + 新入口用例(冻结后修改调用方消息不影响快照;引用不匹配按既有语义拒绝)。

## 3. 回归测试(`packages/hecate-runtime/tests/test_agent_turn_feedback_projection.py`,新增)

- [x] 3.1 双 AgentTurn 串联:第一回合真实跑完两动作 → 投影 → `for_turn_messages` 构造 → 第二回合 responder 捕获的模型调用消息包含第一回合 assistant `tool_calls` 与两条治理后工具结果。验证:`pytest packages/hecate-runtime/tests/test_agent_turn_feedback_projection.py -q` 对应用例通过。
- [x] 3.2 拒绝矩阵:final-answer、failed/withheld/stopped、部分完成、数量/顺序错位、`execution_id`/`tool_call_id` 篡改、重复 ID、结果形状畸形——逐项断言显式拒绝且无消息产出。验证:对应用例通过。
- [x] 3.3 不可变性与恢复差异:投影前后深比较 outcome/base_messages/决策记录;恢复回合(reseed + resumed)投影保留实际治理内容,不做逐字比较。验证:对应用例通过。

## 4. 回归与共享检查

- [x] 4.1 runtime 包全量回归:`pytest packages/hecate-runtime/tests -q` 全绿(含 `test_accepted_action_step.py`、`test_accepted_agent_turn_runtime.py`、`test_tool_call_executor.py`);如既有断言受续接入口影响,逐条核对修正并记录,不允许批量放宽。
- [x] 4.2 共享检查:`bash scripts/dev.sh check all` 通过;`openspec validate step6-agent-turn-feedback-projection --strict` 通过;`python scripts/feature_inventory.py check` 无新增漂移。

## 5. 收尾

- [x] 5.1 在 `verification.md` 记录基点(origin/main `b8fa439f`)、实际测试命令与结果、delta 场景与测试的对应关系、跳过范围及原因。
- [x] 5.2 勾选完成项,自查 diff 仅触及新增投影模块、`port_decision_responder.py` 续接入口、新增测试文件与本 change artifacts;确认未触及循环推进、等待唤醒、完成判断、两个在途 step10 change 范围、共享 skill、评测模块与前端。

## 6. 独立复核与修正

- [x] 6.1 先补回归复现记录完整性、工具参数格式、is_error 与基础消息形状问题：修正前 10 failed/20 passed；修正后通过。
- [x] 6.2 补真实 AgentTurn 首跑→新实例恢复→下一回合链路，验证 BLOCK/SANITIZE、零重复模型/工具/认领，以及空内容/Unicode/元数据白名单；最终专项 35 passed。
- [x] 6.3 重跑 Runtime/durable/生产端口与分层回归、共享检查、OpenSpec strict 及规划检查，记录最终结果与跳过范围。
