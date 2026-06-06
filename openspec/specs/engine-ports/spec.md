## ADDED Requirements

### Requirement: EnginePort abstract interface decouples engine from services
The `EnginePort` ABC SHALL define 7 abstract methods and 4 optional methods that the engine calls for all I/O, with no imports from the services layer.

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

### Requirement: Optional context_assemble method for Context Engineering
The `context_assemble()` method SHALL default to pass-through, returning messages and tools unchanged.

#### Scenario: Default pass-through
- **WHEN** a concrete EnginePort does not override `context_assemble()`
- **THEN** it SHALL return `{"messages": messages, "tools": tools, "metadata": {}}`

### Requirement: Optional agent_execute method for Multi-Agent
The `agent_execute()` method SHALL default to raising NotImplementedError.

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
