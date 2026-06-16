## MODIFIED Requirements

### Requirement: Knowledge retrieval in conversation flow
The system SHALL accept optional `kb_ids` in `ConversationService.chat()`. When provided, it SHALL retrieve relevant chunks via `KnowledgeBaseService.search()` for each KB in parallel, aggregate results globally sorted by score, and pass the top-k chunks to the context assembler. The knowledge retrieval step SHALL be graceful — errors SHALL be logged and the conversation SHALL proceed without citations.

#### Scenario: Chat with knowledge bases
- **WHEN** `kb_ids` is provided to `ConversationService.chat()`
- **THEN** the service SHALL call `knowledge_base_service.search()` for each KB in parallel, aggregate results globally, sort by score descending, and pass the top-k (default 5) chunks to `ContextAssembler.assemble(knowledge=...)`

#### Scenario: Chat without knowledge bases
- **WHEN** `kb_ids` is not provided or is empty
- **THEN** the conversation SHALL proceed as before with no knowledge retrieval or citation attachment

#### Scenario: Knowledge retrieval failure
- **WHEN** `knowledge_base_service.search()` raises an exception for a KB
- **THEN** the service SHALL log the error, skip that KB, and continue with remaining KBs or no citations
