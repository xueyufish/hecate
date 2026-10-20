# Proposal: step7-replayed-tool-result-governance

## Why

共享 `ToolCallExecutor` 在首次执行时先把原始结果镜像到持久 Action 台账(`_mirror_outcome`,tool_call_executor.py:1046),PostTool 输出治理(TOOL_RESULT 中间件链优先、legacy hook 兜底,:1065-1112)在其后才执行 BLOCK/SANITIZE;而三个成功回放出口——恢复预检(:695)、锁内复查(:877)、台账领取竞争失败后的回填(`_hook_claim`)——把台账落盘的 `result_content` 原样作为工具消息返回(`recovery_decision`,action_ledger.py:283)。因此首跑被治理屏蔽的内容,会在进程重启、消息丢失、跨回合恢复或并发回填时重新进入模型上下文。恢复安全属于演进方案 step7 治理闭环与 G2 的未闭合切片,也是多轮 Controller 安全消费工具结果的前置。

## What Changes

- 三个成功回放出口在返回台账回填内容前,统一复用与首次执行一致的 PostTool 输出处理(TOOL_RESULT 中间件链优先于 legacy hook):`ALLOW` 放行、`SANITIZE` 应用 `modified_data["result"]`、`BLOCK` 返回与首跑同构的治理占位消息。
- 回放只做本地输出治理:SHALL NOT 重新执行业务工具、不重复消费审批、不重新认领已成功动作;输出屏蔽/脱敏 SHALL NOT 把台账已记录的业务成功改写为执行失败或触发对账重跑。
- 输出治理在回放路径抛出异常时 fail closed:不返回原始内容,按既有 unknown/待对账语义停止;ownership、fencing、摘要冲突、store_unavailable 判定全部保持不变。
- 明确两类恢复来源的既有差异并保持:messages-channel 历史回填的内容本就是首跑返回的已治理内容,SHALL 保持不变;仅台账来源的内容需要回放治理。
- 保持账本先于 PostTool 落盘的既有顺序(回执持久化优先,`_mirror_outcome` "never undo the effect" 语义不变);台账原始内容与模型可见结果 SHALL 可区分,回放治理将账本记录的内容字符串作为输入,不假设其为原始结构化对象。

**范围外(不动)**:首跑治理顺序、`_log_recorded_content` 通道回退路径、事件存储恢复判定、评测模块、多轮 Controller、前端、共享 skill 文件。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `tool-recovery`:"恢复流程消费工具回执"补充台账回填的输出治理要求;"持久 Action 台账钩子关联 TOOL_CALL/TOOL_RESULT"补充台账原始内容与模型可见结果的区分。
- `guardrail-hook`:新增成功回放复用 PostTool 输出处理的要求(chain 优先、BLOCK/SANITIZE/异常语义、不重执行业务副作用)。

## Impact

- 代码:`packages/hecate-runtime/src/hecate_runtime/tool_call_executor.py`(三个回放出口 + 共享治理 helper);必要时小幅调整 `action_ledger.py` 的决策内容传递;不改 durable SQL schema、不改入口与 Worker 装配。
- 测试:新增 `packages/hecate-runtime/tests/test_replayed_result_governance.py` 与 `tests/test_runtime/test_replayed_result_governance_sql.py`;回归直接执行(`execute_call`/`execute_accepted_call`)、`test_tool_worker*` 与 AcceptedActionStep 相关测试,补 SQL 台账持久恢复验证。
- 规范:`openspec/specs/tool-recovery/spec.md`、`openspec/specs/guardrail-hook/spec.md` 经 delta 同步。
- 与在途工作无交集:`step10-evaluation-dataset-read-access-boundary`(评测 API/Dataset)与 `step10-run-report-query-state-hardening`(Run Report/前端)均不触及 hecate-runtime。
