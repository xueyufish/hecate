# Verification

## 基点

`origin/main` `b8fa439f`(#291),分支 `feat/step6-agent-turn-feedback-projection`,worktree `.worktrees/step6-agent-turn-feedback-projection`。改动文件:`packages/hecate-runtime/src/hecate_runtime/turn_projection.py`(新增)、`packages/hecate-runtime/src/hecate_runtime/port_decision_responder.py`(+`for_turn_messages` 类方法)、`packages/hecate-runtime/tests/test_agent_turn_feedback_projection.py`(新增)、本 change artifacts。未修改任何既有测试或主 specs 之外的行为代码。

## GLM 实现阶段记录（独立复核前）

| 检查 | 命令 | 结果 |
|---|---|---|
| 新投影测试 | `pytest packages/hecate-runtime/tests/test_agent_turn_feedback_projection.py -q` | ✅ 20 passed |
| runtime 包全量回归 | `pytest packages/hecate-runtime/tests -q` | ✅ 284 passed(264 既有 + 20 新增),0 failed |
| 共享检查 | `bash scripts/dev.sh check all` | ✅ ruff check 通过(修正 2 处 B905 strict zip、1 处未用导入后)、ruff format 1619 文件、mypy 962 源文件 0 问题 |
| OpenSpec | `openspec validate step6-agent-turn-feedback-projection --strict` | ✅ valid |
| 规划数据 | `python scripts/feature_inventory.py check` | ✅ 0 errors;189 warnings 为既有登记债,本 change 未触及 feature YAML |

既有断言零修改。实现过程中的修正均为新代码 lint/类型问题(B905 显式 `strict=`、mypy 对 `result["content"]` 的索引收窄改为校验循环内收集 `governed_contents`),无行为影响。

## 覆盖说明(delta 场景 ↔ 测试)

- **多动作配对**:`test_multi_action_turn_projects_paired_messages`——assistant `tool_calls` 顺序/参数与派生 `tool_call_id` 一一对应,两条工具结果同序。
- **只使用治理后结果**:`test_governed_placeholder_is_projected_verbatim`——BLOCK 占位原样投影,无原始内容。
- **显式拒绝矩阵**:`test_final_answer_turn_is_rejected`、`test_non_succeeded_action_is_rejected[failed|withheld|stopped]`、`test_partially_dispatched_turn_is_rejected`、`test_outcome_count_mismatch_is_rejected`、`test_execution_id_mismatch_is_rejected`、`test_out_of_order_outcomes_are_rejected`、`test_embedded_tool_call_id_mismatch_is_rejected`、`test_malformed_result_shape_is_rejected`、`test_duplicate_derived_call_ids_are_rejected`——逐项 `DecisionValidationError` 且无消息产出。
- **不可变**:`test_projection_does_not_mutate_inputs`——篡改投影输出后重投影,原 outcome/record/base 逐字段一致。
- **v1 回退**:`test_v1_record_without_text_falls_back_to_empty_content`。
- **恢复差异**:`test_recovered_turn_projects_actual_governed_content`——resumed 回合保留其实际治理内容,不做逐字比较。
- **续接入口**:`test_continuation_entry_freezes_projected_messages`(构造后篡改调用方列表,模型收不到注入)、`test_continuation_entry_rejects_mismatched_refs_without_model_call`(`model_port.calls == 0`)、`test_existing_constructor_unchanged`。
- **双回合串联**:`test_two_chained_turns_deliver_projected_results_to_second_call`——第一回合真实 `AgentTurn` 跑完两动作(业务调用 2 次),投影经 `for_turn_messages` 构造第二回合,断言第二次模型调用收到 assistant `tool_calls` + 两条工具结果(内容为治理后结果、`tool_call_id` 匹配),第二回合 final answer 且业务调用仍为 2 次。

## 验证范围与保留限制

1. **未复跑**:全仓 pytest(本地门禁不含,CI 为准)、真实模型/真实 PostgreSQL;全部使用确定性 stub,与 runtime 包既有测试同级。
2. **锁内/等待语义不适用**:本切片不含循环推进与等待唤醒,未涉及;`AgentTurn` 自身恢复语义(lookup 命中零模型调用)由既有 `test_accepted_agent_turn_runtime.py` 回归承载,未改动。
3. **`for_turn_messages` 为冻结转发入口**:不叠加消息拼接逻辑,宿主自行组装续接输入;这是 design 固定的最小入口,自动循环属于后续切片。

## 独立复核与修正

复核开始时 HEAD 与 fetch 后的 origin/main 均为 `b8fa439f`，原专项独立复跑为 20 passed。

### 已修正

- 投影原先只校验动作结果身份，未验证接受标记、内容摘要及版本/动作形态；已复用 `snapshot_decision` 校验并核对提交标记和摘要。未提交、被修改或未知版本记录均拒绝。
- 生产 RuntimePort 将 messages 原样交给 LLMService/provider；已安装 OpenAI SDK 的 function tool call 输入契约要求 arguments 为 str。已把模型消息 arguments 改为 canonical JSON 字符串，原决策 dict 不变。单元 model port 也检查实际收到的历史调用参数格式。
- succeeded 但结果带 is_error 的矛盾记录原先会丢弃错误标记；现显式拒绝。基础消息同时校验为有限 JSON 对象列表。

新增复现用例修正前为 10 failed、20 passed；原测试夹具改为携带真实接受标记和正确摘要。既有已跟踪测试未修改。

### 专项验证

```text
.venv/Scripts/python.exe -m pytest packages/hecate-runtime/tests/test_agent_turn_feedback_projection.py -q
35 passed
```

新增实际恢复链路用例通过 AgentTurn、决策存储和 Action Ledger 取得恢复结果，不仅手工设置 resumed：验证原决策不变、恢复模型零调用、工具和认领不增加；BLOCK/SANITIZE 的结果进入下一回合，模型收到的每个 tool_call_id/参数/结果保持配对。另覆盖空结果、Unicode 与额外 raw_result 元数据不进入模型消息。

### 最终扩展验证

```text
.venv/Scripts/python.exe -m pytest packages/hecate-runtime/tests packages/hecate-durable/tests tests/test_core/test_port_decision_responder_adapter.py tests/test_runtime/test_runtime_self_sufficiency.py tests/test_runtime/test_kernel_purity.py tests/test_layering_domain.py -q -ra
508 passed, 7 skipped in 110.17s
```

该结果包含专项 35 项，不将两者相加。7 项跳过均为 PostgreSQL 条件不满足：accepted_decision_pg 的 6 项参数化场景未配置 DURABLE_TEST_POSTGRES_URL，worker_dispatch 的 1 项要求 PostgreSQL 并发 writer 事务；跳过不计为通过。

```text
.venv/Scripts/python.exe scripts/dev.py check all
ruff check: passed
ruff format --check: 1619 files already formatted
mypy: Success: no issues found in 962 source files

openspec validate step6-agent-turn-feedback-projection --strict
valid

.venv/Scripts/python.exe scripts/feature_inventory.py check
check ok: 413 entries, 0 errors, 189 warnings

git diff --check
passed
```

规划警告为现有治理字段/证据/验收登记缺项；本轮未修改 feature YAML。修正只位于本 change 的新投影模块、新测试和 artifacts，保留 GLM 的 responder 显式构造入口，未修改既有已跟踪测试、主 specs、durable 实现、平台入口或其他 worktree。

实际恢复集成测试使用存活的内存决策存储/账本与新 responder/executor 实例；不将此证据宣称为真实进程死亡、真实模型或 PostgreSQL 恢复认证。全仓 CI 未运行。未提交、未推送、未归档。
