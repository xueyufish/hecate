# engine-tool-loop

## Why

PregelRuntime 的 chat 路径（`build_chat_graph`）虽然已经定义了完整的工具循环图（`llm → check_tools → tool_call → llm`），但引擎层从未真正接通工具调用：`LLMWorker` 的 tool-call 检测是 `placeholder`（`response_dict.get("tool_calls")` 永远为 None），且 LLM 节点 config 从不携带 `tools`。结果是 chat 模式的 LLM 永远收不到工具定义，check_tools 永远走 false 分支，ToolWorker 在 Pregel 路径永远不会执行——引擎层的图结构是"空转"的。

## What Changes

- **BREAKING**：扩展 `EnginePort.llm_invoke` 的返回语义，使 LLM 响应可以携带结构化 `tool_calls`（与现有 token 流兼容）。
- `build_chat_graph()` 的 LLM 节点 config 注入 `tools`（来自 agent 的可用工具列表）。
- `LLMWorker` 移除 placeholder 检测，实现真实的 tool-call 检测：从结构化响应中解析 `tool_calls`，设置 `_has_tool_call` 通道，并把 assistant tool_calls 消息写入 `messages` 通道。
- `EnginePortAdapter.llm_invoke` 透传 `LLMService.chat_stream` 已返回的 `tool_calls`（当前只透传 `content`）。
- 新增/更新测试：LLMWorker 工具调用检测、chat 图端到端工具循环（含流式）、`_has_tool_call` 通道断言。

## Capabilities

### New Capabilities
- `engine-tool-loop`: 引擎层 chat 路径的工具调用循环（LLM 结构化工具调用解析 → check_tools 条件路由 → ToolWorker 执行 → 结果回注消息流）

### Modified Capabilities
- `engine-ports`: `EnginePort` 新增可选方法 `llm_invoke_structured`，使 LLM 调用可以返回结构化 `tool_calls`（遵循既有可选方法模式，保持 `llm_invoke` 的 token 流向后兼容）

## Impact

- `src/hecate/engine/ports.py` — `llm_invoke` 签名/文档变更
- `src/hecate/engine/workers/llm_worker.py` — placeholder 检测替换为真实解析
- `src/hecate/engine/templates.py` — `build_chat_graph` LLM 节点注入 tools
- `src/hecate/services/orchestration/engine_port_adapter.py` — 透传 tool_calls
- 间接影响：`engine/routing.py`、`engine/task_allocator.py`（`llm_invoke` 的其他调用方——仅当签名发生类型级变更时才需要适配，若保持 token 流兼容则无影响）
- 测试：`tests/test_engine/test_workers/test_llm_worker.py`、`tests/test_engine/test_templates.py`、相关集成测试