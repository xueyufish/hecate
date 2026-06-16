## 1. Data Models

- [x] 1.1 Create `MemoryBlockModel` ORM in `models/memory.py` — fields: id, agent_id, label, content, position, limit, created_at, updated_at
- [x] 1.2 Create `MemoryModel` ORM in `models/memory.py` — fields: id, content, scope(JSONB), memory_type, importance, access_count, embedding(vector), created_at
- [x] 1.3 Create Pydantic schemas: MemoryBlockCreateSchema, MemoryBlockUpdateSchema, MemoryBlockReadSchema, MemoryCreateSchema, MemoryReadSchema
- [x] 1.4 Generate Alembic migration for memory_blocks and memories tables
- [x] 1.5 Update `alembic/env.py` to import memory models

## 2. L1 Working Memory

- [x] 2.1 Create `services/memory/working_memory.py` with WorkingMemoryService
- [x] 2.2 Implement `create_block(agent_id, label, content, position, limit)` — create MemoryBlockModel
- [x] 2.3 Implement `get_block(agent_id, block_id)` — return block
- [x] 2.4 Implement `update_block(agent_id, block_id, content)` — update block content
- [x] 2.5 Implement `delete_block(agent_id, block_id)` — delete block
- [x] 2.6 Implement `list_blocks(agent_id)` — return all blocks ordered by position
- [x] 2.7 Implement `inject_blocks(messages, blocks)` — insert blocks into messages at configured positions

## 3. L2 Conversation Compression

- [x] 3.1 Create `services/memory/compression.py` with CompressionPipeline
- [x] 3.2 Implement `snip(messages, recent_window)` — remove low-value messages, preserve recent N
- [x] 3.3 Implement `microcompact(messages)` — merge consecutive same-role messages
- [x] 3.4 Implement `autocompact(messages, model)` — LLM summary of older messages
- [x] 3.5 Implement `compress(messages, budget, model)` — orchestrate snip→microcompact→autocompact
- [x] 3.6 Integrate compression into ContextAssembler — replace P1 simple truncation

## 4. L3 User Memory

- [x] 4.1 Create `services/memory/user_memory.py` with UserMemoryService
- [x] 4.2 Implement `extract_facts(messages, model)` — LLM tool calling to extract facts
- [x] 4.3 Implement `store_memory(content, scope, memory_type)` — generate embedding, persist to DB
- [x] 4.4 Implement `retrieve_memories(query, scope, top_k)` — vector similarity search
- [x] 4.5 Implement `update_importance(memory_id, boost)` — adjust importance on access
- [x] 4.6 Implement `delete_memory(memory_id)` — soft delete

## 5. API Layer

- [x] 5.1 Create `api/management/memory.py` with memory block endpoints
- [x] 5.2 Implement `POST /api/agents/{id}/memory-blocks` — create block
- [x] 5.3 Implement `GET /api/agents/{id}/memory-blocks` — list blocks
- [x] 5.4 Implement `GET /api/agents/{id}/memory-blocks/{block_id}` — get block
- [x] 5.5 Implement `PUT /api/agents/{id}/memory-blocks/{block_id}` — update block
- [x] 5.6 Implement `DELETE /api/agents/{id}/memory-blocks/{block_id}` — delete block
- [x] 5.7 Implement `POST /api/memory` — create memory (extract or manual)
- [x] 5.8 Implement `GET /api/memory` — list/search memories
- [x] 5.9 Implement `DELETE /api/memory/{id}` — delete memory
- [x] 5.10 Register memory router in main FastAPI app

## 6. Context Integration

- [x] 6.1 Modify ContextAssembler to inject MemoryBlocks into context
- [x] 6.2 Modify ContextAssembler to inject relevant L3 memories into context
- [x] 6.3 Replace P1 simple truncation with L2 compression pipeline
- [x] 6.4 Add memory token budget tracking to BudgetManager

## 7. Testing

- [x] 7.1 Unit tests for WorkingMemoryService — CRUD, inject_blocks
- [x] 7.2 Unit tests for CompressionPipeline — snip, microcompact, autocompact
- [x] 7.3 Unit tests for UserMemoryService — extract, store, retrieve
- [x] 7.4 Integration tests for memory block API endpoints
- [x] 7.5 Integration tests for memory API endpoints
- [x] 7.6 Integration test for context assembly with memory injection
