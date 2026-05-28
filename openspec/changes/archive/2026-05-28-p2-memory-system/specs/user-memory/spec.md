## ADDED Requirements

### Requirement: Extract facts from conversation
The system SHALL extract persistent facts from conversations using LLM tool calling.

#### Scenario: Extract user preference
- **WHEN** a user says "I prefer Python over JavaScript"
- **THEN** the system SHALL create a memory record with content "User prefers Python over JavaScript", type "semantic"

#### Scenario: Extract procedural knowledge
- **WHEN** a user describes a workflow "First check the logs, then restart the service"
- **THEN** the system SHALL create a memory record with type "procedural"

### Requirement: Store memory with embedding
The system SHALL store extracted memories with vector embeddings for semantic retrieval.

#### Scenario: Memory stored with embedding
- **WHEN** a memory is created
- **THEN** the system SHALL generate an embedding vector and store it with the memory

### Requirement: Retrieve relevant memories
The system SHALL retrieve relevant memories based on semantic similarity to the current query.

#### Scenario: Semantic search
- **WHEN** a user asks "What's my preferred language?"
- **THEN** the system SHALL retrieve memories about language preferences using vector similarity

#### Scenario: Scope filtering
- **WHEN** retrieving memories
- **THEN** the system SHALL filter by scope (user_id, agent_id, session_id as configured)

### Requirement: Memory importance scoring
The system SHALL assign importance scores to memories based on content and access patterns.

#### Scenario: Initial importance
- **WHEN** a memory is first created
- **THEN** the system SHALL assign an initial importance score based on content analysis

#### Scenario: Importance boost on access
- **WHEN** a memory is retrieved and used
- **THEN** the system SHALL increment its access_count and adjust importance

### Requirement: Memory CRUD API
The system SHALL provide CRUD endpoints for memories.

#### Scenario: Create memory
- **WHEN** a user sends POST /api/memory with content and scope
- **THEN** the system creates a memory with embedding and returns 201

#### Scenario: List memories
- **WHEN** a user sends GET /api/memory with scope filters
- **THEN** the system returns matching memories ordered by importance

#### Scenario: Delete memory
- **WHEN** a user sends DELETE /api/memory/{id}
- **THEN** the system deletes the memory and returns 204

### Requirement: Memory injection into context
The system SHALL inject relevant memories into the context before LLM calls.

#### Scenario: Memories added to context
- **WHEN** context is assembled for a conversation
- **THEN** the system SHALL retrieve top-K relevant memories and add them as a system message

#### Scenario: Token budget respected
- **WHEN** injecting memories
- **THEN** the system SHALL not exceed the allocated token budget for memories
