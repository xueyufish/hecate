## Context

Hecate's memory system currently has three layers:

- **L1 Working Memory** (`MemoryBlockModel` in `memory_blocks` table) — Named context blocks per agent, injected into context window. No `workspace_id`.
- **L2 Session Memory** (compression pipeline) — Conversation history with auto-compression. Not a separate model; operates on `ConversationModel`.
- **L3 User Memory** (`MemoryModel` in `memories` table) — User facts with mock embeddings. Uses `scope` JSONB for isolation but no `workspace_id` column.

The existing RAG infrastructure provides `HybridSearcher` (dense + sparse + RRF fusion) over Qdrant, an `embedding_service` for vector generation, and a `VectorStore` ABC with Qdrant/Chroma implementations.

All other models in the system (Agent, Tool, Knowledge, Prompt, Skill, Workflow) have `workspace_id` as a first-class column. Memory models are the exception.

## Goals / Non-Goals

**Goals:**

- Implement L4 Knowledge Memory — agent-scoped, long-term knowledge archive with hybrid search retrieval
- Add `workspace_id` to L1 (MemoryBlockModel) and L3 (MemoryModel) for multi-tenant isolation
- Create new `KnowledgeMemoryModel` (L4) with `workspace_id` from the start
- Reuse existing RAG infrastructure (embedding_service, HybridSearcher, VectorStore) for L4 retrieval
- Provide agent tools (`knowledge_insert`, `knowledge_search`) for L4 interaction
- Provide REST API for L4 CRUD and search

**Non-Goals:**

- Memory consolidation/deduplication (deferred to P4 Feature 4.5 Memory Integration)
- LLM-based memory extraction (L3 currently uses heuristic extraction; L4 uses agent-initiated writes)
- Graph-structured memory (deferred to P3 Feature 4.3a Memory Engine Enhancement)
- Migrating L3 from mock embeddings to real embeddings (separate concern)
- RBAC or permission model changes (workspace_id is sufficient for tenant isolation; RBAC is P3 Feature 10.2)

## Decisions

### D1: L4 Storage — Dual-store (PostgreSQL metadata + Qdrant vectors)

**Choice**: Store L4 knowledge as PostgreSQL rows (metadata, full-text) + Qdrant points (embeddings) in a dedicated collection `hecate_knowledge_memories`.

**Rationale**: Matches Hecate's existing pattern where Qdrant handles vector operations and PostgreSQL handles metadata/filtering. The RAG pipeline already uses this dual-store pattern.

**Alternatives considered**:
- Pure Qdrant (no SQL) — Loses transactional metadata operations, filtering, and joins
- Pure PostgreSQL with pgvector — Requires changing vector store backend, loses hybrid search capability
- Shared collection with RAG documents — Namespace pollution, different retrieval pipelines (rejected per research)

### D2: L4 Write Mechanism — Agent tool calls only

**Choice**: L4 memories are written exclusively via agent tool calls (`knowledge_insert`). No automatic background extraction.

**Rationale**: L4 is an "agent's knowledge archive" — the agent decides what's worth remembering long-term. This matches Letta's approach. Automatic extraction is more appropriate for L3 (user preferences).

**Alternatives considered**:
- Automatic extraction from conversations — Blurs L3/L4 boundary; L3 already does extraction
- Mixed (automatic + manual) — Adds complexity without clear benefit for MVP

### D3: L4 Granularity — Atomic facts, not passages

**Choice**: Each L4 memory is a single atomic fact/statement (e.g., "Company reimbursement requires manager approval").

**Rationale**: Atomic facts enable future consolidation (duplicate detection, merging). Passage-level granularity makes deduplication very difficult. Matches Mem0's approach.

**Alternatives considered**:
- Passage-level (Letta style) — Simpler storage but harder to consolidate later
- Mixed — Overly complex for MVP

### D4: L4 Retrieval — Reuse HybridSearcher over Qdrant

**Choice**: L4 retrieval uses `HybridSearcher` with `embedding_service` for dense+sparse+RRF hybrid search over the dedicated Qdrant collection.

**Rationale**: Hecate already has a production-quality hybrid search pipeline. No need to build a separate retrieval mechanism. The `HybridSearcher` supports dense, sparse, and hybrid modes.

**Alternatives considered**:
- Pure dense search (Letta style) — Weaker retrieval quality; misses keyword-exact matches
- Custom multi-signal scorer — Unnecessary complexity; HybridSearcher already provides RRF fusion

### D5: Tenant Isolation — `workspace_id` as first-class column on all memory models

**Choice**: Add `workspace_id` UUID column with index to `MemoryBlockModel` (L1), `MemoryModel` (L3), and `KnowledgeMemoryModel` (L4). All service queries filter by workspace. Qdrant payloads include `workspace_id` for vector-level filtering.

**Rationale**: Consistent with all other models in the system (Agent, Tool, Knowledge, etc.). First-class column enables efficient indexed queries. JSONB-based isolation (current L3 approach) is slower and less query-friendly.

**Alternatives considered**:
- Keep scope JSONB only — Inconsistent with other models, poor index performance
- workspace_id only in Qdrant payload — No SQL-level enforcement, harder to audit

### D6: L4 Scope — Agent-scoped with optional user context

**Choice**: L4 memories are primarily scoped to `agent_id` (the agent's accumulated knowledge). Each memory has an optional `user_id` field for knowledge about specific users.

**Rationale**: L4 is the agent's knowledge, not the user's. But agents may accumulate user-specific knowledge (e.g., "Customer A uses MySQL 8.0") that should be retrievable per-user.

### D7: L3 `scope` JSONB — Retained alongside new `workspace_id`

**Choice**: Add `workspace_id` as a first-class column to `MemoryModel` (L3) while keeping the existing `scope` JSONB field.

**Rationale**: `workspace_id` handles tenant isolation. `scope` handles intra-tenant filtering (user_id, agent_id, session_id) at a finer granularity. They serve different purposes and should coexist.

### D8: Alembic migration strategy — Default workspace UUID for existing rows

**Choice**: Migration adds `workspace_id` column with server default `UUID('00000000-0000-0000-0000-000000000000')` for existing rows. New rows require explicit `workspace_id`.

**Rationale**: Matches the pattern used in other models (Agent, Tool, etc.) where `workspace_id` defaults to the zero UUID. No data loss, backward-compatible.

## Risks / Trade-offs

- **[Risk] Migration on large `memories` table** → Mitigation: Add column with server default (no rewrite), add index concurrently. Zero-downtime migration.
- **[Risk] Qdrant collection creation timing** → Mitigation: `KnowledgeMemoryService` lazily creates collection on first write if not exists, using `VectorStore.create_collection()`.
- **[Risk] L4 embedding latency on write** → Mitigation: `embedding_service.encode()` is already async. Write path is agent-initiated (not latency-critical).
- **[Trade-off] No consolidation in MVP** → Acceptable: Memories accumulate without dedup. Can be cleaned up later or via manual deletion. Matches Letta and LangGraph approach.
- **[Trade-off] Agent must explicitly write to L4** → Acceptable: Agent prompts can instruct when to use `knowledge_insert`. Future enhancement can add automatic triggers.
