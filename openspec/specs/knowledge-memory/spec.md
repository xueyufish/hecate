# Knowledge Memory — L4 Long-Term Agent Knowledge

## Overview

L4 knowledge memory provides persistent, searchable storage for agent knowledge as atomic facts. It uses a dual-store architecture: PostgreSQL for metadata and Qdrant for vector embeddings (dense + sparse) with hybrid search.

## Requirements

### REQ-1: Knowledge Memory Storage

The system SHALL provide a `KnowledgeMemoryModel` (ORM) and `KnowledgeMemoryService` for storing agent knowledge as atomic facts. Each knowledge memory SHALL have: `workspace_id` (tenant scope), `agent_id` (agent scope), `content` (the fact text), `tags` (JSON array for categorization), `importance` (0.0-1.0 score), `access_count` (retrieval frequency), `source` (how it was created: "agent_tool" or "api"), `user_id` (optional, for user-specific knowledge), and `embedding` stored in Qdrant.

#### Scenario: Agent inserts knowledge via tool call
- **WHEN** Agent calls `knowledge_insert(content="Company reimbursement requires manager approval", tags=["policy", "reimbursement"])`
- **THEN** System creates a new `KnowledgeMemoryModel` row with the content, generates embedding via `embedding_service`, upserts to Qdrant collection `hecate_knowledge_memories` with payload `{workspace_id, agent_id, tags, importance}`, and returns the memory ID

#### Scenario: Knowledge with user context
- **WHEN** Agent calls `knowledge_insert(content="Customer A uses MySQL 8.0", user_id="uuid-of-customer-a")`
- **THEN** System stores the knowledge with optional `user_id` field for user-specific retrieval

#### Scenario: Duplicate content prevention
- **WHEN** Agent calls `knowledge_insert` with content that is identical (after normalization) to an existing knowledge memory for the same agent
- **THEN** System SHALL update the existing memory's `updated_at` timestamp and increment `access_count` instead of creating a duplicate

### REQ-2: Knowledge Memory Hybrid Search

The system SHALL provide semantic retrieval of knowledge memories using `HybridSearcher` over the dedicated Qdrant collection. Search SHALL support dense (vector), sparse (BM25), and hybrid (RRF fusion) modes.

#### Scenario: Agent searches knowledge
- **WHEN** Agent calls `knowledge_search(query="reimbursement policy", top_k=5)`
- **THEN** System generates query embedding, performs hybrid search over Qdrant collection filtered by `{workspace_id, agent_id}`, returns top-K results ordered by relevance score with content and metadata

#### Scenario: Search with user context
- **WHEN** Agent calls `knowledge_search(query="database preferences", user_id="uuid-of-customer-a")`
- **THEN** System adds `user_id` filter to narrow results to knowledge about that specific user

#### Scenario: Search with tag filter
- **WHEN** Agent calls `knowledge_search(query="policy", tags=["reimbursement"])`
- **THEN** System filters results to only include memories with matching tags

### REQ-3: Knowledge Memory CRUD API

The system SHALL provide REST API endpoints for knowledge memory management, all workspace-scoped.

#### Scenario: List knowledge memories for an agent
- **WHEN** `GET /api/agents/{agent_id}/knowledge` is called
- **THEN** Return paginated list of knowledge memories for the agent, ordered by `updated_at` desc

#### Scenario: Get specific knowledge memory
- **WHEN** `GET /api/agents/{agent_id}/knowledge/{memory_id}` is called
- **THEN** Return the knowledge memory details including content, tags, importance, access_count, source

#### Scenario: Create knowledge memory via API
- **WHEN** `POST /api/agents/{agent_id}/knowledge` with `{"content": "...", "tags": [...], "importance": 0.8}` is called
- **THEN** Create a new knowledge memory, generate embedding, store in Qdrant, return 201 with created memory

#### Scenario: Delete knowledge memory
- **WHEN** `DELETE /api/agents/{agent_id}/knowledge/{memory_id}` is called
- **THEN** Soft-delete the memory in PostgreSQL and delete the point from Qdrant

#### Scenario: Search knowledge memories via API
- **WHEN** `POST /api/agents/{agent_id}/knowledge/search` with `{"query": "...", "top_k": 5, "tags": [...]}` is called
- **THEN** Perform hybrid search and return scored results

### REQ-4: Qdrant Collection Management

The system SHALL use a dedicated Qdrant collection `hecate_knowledge_memories` for L4 knowledge vectors. The collection SHALL be lazily created on first write if it does not exist.

#### Scenario: First knowledge insert creates collection
- **WHEN** The first `knowledge_insert` is called and the collection does not exist
- **THEN** System calls `VectorStore.create_collection("hecate_knowledge_memories")` with dense + sparse vector support before upserting

#### Scenario: Collection payload schema
- **WHEN** A knowledge memory is upserted to Qdrant
- **THEN** The point payload SHALL include: `workspace_id` (UUID string), `agent_id` (UUID string), `tags` (JSON array), `importance` (float), `user_id` (optional UUID string), `text` (the content for sparse retrieval)
