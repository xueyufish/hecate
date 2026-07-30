## ADDED Requirements — 新增需求

### Requirement: LLM Worker 调用 PreLLMHook 和 PostLLMHook — LLM Worker invokes PreLLMHook and PostLLMHook
`_LLMWorker` 应在 LLM 调用前调用 `PreLLMHook.on_pre_llm_call()`，在收到 LLM 响应后调用 `PostLLMHook.on_post_llm_call()`。如果任一 Hook 返回 `GuardrailResult(action=BLOCK)`，Worker 应跳过 LLM 调用（对于 pre-hook）或替换响应（对于 post-hook）并返回拒绝消息。

#### Scenario: PreLLMHook 阻止提示注入 — PreLLMHook blocks prompt injection
- **当** 一个 CONVERSATION 节点执行且 `PreLLMHook.on_pre_llm_call()` 返回 `GuardrailResult(action=BLOCK, reason="prompt injection detected")`
- **则** Worker 不应调用 LLM，而应在 channel_updates 中返回 `{"messages": [{"role": "assistant", "content": "I cannot process this request: prompt injection detected"}]}`

#### Scenario: PostLLMHook 阻止有害输出 — PostLLMHook blocks harmful output
- **当** LLM 响应且 `PostLLMHook.on_post_llm_call()` 返回 `GuardrailResult(action=BLOCK, reason="harmful content")`
- **则** Worker 应返回安全的替换消息而非原始 LLM 响应

#### Scenario: Hook 允许正常执行 — Hook allows normal execution
- **当** 两个 Hook 都返回 `GuardrailResult(action=ALLOW)`
- **则** Worker 应正常继续——LLM 调用和响应不变

### Requirement: Tool Worker 调用 PreToolHook 和 PostToolHook — Tool Worker invokes PreToolHook and PostToolHook
`_ToolWorker` 应在工具执行前调用 `PreToolHook.on_pre_tool_call()`，在工具执行后调用 `PostToolHook.on_post_tool_call()`。如果 PreToolHook 阻止，则不应执行该工具。

#### Scenario: PreToolHook 阻止危险工具 — PreToolHook blocks dangerous tool
- **当** 一个 TOOL_CALL 节点执行且 `PreToolHook.on_pre_tool_call(name="bash", args={...})` 返回 `GuardrailResult(action=BLOCK, reason="dangerous command")`
- **则** Worker 不应执行该工具，而应返回包含阻止原因的工具结果消息

#### Scenario: PostToolHook 验证工具结果 — PostToolHook validates tool result
- **当** 工具执行且 `PostToolHook.on_post_tool_call()` 返回 `GuardrailResult(action=BLOCK, reason="sensitive data")`
- **则** Worker 应返回经过净化的工具结果而非原始输出

### Requirement: WorkflowExecutionService 将 Hooks 注入 Workers — WorkflowExecutionService injects hooks into Workers
`WorkflowExecutionService` 应接受可选的 Guardrail Hooks 并在构造期间将它们传递给 Workers。当未提供时，应使用默认的 Hooks（NoOp）。

#### Scenario: 注入自定义 Hooks — Custom hooks injected
- **当** `execute(pre_llm_hook=my_hook, post_tool_hook=my_tool_hook)` 被调用
- **则** 服务创建的 Workers 应使用提供的 Hooks

#### Scenario: 未提供 Hooks — No hooks provided
- **当** 不带 Hook 参数调用 `execute()`
- **则** Workers 应使用 NoOp 默认值（全部调用允许）

### Requirement: 从 three_layer 模板中移除 Guard 节点 — Guard node removed from three_layer template
`build_three_layer_graph()` 函数不应包含 guard CONVERSATION 节点。图应从 planner 节点开始。Guard 保护由 Workers 内部的 Hooks 提供。

#### Scenario: 三层图以 planner 开始 — Three-layer graph starts at planner
- **当** `build_three_layer_graph()` 被调用
- **则** 图入口点应为 "planner"（而非 "guard"）
- **并且** 返回的 GraphConfig 中不应存在 id 为 "guard" 的节点
