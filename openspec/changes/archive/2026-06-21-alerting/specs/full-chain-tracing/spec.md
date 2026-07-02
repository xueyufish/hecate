## MODIFIED Requirements

### Requirement: Span creation in PregelRuntime and Workers
PregelRuntime and Workers SHALL create spans at execution boundaries via `EnginePort.create_span` and `EnginePort.end_span`. LLMWorker SHALL additionally instrument time-to-first-token (TTFT) for streaming LLM calls by recording `ttft_ms` in the span's metadata.

#### Scenario: PregelRuntime creates node execution span
- **WHEN** PregelRuntime starts executing a node
- **THEN** it SHALL call `engine_port.create_span(name="node:{node_id}", attributes={"superstep": N})` and pass the returned `span_id` to the corresponding `_emit` call

#### Scenario: LLMWorker creates generation span
- **WHEN** LLMWorker calls `llm_invoke`
- **THEN** it SHALL create a span with `type="generation"`, `name="llm:{model}"`, and record `usage` (input_tokens, output_tokens) on span end

#### Scenario: LLMWorker records TTFT for streaming responses
- **WHEN** LLMWorker processes a streaming LLM response and receives the first chunk
- **THEN** it SHALL record `ttft_ms` (milliseconds from request start to first chunk arrival) in the span's `metadata` field

#### Scenario: ToolWorker creates tool span
- **WHEN** ToolWorker executes a tool
- **THEN** it SHALL create a span with `type="tool"`, `name="tool:{tool_name}"`, and record `output_data` on span end

#### Scenario: Security hook creates guardrail span
- **WHEN** a security hook scans input/output
- **THEN** it SHALL create a span with `type="span"`, `name="guardrail:{hook_name}"`, and record scan result in metadata
