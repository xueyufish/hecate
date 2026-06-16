## Why

Hecate has organization management (10.1) and RBAC (10.2) implemented, but 14 resource models still lack `workspace_id` — meaning conversations, messages, sessions, documents, checkpoints, evidence, workflow versions/runs, prompt versions, and evaluation data have no tenant boundary. Any authenticated user with a valid agent_id can query cross-tenant data. Vector stores (Qdrant/Chroma) also lack `workspace_id` payload filtering, relying solely on indirect knowledge-base scoping. This is the last gap before the multi-tenant data layer is complete.

## What Changes

- Add `workspace_id` UUID FK column to 14 models: ConversationModel, MessageModel, SessionModel, DocumentModel, EvidenceModel, CheckpointModel, BudgetSnapshotModel, WorkflowVersionModel, WorkflowRunModel, PromptVersionModel, EvaluationDatasetModel, EvaluationItemModel, EvaluationRunModel, EvaluationScoreModel.
- Add Alembic migration with backfill via parent entity relationships (e.g., `conversation.workspace_id ← agent.workspace_id`).
- Add `workspace_id` payload field to all Qdrant/Chroma vector insertions and filter on all search queries.
- Update all service-layer queries for the 14 newly-scoped models to filter by `workspace_id`.
- Update feature catalog to scope 10.5 to data-only isolation (compute/network deferred to 9.4c/9.4d/9.7).

## Capabilities

### New Capabilities
- `tenant-data-isolation`: Workspace-scoped data isolation across all resource models and vector stores — every query enforces workspace_id, every vector payload includes workspace_id.

### Modified Capabilities
- `data-models`: Add workspace_id FK to 14 resource models (ConversationModel, MessageModel, SessionModel, DocumentModel, EvidenceModel, CheckpointModel, BudgetSnapshotModel, WorkflowVersionModel, WorkflowRunModel, PromptVersionModel, EvaluationDatasetModel, EvaluationItemModel, EvaluationRunModel, EvaluationScoreModel).
- `memory-isolation`: Add workspace_id payload filter to Qdrant/Chroma vector store operations (currently spec only covers SQL-level isolation, not vector DB).

## Impact

- **Models**: 14 ORM models gain new column + index + FK.
- **Services**: ConversationService, WorkflowExecutionService, EvaluationDatasetService, and all services touching the 14 models must add workspace_id parameter and filter.
- **API**: All endpoints touching conversations, messages, sessions, documents, evidence, checkpoints, workflow versions/runs, prompt versions, and evaluation data must pass workspace_id from AuthContext.
- **Vector DB**: Qdrant and Chroma store adapters must inject workspace_id into payloads and filter on search.
- **Migration**: Single Alembic migration adding 14 columns with backfill. Existing rows get workspace_id from parent entity (agent/knowledge-base).
- **Tests**: New tests for cross-tenant isolation (tenant A cannot read tenant B's data) across all affected models and vector stores.
