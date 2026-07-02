## Context

Hecate already has three-layer memory service code but it is not wired into the conversation flow:

- **L1 WorkingMemoryService** (`services/memory/working_memory.py`): Named memory block CRUD, `inject_blocks()` method injects block content into messages
- **L2 CompressionPipeline** (`services/memory/compression.py`): Three-level compression (snip → microcompact → autocompact), `compress()` returns `CompressionResult`
- **L3 UserMemoryService** (`services/memory/user_memory.py`): User fact storage + vector retrieval, `store_memory()` / `retrieve_memories()` / `extract_facts()`
- **ContextAssembler** (`services/context/assembler.py`): Already has `memory_blocks` and `user_memories` parameters and injection logic
- **ConversationService** (`services/conversation.py`): Currently calls no memory services, purely stateless

Data models are ready: `MemoryBlockModel` (L1), `MemoryModel` (L3). API skeleton exists: `api/management/memory.py`.

## Goals / Non-Goals

**Goals:**
- ConversationService automatically injects L1 working memory into context every turn
- Automatic L2 compression trigger when conversation history exceeds threshold
- L3 user memory extraction after conversation completion / turn end
- Provide memory management REST API (CRUD blocks, view user memories, compression status)
- Frontend can view and edit working memory, browse user memories

**Non-Goals:**
- No cross-Agent memory sharing (P3 scope)
- No memory versioning or rollback
- No custom compression strategy configuration (use default thresholds)
- No memory import/export

## Decisions

### D1: Memory Injection Timing

**Decision**: Inject in `ContextAssembler.assemble()`, with `ConversationService` loading memories from DB before calling assemble.

**Rationale**: ContextAssembler already has `memory_blocks` and `user_memories` parameters, no need to modify the assembler itself. ConversationService is the single entry point for conversation orchestration, making it the right place for memory loading.

### D2: L2 Compression Trigger Strategy

**Decision**: Use token count threshold trigger. When `TokenCounter.count_messages(messages)` exceeds `compression_threshold` (default 4000 tokens), compress before injecting new messages.

**Rationale**: CompressionPipeline already implements complete three-level compression logic. Token-based triggering is more precise than message count. Threshold is configurable.

### D3: L3 User Memory Extraction Timing

**Decision**: After each Assistant turn, call `UserMemoryService.extract_facts()` to extract new facts, stored asynchronously. Does not block response.

**Rationale**: `extract_facts()` is already implemented (LLM-based key information extraction). Async avoids adding latency. Per-turn extraction ensures timely memory updates.

### D4: Memory API Routes

**Decision**: Reuse existing `api/management/memory.py`, extending it with:
- `GET /api/agents/{agent_id}/memory/blocks` — list working memory blocks
- `POST /api/agents/{agent_id}/memory/blocks` — create/update block
- `DELETE /api/agents/{agent_id}/memory/blocks/{block_id}` — delete block
- `GET /api/users/{user_id}/memories` — list user memories
- `GET /api/sessions/{session_id}/compression` — view compression status

**Rationale**: Memory blocks are bound to Agents (Agent config defines which blocks are needed), user memories are bound to users. Matches resource ownership relationship.

### D5: Frontend Memory Panel

**Decision**: Add a Memory tab on the Agent detail page, showing a working memory block list (editable) and user memory list (read-only).

**Rationale**: Unified entry point with Agent configurator, no separate page needed.

## Risks / Trade-offs

- **L3 extraction cost**: One extra LLM call per turn for fact extraction. Mitigation: only trigger when Assistant response contains personal information / preference content (can skip simple Q&A).
- **Compression information loss**: Autocompact summary may lose details. Mitigation: keep original messages in DB, only use compressed version in context.
- **Memory consistency**: Multiple sessions updating the same user memory concurrently. Mitigation: use `updated_at` timestamp, last-write-wins (simple and effective).
