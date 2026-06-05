## ADDED Requirements

### Requirement: LLM Worker invokes PreLLMHook and PostLLMHook
The `_LLMWorker` SHALL call `PreLLMHook.on_pre_llm_call()` before LLM invocation and `PostLLMHook.on_post_llm_call()` after receiving the LLM response. If either hook returns `GuardrailResult(action=BLOCK)`, the worker SHALL skip LLM invocation (for pre-hook) or replace the response (for post-hook) and return a rejection message.

#### Scenario: PreLLMHook blocks prompt injection
- **WHEN** a CONVERSATION node executes and `PreLLMHook.on_pre_llm_call()` returns `GuardrailResult(action=BLOCK, reason="prompt injection detected")`
- **THEN** the worker SHALL NOT invoke the LLM and SHALL return `{"messages": [{"role": "assistant", "content": "I cannot process this request: prompt injection detected"}]}` in channel_updates

#### Scenario: PostLLMHook blocks harmful output
- **WHEN** the LLM responds and `PostLLMHook.on_post_llm_call()` returns `GuardrailResult(action=BLOCK, reason="harmful content")`
- **THEN** the worker SHALL return a safe replacement message instead of the original LLM response

#### Scenario: Hook allows normal execution
- **WHEN** both hooks return `GuardrailResult(action=ALLOW)`
- **THEN** the worker SHALL proceed normally — LLM invocation and response are unchanged

### Requirement: Tool Worker invokes PreToolHook and PostToolHook
The `_ToolWorker` SHALL call `PreToolHook.on_pre_tool_call()` before tool execution and `PostToolHook.on_post_tool_call()` after tool execution. If PreToolHook blocks, the tool SHALL NOT execute.

#### Scenario: PreToolHook blocks dangerous tool
- **WHEN** a TOOL_CALL node executes and `PreToolHook.on_pre_tool_call(name="bash", args={...})` returns `GuardrailResult(action=BLOCK, reason="dangerous command")`
- **THEN** the worker SHALL NOT execute the tool and SHALL return a tool result message with the block reason

#### Scenario: PostToolHook validates tool result
- **WHEN** a tool executes and `PostToolHook.on_post_tool_call()` returns `GuardrailResult(action=BLOCK, reason="sensitive data")`
- **THEN** the worker SHALL return a sanitized tool result instead of the raw output

### Requirement: WorkflowExecutionService injects hooks into Workers
The `WorkflowExecutionService` SHALL accept optional Guardrail Hooks and pass them to Workers during construction. Default hooks (NoOp) SHALL be used when none are provided.

#### Scenario: Custom hooks injected
- **WHEN** `execute(pre_llm_hook=my_hook, post_tool_hook=my_tool_hook)` is called
- **THEN** the Workers created by the service SHALL use the provided hooks

#### Scenario: No hooks provided
- **WHEN** `execute()` is called without hook arguments
- **THEN** the Workers SHALL use NoOp defaults (all calls allowed)

### Requirement: Guard node removed from three_layer template
The `build_three_layer_graph()` function SHALL NOT include a guard CONVERSATION node. The graph SHALL start at the planner node. Guard protection is provided by Hooks inside Workers.

#### Scenario: Three-layer graph starts at planner
- **WHEN** `build_three_layer_graph()` is called
- **THEN** the graph entry point SHALL be "planner" (not "guard")
- **AND** no node with id "guard" SHALL exist in the returned GraphConfig
