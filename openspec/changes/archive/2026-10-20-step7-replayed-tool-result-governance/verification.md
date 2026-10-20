# Verification

## 基点

`origin/main` `e8e52e1f`(#288),分支 `feat/step7-replayed-tool-result-governance`,worktree `.worktrees/step7-replayed-tool-result-governance`。改动文件:`packages/hecate-runtime/src/hecate_runtime/tool_call_executor.py`、`packages/hecate-runtime/tests/test_replayed_result_governance.py`(新增)、`tests/test_runtime/test_replayed_result_governance_sql.py`(新增)、本 change artifacts。未修改任何既有测试断言。

## GLM 实现阶段记录（独立复核前）

| 检查 | 命令 | 结果 |
|---|---|---|
| 共享检查 | `bash scripts/dev.sh check all` | ✅ ruff check 通过(修正 1 处 E501 + 1 处 SIM114 后);ruff format 1616 文件已格式化(执行器经一次 format);mypy 960 源文件 0 问题 |
| 新治理测试(runtime 包) | `pytest packages/hecate-runtime/tests/test_replayed_result_governance.py -q` | ✅ 13 passed |
| SQL 持久恢复测试 | `pytest tests/test_runtime/test_replayed_result_governance_sql.py -q` | ✅ 3 passed(block/sanitize/allow × 双实例 SQL) |
| runtime 包全量回归 | `pytest packages/hecate-runtime/tests -q` | ✅ 238 passed(225 既有 + 13 新增) |
| 关联面回归 | `pytest tests/test_runtime/test_action_hook.py tests/test_runtime/test_tool_worker.py tests/test_runtime/test_tool_worker_wiring.py tests/test_runtime/test_g2_recovery_states.py -q` | ✅ 70 passed |
| 合并回归(格式化后复跑) | 上述全部合并执行 | ✅ 311 passed,0 failed |
| OpenSpec | `openspec validate step7-replayed-tool-result-governance --strict` | ✅ valid |
| 规划数据 | `python scripts/feature_inventory.py check` | ✅ 0 errors;189 warnings 为既有登记债,本 change 未触及 feature YAML |

## 实现要点与设计细化

1. **来源标记代替出口侧猜测**:实现时确认锁内复查出口以 `ledger_claimed=True` 跳过台账解析,其内容结构上不可能来自台账;且 `recovery_decision` 优先返回 context 中已有的 tool 消息副本(已治理)。因此治理标记(`_replay_govern`)在内容来源处设置——`_recovery_outcome` 仅当台账 `result_content` 被实际消费时打标,`_hook_claim` 的 ledger 判定同理;出口侧只治理带标记的回填并剥除标记。该细化已同步至 design.md,与 delta spec 的"通道历史不二次治理"场景一致。
2. **三个出口全部接入治理调用点**:恢复预检与 `_hook_claim` 竞争回填为实际治理路径;锁内出口为防御性透传(结构上无台账内容)。治理置于 `accepted.status = "succeeded"` 置位之后,治理异常不回退业务成功标记。
3. **首跑语义零变化**:BLOCK 不触碰 denial bookkeeping 的差异仅存在于回放路径(恢复不得重触发 denial/approval 状态,与既有恢复预检注释一致);首跑 BLOCK 的 denial 行为保持不变。

## 覆盖说明(delta 场景 ↔ 测试)

- **三态治理(runtime 包)**:chain BLOCK/SANITIZE/ALLOW(`test_precheck_ledger_backfill_replays_chain[*]`)与 legacy hook 三态(`..._replays_legacy_hook[*]`);SANITIZE 断言脱敏内容且无 `SECRET-VALUE`。
- **零业务副作用**:全部治理用例断言 `port.calls == []`;端到端首跑 + 重放 `test_first_run_then_replay_dispatches_exactly_once` 断言业务调用恰一次、台账 outcome/原文不变。
- **幂等**:`test_repeated_sanitize_does_not_amplify`——对同一账本原文重复恢复产生相同脱敏结果；该测试不声称曾把脱敏结果重新写入账本。
- **channel 历史不二次治理**:`test_channel_history_content_is_not_re_governed`(治理 mock 计数 0);messages 副本同理(`test_context_message_copy_is_not_re_governed`)。
- **治理异常 fail-closed**:`test_governance_failure_propagates_without_raw_content`——异常传播,无任何原文返回。
- **无治理透传**:`test_replay_without_governance_passes_through`(未配置 chain/hook 时与现状逐字节一致)。
- **竞争回填**:`test_claim_race_backfill_is_governed`(首解析让位后经 `_hook_claim` 台账判定,治理生效、claims 恰一次)。
- **SQL 持久恢复**:`test_sql_ledger_replay_governs_pre_governance_payload[*]`——双 executor 实例共享 SQL 台账模拟重启,首跑 BLOCK 后跨实例回放 block/sanitize/allow 三态治理,业务调用恰一次,`result_payload` 原文持久保留(可区分性)。

## 验证范围与保留限制

1. **未复跑**:全仓 pytest(本地门禁不含,CI 为准)、PostgreSQL 真实进程矩阵、真实模型路径;SQL 用例使用进程内 SQLite 组件实现,与 `hecate-durable` 既有测试同一级别。
2. **锁内出口的治理调用为防御性透传**:该出口结构上取不到台账内容(见实现要点 1),不声称其消费了台账原文；独立复核另补晚到 TOOL_RESULT/CHANNEL_WRITE 的实际锁内出口测试，验证已治理内容透传。
3. **PostTool 非确定性 guard**:重放结论不承诺与首跑逐字一致(LLM judge 类),spec 的治理边界在 BLOCK/SANITIZE/异常情况下断言不泄露原文，同时断言零副作用、业务成功不改写;确定性 stub 不覆盖非确定 guard 的行为差异。
4. **相邻缺口未处理**:首跑 BLOCK 的 denial bookkeeping 与台账原文的长期保留策略、`AcceptedActionBatch` 的批量恢复出口(复用同一 `execute_accepted_call` 路径,由 runtime 包回归覆盖),不属本切片扩展范围。

## 独立复核

复核 fetch 后 HEAD/origin/main 均为 `e8e52e1f`。原专项先独立复跑为 16 passed。

### 已复现并修正

- `_is_governed_backfill` 原先以 content 真值过滤，空字符串跳过 PostTool：chain 与 legacy 两项失败；修正为字符串类型判定。
- `_hook_claim` 不论实际消息来源一律打治理标记：竞争失败回填 context 中已治理消息时被再次 SANITIZE；修正为与预检相同的来源判定。
- 恢复预检在首跑 context 补齐前返回：PostTool 缺失稳定 invocation_id 或沿用调用方伪值；修正为把已派生的 execution_id 显式贯穿 helper，并覆盖上下文同名值。

新增复现用例修正前为 4 failed、14 passed；修正后和补充矩阵最终专项命令：

```text
.venv/Scripts/python.exe -m pytest packages/hecate-runtime/tests/test_replayed_result_governance.py tests/test_runtime/test_replayed_result_governance_sql.py -q
42 passed
```

其中 runtime 专项 39 项、SQLite SQL 3 项。增加了真实 Worker 与 AcceptedActionStep 入口、chain 优先级/异常、legacy matcher、空内容、两出口身份一致、竞争来源判定、晚到锁内恢复的测试。AcceptedActionStep 的业务成功状态与稳定执行身份不变；BLOCK/SANITIZE 不增加 claim、业务调用或审批请求，治理错误依既有入口返回 stopped/reconciliation_required，撤销 owner 在回放治理前被拒绝。

### 最终扩展验证

```text
.venv/Scripts/python.exe -m pytest packages/hecate-runtime/tests packages/hecate-durable/tests tests/test_runtime/test_replayed_result_governance_sql.py tests/test_runtime/test_action_hook.py tests/test_runtime/test_tool_worker.py tests/test_runtime/test_tool_worker_wiring.py tests/test_runtime/test_g2_recovery_states.py tests/test_runtime/test_tool_receipts.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_runtime/test_kernel_purity.py tests/test_layering_domain.py -q -ra
556 passed, 7 skipped in 149.35s
```

7 项跳过的原因已核对：accepted_decision_pg 的 6 项参数化测试未设置 DURABLE_TEST_POSTGRES_URL；worker_dispatch 的 1 项需要 PostgreSQL 并发 writer 事务。本轮没有运行真实 PostgreSQL 矩阵，不将跳过计为通过。上述结果包含专项 42 项，两个数字不相加。

```text
.venv/Scripts/python.exe scripts/dev.py check all
ruff check: passed
ruff format --check: 1616 files already formatted
mypy: Success: no issues found in 960 source files

openspec validate step7-replayed-tool-result-governance --strict
valid

.venv/Scripts/python.exe scripts/feature_inventory.py check
check ok: 413 entries, 0 errors, 189 warnings

git diff --check
passed
```

feature YAML 未修改，189 条警告来自现有登记中的治理字段/证据/验收缺项，本轮不扩展处理。共享检查通过后仅调整 SQL 测试的说明文字及 change 文档；三个涉及代码/测试文件的 scoped ruff 与格式检查再次通过。

未修改既有已跟踪测试断言、主 specs 或其他 worktree。全仓 CI、真实模型及真实 PostgreSQL 组合未运行。未提交、未推送、未归档。
