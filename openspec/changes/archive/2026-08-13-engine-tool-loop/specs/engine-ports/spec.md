## ADDED Requirements

### Requirement: llm_invoke_structured returns structured LLM responses with tool_calls
The `EnginePort` SHALL provide an optional `llm_invoke_structured` method that invokes the LLM and yields structured chunks capable of carrying `tool_calls`, enabling the execution engine to detect and execute tool calls in the Pregel chat graph. The method SHALL be optional: a default implementation SHALL delegate to `llm_invoke`, collect all tokens, and yield a single chunk with `tool_calls=None`. Chunks SHALL be dicts with `content` (str | None) and `tool_calls` (list[dict] | None) keys.

#### Scenario: Structured invocation with tool call result
- **WHEN** `llm_invoke_structured(messages=[...], config={"model": "gpt-4o", "tools": [...]})` is called on an EnginePort implementation that overrides the method
- **THEN** it SHALL yield content token chunks during streaming and a final chunk carrying the complete parsed `tool_calls` list when the LLM requested tools, or `tool_calls=None` when it did not

#### Scenario: Default implementation degrades to plain token stream
- **WHEN** `llm_invoke_structured(messages, config)` is called on an EnginePort implementation that does NOT override the method
- **THEN** it SHALL delegate to `llm_invoke`, concatenate all yielded tokens, and yield a single chunk `{"content": <full text>, "tool_calls": None}` without raising

#### Scenario: Structured invocation without tools config
- **WHEN** `llm_invoke_structured(messages=[...], config={"model": "gpt-4o"})` is called with no `tools` in config
- **THEN** it SHALL behave like a plain streaming LLM call, yielding content chunks with `tool_calls` always `None`

#### Scenario: Streaming chunks preserve token order
- **WHEN** `llm_invoke_structured` yields chunks during streaming
- **THEN** the concatenation of all `content` values in yield order SHALL equal the complete LLM response text

### Requirement: EnginePortAdapter accumulates streaming tool_calls deltas
The `EnginePortAdapter` SHALL override `llm_invoke_structured` to stream content tokens from `LLMService.chat_stream` and accumulate the per-chunk `tool_calls` deltas (LiteLLM streaming delta format, keyed by `index`) into complete tool call definitions before yielding the final result.

#### Scenario: Tool call arguments spread across multiple chunks
- **WHEN** `LLMService.chat_stream` yields multiple chunks where tool call arguments for the same `index` arrive incrementally (delta fragments)
- **THEN** `llm_invoke_structured` SHALL concatenate argument fragments per `index` and yield a final chunk with the fully assembled tool call (complete `function.name` and `function.arguments`)

#### Scenario: No tool calls in stream
- **WHEN** `LLMService.chat_stream` yields only content chunks with `tool_calls=None`
- **THEN** `llm_invoke_structured` SHALL yield content chunks and a final chunk with `tool_calls=None`

### Requirement: Chat graph LLM node receives tools from agent
The `build_chat_graph` graph template SHALL accept a `tools` parameter and inject it into the `llm` node config under the `tools` key, so `LLMWorker` receives tool definitions in `node_config.get("tools")`.

#### Scenario: Tools injected into LLM node config
- **WHEN** `build_chat_graph(model=..., system_prompt=..., tools=[{"type": "function", "function": {...}}])` is called
- **THEN** the returned GraphConfig SHALL contain an `llm` node whose config includes `"tools"` equal to the provided list

#### Scenario: No tools provided
- **WHEN** `build_chat_graph(model=..., system_prompt=...)` is called without `tools`
- **THEN** the `llm` node config SHALL either omit the `tools` key or set it to `None` without raising

### Requirement: LLMWorker detects tool calls from structured responses
The `LLMWorker` SHALL invoke `port.llm_invoke_structured` when tools are present in node config, parse the returned `tool_calls`, and when detected: set the `_has_tool_call` channel flag to `True` and write an assistant message carrying `tool_calls` into the `messages` channel, so the `check_tools` condition node routes execution to the `tool_call` node.

#### Scenario: LLM returns tool calls (non-streaming)
- **WHEN** the LLM's structured response contains a non-empty `tool_calls` list during non-streaming execution
- **THEN** `LLMWorker` SHALL return a WorkerResult whose `channel_updates` include `"_has_tool_call": True` and an assistant message with `role="assistant"`, `content` equal to the response text, and `tool_calls` equal to the parsed list

#### Scenario: LLM returns plain text (non-streaming)
- **WHEN** the LLM's structured response contains no `tool_calls`
- **THEN** `LLMWorker` SHALL return a WorkerResult with `"_has_tool_call"` unset (or `False`) and a plain assistant message with no `tool_calls` key

#### Scenario: LLM returns tool calls (streaming)
- **WHEN** the LLM's structured response contains `tool_calls` during streaming execution
- **THEN** `LLMWorker` SHALL yield content token chunks as usual and emit a final result carrying `_has_tool_call: True` and the parsed tool calls, without changing the SSE content chunk shape

#### Scenario: No tools configured
- **WHEN** node config has no `tools`
- **THEN** `LLMWorker` SHALL keep invoking `port.llm_invoke` (plain token stream) with existing behavior unchanged