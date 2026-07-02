## ADDED Requirements

### Requirement: LLMService provides model-agnostic chat via LiteLLM
The `LLMService` SHALL wrap LiteLLM with lazy import, supporting streaming/non-streaming responses, model fallback, and intelligent routing.

#### Scenario: Lazy import of litellm
- **WHEN** litellm is not installed
- **THEN** `_get_litellm()` SHALL raise ImportError with message "litellm is required for LLM service"

#### Scenario: Chat completion
- **WHEN** `chat(messages, model="gpt-4o")` is called
- **THEN** it SHALL return `LLMResponse` with content, tool_calls, model, usage, and finish_reason

#### Scenario: Streaming chat
- **WHEN** `chat_stream(messages, model="gpt-4o")` is called
- **THEN** it SHALL yield dict chunks with "content", "tool_calls", and "finish_reason" keys

#### Scenario: Model fallback on failure
- **WHEN** the primary model fails and `fallback_models` is configured
- **THEN** the service SHALL try each fallback model in order; if all fail, raise RuntimeError("All models failed")

#### Scenario: Model routing via ModelRouter
- **WHEN** no explicit model is provided but `routing_config` is given and `router` is configured
- **THEN** the service SHALL use `ModelRouter.select_model()` with the specified strategy

#### Scenario: Default model
- **WHEN** no model, routing_config, or fallback_models are provided
- **THEN** the service SHALL default to "gpt-4o"

### Requirement: Tool calling protocol with OpenAI format
The `tool_calling` module SHALL convert Hecate tool definitions to OpenAI function format, parse LLM tool call responses, and inject results into message history.

#### Scenario: Format tools for LLM
- **WHEN** `format_tools_for_llm(tools)` is called with Hecate tool definitions
- **THEN** it SHALL return `[{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]`

#### Scenario: Parse tool calls
- **WHEN** `parse_tool_calls(tool_calls)` is called with raw LLM tool calls
- **THEN** it SHALL return parsed list with id, name, and arguments (JSON-decoded)

#### Scenario: Create tool result message
- **WHEN** `create_tool_result_message(tool_call_id, result)` is called
- **THEN** it SHALL return `{"role": "tool", "tool_call_id": ..., "content": ...}`

#### Scenario: Inject tool results
- **WHEN** `inject_tool_results(messages, tool_calls, results)` is called
- **THEN** it SHALL append tool result messages and update the last assistant message with tool_calls if not already present
