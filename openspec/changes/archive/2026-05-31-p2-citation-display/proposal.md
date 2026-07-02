## Why

When agents answer questions using knowledge base content, users cannot see which source documents were used or verify the response. This undermines trust — especially in enterprise settings where traceability is critical. The RAG pipeline already retrieves scored chunks via hybrid search, but citations are discarded before reaching the API response.

## What Changes

- **Add citation data model** — A `Citation` schema that captures chunk provenance (kb_id, kb_name, document_name, chunk_id, score, content_snippet, position). Follows OpenAI's `annotations` pattern.
- **Inject RAG context into conversation flow** — Modify `ConversationService.chat()` to accept optional `kb_ids`, retrieve knowledge via `KnowledgeBaseService`, and pass chunks to `ContextAssembler` with citation markers.
- **Format LLM prompt with numbered references** — Inject retrieved chunks into the system/user message as numbered context blocks (e.g., `[1] chunk text...`) so the LLM can cite them inline.
- **Attach citations to API responses** — Add `annotations` field to `ChatCompletionResponse` and `ChatCompletionChunk` (non-streaming and streaming). Citations are sent as structured metadata alongside the assistant message, following OpenAI's `url_citation` pattern adapted for knowledge base sources.
- **Persist citations in message metadata** — Store citations array in `MessageModel.metadata_` JSONB column for conversation history retrieval.
- **Yield citation SSE event** — In streaming mode, emit a dedicated `{"type": "citations", ...}` SSE event before the final `[DONE]` so the frontend can render citation panels.

## Capabilities

### New Capabilities
- `citation-display`: Citation data model, LLM prompt formatting with numbered references, citation attachment to API responses (REST + SSE), persistence in message metadata, and citation retrieval endpoint.

### Modified Capabilities
- `context-assembler`: Add `_inject_knowledge()` method to assemble numbered reference blocks from RAG chunks and inject them into the system message alongside citation position mapping.

## Impact

- **Services**: `conversation.py` — add `kb_ids` parameter and RAG retrieval step before context assembly.
- **Services**: `context/assembler.py` — add knowledge injection method.
- **API**: `api/v1/chat.py` — add `annotations` field to response models; accept optional `kb_ids` in request.
- **Models**: `message.py` — store citations in existing `metadata_` JSONB column (no schema change).
- **Engine**: `engine/ports.py` — `knowledge_query()` return type enriched with citation position metadata.
- **Frontend**: `web/` — consume `annotations` field in chat response to render citation panels.
