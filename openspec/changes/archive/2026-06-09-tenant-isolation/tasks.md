## 1. Schema — Add workspace_id to 14 Models

- [x] 1.1 Add `workspace_id` column + FK + composite index to ConversationModel (`src/hecate/models/conversation.py`)
- [x] 1.2 Add `workspace_id` column + FK + composite index to MessageModel (`src/hecate/models/message.py`)
- [x] 1.3 Add `workspace_id` column + FK + composite index to SessionModel (`src/hecate/models/session.py`)
- [x] 1.4 Add `workspace_id` column + FK + composite index to DocumentModel (`src/hecate/models/document.py`)
- [x] 1.5 Add `workspace_id` column + FK + composite index to EvidenceModel (`src/hecate/models/evidence.py`)
- [x] 1.6 Add `workspace_id` column + FK + composite index to CheckpointModel (`src/hecate/models/checkpoint.py`)
- [x] 1.7 Add `workspace_id` column + FK + composite index to BudgetSnapshotModel (`src/hecate/models/budget.py`)
- [x] 1.8 Add `workspace_id` column + FK + composite index to WorkflowVersionModel + WorkflowRunModel (`src/hecate/models/workflow.py`)
- [x] 1.9 Add `workspace_id` column + FK + composite index to PromptVersionModel (`src/hecate/models/prompt.py`)
- [x] 1.10 Add `workspace_id` column + FK + composite index to EvaluationDatasetModel + EvaluationItemModel + EvaluationRunModel + EvaluationScoreModel (`src/hecate/models/evaluation.py`)
- [x] 1.11 Update Pydantic Create/Read schemas for all 14 models to include `workspace_id`

## 2. Alembic Migration

- [x] 2.1 Create migration `013_tenant_isolation_workspace_id.py` — add nullable `workspace_id` column to all 14 tables + composite indexes
- [x] 2.2 Implement topological backfill: parent models first (ConversationModel, SessionModel, DocumentModel, WorkflowVersionModel, PromptVersionModel, EvaluationDatasetModel), then child models (MessageModel, EvidenceModel, CheckpointModel, BudgetSnapshotModel, WorkflowRunModel, EvaluationItemModel, EvaluationRunModel, EvaluationScoreModel)
- [x] 2.3 Add NOT NULL constraint + FK to `workspaces(id)` after backfill. Orphaned rows default to zero UUID

## 3. Vector Store Workspace Filtering

- [x] 3.1 Add `workspace_id` to payload in Qdrant store adapter upsert (`src/hecate/services/rag/qdrant_store.py`)
- [x] 3.2 Add mandatory `workspace_id` filter to Qdrant search queries (`src/hecate/services/rag/qdrant_store.py`)
- [x] 3.3 Add `workspace_id` to metadata in Chroma store adapter upsert (`src/hecate/services/rag/chroma_store.py`)
- [x] 3.4 Add mandatory `workspace_id` filter to Chroma search queries (`src/hecate/services/rag/chroma_store.py`)
- [x] 3.5 Update KnowledgeBaseService to pass `workspace_id` through to vector store operations (`src/hecate/services/rag/service.py`)
- [x] 3.6 Update KnowledgeMemoryService search to include `workspace_id` filter in Qdrant queries (`src/hecate/services/memory/knowledge_memory.py`)
- [x] 3.7 Add graceful fallback: if vector payload lacks `workspace_id`, fall back to `knowledge_base_id` filter with warning log

## 4. Service Layer — Workspace Enforcement

- [x] 4.1 Update ConversationService to accept and filter by `workspace_id` on all queries (`src/hecate/services/conversation.py`)
- [x] 4.2 Ensure SessionModel queries inherit `workspace_id` from agent context (already indirect via agent_id — add direct filter)
- [x] 4.3 Update WorkflowExecutionService to pass `workspace_id` when creating workflow versions/runs (`src/hecate/services/workflow/execution_service.py`)
- [x] 4.4 Update EvaluationDatasetService to accept and filter by `workspace_id` on all queries (`src/hecate/services/evaluation/dataset_service.py`)
- [x] 4.5 Update remaining services (EvidenceModel, CheckpointModel, BudgetSnapshotModel, PromptVersionModel) to pass `workspace_id` from parent entity context

## 5. API Layer — Pass AuthContext.workspace_id

- [x] 5.1 Update conversation API endpoints to pass `ctx.workspace_id` to ConversationService
- [x] 5.2 Update session API endpoints to pass `ctx.workspace_id` for session queries
- [x] 5.3 Update workflow version/run API endpoints to pass `ctx.workspace_id`
- [x] 5.4 Update evaluation API endpoints to pass `ctx.workspace_id` to EvaluationDatasetService
- [x] 5.5 Update prompt version API endpoints to pass `ctx.workspace_id`

## 6. Tests

- [x] 6.1 Test cross-tenant isolation: create data in workspace A, verify workspace B cannot access it — cover ConversationModel, MessageModel, SessionModel, DocumentModel
- [x] 6.2 Test cross-tenant isolation for workflow versions/runs and evaluation models
- [x] 6.3 Test vector store workspace_id payload is included on upsert and enforced on search
- [x] 6.4 Test migration: fresh install produces correct schema, upgrade populates workspace_id correctly
- [x] 6.5 Test graceful fallback: vector search with missing workspace_id payload falls back to knowledge_base_id filter

## 7. Documentation

- [x] 7.1 Update `docs/features/feature-catalog.md`: mark 10.5 Tenant Isolation as ✅, scope description to data-only isolation
- [x] 7.2 Update `docs/features/roadmap.md`: mark 10.5 as complete in Sprint 4, update statistics
