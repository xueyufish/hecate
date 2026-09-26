# Tasks: chat-loop-engine-convergence

## 1. 开关与装配对齐

- [x] 1.1 `core/config.py`：新增 `CHAT_TOOL_LOOP_ENGINE_ENABLED: bool = False`（注释说明渐进迁移语义）。验证：默认值断言。
- [x] 1.2 `studio/workflows/execution_service.py`：`__init__` 与 `_create_composite_worker` 支持下传 `middleware_chains` / `denial_tracker` / ToolWorker `event_store`。验证：构造单测——bundle 参数到达 ToolWorker。
- [x] 1.3 B1a 回执增补：TOOL_RESULT payload 增加 `result_digest`（结果摘要，供恢复回填校验）；`get_tool_receipt` 透出。验证：回执单测更新。

## 2. 分叉翻转

- [x] 2.1 `channel/api/v1/chat.py`：`agent_tools` 分支改为受 `CHAT_TOOL_LOOP_ENGINE_ENABLED` 控制；开启时有工具 agent（含 enhanced 组合）落入引擎分支，`assemble_guardrails` bundle 传入 `WorkflowExecutionService`；修复 enhanced 分支丢 agent 工具。验证：开关两态路由单测。
- [x] 2.2 流式映射：SSE 层过滤中间迭代（按 D4——先写 StreamMode.MESSAGES 事件形状探针测试定取向），保持"只流式最终答案"。验证：流式形状测试（两轮工具调用仅最终文本下发）。

## 3. 恢复不重放

- [x] 3.1 `ToolWorker._execute_single_tool`：执行前按 `execution_id` 查回执——`succeeded` 跳过执行回填（占位摘要 + recovered 标记）；`unknown` 不执行并标记人工核对（事件载体实现时定）；无回执按 `should_auto_retry`。验证：回执三态单测。
- [x] 3.2 断线重连集成测试：会话执行中途中断（工具已成功、结果未落通道）→ 恢复 → 断言该工具未二次执行（stub 调用计数）、通道有回填结果。验证：全绿。

## 4. 一致性与回归

- [x] 4.1 三入口一致性矩阵：同一 agent（stub LLM + stub 工具）经 HTTP / MCP `agent_chat` / `execution_service` 直调，断言 TOOL_CALL/TOOL_RESULT 序列与审批记录一致。验证：全绿。
- [x] 4.2 开关关闭回归：既有 chat 测试全量（直连路径行为不变）；guardrail 测试集双路径（直连/引擎）跑同一断言集。验证：全绿。
- [x] 4.3 门禁：`ruff check`、`ruff format --check`、`mypy src/`、`pytest tests/test_api tests/test_runtime tests/test_services -q`。验证：0 错误。
- [x] 4.4 `openspec validate chat-loop-engine-convergence` 通过；`.env.example` 补开关注释。
