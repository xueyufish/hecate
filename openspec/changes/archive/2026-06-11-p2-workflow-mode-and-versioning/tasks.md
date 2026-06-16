## 1. Models & Schemas

- [x] 1.1 Add `execution_mode` field to `WorkflowModel` (String(20), default="conversational", nullable=False) and update `WorkflowCreateSchema`, `WorkflowUpdateSchema`, `WorkflowReadSchema`, `WorkflowDetailSchema`
- [x] 1.2 Add `published_version` field to `WorkflowModel` (Integer, nullable=True, default=None) and update read/detail schemas
- [x] 1.3 Add `labels` field to `WorkflowVersionModel` (JSON, default=list) and update `WorkflowVersionReadSchema`
- [x] 1.4 Add Alembic migration for the three new columns

## 2. Engine Layer — Execution Mode Validation

- [x] 2.1 Add `execution_mode` parameter to `GraphCompiler.compile()` — default "conversational", validate INTERRUPT/SUGGESTION nodes forbidden in task mode
- [x] 2.2 Add `ExecutionMode` enum to `engine/types.py` with values CONVERSATIONAL and TASK
- [x] 2.3 Add system variables to channel initialization: `sys.execution_mode`, `sys.conversation_id`, `sys.dialogue_count` (conversational only)

## 3. Engine Layer — Runtime Behavior

- [x] 3.1 Add execution_mode parameter to `PregelRuntime.execute()` — disable checkpointing in task mode, override StreamMode.MESSAGES to StreamMode.VALUES in task mode
- [x] 3.2 Update `WorkflowExecutionService` to pass execution_mode from WorkflowModel to PregelRuntime

## 4. Service Layer — Workflow Mode & Version

- [x] 4.1 Update `WorkflowService.create_workflow()` to accept and persist execution_mode
- [x] 4.2 Update `WorkflowService.update_workflow()` to validate execution_mode changes and recompile if graph_dsl changed
- [x] 4.3 Implement `WorkflowService.publish_version(workflow_id, version)` — set published_version, manage production label, create audit log
- [x] 4.4 Implement `WorkflowService.get_version_by_label(workflow_id, label)` — query by labels field
- [x] 4.5 Implement `WorkflowService.get_published_version(workflow_id)` — return version at published_version pointer
- [x] 4.6 Implement `WorkflowService.diff_versions(workflow_id, v1, v2)` — structural JSON diff using deepdiff, return categorized result

## 5. API Layer — New Endpoints

- [x] 5.1 Add `POST /api/workflows/{id}/publish/{version}` endpoint
- [x] 5.2 Add `GET /api/workflows/{id}/diff?v1=&v2=` endpoint
- [x] 5.3 Add `GET /api/workflows/{id}/published` endpoint
- [x] 5.4 Update existing workflow CRUD endpoints to include execution_mode and published_version in responses

## 6. Dependencies

- [x] 6.1 Add `deepdiff` to `[dev]` optional dependency group in pyproject.toml

## 7. Tests

- [x] 7.1 Test WorkflowModel execution_mode field: create with default, create with task, update mode
- [x] 7.2 Test GraphCompiler task mode validation: INTERRUPT node rejected, SUGGESTION node rejected, conversational allows all
- [x] 7.3 Test PregelRuntime task mode behavior: no checkpointing, stream mode override
- [x] 7.4 Test WorkflowService.publish_version: publish, republish, publish non-existent version
- [x] 7.5 Test WorkflowService.diff_versions: node changes, identical versions, non-existent version
- [x] 7.6 Test Workflow API publish/diff/published endpoints
- [x] 7.7 Test system variables: sys.execution_mode in both modes, sys.conversation_id only in conversational
