## 1. Service Layer — Memory Integration into ConversationService

- [x] 1.1 Load L1 working memory blocks in `ConversationService.chat()` — call `WorkingMemoryService.list_blocks(agent_id)` and pass results to `ContextAssembler.assemble(memory_blocks=...)`
- [x] 1.2 Implement L2 compression trigger in `ConversationService.chat()` — check token count, call `CompressionPipeline.compress()` to compress history when exceeding `compression_threshold`
- [x] 1.3 Implement L3 user memory retrieval in `ConversationService.chat()` — call `UserMemoryService.retrieve_memories(user_id, query)` to get relevant memories, pass to `ContextAssembler.assemble(user_memories=...)`
- [x] 1.4 Call `UserMemoryService.extract_facts()` after Assistant response in `ConversationService.chat()` — async execution, does not block response
- [x] 1.5 Register `update_memory_block` and `search_user_memory` memory tools to Agent tool list — auto-register when Agent has working memory configured or user has L3 memory enabled

## 2. API Layer — Memory Management Endpoints

- [x] 2.1 Extend `api/management/memory.py` — `GET /api/agents/{agent_id}/memory/blocks` list working memory blocks
- [x] 2.2 `POST /api/agents/{agent_id}/memory/blocks` create/update memory block
- [x] 2.3 `PUT /api/agents/{agent_id}/memory/blocks/{block_id}` update specific memory block
- [x] 2.4 `DELETE /api/agents/{agent_id}/memory/blocks/{block_id}` delete memory block
- [x] 2.5 `GET /api/users/{user_id}/memories` list user memories (paginated)
- [x] 2.6 `GET /api/users/{user_id}/memories/search?q={query}` semantic search user memories
- [x] 2.7 `DELETE /api/users/{user_id}/memories/{memory_id}` delete specific user memory
- [x] 2.8 `GET /api/sessions/{session_id}/compression` return session compression history
- [x] 2.9 Register memory management routes in `main.py`

## 3. Tests

- [x] 3.1 `tests/test_services/test_memory/test_integration.py` — test ConversationService memory injection integration (L1 injection, L2 compression trigger, L3 retrieval + extraction)
- [x] 3.2 `tests/test_api/test_memory.py` — test memory management API endpoints (CRUD blocks, user memory list/search/delete, compression status)
- [x] 3.3 Update `tests/test_services/test_context/test_integration.py` — add memory injection integration test for ContextAssembler

## 4. Documentation

- [x] 4.1 Update `docs/features/feature-catalog.md` — mark 4.2 session memory, 4.3 user memory as implemented
- [x] 4.2 Run ruff + mypy + pytest full validation
