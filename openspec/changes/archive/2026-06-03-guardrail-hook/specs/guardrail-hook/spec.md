## ADDED Requirements — 新增需求

### Requirement：4 个 Guardrail Hook ABC 用于 LLM/工具生命周期 — 4 个 Guardrail Hook ABC 用于 LLM/工具生命周期
引擎 SHALL 在 `engine/guardrail.py` 中定义 4 个 Hook ABC：`PreLLMHook`（on_pre_llm_call）、`PostLLMHook`（on_post_llm_call）、`PreToolHook`（on_pre_tool_call）、`PostToolHook`（on_post_tool_call）。

#### Scenario：PreLLMHook 在 LLM 调用之前运行
- **WHEN** 在 引擎中调用 `on_pre_llm_call(invocation)`
- **THEN** 它 SHALL 能够修改 invocation 或将 blocked 设置为 True

#### Scenario：PostLLMHook 在 LLM 调用之后运行
- **WHEN** 在 引擎中调用 `on_post_llm_call(result)`
- **THEN** 它 SHALL 接收 LLMResult 进行审计/监控

#### Scenario：PreToolHook 在工具执行之前运行
- **WHEN** 在 引擎中调用 `on_pre_tool_call(call)`
- **THEN** 它 SHALL 能够修改 ToolCall 或将 blocked 设置为 True

#### Scenario：PostToolHook 在工具执行之后运行
- **WHEN** 在 引擎中调用 `on_post_tool_call(result)`
- **THEN** 它 SHALL 接收 ToolResult 进行审计/监控

### Requirement：GuardrailRegistry 管理多个 hook — GuardrailRegistry 管理多个 hook
`GuardrailRegistry` SHALL 持有 4 个单独的 hook 列表（pre_llm、post_llm、pre_tool、post_tool），每个类型有 add_* 方法，以及运行所有注册 hook 的 run_* 方法。

#### Scenario：注册和运行多个 pre LLM hooks
- **WHEN** 两个 PreLLMHook 实例被 add_pre_llm 添加到 registry
- **THEN** `run_pre_llm(invocation)` SHALL 按注册顺序调用两个 hook

#### Scenario：Pre hook 阻止执行
- **WHEN** `run_pre_llm(invocation)` 被调用且一个 hook 将 `invocation.blocked` 设置为 True
- **THEN** 它 SHALL 返回 `True`（表示阻止）

### Requirement：NoOp hook 保留当前行为 — NoOp hook 保留当前行为
引擎 SHALL 为每个 Hook ABC 提供 NoOp 实现，不修改输入且不阻止执行。

#### Scenario：NoOpPreLLMHook 不修改
- **WHEN** 在 NoOpPreLLMHook 上调用 `on_pre_llm_call(invocation)`
- **THEN** invocation 未被修改，blocked 保持 False

### Requirement：LLMWorker 集成 — LLMWorker 集成
`LLMWorker` SHALL 在其构造函数中接受一个可选的 `GuardrailRegistry`。在 4 个点 SHALL 调用 guardrail：在 llm_invoke 之前和之后，以及在 tool_execute 之前和之后。

#### Scenario：未配置 guardrails
- **WHEN** 未提供 GuardrailRegistry 构建 LLMWorker
- **THEN** 执行与当前实现相同（不调用 hook）