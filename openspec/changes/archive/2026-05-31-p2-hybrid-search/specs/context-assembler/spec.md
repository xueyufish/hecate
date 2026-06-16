## MODIFIED Requirements

### Requirement: Context Assembler assembles context before LLM invocation
The system SHALL provide a `ContextAssembler` that accepts raw messages, tools, knowledge chunks, and session metadata, and returns an `AssembledContext` containing the final messages list, tool definitions, and metadata to pass to the LLM service. When the agent has associated knowledge bases, the assembler SHALL call `EnginePort.knowledge_query()` to retrieve relevant chunks and inject them into the context.

#### Scenario: Simple pass-through when context engineering is disabled
- **WHEN** context engineering is not enabled for the agent
- **THEN** the assembler SHALL return the original messages and tools unchanged

#### Scenario: Assembly with knowledge retrieval
- **WHEN** the agent has associated knowledge bases and a knowledge query is provided
- **THEN** the assembler SHALL call `EnginePort.knowledge_query(query, kb_ids)` and include the returned chunks in the assembled context metadata

#### Scenario: Knowledge retrieval failure
- **WHEN** `EnginePort.knowledge_query()` raises an exception
- **THEN** the assembler SHALL log the error and continue assembly without knowledge chunks (graceful degradation)
