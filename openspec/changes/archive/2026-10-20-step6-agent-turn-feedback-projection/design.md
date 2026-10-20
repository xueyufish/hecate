# Design: step6-agent-turn-feedback-projection

## 背景

`AgentTurn.run()` 返回 `AgentTurnOutcome(record, resumed, report)`:`record` 是已提交决策(`AcceptedDecisionRecord`,含 `actions` 有序元组与 `accepted_output`),`report` 是 `AcceptedActionBatchReport(outcomes, unprocessed_action_keys)`——`AcceptedActionOutcome(status, execution_id, result, stop_reason)` 按持久化顺序排列,`result` 即执行器返回的治理后工具消息 dict(`{"role": "tool", "tool_call_id", "content"}`;经 #291,回放路径同样返回治理后消息)。`PortDecisionResponder.__init__` 要求宿主自备 `messages` 并经 `canonical_json` 冻结。两者之间没有统一、可校验的衔接。

## 模块设计

新增 `packages/hecate-runtime/src/hecate_runtime/turn_projection.py`:

```python
def project_turn_messages(
    outcome: AgentTurnOutcome,
    base_messages: list[dict] | None = None,
) -> list[dict]:
```

- **校验先行**(全部通过才开始构造),显式拒绝集合:
  1. 先复用 `snapshot_decision` 的版本、动作、JSON 校验并隔离可变数据；检查 `accepted_at` 非空且内容摘要与重新计算的摘要一致，再拒绝最终回答回合；
  2. `len(report.outcomes) != len(record.actions)` 或 `report.unprocessed_action_keys` 非空(部分完成);
  3. 任一 `outcome.status != "succeeded"`;
  4. 对每个位置 `i`:`outcome.execution_id != execution_id_for_action(run_ref, decision_step_id, action_key)`(关联不符);
  5. 结果形状畸形:`result` 非 dict、`role != "tool"`、`content` 非字符串、内嵌 `tool_call_id != tool_call_id_for_action(...)`、调用 ID 重复，以及虽标为 succeeded 却携带 `is_error` 的矛盾结果。基础 messages 必须是有限 JSON 对象列表。
  错误类型复用 `DecisionValidationError`(消息区分原因类别),与栈内既有校验风格一致。
- **构造**:
  - assistant 消息:`{"role": "assistant", "content": accepted_output.get("text", "") or "", "tool_calls": [...]}`,每个条目 `{"id": tool_call_id_for_action(...), "type": "function", "function": {"name": action.tool, "arguments": <canonical JSON 字符串>}}`;决策事实中的 arguments 保持 dict，模型消息中的 `function.arguments` 使用 `canonical_json(action.arguments)` 编码为 JSON 字符串，匹配生产 RuntimePort → LLMService → provider 的消息格式；禁止用 Python `str(dict)` 代替 JSON。
  - 工具结果消息:逐动作 `{"role": "tool", "tool_call_id": <派生>, "content": result["content"]}`(治理后内容原样保留,不解析、不改写);`result` 中其他键(如 `is_error`)不透传——成功回填的本切片不含错误结果；除非 succeeded 已拒绝外，`is_error` 为真的矛盾结果同样拒绝。
  - 输出 = 深拷贝(`base_messages` 或 `[]`)+ 新消息;全部嵌套深拷贝,输入不可变。

## PortDecisionResponder 续接入口

新增类方法(保留 `__init__` 与 `respond` 不动):

```python
@classmethod
def for_turn_messages(cls, port, *, messages, config, tool_definitions, expected_refs, on_usage=None) -> PortDecisionResponder:
```

- 直接以既有构造逻辑冻结传入消息(投影结果 + 宿主新一轮输入由宿主先行拼接,或 `messages` 即投影输出、宿主再以参数追加——实现取"入口只做冻结转发",不新增拼接逻辑,避免两套消息语义)。
- 冻结、引用校验、工具快照校验全部走既有路径;`respond` 语义不变。
- 命名与文档明确"显式续接入口":不引入循环、不存储继续位置。

## 双 AgentTurn 串联测试

`packages/hecate-runtime/tests/test_agent_turn_feedback_projection.py`:

- 复用 `test_accepted_action_step.py` 的 stub 模式(`StubDecisionStore`、记录捕获型 model port);第一回合用真实 `AgentTurn` 跑完两动作,`project_turn_messages(outcome)` 产出消息,经 `for_turn_messages` 构造第二回合 responder,再跑第二个 `AgentTurn`;断言捕获的第二次模型调用 `messages` 含第一回合的 assistant `tool_calls` 与两条工具结果(内容为治理后结果)。
- 拒绝矩阵:final-answer、failed/withheld/stopped、部分完成(unprocessed 非空)、数量/顺序错位、`execution_id`/`tool_call_id` 篡改、重复 ID、结果形状畸形——逐项断言 `DecisionValidationError` 且无消息产出。
- 不变性:投影前后深比较 `outcome`/`base_messages`/决策记录。
- v1 回退:`accepted_output` 无 `text` → assistant content 空串。
- 序列化差异:保留人工构造 resumed 数据的单元用例，并增加实际 AgentTurn 首跑后由新 responder/executor 恢复的集成测试；模型零重问、工具/认领不增加，BLOCK/SANITIZE 后的回填内容交给下一回合，消息只保留实际受治理文本。

## 不变式与非目标

- 不读取台账、不重新执行工具、不发起模型调用;投影是纯函数。
- 不做自动循环、继续位置存储、等待唤醒、Task 完成判断、入口切流、数据库迁移。
- 既有 `PortDecisionResponder` 构造与 `respond`、`AgentTurn`、`AcceptedActionStep/Batch` 语义零变化(runtime 包全量回归承载)。
- 不修改两个在途 step10 change 的文件范围、共享 skill、评测模块与前端。

## 独立复核边界说明

- 投影复用既有决策校验并核对接受标记与内容摘要；这只是可信内部 `AgentTurnOutcome` 的完整性检查，不是签名、权限验证或外部提交证明，也不读取账本重新认定工具结果。
- `accepted_output` 在 v1 仍允许任意合法 JSON；缺少可用 text 时输出空 assistant content，不将历史字段改造成新持久契约。
- 参数的模型消息编码与结果内容处理分开：前者是已知 dict 的标准 JSON 编码；后者始终逐字保留治理后的字符串，不猜测解析 Python repr 或 JSON。
