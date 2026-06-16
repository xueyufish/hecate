## ADDED Requirements

### Requirement: Citation data model
The system SHALL define a `Citation` schema with fields: `position` (int, 1-indexed), `kb_id` (UUID), `kb_name` (str), `document_name` (str), `chunk_id` (str), `score` (float), `content_snippet` (str, max 150 chars). Citations SHALL be serialized as OpenAI-compatible `annotations` on the assistant message with `type: "kb_citation"`.

#### Scenario: Citation serialization
- **WHEN** an assistant message has associated citations
- **THEN** the API response SHALL include an `annotations` array on the message object, where each entry has `type: "kb_citation"` and a `kb_citation` payload containing all citation fields

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

### Requirement: Numbered reference prompt injection
The system SHALL format retrieved knowledge chunks as numbered reference blocks (e.g., `[1] "chunk text..." (Source: filename)`) and inject them into the LLM prompt context. Each chunk SHALL be assigned a sequential position number starting from 1.

#### Scenario: Prompt with 3 retrieved chunks
- **WHEN** 3 chunks are retrieved from knowledge bases
- **THEN** the system SHALL format them as `[1] "text..." (Source: doc.pdf)\n[2] "text..." (Source: report.docx)\n[3] "text..." (Source: guide.md)` and prepend them to the system message or inject as a dedicated context block

#### Scenario: Chunk text truncation
- **WHEN** a chunk's content exceeds 500 characters
- **THEN** the system SHALL truncate it to 500 characters with an ellipsis suffix

### Requirement: Citations in REST API response
The system SHALL include an `annotations` field on `ChatCompletionResponse.choices[0].message` containing the `kb_citation` annotations for the response. Each annotation SHALL map to a retrieved chunk with its position, source metadata, and score.

#### Scenario: Non-streaming response with citations
- **WHEN** a non-streaming chat completion uses knowledge bases
- **THEN** the response SHALL contain `message.annotations` with one `kb_citation` entry per retrieved chunk, each including position, kb_id, kb_name, document_name, chunk_id, score, and content_snippet

### Requirement: Citations in SSE streaming
The system SHALL emit a dedicated `citations` SSE event after all content chunks but before the `[DONE]` sentinel. The event SHALL contain the full citations array.

#### Scenario: Streaming response with citations
- **WHEN** a streaming chat completion uses knowledge bases
- **THEN** the SSE stream SHALL include `data: {"type": "citations", "citations": [...]}\n\n` before `data: [DONE]\n\n`

#### Scenario: Streaming response without knowledge bases
- **WHEN** a streaming chat completion does not use knowledge bases
- **THEN** the SSE stream SHALL NOT include a `citations` event

### Requirement: Citation persistence
The system SHALL store the citations array in `MessageModel.metadata_["citations"]` when the assistant message is persisted. The stored citations SHALL include all fields from the `Citation` schema.

#### Scenario: Message stored with citations
- **WHEN** an assistant message is persisted after a RAG-enabled conversation
- **THEN** the message's `metadata_` column SHALL contain a `citations` key with the full citations array

### Requirement: Citation retrieval endpoint
The system SHALL provide `GET /api/messages/{id}/citations` that returns the citations array for a given message. If the message has no citations, it SHALL return an empty array.

#### Scenario: Retrieve citations for a message
- **WHEN** a client calls `GET /api/messages/{id}/citations` for a message with citations
- **THEN** the response SHALL be `{"citations": [...], "message_id": "uuid"}`

#### Scenario: Message not found
- **WHEN** a client calls `GET /api/messages/{id}/citations` for a non-existent message
- **THEN** the response SHALL be 404

### Requirement: Chat completion request accepts kb_ids
The `ChatCompletionRequest` model SHALL accept an optional `kb_ids` field (list of UUID strings). When provided, the endpoint SHALL forward it to `ConversationService.chat()`.

#### Scenario: Request with kb_ids
- **WHEN** a client sends `POST /v1/chat/completions` with `kb_ids: ["uuid1", "uuid2"]`
- **THEN** the endpoint SHALL pass these IDs to the conversation service for knowledge retrieval

#### Scenario: Request without kb_ids
- **WHEN** a client sends `POST /v1/chat/completions` without `kb_ids`
- **THEN** the endpoint SHALL proceed without knowledge retrieval (backward compatible)
