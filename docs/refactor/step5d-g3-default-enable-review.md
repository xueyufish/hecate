# step5d G3 默认启用证据复核（g3-entry-evidence）

## 范围与结论

本记录对应演进计划 step5d 剩余项"G3 实际入口的多轮、拒绝、取消、断连/恢复及默认启用证据；复用现有开关和路由，旧循环限制须显式保留"。本轮只补证据与决策记录，不改路由解析、开关机制或入口装配。

**决策：全局默认 `CHAT_TOOL_LOOP_ENGINE_ENABLED` 维持关闭。**HTTP 入口的引擎路径证据已齐备（下表）；MCP `agent_chat` 与 workflow 入口的引擎路径真实入口证据仍缺，按 `unified-chat-execution` 规格"逐入口证据积累、缺证据不切默认"的约束，默认不翻转。翻转前置条件：逐入口真实证据齐备 + 本决策记录更新为翻转结论 + 放量记录就位。

## 逐入口证据清单

### HTTP 入口（`/v1/chat/completions`、`/v1/agents/{id}/chat/completions`）

| 证据项 | 测试 | 结论 |
|---|---|---|
| 多轮工具调用（非流式） | `test_chat_engine_g3_entry.py::test_real_http_non_streaming_multi_round_tool_call` | 引擎路径完成，事件含配对 TOOL_CALL/TOOL_RESULT |
| 多轮工具调用（流式，中间迭代静默） | `test_chat_engine_g3_entry.py::test_real_http_streaming_multi_round_silent_intermediates` | 仅最终答案流式下发 |
| 审批拒绝传递 | `test_chat_engine_g3_entry.py::test_real_http_approval_denial_surfaces` | 工具不执行，拒绝以工具消息闭环，APPROVAL_ASKED/DECIDED 落事件 |
| 确定性策略拒绝（非审批） | `test_chat_engine_g3_entry.py::test_engine_deterministic_policy_denial_does_not_terminate` | 工具不执行，拒绝以工具消息闭环且对话不终止，无审批事件对 |
| 断线/恢复 | `test_chat_engine_g3_entry.py::test_stream_disconnect_persists_state_and_resume_is_clean` | 服务端完成进行中轮，快照持久，恢复不重做副作用，配对完整 |
| 会话中取消（模型边界） | `test_chat_engine_g3_entry.py::test_engine_cancel_at_model_boundary_stops_and_resume_is_clean` | 边界取消检查生效，SSE 呈现 `execution_stopped`/`cancelled`，进行中轮恰好一次，恢复干净 |
| 跨请求多轮会话连续 | `test_chat_engine_g3_entry.py::test_engine_multi_turn_session_continuity` | 续轮恢复前轮工具环，亲缘保持 engine，前轮回执不重执行，配对跨轮完整 |
| 会话亲缘与放量覆盖 | `test_chat_engine_g3_entry.py::test_session_keeps_engine_path_after_override_flip` | 覆盖翻转后续轮不切换路径 |
| Task/Run 关联 | `test_chat_engine_g3_entry.py::test_http_entry_correlates_task_and_run` | 入口执行落 Task/Run |
| 回退路径钉住（开关关闭） | `test_chat_engine_g3_entry.py::test_flag_off_real_entry_keeps_direct_loop` | 直连循环响应语义不变，引擎事件日志零条目 |
| 停止语义（unknown/budget/iteration/结构化输出） | S12 场景（`test_s12_autonomous_multiturn.py`，双路径） | step5e 已交付，见机制矩阵与该 change 复核记录 |

### MCP `agent_chat` 入口

无引擎路径真实入口证据。`unified-chat-execution` 的"跨入口执行一致性"要求 HTTP/MCP/workflow 事件序列与审批记录一致；该入口的引擎路径证据是默认翻转的前置条件之一。

### Workflow 入口

无引擎路径真实入口证据。Workflow 执行模式与聊天引擎路径的关系由 step5e 的机制矩阵描述；其入口级证据同样缺位。

## 放量记录核对

workspace 覆盖变更写 `CHAT_ENGINE_ROLLOUT_CHANGED` 放量记录（workspace、操作者、旧值→新值）由 `entry_routing.record_rollout_change` 承担，规格场景"覆盖变更写入放量记录"与亲缘用例覆盖读取路径。本轮未新增放量机制。

## 旧循环限制显式保留

开关关闭（默认）时直连循环作为回退路径保留，其边界在证据与文档中成文：

- 直连循环不经引擎事件日志——`test_flag_off_real_entry_keeps_direct_loop` 断言零引擎事件；因此也不具备引擎路径的持久回放保证（重放身份回执依赖治理账本，恢复语义弱于引擎路径）。
- 直连循环的限制与两路径机制差异见 [execution-mechanism-matrix](../design/execution-mechanism-matrix.md)；停止语义与重放身份的双路径对齐证据见 S12 场景（`openspec/changes/archive/2026-10-12-builtin-autonomous-execution/`）。
- 规格场景"关态行为有真实入口钉住"防止该限制被默认翻转 silently 抹去。

## 本轮证据测试暴露的行为边界（未修复，如实登记）

1. **跨轮回执内容为对账标记**：引擎路径续轮恢复的前轮工具回执内容为 `[reconciliation required]`（运行时账本记录成功但结果内容不可用的既有 G2 边界），非真实结果内容。多轮连续性用例按"存在性"断言该回执，不断言内容。默认翻转评估时该边界需先行收敛或显式接受。
2. **ASGI 测试传输不投递中途断连**：取消证据在传输接缝注入断连信号（与 S12 取消场景同构），下游（入口接线、边界检查、SSE 终止渲染、恢复）全部真实。真实服务器上断连经 starlette 断连监听呈现，语义一致。
3. **真实模型维度不在本轮范围**：全部证据基于确定性桩；模型质量维度归 step5e 已登记的 skipped 真实轨。

## 验证

- `tests/test_channel/test_chat_engine_g3_entry.py`：10 passed（6 既有 + 4 新增）。
- 定向回归与门槛结果见 change 复核记录。
