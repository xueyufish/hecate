## Context

Hecate's RAG pipeline retrieves scored document chunks via `HybridSearcher`, but citations are lost before reaching the API response. The `/v1/chat/completions` endpoint goes directly to `llm_service` without any knowledge retrieval. The `ConversationService` has a `ContextAssembler` that accepts a `knowledge` parameter but never passes it. Meanwhile, `MessageModel` already has a `metadata_` JSONB column suitable for storing citation arrays.

The industry has converged on OpenAI's `annotations` pattern — an array of structured citation objects on the assistant message — which is what we'll adopt.

## Goals / Non-Goals

**Goals:**
- When an agent has associated knowledge bases, retrieve relevant chunks and format them as numbered references in the LLM prompt
- Return structured citations (annotations) in both REST and SSE streaming responses
- Persist citations in message metadata for conversation history retrieval
- Provide a `GET /api/messages/{id}/citations` endpoint for citation lookup
- Maintain OpenAI API compatibility (annotations are an additive, non-breaking extension)

**Non-Goals:**
- Inline citation marker insertion by the LLM (we format the prompt with numbered references, but the LLM may or may not use `[1]` notation — we don't force it)
- Web search citation (only KB/RAG citations for now; the `annotations` field is extensible for future web citation types)
- Citation deduplication across multiple KBs (acceptable for P2)
- Frontend citation panel rendering (backend-only; frontend will be a separate change)

## Decisions

### D1: Follow OpenAI's `annotations` pattern

OpenAI returns `annotations` as an array on `ChatCompletionMessage`. Each annotation has a `type` (e.g., `url_citation`) and typed payload. We adopt this pattern with our own `kb_citation` type:

```json
{
  "type": "kb_citation",
  "kb_citation": {
    "position": 1,
    "kb_id": "uuid",
    "kb_name": "My Knowledge Base",
    "document_name": "report.pdf",
    "chunk_id": "abc123",
    "score": 0.92,
    "content_snippet": "First 150 chars of chunk..."
  }
}
```

**Rationale**: Industry standard, non-breaking (unknown fields are ignored by OpenAI clients), extensible for future citation types.

### D2: Inject knowledge at ConversationService level, not chat.py

The `/v1/chat/completions` endpoint currently bypasses `ConversationService` entirely. Instead of modifying the raw endpoint, we add an optional `kb_ids` parameter to `ConversationService.chat()` and handle knowledge retrieval there.

**Rationale**: Keeps `/v1/chat/completions` as a thin passthrough. The conversation service is the right place for orchestration logic. The endpoint just needs to accept and forward `kb_ids`.

### D3: Numbered reference prompt format

When knowledge chunks are retrieved, they are formatted as:

```
The following reference documents are available:
[1] "chunk text from document A..." (Source: report.pdf)
[2] "chunk text from document B..." (Source: manual.docx)
...
```

This is appended to the system message (or injected as a separate system message). The LLM naturally uses `[1]`, `[2]` notation when citing sources.

**Rationale**: Simple, effective, works with all LLM providers. No special prompting required.

### D4: SSE citation delivery via dedicated event

In streaming mode, citations are emitted as a separate SSE event after all content chunks but before `[DONE]`:

```
data: {"type": "citations", "citations": [...]}
data: [DONE]
```

**Rationale**: Citations are only available after the full response is generated (we know which chunks were retrieved, not which the LLM actually used). Sending them before `[DONE]` lets the frontend render the citation panel immediately.

### D5: No new database table — use metadata_ column

Citations are stored as a JSON array in `MessageModel.metadata_["citations"]`. No new table, no migration.

**Rationale**: `metadata_` is already a JSONB column. Citation data is read-heavy, write-once per message. No need for relational queries on individual citation fields.

## Risks / Trade-offs

- **Prompt token overhead**: Injecting 5-10 chunks into the system message adds ~500-2000 tokens. Mitigated by limiting chunk count (default 5) and truncating chunk text to 500 chars.
- **LLM may ignore references**: The LLM might not use `[1]` notation consistently. Citations are still returned as metadata regardless — the frontend can show "Sources used" without inline markers.
- **Streaming citation timing**: Citations are sent after content completes. If the SSE connection drops before `[DONE]`, citations are lost. Mitigated by the REST endpoint for citation retrieval (`GET /api/messages/{id}/citations`).
- **OpenAI client compatibility**: The `annotations` field is not recognized by older OpenAI SDK versions. This is non-breaking (unknown fields are silently ignored by most clients).
