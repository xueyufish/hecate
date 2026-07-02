## Context

Feature 3.2.7 "多知识库关联" requires one agent to be associated with multiple knowledge bases. The current implementation stores `knowledge_base_ids` as a JSON array on `AgentModel` — the data model already supports M:N semantics. The RAG pipeline (`ConversationService._retrieve_knowledge()`) already iterates over multiple KB IDs. The agent configurator frontend already provides a multi-select `KnowledgeSelector` component.

However, several integration gaps prevent this from being production-ready:

1. **No KB validation** — Agents can reference non-existent or deleted KB IDs
2. **No cascade cleanup** — Deleting a KB leaves stale references in agent records
3. **Chat doesn't auto-load KBs** — The frontend chat page doesn't pass agent's KB IDs to the chat endpoint
4. **No reverse lookup** — Cannot query "which agents use this KB?"
5. **Per-KB ranking** — Current search gets top-N per KB then merges; global ranking across KBs is more accurate

## Goals / Non-Goals

**Goals:**
- Validate KB IDs when creating or updating agents
- Cascade cleanup when a KB is deleted
- Auto-load agent KB IDs in chat flow (frontend fetches agent config, passes `kb_ids`)
- Show active KB indicators in chat UI
- Reverse-lookup API to find agents using a specific KB
- Cross-KB search result aggregation with global score ranking

**Non-Goals:**
- Migrate from JSON array to join table (deferred — JSON array is sufficient for current scale)
- KB priority/weighting per agent (future enhancement)
- KB access control or permission checks (P3 multi-tenancy concern)
- Real-time KB sync or webhook notifications

## Decisions

### D1: Keep JSON array for agent-KB relationship (no join table)

**Decision**: Retain `knowledge_base_ids` as a JSON column on `agents` table. Add application-level validation and cascade cleanup.

**Rationale**: A join table would provide referential integrity and better query performance, but introduces:
- Alembic migration complexity (data migration from JSON to join table + backfill)
- ORM relationship configuration changes
- API schema changes (internal representation diverges from API contract)
- Risk of breaking existing codepaths

At current scale (<10K agents, <100 KBs), the JSON array approach is adequate. Application-level validation achieves the same integrity guarantees. If scale becomes an issue, migration to a join table can be a separate change.

**Alternatives considered**:
- Join table `agent_knowledge_bases(agent_id, kb_id, priority, created_at)` — better for large scale, but over-engineering for P2
- Hybrid: keep JSON for reads, join table for integrity — adds complexity without clear benefit

### D2: Validation via batch query in agent CRUD

**Decision**: When creating or updating an agent, validate all KB IDs with a single `SELECT ... WHERE id IN (...)` query against `knowledge_bases` table.

**Rationale**: Single query is efficient. Return a 400 error listing which KB IDs are invalid. This happens at the API layer before the database write.

### D3: Cascade cleanup via post-delete hook in KB service

**Decision**: When soft-deleting a KB (`deleted_at` set), run a cleanup query: `UPDATE agents SET knowledge_base_ids = array_remove(knowledge_base_ids, :kb_id) WHERE :kb_id = ANY(knowledge_base_ids)`. For SQLite tests, use JSON manipulation at application level.

**Rationale**: Keeps cleanup logic in the service layer (not a DB trigger). Works with the soft-delete pattern already used by `BaseModel`.

**Alternatives considered**:
- Database trigger — adds DB-specific logic, harder to test
- Background job — over-engineered for a synchronous operation
- No cleanup, let validation catch stale references — poor UX, agents silently lose KB context

### D4: Frontend auto-loads KB IDs from agent config

**Decision**: The chat page fetches the agent's config (already does this for model name) and includes `knowledge_base_ids` in the `/v1/chat/completions` request as `kb_ids`.

**Rationale**: Minimal change. The chat page already fetches agent data via `GET /api/agents/{agent_id}`. Just extract `knowledge_base_ids` and pass them.

### D5: Reverse lookup via custom SQL query

**Decision**: Add `GET /api/knowledge-bases/{id}/agents` endpoint that queries `WHERE knowledge_base_ids::jsonb @> :kb_id::jsonb` (PostgreSQL) or application-level filter (SQLite).

**Rationale**: Simple endpoint for admin use. PostgreSQL JSON containment operator is efficient. SQLite fallback scans all agents (acceptable for admin tool).

## Risks / Trade-offs

- **[JSON column performance]** → For <10K agents, JSON column lookups are fast enough. If scale grows, migrate to join table in a dedicated change.
- **[Race condition on KB delete]** → KB soft-delete and agent cleanup are not atomic. A brief window exists where agents reference a deleted KB. Mitigation: validation catches this on next update.
- **[SQLite vs PostgreSQL JSON handling]** → Cascade cleanup query differs between SQLite and PostgreSQL. Use application-level cleanup as fallback for test environments.
- **[Stale references in long-lived agent configs]** → If a KB is deleted between agent creation and chat, the chat will log a warning and skip the deleted KB (existing graceful handling in `_retrieve_knowledge`).
