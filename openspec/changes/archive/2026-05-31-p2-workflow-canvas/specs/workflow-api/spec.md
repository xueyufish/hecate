## ADDED Requirements

### Requirement: Create workflow
`POST /api/workflows` SHALL accept a name and optional graph_dsl, create a WorkflowModel, compile the DSL into a WorkflowVersionModel (version 1), and return the workflow with its first version.

#### Scenario: Create empty workflow
- **WHEN** POST /api/workflows with `{"name": "My Flow"}`
- **THEN** response is 201 with workflow id, name, current_version=1, and an empty graph_dsl version

#### Scenario: Create workflow with initial DSL
- **WHEN** POST /api/workflows with `{"name": "My Flow", "graph_dsl": {...}}`
- **THEN** response is 201 with the DSL compiled and stored as version 1

### Requirement: List workflows
`GET /api/workflows` SHALL return a paginated list of workflows with name, current_version, created_at, updated_at.

#### Scenario: List workflows with pagination
- **WHEN** GET /api/workflows?page=1&page_size=20
- **THEN** response is 200 with `{"items": [...], "total": int}`

### Requirement: Get workflow with current version
`GET /api/workflows/{id}` SHALL return the workflow metadata plus the current version's graph_dsl and compiled_graph.

#### Scenario: Get existing workflow
- **WHEN** GET /api/workflows/{id} for an existing workflow
- **THEN** response is 200 with workflow fields plus `version` containing graph_dsl and compiled_graph

#### Scenario: Get non-existent workflow
- **WHEN** GET /api/workflows/{id} for a non-existent ID
- **THEN** response is 404

### Requirement: Update workflow creates new version
`PUT /api/workflows/{id}` SHALL accept name and/or graph_dsl changes, increment current_version, compile the new DSL, and store a new WorkflowVersionModel. Previous versions SHALL remain immutable.

#### Scenario: Update workflow DSL
- **WHEN** PUT /api/workflows/{id} with `{"graph_dsl": {...}, "change_summary": "added condition node"}`
- **THEN** response is 200 with current_version incremented and a new version created

#### Scenario: Update workflow name only
- **WHEN** PUT /api/workflows/{id} with `{"name": "New Name"}`
- **THEN** name is updated but no new version is created (DSL unchanged)

### Requirement: Delete workflow
`DELETE /api/workflows/{id}` SHALL soft-delete the workflow (set deleted_at). Versions remain for audit.

#### Scenario: Delete existing workflow
- **WHEN** DELETE /api/workflows/{id}
- **THEN** response is 204 and subsequent GET returns 404

### Requirement: Validate workflow DSL
`POST /api/workflows/{id}/validate` SHALL run the DSL through the compiler (dry-run) without executing and return validation errors or success.

#### Scenario: Validate valid DSL
- **WHEN** POST /api/workflows/{id}/validate with valid graph_dsl
- **THEN** response is 200 with `{"valid": true}`

#### Scenario: Validate invalid DSL
- **WHEN** POST /api/workflows/{id}/validate with graph_dsl missing required edges
- **THEN** response is 200 with `{"valid": false, "errors": ["..."]}`

### Requirement: Get workflow version history
`GET /api/workflows/{id}/versions` SHALL return all versions ordered by version number descending.

#### Scenario: List versions
- **WHEN** GET /api/workflows/{id}/versions
- **THEN** response is 200 with array of versions including change_summary and created_at
