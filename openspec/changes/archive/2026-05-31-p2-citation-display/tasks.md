## 1. Citation Data Model

- [x] 1.1 Create `Citation` Pydantic schema in `src/hecate/services/rag/types.py` (new file) — fields: `position: int`, `kb_id: UUID`, `kb_name: str`, `document_name: str`, `chunk_id: str`, `score: float`, `content_snippet: str = Field(max_length=150)`. Add `to_annotation()` method that returns `{"type": "kb_citation", "kb_citation": {...}}`.
- [x] 1.2 Create `KbCitationAnnotation` schema — wraps `Citation` in OpenAI-compatible annotation format. Add `CitationResponse` schema with `citations: list[Citation]` and `message_id: UUID`.

## 2. Context Assembler — Knowledge Injection

- [x] 2.1 Add `_inject_knowledge()` method to `ContextAssembler` in `src/hecate/services/context/assembler.py` — accepts `knowledge: list[dict]`, formats numbered reference blocks (`[1] "text..." (Source: filename)`), truncates chunks >500 chars, returns formatted text and `citation_map` dict mapping position → citation metadata.
- [x] 2.2 Update `ContextAssembler.assemble()` to call `_inject_knowledge()` when `knowledge` parameter is non-empty — prepend formatted text to system message or inject as dedicated context block; store `citation_map` in `AssembledContext.metadata`.

## 3. Conversation Service — Knowledge Retrieval

- [x] 3.1 Add `kb_ids: list[UUID] | None = None` parameter to `ConversationService.chat()` in `src/hecate/services/conversation.py`. When provided, call `knowledge_base_service.search()` for each KB's collection (lookup collection name from `KnowledgeBaseModel` via DB), aggregate results, sort by score, take top-5.
- [x] 3.2 Pass retrieved chunks to `assembler.assemble(knowledge=chunks)` — build citation list from the assembled `citation_map` and store it in the response dict as `citations`.
- [x] 3.3 In `_complete_chat()` — include `citations` in the returned response dict alongside `content`, `model`, `usage`.
- [x] 3.4 In `_stream_chat()` — yield `{"type": "citations", "citations": [...]}` event before the `{"type": "done"}` event when citations are available.

## 4. API Layer — Request & Response Models

- [x] 4.1 Add `kb_ids: list[str] | None = None` field to `ChatCompletionRequest` in `src/hecate/api/v1/chat.py`.
- [x] 4.2 Add `annotations: list[dict] | None = None` field to `ChatMessage` model for citation annotations on assistant messages.
- [x] 4.3 Update `create_chat_completion()` endpoint — when `kb_ids` is provided, validate UUIDs, convert to `list[UUID]`, and pass to a new conversation service integration path (currently the endpoint goes directly to `llm_service`; add conditional path through `ConversationService` when `kb_ids` is present).
- [x] 4.4 In non-streaming response path — attach `annotations` (from `response["citations"]` transformed via `to_annotation()`) to the assistant message in `ChatCompletionResponse`.
- [x] 4.5 In streaming response path — yield `data: {"type": "citations", "citations": [...]}\n\n` SSE event after all content chunks but before `data: [DONE]\n\n` when citations are available.

## 5. Citation Persistence & Retrieval

- [x] 5.1 In `ConversationService._complete_chat()` and `_stream_chat()` — when citations are present, include them in the response metadata so the caller can persist them in `MessageModel.metadata_["citations"]` when saving the message.
- [x] 5.2 Add `GET /api/messages/{id}/citations` endpoint in `src/hecate/api/management/messages.py` (or create if not exists) — look up message by ID, return `{"citations": metadata_.get("citations", []), "message_id": id}`. Return 404 if message not found.

## 6. Tests

- [x] 6.1 Write `tests/test_services/test_rag/test_citation_types.py` — test `Citation` schema creation, `to_annotation()` output format, validation (max_length on content_snippet).
- [x] 6.2 Write `tests/test_services/test_context/test_knowledge_injection.py` — test `ContextAssembler._inject_knowledge()` with 3 chunks, verify numbered format, truncation at 500 chars, citation_map output. Test with None and empty list (no-op).
- [x] 6.3 Write `tests/test_api/test_citation_chat.py` — test `POST /v1/chat/completions` with `kb_ids` (non-streaming) returns `annotations` on response; test streaming yields `citations` SSE event; test without `kb_ids` returns no annotations (backward compatible); test 404 for invalid KB ID.
- [x] 6.4 Write `tests/test_api/test_message_citations.py` — test `GET /api/messages/{id}/citations` returns citations for a message with citations; test empty array for message without citations; test 404 for non-existent message.
- [x] 6.5 Full validation: `ruff check src/hecate/ tests/` + `ruff format --check src/ tests/` + `mypy src/` + `pytest tests/ -q`
