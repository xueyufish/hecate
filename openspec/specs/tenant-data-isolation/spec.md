# Tenant Data Isolation — Multi-Tenant Data Isolation for Resource Models

## Overview

Enforces workspace-level data isolation across all resource models (conversations, messages, sessions, documents, evidence, checkpoints, budgets, workflow versions/runs, prompt versions, evaluation models). Every resource model has a `workspace_id` as a first-class column, and all service-layer queries filter by `workspace_id` to prevent cross-tenant data access.

## Requirements

### Requirement: Workspace-scoped data isolation for all resource models

Every resource model that belongs to a tenant SHALL have a `workspace_id` UUID column with a foreign key to `WorkspaceModel.id`. All service-layer queries for these models SHALL filter by `workspace_id` to prevent cross-tenant data access. The models in scope are: ConversationModel, MessageModel, SessionModel, DocumentModel, EvidenceModel, CheckpointModel, BudgetSnapshotModel, WorkflowVersionModel, WorkflowRunModel, PromptVersionModel, EvaluationDatasetModel, EvaluationItemModel, EvaluationRunModel, EvaluationScoreModel.

#### Scenario: Query conversations within workspace
- **WHEN** `ConversationService.list(db, workspace_id=ws_id)` is called
- **THEN** only conversations where `workspace_id == ws_id` SHALL be returned

#### Scenario: Query messages within workspace
- **WHEN** `MessageModel` rows are queried for a conversation in workspace `ws_id`
- **THEN** only messages where `workspace_id == ws_id` SHALL be returned

#### Scenario: Query sessions within workspace
- **WHEN** sessions are listed for a workspace
- **THEN** only sessions where `workspace_id == ws_id` SHALL be returned

#### Scenario: Query documents within workspace
- **WHEN** documents are listed for a knowledge base in workspace `ws_id`
- **THEN** only documents where `workspace_id == ws_id` SHALL be returned

#### Scenario: Cross-tenant access denied
- **WHEN** a request authenticated as workspace A attempts to read a resource in workspace B
- **THEN** the service SHALL return no results (empty list) or raise 404 for single-resource lookups

#### Scenario: Create resource inherits workspace
- **WHEN** a new resource (conversation, message, session, document, etc.) is created
- **THEN** the resource `workspace_id` SHALL be set from the authenticated `AuthContext.workspace_id`

### Requirement: Vector store workspace payload filtering

All vector store adapters (Qdrant, Chroma) SHALL include `workspace_id` in the payload metadata on every vector insertion and SHALL apply a mandatory `workspace_id` filter condition on every search query.

#### Scenario: Vector insertion includes workspace_id payload
- **WHEN** a document chunk is embedded and stored in the vector store
- **THEN** the point payload SHALL include `workspace_id` matching the knowledge base's workspace

#### Scenario: Vector search filters by workspace_id
- **WHEN** a hybrid or dense search is executed for a workspace
- **THEN** the search query SHALL include a payload filter `workspace_id == ws_id`

#### Scenario: Vector search without workspace_id falls back gracefully
- **WHEN** a vector point lacks the `workspace_id` payload field (legacy data)
- **THEN** the search SHALL fall back to `knowledge_base_id` filtering and log a warning

### Requirement: Alembic migration with backfill

A single Alembic migration SHALL add `workspace_id` columns to all 14 tables with composite indexes, backfill existing rows via parent entity relationships in topological order, then add NOT NULL and FK constraints.

#### Scenario: Migration adds columns and indexes
- **WHEN** `alembic upgrade head` is run
- **THEN** all 14 tables have new `workspace_id` UUID columns with `idx_<table>_workspace` composite indexes

#### Scenario: Backfill populates workspace_id from parents
- **WHEN** migration runs on an existing database
- **THEN** existing rows get `workspace_id` populated from their parent entity's `workspace_id`

#### Scenario: Backfill order respects dependencies
- **WHEN** migration backfills data
- **THEN** parent models (ConversationModel, SessionModel, EvaluationDatasetModel, etc.) are backfilled before child models (MessageModel, EvidenceModel, EvaluationItemModel, etc.)

#### Scenario: Default workspace for orphaned rows
- **WHEN** a row has no resolvable parent entity (orphaned data)
- **THEN** its `workspace_id` SHALL be set to the zero-UUID default workspace
