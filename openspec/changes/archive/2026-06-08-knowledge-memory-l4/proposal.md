## Why

Hecate's memory system has L1 (working memory), L2 (session compression), and L3 (user facts), but lacks L4 — a long-term knowledge archive where agents store and retrieve accumulated knowledge across conversations. Additionally, all existing memory models (L1 MemoryBlockModel, L3 MemoryModel) are missing `workspace_id` for multi-tenant isolation, which is a fundamental requirement for the platform.

## What Changes

- **New: Knowledge Memory (L4)** — Agent-scoped long-term knowledge storage with semantic retrieval. Agents actively write facts via tool calls (`knowledge_insert`) and retrieve via search (`knowledge_search`). Stored in Qdrant (dedicated collection) for hybrid vector+BM25 search with PostgreSQL metadata.
- **Fix: Tenant isolation for L1 and L3** — Add `workspace_id` as a first-class column to `MemoryBlockModel` (L1) and `MemoryModel` (L3). All existing queries updated to filter by workspace.
- **New: L4 REST API** — CRUD + search endpoints for agent knowledge memories, workspace-scoped.
- **New: Alembic migration** — Add `workspace_id` column + index to `memory_blocks` and `memories` tables; create new `knowledge_memories` table.
- **New: Memory Isolation enforcement** — All memory API endpoints validate workspace ownership via auth context.

## Capabilities

### New Capabilities

- `knowledge-memory`: L4 knowledge memory storage, retrieval, and agent tool interface — Qdrant-backed hybrid search with PostgreSQL metadata, workspace-scoped isolation, atomic fact granularity
- `memory-isolation`: Add `workspace_id` first-class column to L1 (MemoryBlockModel) and L3 (MemoryModel), update all services and APIs to enforce tenant isolation

### Modified Capabilities

- `memory-api`: Add L4 knowledge memory endpoints (CRUD + search), update existing L1/L3 endpoints to accept and validate workspace context
- `session-memory`: Wire L4 knowledge memory into the conversation flow — agent registers `knowledge_insert` and `knowledge_search` tools when L4 is enabled

## Impact

- **Database**: Alembic migration adds columns to 2 existing tables + creates 1 new table. Existing rows get `workspace_id = UUID('00000000-0000-0000-0000-000000000000')` (default workspace).
- **Services**: `WorkingMemoryService`, `UserMemoryService` — all methods gain `workspace_id` parameter. New `KnowledgeMemoryService` for L4.
- **API**: All `/api/agents/{id}/memory-blocks`, `/api/memory`, `/api/users/{id}/memories` endpoints gain workspace filtering. New `/api/agents/{id}/knowledge` endpoints.
- **RAG/Qdrant**: New dedicated collection `hecate_knowledge_memories` with payload-based workspace filtering.
- **Dependencies**: No new external dependencies — reuses existing `embedding_service`, `HybridSearcher`, and `VectorStore` from `services/rag/`.
