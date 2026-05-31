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

### Requirement: Knowledge injection with citation position mapping
The `ContextAssembler` SHALL accept a `knowledge` parameter (list of chunk dicts) and inject formatted numbered reference blocks into the LLM context. Each chunk SHALL be assigned a sequential position number. The assembler SHALL return a `citation_map` in `AssembledContext.metadata` mapping position numbers to citation metadata.

#### Scenario: Knowledge chunks injected as numbered references
- **WHEN** `knowledge` parameter is provided with 3 chunks
- **THEN** the assembler SHALL format them as `[1] "text..." (Source: filename)\n[2] ...` and inject them into the system message, and return `metadata.citation_map` with `{1: {chunk_id, kb_id, ...}, 2: {...}, 3: {...}}`

#### Scenario: Knowledge parameter is None or empty
- **WHEN** `knowledge` parameter is None or empty list
- **THEN** the assembler SHALL proceed without injection (no change to messages)

#### Scenario: Chunk content exceeds 500 characters
- **WHEN** a knowledge chunk's content exceeds 500 characters
- **THEN** the assembler SHALL truncate the content to 500 characters with "..." suffix before injection

### Requirement: Context Assembler supports suggestion generation mode
The `ContextAssembler` SHALL accept a `suggestion_mode` parameter (default None). When set to `"opening"` or `"followup"`, the assembler SHALL build a suggestion-generation prompt instead of the standard chat context. The prompt SHALL include the agent persona and relevant conversation context formatted for structured question generation.

#### Scenario: Opening remarks suggestion mode
- **WHEN** `suggestion_mode="opening"` is provided with agent persona and capabilities
- **THEN** the assembler SHALL return an `AssembledContext` with a single system message containing the opening remarks prompt template and a user message with agent metadata

#### Scenario: Follow-up suggestion mode
- **WHEN** `suggestion_mode="followup"` is provided with conversation history and agent persona
- **THEN** the assembler SHALL return an `AssembledContext` with a system message containing the follow-up prompt template and messages containing the last 2 turns of conversation

#### Scenario: Default mode (no change)
- **WHEN** `suggestion_mode` is None
- **THEN** the assembler SHALL proceed with standard context assembly as before
