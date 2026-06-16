## Why

Agent conversations have no memory — every turn is a stateless call, unable to remember user preferences across sessions or automatically compress history in long conversations. The `services/memory/` directory already has complete three-layer memory service code (L1 working memory, L2 conversation compression, L3 user memory), and `ContextAssembler` already has `memory_blocks` and `user_memories` injection interfaces, but `ConversationService` never calls them. Need to wire the existing memory services into the conversation flow so Agents have full memory capabilities.

## What Changes

- Wire `CompressionPipeline` (L2) into `ConversationService` for automatic compression in long conversations
- Wire `WorkingMemoryService` (L1) into `ContextAssembler` so Agents can read/write named memory blocks every turn
- Wire `UserMemoryService` (L3) into the conversation flow for cross-session user fact persistence
- Automatically extract user memories (preferences, facts, key info) at conversation end
- Add memory management API (CRUD memory blocks, view user memories, manually trigger compression)
- Add frontend memory panel (view/edit working memory, user memory list)

## Capabilities

### New Capabilities

- `session-memory`: In-session memory integration — L1 working memory injection into context + L2 automatic conversation compression + L3 user memory extraction and retrieval
- `memory-api`: Memory management REST API — CRUD working memory blocks, view/search user memories, compression status query

### Modified Capabilities

- (No existing specs need modification)

## Impact

- **Service layer**: `ConversationService` adds memory invocation logic (compression, extraction, injection)
- **Context layer**: `ContextAssembler`'s `memory_blocks` / `user_memories` parameters will actually be populated
- **API layer**: `api/management/memory.py` already has a skeleton, needs endpoint expansion
- **Data layer**: `MemoryBlockModel`, `MemoryModel` already exist, need Alembic migration confirmation
- **Dependencies**: No new external dependencies, all based on existing code
