# Design: step7-replayed-tool-result-governance

## 背景(已核实的调用链事实)

首跑路径(`_execute_call`):工具返回原始 `result` → :1046 `_mirror_outcome(..., result=result)` 把原始内容镜像到持久台账 → :1065 起 PostTool 治理(`Phase.TOOL_RESULT` 中间件链优先,`post_data={"name","result","context"}`;无 chain 时 legacy `PostToolHook` 兜底)执行 BLOCK(返回 `"Result sanitized: {reason}"`、无 `is_error`)或 SANITIZE(原地替换 `result`)。台账因此始终持有**治理前**的原文——这是故意的持久化顺序(`_mirror_outcome`:"never undo the effect";写失败保持 claimed 待对账),本设计不改变它。

三个成功回放出口把 `recovery_decision` 的结果原样返回,均不经过 PostTool:

| 出口 | 位置 | 内容来源 |
|---|---|---|
| 恢复预检(`claim=False`) | tool_call_executor.py:695-708 | `merged.result_content`(台账)或 `_log_recorded_content`(channel 日志) |
| 锁内复查(`claim=True`) | :877-907 | 同上 |
| 台账领取竞争失败回填 | `_hook_claim` :542-554 | `resolution.result_content`(台账) |

`recovery_decision`(action_ledger.py:283)把 `recorded_content` 直接放进 `{"role": "tool", "tool_call_id", "content": recorded_content}` 返回。首跑被 BLOCK/SANITIZE 的调用在重启、消息丢失、跨回合恢复或并发回填时任一场景下原文重新进入模型。

**两类恢复来源的既有差异(必须保持)**:TOOL_RESULT 事件只存 `result_length`/`result_digest` 不存内容;`_log_recorded_content` 从 messages channel 写日志恢复的是首跑**返回**的消息内容(BLOCK 时即占位文本),天然已治理——该路径 SHALL 保持现状。需要治理的只有**台账来源**的 `recorded_content`。

## 拦截谓词

治理 helper 只作用于"成功回填"形态的 decision:`role == "tool"`、**无 `is_error`**、`content` 为字符串（包括空字符串；只有 `None` 表示内容缺失）。其余形态不治理:占位/失败消息(`_withheld_result` 恒带 `is_error=True`)、needs_review/reconciliation 标记(在 `_checked_recovery_result` 中已抛 `ExecutionStoppedError`,不会以 dict 返回)、无内容的决策。这些形态或已是治理产物,或不含业务结果内容。

## 治理 helper

新增 `_govern_replayed_result(name, tc_id, execution_id, content, base_context) -> dict`,在三个出口的 `return` 前调用(置于 `accepted.status = "succeeded"` 置位**之后**,保证治理异常时业务成功标记不回退):

- 复用与首跑完全一致的调用形态:chain 存在则 `post_chain.run({"name", "result": content, "context"})`;否则 legacy hook 按 `ToolMatcher.match` 匹配后调用。`context` 以首跑同构方式构造(`dict(base_context)` + `tool_call_id` + `invocation_id`;预检出口发生在 :711 注入之前,helper 内从已计算的稳定执行身份补齐，覆盖调用方同名值且不修改原 context)。
- BLOCK → `{"role": "tool", "tool_call_id": tc_id, "content": f"Result sanitized: {reason}"}`(与首跑同构,无 `is_error`);SANITIZE → content 替换为 `modified_data["result"]`;ALLOW → content 原样。无 chain 且 hook 不匹配 → 原样透传。
- 输入形状:`result_content` 经 durable `_stringify` **恒为字符串**(原始对象为 dict 时是 JSON dump)。治理实现 MUST 接受 str 输入;依赖结构化字段的治理(如检查 `result["rows"]`)需自行解析——这是对治理实现文档化的约束,不在本 change 扩展。
- 异常:向上传播(与首跑 PostTool 异常行为一致),不捕获、不降级返回原文;调用方按既有异常路径处置(Worker/Step 的执行停止语义)。
- 幂等与非确定性:spec 不承诺重放结论与首跑逐字一致(LLM judge 类 guard 天然非确定),承诺的硬边界是"治理返回 BLOCK/SANITIZE 或抛异常时原文不泄露 + 零业务副作用 + 已发生成功不改写"。测试全部使用确定性 stub。

`recovery_decision` 决策表本身不改;治理是出口侧的包装,decision 谓词与 ownership/fencing/摘要冲突逻辑不动。

