## Why — 为什么

`services/orchestration/handoff.py` 模块在 2026-06 发布（P2 agent-communication-and-routing），但它是死代码：其函数（`inject_handoff_tools`、`is_handoff_tool_call`、`create_handoff_worker_result`）在 `src/hecate/` 中没有任何调用者。DSL 模式、解析器和编译器支持 `trigger="handoff"` 和 `trigger="dynamic_handoff"`，但没有 Worker 注入 `handoff_to_agent` 工具、检测 LLM 何时调用它、或返回 `Command(goto=...)`。Pregel 运行时已经支持 `Command(goto=...)`（pregel.py:502）— 接线只差一层。与此同时，行业框架（OpenAI Agents SDK、Google ADK、LangGraph、AutoGen）已趋同于 "handoff as special tool call" 加上结构化上下文传递，而 Hecate 两者都缺乏。

## What Changes — 变更内容

- **将 `handoff.py` 接入执行路径**：当调用 AGENT 节点有出站 handoff 边时，`AgentExecutionPort.agent_execute()` 注入 `handoff_to_agent` 工具；`AgentWorker` 从端口结果字典读取 handoff 信号，并返回 `WorkerResult(command=Command(goto=target))`。
- **为 handoff 添加 `context_mode`**：`inherited`（默认，完整历史 — 匹配 OpenAI Swarm）、`isolated`（新上下文 — 匹配 Claude Code 子 Agent）、`summarized`（折叠摘要 — 匹配 OpenAI `nest_handoff_history`）。与 `invocation_mode` 一起存储在节点配置中。
- **修复 handoff 上的工具调用配对**：当 LLM 调用 `handoff_to_agent` 时，生成的通道更新必须包含配对的 `AIMessage(tool_call)` 和合成的 `ToolMessage(ack)`，以便下一个 Agent 看到有效的对话历史（LangGraph 契约）。
- **每个目标的 handoff 工具描述**：每个注入的 handoff 工具携带目标 Agent 的 `description`/`handoff_description`，以便 LLM 可以准确路由（匹配 Agents SDK `handoff_description`、ADK `agent.description`、Agentforce `go_to_X description`）。
- **`PregelRuntime` 在 `execution_context` 中填充 `handoff_targets`**：Worker 需要知道哪些目标是有效的，而无需看到完整的编译图。运行时检查出边并将列表传递给 Worker。
- **可选的 `OnHandoffHook`**（如果范围扩大，推迟到后续）：一个守卫样式的钩子，在调用 handoff 时触发，启用副作用（遥测、预取、认证检查）。现有的 PreLLMHook/PostLLMHook 框架是模板。

## Capabilities — 能力

### 新能力
<!-- 无 — 此变更扩展现有能力。 -->

### 修改的能力
- `agent-handoff`：将现有的 handoff 模块接入执行路径，以便实际产生 `Command(goto=...)`。为下游 Agent 上下文工程添加 `context_mode`（inherited/isolated/summarized）。要求在 handoff 完成时进行 AIMessage+ToolMessage 配对。每个目标的 handoff 工具描述。

## Impact — 影响

- **`src/hecate/services/orchestration/agent_execution_port.py`** — 当 handoff 边存在时注入 `handoff_to_agent` 工具；检测 LLM 响应中的工具调用；在结果字典中返回 `handoff_to`；在 LLM 调用前应用 `context_mode` 过滤到消息。
- **`src/hecate/engine/workers/agent_worker.py`** — 从端口结果读取 `handoff_to`，转换为 `WorkerResult(command=Command(goto=...))`；在 channel_updates 中配对 `AIMessage` + `ToolMessage`。
- **`src/hecate/engine/pregel.py`** — 在 `_dispatch_node` / Worker 调用中，从出站 handoff/dynamic_handoff 边填充 `execution_context["handoff_targets"]`。
- **`src/hecate/engine/graph-dsl.schema.json`** — 在 AGENT 节点配置上添加可选的 `handoff` 对象：`{context_mode: "inherited"|"isolated"|"summarized", description?: string}`。
- **`src/hecate/services/orchestration/handoff.py`** — 扩展 `build_handoff_tool_schema` 以接受每个目标的描述；添加 `filter_messages_for_handoff(messages, context_mode)` 辅助函数。
- **`tests/test_services/test_orchestration/test_agent_execution_port.py`** — 添加 handoff 工具注入、handoff 检测、context_mode 过滤的测试。
- **`tests/test_engine/test_handoff.py`** — 添加集成风格测试，通过 Pregel 运行时端到端地执行 `AgentWorker` → `AgentExecutionPort` → `Command(goto=...)`。
- **`docs/design/engine-design.md`** — 记录 handoff 执行路径和三种 `context_mode` 策略。

没有破坏性 DSL 变更：没有 `handoff.context_mode` 的图继续使用默认的 `inherited` 工作。
