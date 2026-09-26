# Proposal: chat-loop-engine-convergence

## Why

外部安全评审 P1 #9：`channel/api/v1/chat.py` 的直连工具循环（path-A，`_chat_with_tools` / `_stream_chat_with_tools`）绕过 Pregel 运行时，不产生引擎事件、没有检查点、也无法消费 B1a 的工具回执。分叉点在"agent 是否配置了工具"：有工具的 agent 永远走直连循环，无工具的才进引擎。由此产生四类分叉：

- **事件与审计**：path-A 的工具执行零引擎事件（无 TOOL_CALL/TOOL_RESULT 回执——B1a 的回执只在引擎 ToolWorker 生效），而 MCP 的 `agent_chat` 走 `WorkflowExecutionService`，同一路径上行为已分叉。
- **装配缺口**：引擎侧 ToolWorker 装配缺 `middleware_chains` / `denial_tracker` / `event_store`（直连路径经 `assemble_guardrails` 三者齐备）——即使迁入引擎，gating 也不对等。
- **存量 bug**：`use_enhanced` 分支（kb_ids/opening/suggestions）只传 `tools=request.tools`，agent 配置的工具在该分支丢失。
- **恢复语义**：path-A 无断线恢复；评审验收"断线重连不重新执行已完成的写操作"依赖引擎 resume + B1a 回执，二者 path-A 都不占。

## What Changes

- **功能开关** `CHAT_TOOL_LOOP_ENGINE_ENABLED`（默认 `false`）：开启时，配置了工具的 agent 的 chat 请求（流式与非流式）改走 `WorkflowExecutionService` → Pregel chat 图（`llm → check_tools → tool_call` 循环已存在，无新建子图）；关闭时保持直连循环（回退路径保留至开关稳定，删除另行决定）。
- **引擎装配对齐**：`WorkflowExecutionService` 接受并下传完整 guardrail bundle——`middleware_chains`、`denial_tracker`、`event_store` 进入引擎 ToolWorker，与直连路径 gating 等价。
- **流式语义保持**：迁移后仍"中间迭代静默执行、只流式最终答案"——chat.py 的 SSE 映射层按引擎事件过滤中间 LLM 迭代（现行为兼容）；审批保持直连路径的同步问询语义（引擎 interrupt 语义留作后续）。
- **断线重连消费回执**（B1a 消费方落地）：恢复执行时按 `execution_id` 查回执——`succeeded` 跳过执行并回填结果；`unknown` 不执行并标记人工核对。
- **顺带修复**：`use_enhanced` 分支丢 agent 工具的问题随分叉统一自然消除。

## Capabilities

### New Capabilities

- `unified-chat-execution`: 有工具 agent 的 chat 请求经统一运行时执行——开关语义、guardrail 装配等价、流式过滤语义、跨入口一致性、恢复不重放。

### Modified Capabilities

- `tool-recovery`: ADDED requirement——恢复/重连流程 SHALL 消费工具回执（`succeeded` 跳过执行回填结果，`unknown` 进入人工核对），补全其"恢复与重试判定 SHALL 依据回执"的消费侧。

## Impact

- **代码**：`core/config.py`（新开关）、`studio/workflows/execution_service.py`（bundle 参数 + ToolWorker 装配补全）、`channel/api/v1/chat.py`（分叉翻转 + SSE 映射过滤中间迭代 + enhanced 分支工具修复）、恢复路径的回执查询接线（`check_tools`/ToolWorker 入口）。
- **行为**：开关开启时有工具 agent 获得引擎事件、回执、检查点；关闭时行为零变化。MCP 与 workflow 入口不变（本就经引擎）。
- **测试**：三入口一致性矩阵（工具事件/审批记录/结束原因）、流式中间迭代过滤、恢复不重放（succeeded/unknown）、开关关闭回归。
- **无数据库迁移**；无 BREAKING（默认关闭）。
