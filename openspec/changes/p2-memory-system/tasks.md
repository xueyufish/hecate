## 1. Data Models

- [ ] 1.1 Create `MemoryBlockModel` ORM in `models/memory.py` — fields: id, agent_id, label, content, position, limit, created_at, updated_at
- [ ] 1.2 Create `MemoryModel` ORM in `models/memory.py` — fields: id, content, scope(JSONB), memory_type, importance, access_count, embedding(vector), created_at
- [ ] 1.3 Create Pydantic schemas: MemoryBlockCreateSchema, MemoryBlockUpdateSchema, MemoryBlockReadSchema, MemoryCreateSchema, MemoryReadSchema
- [ ] 1.4 Generate Alembic migration for memory_blocks and memories tables
- [ ] 1.5 Update `alembic/env.py` to import memory models

## 2. L1 Working Memory

- [ ] 2.1 Create `services/memory/working_memory.py` with WorkingMemoryService
- [ ] 2.2 Implement `create_block(agent_id, label, content, position, limit)` — create MemoryBlockModel
- [ ] 2.3 Implement `get_block(agent_id, block_id)` — return block
- [ ] 2.4 Implement `update_block(agent_id, block_id, content)` — update block content
- [ ] 2.5 Implement `delete_block(agent_id, block_id)` — delete block
- [ ] 2.6 Implement `list_blocks(agent_id)` — return all blocks ordered by position
- [ ] 2.7 Implement `inject_blocks(messages, blocks)` — insert blocks into messages at configured positions

## 3. L2 Conversation Compression

- [ ] 3.1 Create `services/memory/compression.py` with CompressionPipeline
- [ ] 3.2 Implement `snip(messages, recent_window)` — remove low-value messages, preserve recent N
- [ ] 3.3 Implement `microcompact(messages)` — merge consecutive same-role messages
- [ ] 3.4 Implement `autocompact(messages, model)` — LLM summary of older messages
- [ ] 3.5 Implement `compress(messages, budget, model)` — orchestrate snip→microcompact→autocompact
- [ ] 3.6 Integrate compression into ContextAssembler — replace P1 simple truncation

## 4. L3 User Memory

- [ ] 4.1 Create `services/memory/user_memory.py` with UserMemoryService
- [ ] 4.2 Implement `extract_facts(messages, model)` — LLM tool calling to extract facts
- [ ] 4.3 Implement `store_memory(content, scope, memory_type)` — generate embedding, persist to DB
- [ ] 4.4 Implement `retrieve_memories(query, scope, top_k)` — vector similarity search
- [ ] 4.5 Implement `update_importance(memory_id, boost)` — adjust importance on access
- [ ] 4.6 Implement `delete_memory(memory_id)` — soft delete

## 5. API Layer

- [ ] 5.1 Create `api/management/memory.py` with memory block endpoints
- [ ] 5.2 Implement `POST /api/agents/{id}/memory-blocks` — create block
- [ ] 5.3 Implement `GET /api/agents/{id}/memory-blocks` — list blocks
- [ ] 5.4 Implement `GET /api/agents/{id}/memory-blocks/{block_id}` — get block
- [ ] 5.5 Implement `PUT /api/agents/{id}/memory-blocks/{block_id}` — update block
- [ ] 5.6 Implement `DELETE /api/agents/{id}/memory-blocks/{block_id}` — delete block
- [ ] 5.7 Implement `POST /api/memory` — create memory (extract or manual)
- [ ] 5.8 Implement `GET /api/memory` — list/search memories
- [ ] 5.9 Implement `DELETE /api/memory/{id}` — delete memory
- [ ] 5.10 Register memory router in main FastAPI app

## 6. Context Integration

- [ ] 6.1 Modify ContextAssembler to inject MemoryBlocks into context
- [ ] 6.2 Modify ContextAssembler to inject relevant L3 memories into context
- [ ] 6.3 Replace P1 simple truncation with L2 compression pipeline
- [ ] 6.4 Add memory token budget tracking to BudgetManager

## 7. Testing

- [ ] 7.1 Unit tests for WorkingMemoryService — CRUD, inject_blocks
- [ ] 7.2 Unit tests for CompressionPipeline — snip, microcompact, autocompact
- [ ] 7.3 Unit tests for UserMemoryService — extract, store, retrieve
- [ ] 7.4 Integration tests for memory block API endpoints
- [ ] 7.5 Integration tests for memory API endpoints
- [ ] 7.6 Integration test for context assembly with memory injection
