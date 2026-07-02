## MODIFIED Requirements

### Requirement: EnginePort abstract interface decouples engine from services
The `EnginePort` ABC SHALL define 7 abstract methods and 4 optional methods that the engine calls for all I/O, with no imports from the services layer. Additionally, it SHALL expose an optional `event_store` property for event persistence.

#### Scenario: LLM invocation
- **WHEN** `llm_invoke(messages, config)` is called
- **THEN** it SHALL return an `AsyncGenerator[str, None]` yielding tokens

#### Scenario: Tool execution
- **WHEN** `tool_execute(name, args, context)` is called
- **THEN** it SHALL return the tool's result (type depends on the tool)

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

#### Scenario: Event store not configured
- **WHEN** a concrete EnginePort does not set `event_store`
- **THEN** the property SHALL return `None`

#### Scenario: Event store configured
- **WHEN** a concrete EnginePort sets `event_store` to an EventStore instance
- **THEN** `port.event_store` SHALL return that instance
