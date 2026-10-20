# Tasks: step7-replayed-tool-result-governance

## 1. 治理 helper 与三个回放出口(`packages/hecate-runtime/src/hecate_runtime/tool_call_executor.py`)

- [x] 1.1 新增 `_govern_replayed_result(name, tc_id, execution_id, content, base_context)`:复用首跑 PostTool 形态(chain 优先于 legacy hook,含 `ToolMatcher.match` 兜底),BLOCK/SANITIZE/ALLOW 与首跑同构,`context` 自行补齐 `tool_call_id`/`invocation_id`;异常向上传播不返回原文。验证:模块可导入、`python -m pytest packages/hecate-runtime/tests/test_replayed_result_governance.py -q`(先落最小用例)。
- [x] 1.2 恢复预检出口(:695 区域):`accepted.status` 置位后、`return recovery` 前对"成功回填"形态(无 `is_error`、content 为字符串，含空字符串)应用治理;channel 历史来源与错误/占位形态不治理。验证:对应出口用例通过。
- [x] 1.3 锁内复查出口(:877-907 区域):同 1.2 的谓词与治理;span 结束语义保持。验证:对应出口用例通过。
- [x] 1.4 `_hook_claim` 竞争回填出口:decision 返回前应用同一治理;`ExecutionStoppedError` 标记路径不受影响。验证:对应出口用例通过。

## 2. 回归测试(`packages/hecate-runtime/tests/test_replayed_result_governance.py`,新增)

- [x] 2.1 预检与竞争回填的 chain/legacy × ALLOW/BLOCK/SANITIZE 矩阵，锁内复查验证晚到 channel 回填不二次治理；仅回放业务调用为零，首跑后回放累计恰一次，台账 outcome 保持 `succeeded`。验证:`pytest packages/hecate-runtime/tests/test_replayed_result_governance.py -q` 对应用例通过。
- [x] 2.2 泄露封闭:新 executor、无历史消息、仅台账恢复 + BLOCK/SANITIZE stub,断言原文不出现在任何消息;治理 stub 抛异常时断言异常传播且无原文。验证:对应用例通过。
- [x] 2.3 透传与优先级:channel 历史含首跑 BLOCK 占位时原样返回且治理 mock 计数为 0;无 chain 且 hook 不匹配时透传不变;chain 存在时 legacy hook 不被调用。验证:对应用例通过。
- [x] 2.4 SQL 持久恢复:用 `hecate-durable` SQL action ledger(进程内 SQLite)+ 两个 executor 实例模拟重启,断言跨实例回放治理生效;SANITIZE 输入为已治理内容时不放大泄露。验证:对应用例通过。

## 3. 回归与共享检查

- [x] 3.1 Runtime 包回归:`pytest packages/hecate-runtime/tests -q` 全绿(test_accepted_action_step、test_accepted_action_batch_runtime、test_accepted_agent_turn_runtime、test_decision_step 等);`pytest tests/test_runtime/test_action_hook.py tests/test_runtime/test_tool_worker.py tests/test_runtime/test_tool_worker_wiring.py tests/test_runtime/test_g2_recovery_states.py -q` 全绿;如既有断言依赖"回放返回原文"的旧行为,按新契约核对修正并逐条记录,不允许批量放宽。
- [x] 3.2 共享检查:`bash scripts/dev.sh check all` 通过;`openspec validate step7-replayed-tool-result-governance --strict` 通过;`python scripts/feature_inventory.py check` 无新增漂移。

## 4. 收尾

- [x] 4.1 在 `verification.md` 记录基点(origin/main `e8e52e1f`)、实际测试命令与结果、三出口与 delta 场景的对应关系、跳过范围及原因;确认业务调用计数、审批消费计数、台账 outcome 断言齐备。
- [x] 4.2 勾选完成项,自查 diff 仅触及 `tool_call_executor.py`(必要时 `action_ledger.py` 小幅调整)、新增测试文件、本 change artifacts 与两个 delta spec;确认未触及多轮 Controller、评测模块、前端与共享 skill 文件。

## 5. 独立复核

- [x] 5.1 先复现再修正空字符串跳过治理、竞争回填重复治理、恢复预检缺失可信 invocation_id；新增回归修正前 4 failed，修正后通过。
- [x] 5.2 补齐两个台账出口的 chain/legacy 三态与 chain 异常测试，Worker/AcceptedActionStep 实际入口、审批/认领不重复、ownership 拒绝及锁内晚到 channel 恢复断言；专项合计 42 passed。
- [x] 5.3 重跑影响面回归、共享检查、OpenSpec strict 与规划检查，记录实际结果及跳过原因。
