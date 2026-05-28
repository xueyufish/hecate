## 1. Data Models

- [x] 1.1 Create `WorkflowModel` ORM in `models/workflow.py` — fields: id, name, workspace_id, current_version, created_at, updated_at, deleted_at
- [x] 1.2 Create `WorkflowVersionModel` ORM in `models/workflow.py` — fields: id, workflow_id, version, graph_dsl(JSONB), compiled_graph(JSONB), change_summary, created_at
- [x] 1.3 Create Pydantic schemas: WorkflowCreateSchema, WorkflowUpdateSchema, WorkflowReadSchema, WorkflowVersionReadSchema
- [x] 1.4 Generate Alembic migration for workflow and workflow_versions tables
- [x] 1.5 Update `alembic/env.py` to import workflow model

## 2. Service Layer

- [x] 2.1 Create `services/workflow_service.py` with WorkflowService class
- [x] 2.2 Implement `create_workflow(name, graph_dsl, workspace_id)` — validate DSL with GraphCompiler, create WorkflowModel + WorkflowVersionModel(v1)
- [x] 2.3 Implement `get_workflow(workflow_id)` — return workflow with current version
- [x] 2.4 Implement `update_workflow(workflow_id, name?, graph_dsl?)` — update name or create new version with DSL validation
- [x] 2.5 Implement `delete_workflow(workflow_id)` — soft delete
- [x] 2.6 Implement `list_workflows(workspace_id, page, page_size)` — paginated list excluding deleted
- [x] 2.7 Implement `list_versions(workflow_id)` — all versions ordered by version number
- [x] 2.8 Implement `get_version(workflow_id, version)` — specific version details
- [x] 2.9 Implement `rollback_to_version(workflow_id, target_version)` — create new version with target's graph_dsl

## 3. API Layer

- [x] 3.1 Create `api/management/workflows.py` with CRUD endpoints
- [x] 3.2 Implement `POST /api/workflows` — create workflow
- [x] 3.3 Implement `GET /api/workflows/{id}` — read workflow
- [x] 3.4 Implement `PUT /api/workflows/{id}` — update workflow
- [x] 3.5 Implement `DELETE /api/workflows/{id}` — delete workflow
- [x] 3.6 Implement `GET /api/workflows` — list workflows with pagination
- [x] 3.7 Implement `GET /api/workflows/{id}/versions` — list versions
- [x] 3.8 Implement `GET /api/workflows/{id}/versions/{version}` — get specific version
- [x] 3.9 Implement `POST /api/workflows/{id}/rollback/{version}` — rollback to version
- [x] 3.10 Register workflow router in main FastAPI app

## 4. Testing

- [x] 4.1 Unit tests for WorkflowModel and schemas
- [x] 4.2 Unit tests for WorkflowService — create, get, update, delete, list
- [x] 4.3 Unit tests for WorkflowService — versions, rollback
- [x] 4.4 Integration tests for API endpoints — CRUD operations
- [x] 4.5 Integration tests for API endpoints — version management
- [x] 4.6 Test invalid graph_dsl rejection (422 responses)