**实现细化(标记在源头打)**:锁内复查出口以 `ledger_claimed=True` 跳过台账解析,其内容只能来自事件存储(无内容)或 channel 日志(已治理)——台账原文在结构上不可达,因此治理标记由**内容来源处**设置:`_recovery_outcome` 在 `merged.result_content` 非空且 context 尚无该调用的 tool 消息时(此时 `recovery_decision` 才会真正消费台账内容,否则返回的是 messages 里的已治理副本)给成功回填 dict 打 `_replay_govern` 私有标记;`_hook_claim` 的 ledger 判定同理。出口侧 `_maybe_govern_recovery` 只治理带标记的 dict 并在返回前剥除标记;不带标记的形态(含 channel 历史与 messages 副本)原样透传。三个出口均保留治理调用点,锁内出口为结构性透传防御。

## 不变式

- 台账落盘顺序与 `_mirror_outcome` 语义不变(原文是持久事实,design 明确"台账原文 vs 模型可见内容"可区分是预期行为)。
- 回放治理为本地输出处理:不触发 `tool_execute`/`tool_execute_sandbox`、不触碰审批回调、不做新的台账 claim;`accepted.status` 保持 `succeeded`。
- channel 历史回填路径(`_log_recorded_content` → decision)不二次治理。
- 未配置任何 PostTool 治理的部署:回放行为与现状逐字节一致(透传)。
- 事件存储判定、四态恢复、store_unavailable fail-closed 全部不变。

## 测试设计

新增 `packages/hecate-runtime/tests/test_replayed_result_governance.py`(runtime 包自带测试树,与被测代码同包):

- **可达出口矩阵**:恢复预检与 `_hook_claim` 竞争回填分别覆盖 chain/legacy hook 的 ALLOW/BLOCK/SANITIZE；锁内复查以晚到 TOOL_RESULT + CHANNEL_WRITE 验证已治理内容透传。仅回放用例业务调用为零，首跑后回放用例累计业务调用恰一次，台账 outcome 不变。
- **无历史消息仅账本恢复**:channel 日志为空 + 台账原文 + BLOCK stub → 断言原文不出现在任何消息(首跑已治理内容也不假设存在)。
- **治理异常**:stub 抛异常 → 断言异常传播且结果中无原文。
- **channel 历史优先**:预置含 BLOCK 占位的 channel 消息 → 断言原样返回、治理未被调用(mock 计数为 0)。
- **未配置治理**:透传不变(回归锚)。
- **SQL 持久恢复**:用 `hecate-durable` SQL action ledger(SQLite 组件实现)+ 两个 executor 实例模拟进程重启,验证跨实例回放治理生效。
- **SANITIZE 幂等压力**:治理输入为已治理内容时输出仍安全(SANITIZE 二次应用不放大泄露)。

回归:`packages/hecate-runtime/tests/`(test_accepted_action_step.py、test_accepted_action_batch_runtime.py、test_accepted_agent_turn_runtime.py、test_decision_step.py)及 `tests/test_runtime/test_action_hook.py`、`test_tool_worker.py`、`test_tool_worker_wiring.py`、`test_g2_recovery_states.py`;共享检查 + OpenSpec strict + feature inventory check。

## 兼容性

| 场景 | 行为 |
|---|---|
| 未配置 PostTool 治理 | 回放透传,与现状一致(零变化) |
| 首跑 ALLOW,后经台账回放 | 治理放行,内容不变(与现状一致) |
| 首跑 SANITIZE/BLOCK,后经台账回放 | **行为变化**:从"原文泄露"变为"治理后内容"——即本 change 的目的 |
| channel 历史可得 | 不变(已治理内容原样回填) |
| 治理在回放路径抛异常 | 从"原文返回"变为"安全停止" |

## 非目标

- 多轮 Agent Controller、入口接入、评测模块、前端、共享 skill 文件。
- 台账 schema 变更或 durable 迁移(result_payload 格式不变,仅消费侧加治理)。
- 事件存储恢复路径(`resolve_tool_execution_state`)的决策逻辑。
- PostTool 治理实现本身的改造(中间件/hook 语义不动,仅新增回放消费点)。

## 独立复核修正

- 空字符串为有效已记录结果，不能跳过治理。
- `_hook_claim` 竞争失败也先核对实际使用的内容来源；若返回 context 的既有工具消息，不给其打台账原文标记。
- 恢复预检早于首跑 context 注入，治理 helper 显式接收服务端 `execution_id` 并覆盖不可信的同名 context 值。
- 新增 Worker 与 AcceptedActionStep 真实入口测试：审批只消费一次、原认领不增加、治理错误返回 stopped 报告且台账成功保持、失效 ownership 在治理前拒绝。
