## 1. Data Models & Migration

- [x] 1.1 Add `workspace_id` column to `MemoryBlockModel` in `src/hecate/models/memory.py` — UUID column with index, nullable=False, default zero UUID
- [x] 1.2 Add `workspace_id` column to `MemoryModel` in `src/hecate/models/memory.py` — UUID column with index, nullable=False, default zero UUID
- [x] 1.3 Create `KnowledgeMemoryModel` in `src/hecate/models/memory.py` — ORM model with fields: workspace_id, agent_id, content, tags (JSON), importance (Float), access_count (Integer), source (String), user_id (optional UUID FK), soft-delete via BaseModel
- [x] 1.4 Create Pydantic schemas for L4 in `src/hecate/models/memory.py` — KnowledgeMemoryCreateSchema, KnowledgeMemoryUpdateSchema, KnowledgeMemoryReadSchema, KnowledgeMemorySearchSchema
- [x] 1.5 Update `MemoryBlockCreateSchema` and `MemoryCreateSchema` to include `workspace_id` field
- [x] 1.6 Update `MemoryBlockReadSchema` and `MemoryReadSchema` to include `workspace_id` field
- [ ] 1.7 Create Alembic migration — Add `workspace_id` to `memory_blocks` and `memories` tables with default zero UUID + indexes; create `knowledge_memories` table
- [x] 1.8 Register `KnowledgeMemoryModel` in `tests/conftest.py` for test database setup

## 2. Memory Isolation — Service Layer Updates

- [x] 2.1 Update `WorkingMemoryService` in `src/hecate/services/memory/working_memory.py` — Add `workspace_id` parameter to all methods, filter all queries by workspace_id
- [x] 2.2 Update `UserMemoryService` in `src/hecate/services/memory/user_memory.py` — Add `workspace_id` parameter to all methods, filter all queries by workspace_id
- [x] 2.3 Update `MemoryBlockModel` create path — Set workspace_id from agent's workspace when creating blocks
- [x] 2.4 Update `MemoryModel` create path — Set workspace_id from auth context when creating memories

## 3. L4 Knowledge Memory Service

- [x] 3.1 Create `src/hecate/services/memory/knowledge_memory.py` — `KnowledgeMemoryService` class with `__init__(db, vector_store)` accepting AsyncSession and VectorStore
- [x] 3.2 Implement `insert_knowledge(agent_id, workspace_id, content, tags, importance, user_id, source)` — Create KnowledgeMemoryModel row, generate embedding via embedding_service, upsert to Qdrant collection
- [x] 3.3 Implement `search_knowledge(agent_id, workspace_id, query, top_k, tags, user_id, mode)` — Hybrid search over Qdrant with workspace_id + agent_id payload filter, return scored results
- [x] 3.4 Implement `get_knowledge(agent_id, workspace_id, memory_id)` — Retrieve single knowledge memory with workspace + agent ownership check
- [x] 3.5 Implement `list_knowledge(agent_id, workspace_id, tags, limit, offset)` — List with pagination, tag filter, ordered by updated_at desc
- [x] 3.6 Implement `delete_knowledge(agent_id, workspace_id, memory_id)` — Soft-delete in PostgreSQL + delete point from Qdrant
- [x] 3.7 Implement `_ensure_collection()` — Lazy-create Qdrant collection `hecate_knowledge_memories` on first write with dense + sparse vector support
- [x] 3.8 Implement `_upsert_to_qdrant(memory)` — Generate embedding, build payload {workspace_id, agent_id, tags, importance, user_id, text}, upsert to Qdrant
- [x] 3.9 Implement duplicate detection — On insert, check for existing knowledge with same content + agent_id, update existing if found instead of creating duplicate

## 4. API Layer

- [x] 4.1 Update L1 memory block endpoints in `src/hecate/api/management/memory.py` — Pass workspace_id from agent lookup to WorkingMemoryService calls
- [x] 4.2 Update L3 user memory endpoints in `src/hecate/api/management/memory.py` — Pass workspace_id from auth context to UserMemoryService calls
- [x] 4.3 Create L4 knowledge memory endpoints — `POST /api/agents/{agent_id}/knowledge` (create), `GET` (list), `GET /{memory_id}` (get), `DELETE /{memory_id}` (delete), `POST /search` (search)
- [x] 4.4 Register knowledge memory routes in `src/hecate/main.py` — Already registered (memory_router at line 152)

## 5. Agent Tools

- [x] 5.1 Create `src/hecate/services/memory/knowledge_tools.py` — Define `knowledge_insert` and `knowledge_search` tool schemas (JSON Schema for LLM tool calling)
- [ ] 5.2 Implement `knowledge_insert` tool handler — Parse args, call KnowledgeMemoryService.insert_knowledge, return confirmation
- [ ] 5.3 Implement `knowledge_search` tool handler — Parse args, call KnowledgeMemoryService.search_knowledge, return formatted results
- [ ] 5.4 Wire tool registration — Register knowledge tools in agent tool list when knowledge memory is enabled

## 6. Tests

- [x] 6.1 Test `KnowledgeMemoryModel` ORM — Create, read, update, soft-delete, workspace_id filtering
- [x] 6.2 Test `KnowledgeMemoryService` — insert_knowledge, search_knowledge, list_knowledge, delete_knowledge, duplicate detection
- [x] 6.3 Test workspace isolation — Verify L1/L3/L4 queries only return data within the correct workspace
- [x] 6.4 Test L4 API endpoints — CRUD + search via httpx AsyncClient, verify workspace scoping
- [x] 6.5 Test updated L1/L3 APIs — Verify workspace_id filtering on existing memory block and user memory endpoints
- [ ] 6.6 Test Qdrant integration — Verify collection creation, payload structure, hybrid search with workspace filter
- [x] 6.7 Test knowledge tools — Verify tool schema, insert handler, search handler
