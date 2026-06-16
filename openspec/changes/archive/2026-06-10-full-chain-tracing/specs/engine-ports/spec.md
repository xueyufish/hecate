## MODIFIED Requirements

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
