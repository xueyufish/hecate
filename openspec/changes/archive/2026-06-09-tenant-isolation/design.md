## Context

Hecate has 10 resource models with `workspace_id` (agents, workflows, tools, knowledge bases, prompts, memory blocks, memories, knowledge memories, skills, API keys) enforced through the `AuthContext` → `workspace_id` flow established in the organization-rbac change (10.1 + 10.2). However, 14 models lack `workspace_id` entirely, leaving conversations, messages, sessions, documents, evidence, checkpoints, budget snapshots, workflow versions/runs, prompt versions, and evaluation datasets/items/runs/scores without tenant boundaries.

Current auth flow:
```
Request → Bearer token → get_auth_context() → AuthContext(workspace_id) → service.workspace_id filter
```

This flow works for the 10 scoped models but breaks for the 14 unscoped ones — services either don't filter at all or rely on indirect joins (e.g., `session → agent → workspace_id`), which is fragile and inconsistent.

Vector stores (Qdrant, Chroma) store embeddings without `workspace_id` payload fields. Current isolation depends entirely on the knowledge-base → workspace relationship, which means a direct vector store query bypasses tenant boundaries.

## Goals / Non-Goals

**Goals:**

- Add `workspace_id` FK to all 14 unscoped resource models.
- Backfill existing rows with correct `workspace_id` via parent entity relationships.
- Add `workspace_id` payload to all vector store insertions and filter on all search queries.
- Ensure every service query for the 14 models enforces `workspace_id`.
- Every API endpoint passes `workspace_id` from `AuthContext` to the service layer.

**Non-Goals:**

- Compute isolation (sandbox per workspace). Deferred to 9.4c/9.4d.
- Network isolation (egress control, domain whitelists). Deferred to 9.7.
- PostgreSQL Row-Level Security (RLS). Application-level filtering is sufficient.
- Automatic query interceptor (SQLAlchemy session event or middleware). Manual service-level filtering keeps the pattern explicit and auditable.
- `ModelProviderModel` / `ModelRegistryModel` — these are intentionally global (shared model configuration across all workspaces).
- `UserModel` / `OrganizationModel` — cross-tenant by design.

## Decisions

### D1: Direct workspace_id FK on all models (not indirect JOINs)

**Decision**: Add `workspace_id` as a direct FK column on all 14 models, including high-traffic tables (ConversationModel, MessageModel, SessionModel).

**Alternatives considered**:
- **Indirect via agent_id JOIN**: Every query would need 2-3 JOINs to reach `workspace_id`. The JOIN penalty on every chat request is permanent and unacceptable for the hottest tables in the system.
- **SQLAlchemy query interceptor**: Automatic workspace injection via session events. Adds invisible magic that makes debugging harder. Violates the project's explicit-is-better-than-implicit convention.

**Rationale**: Direct FK gives O(1) workspace lookup per row, no JOINs, and a hard DB-level guarantee that data never leaks across tenants. Migration cost is one-time; query performance benefit is permanent.

### D2: Backfill via parent entity relationships

**Decision**: Migration populates `workspace_id` by joining to the parent entity's `workspace_id`:

| Model | Parent → workspace_id source |
|-------|------------------------------|
| ConversationModel | `agent_id → AgentModel.workspace_id` |
| MessageModel | `conversation_id → ConversationModel.agent_id → AgentModel.workspace_id` (after ConversationModel is backfilled) |
| SessionModel | `agent_id → AgentModel.workspace_id` |
| DocumentModel | `knowledge_base_id → KnowledgeBaseModel.workspace_id` |
| EvidenceModel | `session_id → SessionModel.agent_id → AgentModel.workspace_id` (after SessionModel is backfilled) |
| CheckpointModel | `session_id → SessionModel.agent_id → AgentModel.workspace_id` (after SessionModel is backfilled) |
| BudgetSnapshotModel | `session_id → SessionModel.agent_id → AgentModel.workspace_id` (after SessionModel is backfilled) |
| WorkflowVersionModel | `workflow_id → WorkflowModel.workspace_id` |
| WorkflowRunModel | `workflow_id → WorkflowModel.workspace_id` |
| PromptVersionModel | `prompt_id → PromptModel.workspace_id` |
| EvaluationDatasetModel | `agent_id → AgentModel.workspace_id` |
| EvaluationItemModel | `dataset_id → EvaluationDatasetModel.workspace_id` (after EvaluationDatasetModel is backfilled) |
| EvaluationRunModel | `dataset_id → EvaluationDatasetModel.workspace_id` (after EvaluationDatasetModel is backfilled) |
| EvaluationScoreModel | `run_id → EvaluationRunModel.workspace_id` (after EvaluationRunModel is backfilled) |

**Rationale**: Every model has a clear parent chain to a workspace-scoped entity. The backfill order follows dependency depth (parents first, children second).

### D3: Vector DB payload filter with workspace_id

**Decision**: Add `workspace_id` to vector store payloads on every upsert and include it as a mandatory filter condition on every search query.

**Alternatives considered**:
- **Per-workspace collections**: 1000 workspaces = 1000 collections. Collection proliferation, management overhead, and cross-workspace search becomes impossible.
- **Per-knowledge-base collections only (current)**: Indirect isolation works but has no defense-in-depth. A bug in knowledge_base_id filtering would expose cross-tenant vectors.

**Rationale**: Payload filtering is the standard multi-tenancy pattern in vector databases (Qdrant recommends it). One filter condition adds negligible overhead. Defense-in-depth against knowledge_base_id bypass.

### D4: Composite indexes on (workspace_id, deleted)

**Decision**: Add `Index("idx_<table>_workspace", "workspace_id", "deleted")` on all 14 tables, matching the existing index pattern on the 10 already-scoped models.

**Rationale**: Consistent with existing index naming convention. The composite index supports the most common query pattern: `WHERE workspace_id = :ws AND deleted = false`.

## Risks / Trade-offs

**[R1] Large migration touching 14 tables** → Migration runs UPDATE on potentially millions of rows (conversations, messages). **Mitigation**: Batch updates (UPDATE ... LIMIT 10000 in a loop) to avoid locking tables for extended periods. Run during low-traffic window.

**[R2] Backfill order dependency** → Some models depend on others being backfilled first (e.g., MessageModel needs ConversationModel). **Mitigation**: Migration runs backfill in topological order (parents before children). Wrapped in a transaction with explicit ordering.

**[R3] Vector store backfill** → Existing vectors lack `workspace_id` payload. **Mitigation**: Batch payload update via Qdrant/Chroma scroll + update API. Graceful degradation: if payload is missing, fall back to knowledge_base_id filtering (current behavior).

**[R4] Service method signature changes** → All service methods for the 14 models gain `workspace_id` parameter. **Mitigation**: `workspace_id` defaults to `None` → zero UUID, matching existing pattern. API endpoints already pass `AuthContext.workspace_id`.

## Migration Plan

1. **Phase 1 — Schema**: Add `workspace_id` column to 14 tables (nullable first, no FK constraint yet). Add composite indexes.
2. **Phase 2 — Backfill**: Populate `workspace_id` from parent entities in topological order.
3. **Phase 3 — Constraints**: Add NOT NULL constraint + FK to `workspaces(id)`. Existing zero-UUID rows reference the bootstrap default workspace.
4. **Phase 4 — Vector DB**: Batch update Qdrant/Chroma payloads with `workspace_id`.
5. **Phase 5 — Code**: Deploy service/API changes that enforce `workspace_id` filtering.

**Rollback**: Migration is reversible — downgrade drops columns and indexes. Vector store payload updates are non-destructive (additive only).
