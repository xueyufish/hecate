## ADDED Requirements

### Requirement: KB ID validation on agent create/update
When creating or updating an agent with `knowledge_base_ids`, the system SHALL validate that every KB ID references an existing, non-deleted knowledge base. If any KB ID is invalid, the system SHALL reject the request with HTTP 400 and include a message listing the invalid IDs.

#### Scenario: Create agent with valid KB IDs
- **WHEN** a user creates an agent with `knowledge_base_ids: ["kb-uuid-1", "kb-uuid-2"]` where both KBs exist and are not deleted
- **THEN** the system SHALL accept the request and store the agent with the specified KB IDs

#### Scenario: Create agent with non-existent KB ID
- **WHEN** a user creates an agent with `knowledge_base_ids: ["kb-uuid-1", "non-existent-uuid"]`
- **THEN** the system SHALL reject the request with HTTP 400 and a message like `"Invalid knowledge_base_ids: non-existent-uuid not found"`

#### Scenario: Create agent with soft-deleted KB ID
- **WHEN** a user creates an agent with `knowledge_base_ids: ["deleted-kb-uuid"]` where the KB has `deleted_at` set
- **THEN** the system SHALL reject the request with HTTP 400 and a message indicating the KB is not found

#### Scenario: Update agent with empty KB list
- **WHEN** a user updates an agent with `knowledge_base_ids: []`
- **THEN** the system SHALL accept the request and clear the agent's KB associations

#### Scenario: Create agent without KB IDs
- **WHEN** a user creates an agent without specifying `knowledge_base_ids`
- **THEN** the system SHALL accept the request with default empty list `[]`

### Requirement: Cascade cleanup on KB deletion
When a knowledge base is soft-deleted, the system SHALL remove its ID from the `knowledge_base_ids` array of all agents that reference it. The cleanup SHALL be synchronous and complete before the delete response is returned.

#### Scenario: Delete KB referenced by multiple agents
- **WHEN** a knowledge base with ID `kb-uuid` is soft-deleted, and agents A and B both have `kb-uuid` in their `knowledge_base_ids`
- **THEN** both agents A and B SHALL have `kb-uuid` removed from their `knowledge_base_ids`, and the delete response SHALL reflect the completed cleanup

#### Scenario: Delete KB not referenced by any agent
- **WHEN** a knowledge base is soft-deleted and no agents reference it
- **THEN** the system SHALL complete the deletion without error

#### Scenario: Delete KB referenced by one agent
- **WHEN** a knowledge base is soft-deleted, and agent A has `[kb-1, kb-2, kb-deleted]` in its `knowledge_base_ids`
- **THEN** agent A's `knowledge_base_ids` SHALL become `[kb-1, kb-2]`

### Requirement: Reverse lookup — agents using a KB
The system SHALL provide a `GET /api/knowledge-bases/{id}/agents` endpoint that returns a paginated list of agents that reference the specified knowledge base in their `knowledge_base_ids`.

#### Scenario: Query agents for a KB
- **WHEN** a user requests `GET /api/knowledge-bases/{kb-id}/agents`
- **THEN** the system SHALL return `{"items": [...], "total": N}` where items are agents with `kb-id` in their `knowledge_base_ids`

#### Scenario: Query agents for a non-existent KB
- **WHEN** a user requests `GET /api/knowledge-bases/{non-existent-id}/agents`
- **THEN** the system SHALL return HTTP 404

#### Scenario: Paginated reverse lookup
- **WHEN** a user requests `GET /api/knowledge-bases/{kb-id}/agents?page=2&page_size=10`
- **THEN** the system SHALL return the second page of 10 agents, with a total count

### Requirement: Cross-KB search result aggregation
When searching across multiple knowledge bases, the system SHALL perform searches in parallel, aggregate all results, sort by score descending globally, and return the top-k results across all KBs.

#### Scenario: Search across 3 KBs
- **WHEN** an agent has 3 associated KBs and a user sends a message
- **THEN** the system SHALL search all 3 KBs in parallel, merge results, sort by score descending, and return the top 5 globally ranked chunks

#### Scenario: One KB search fails
- **WHEN** searching across 3 KBs and one search raises an exception
- **THEN** the system SHALL log the error for that KB, aggregate results from the remaining 2 KBs, and return the top 5 globally ranked chunks

### Requirement: Chat auto-loads agent KB IDs
The frontend chat page SHALL fetch the agent's configuration when a conversation is loaded, extract `knowledge_base_ids`, and pass them as `kb_ids` in every `/v1/chat/completions` request.

#### Scenario: Chat with agent having 2 KBs
- **WHEN** a user opens a conversation with agent A that has `knowledge_base_ids: ["kb-1", "kb-2"]`
- **THEN** every chat message SHALL include `kb_ids: ["kb-1", "kb-2"]` in the request, and the response SHALL include citations from both KBs

#### Scenario: Chat with agent having no KBs
- **WHEN** a user opens a conversation with agent A that has `knowledge_base_ids: []`
- **THEN** chat messages SHALL NOT include `kb_ids` and the response SHALL NOT include citations

### Requirement: Active KB indicators in chat UI
The chat page SHALL display badges or indicators showing which knowledge bases are active for the current conversation. Each indicator SHALL show the KB name.

#### Scenario: Chat with 2 active KBs
- **WHEN** a user chats with an agent associated with 2 KBs
- **THEN** the chat UI SHALL display 2 badges showing the KB names near the chat header or input area

#### Scenario: Chat with no KBs
- **WHEN** a user chats with an agent that has no KB associations
- **THEN** the chat UI SHALL NOT display any KB indicators
