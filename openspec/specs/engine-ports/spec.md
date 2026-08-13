## Purpose
Define the EnginePort abstract interface that decouples the execution engine from external capability services (LLM providers, tool runners, knowledge bases, checkpoint storage, conversation history).
## Requirements
### Requirement: EnginePort abstract interface decouples engine from services
The `EnginePort` ABC SHALL define 7 abstract methods and 6 optional methods that the engine calls for all I/O, with no imports from the services layer. The 2 new optional methods `create_span` and `end_span` enable the engine layer to create observability spans without importing OpenTelemetry directly.

#### Scenario: LLM invocation
- **WHEN** `llm_invoke(messages, config)` is called
- **THEN** it SHALL return an `AsyncGenerator[str, None]` yielding tokens

#### Scenario: Tool execution routes through ToolRegistry
- **WHEN** `tool_execute(name, args, context)` is called
- **THEN** it SHALL route the call through ToolRegistry, which resolves the tool by name and source type, executes it via the appropriate executor, and returns the tool's result

#### Scenario: Tool execution via registry
- **WHEN** `tool_execute("web_search", {"query": "test"}, context)` is called
- **THEN** the adapter SHALL delegate to `ToolRegistry.execute("web_search", {"query": "test"}, context)` and return the registry's result

#### Scenario: Tool not found
- **WHEN** `tool_execute("nonexistent", args, context)` is called and the tool does not exist
- **THEN** it SHALL raise `ValueError` with message indicating the tool was not found

#### Scenario: Knowledge query
- **WHEN** `knowledge_query(query, kb_ids)` is called
- **THEN** it SHALL return a list of document chunk dicts with content and metadata

#### Scenario: Checkpoint save
- **WHEN** `checkpoint_save(state)` is called
- **THEN** it SHALL persist the state and return a UUID checkpoint ID

#### Scenario: Checkpoint load
- **WHEN** `checkpoint_load(checkpoint_id)` is called
- **THEN** it SHALL return the checkpoint's state dict

#### Scenario: Conversation load
- **WHEN** `conversation_load(session_id)` is called
- **THEN** it SHALL return a list of message dicts in chronological order

#### Scenario: Conversation save
- **WHEN** `conversation_save(session_id, messages)` is called
- **THEN** it SHALL persist the message list for the session

#### Scenario: Create a span for observability
- **WHEN** `create_span(name="llm_call", attributes={"model": "gpt-4o"})` is called
- **THEN** it SHALL return a `SpanContext` dataclass with `span_id`, `trace_id`, and `parent_id` fields, or `None` if tracing is disabled

#### Scenario: Create a span with explicit parent
- **WHEN** `create_span(name="tool_call", parent_id=<parent_span_id>, attributes={"tool": "search"})` is called
- **THEN** it SHALL create a child span under the specified parent and return its `SpanContext`

#### Scenario: End a span with output and usage
- **WHEN** `end_span(span_id=<id>, output_data={"result": "ok"}, usage={"input_tokens": 50})` is called
- **THEN** it SHALL finalize the span with the provided output and usage data

#### Scenario: Create span when tracing is disabled
- **WHEN** `create_span(name="llm_call")` is called and no trace context exists
- **THEN** it SHALL return `None` without raising an exception

#### Scenario: End span that does not exist
- **WHEN** `end_span(span_id=<unknown_id>)` is called
- **THEN** it SHALL return without raising an exception (no-op)

### Requirement: Optional context_assemble method for Context Engineering
The `context_assemble()` method SHALL default to pass-through, returning messages and tools unchanged.

#### Scenario: Default pass-through
- **WHEN** a concrete EnginePort does not override `context_assemble()`
- **THEN** it SHALL return `{"messages": messages, "tools": tools, "metadata": {}}`

### Requirement: Optional agent_execute method for Multi-Agent
The `agent_execute()` method SHALL accept an optional `agent_definition: AgentDefinition | None = None` parameter. When provided, the execution SHALL use the AgentDefinition's overrides (tool filter, context mode, model override, max_turns) instead of the agent's defaults.

#### Scenario: Agent execution without definition (existing behavior)
- **WHEN** `agent_execute(agent_id=UUID("..."), messages=[...], channel_snapshot={})` is called without agent_definition
- **THEN** the execution SHALL proceed using the agent's configured tools, prompt, model, and context (existing behavior unchanged)

#### Scenario: Agent execution with definition override
- **WHEN** `agent_execute(agent_id=UUID("..."), messages=[...], channel_snapshot={}, agent_definition=AgentDefinition(agent_id=UUID("..."), tools=["web_search"], context_mode="isolated"))` is called
- **THEN** the execution SHALL use only `["web_search"]` as the tool list, create an isolated message context, and use the agent's configured model

#### Scenario: Unimplemented agent execution
- **WHEN** a concrete EnginePort does not override `agent_execute()`
- **THEN** calling it SHALL raise NotImplementedError with message "agent_execute requires a concrete EnginePort adapter"

### Requirement: Optional tool_execute_sandbox method
The `tool_execute_sandbox()` method SHALL default to delegating to `tool_execute()`.

#### Scenario: Sandbox fallback
- **WHEN** a concrete EnginePort does not override `tool_execute_sandbox()`
- **THEN** it SHALL fall back to calling `self.tool_execute(name, args, context)`

### Requirement: Optional evidence_query method
The `evidence_query()` method SHALL default to returning an empty list.

#### Scenario: No evidence adapter
- **WHEN** a concrete EnginePort does not override `evidence_query()`
- **THEN** it SHALL return `[]`

### Requirement: Agent execution loads skills into system prompt
When `agent_execute()` is called for a sub-agent, the system SHALL load the agent's skills via `SkillLoader` and inject the formatted XML block into the system message alongside the agent's persona.

#### Scenario: Sub-agent with persona and skills
- **WHEN** `agent_execute(agent_id, messages, channel_snapshot)` is called for an agent with `persona="Expert coder"` and `skills=["code-review"]`
- **THEN** the system message SHALL be `"Expert coder\n\n<skills>\n<skill name=\"code-review\">\n...\n</skill>\n</skills>"` followed by the conversation messages

#### Scenario: Sub-agent with no skills
- **WHEN** `agent_execute()` is called for an agent with `skills=[]`
- **THEN** the system message SHALL be the agent's persona only, unchanged from current behavior

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

