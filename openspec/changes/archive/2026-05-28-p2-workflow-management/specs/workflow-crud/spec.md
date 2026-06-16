## ADDED Requirements

### Requirement: Create workflow
The system SHALL provide an API endpoint `POST /api/workflows` that accepts a workflow definition (name, graph_dsl) and creates a new workflow with an initial version.

#### Scenario: Successful creation
- **WHEN** a user sends a POST request with valid name and graph_dsl
- **THEN** the system creates a WorkflowModel and WorkflowVersionModel (version=1), returns 201 with the workflow data

#### Scenario: Invalid graph_dsl
- **WHEN** a user sends a POST request with graph_dsl that fails GraphCompiler validation
- **THEN** the system returns 422 with the validation error details

#### Scenario: Missing required fields
- **WHEN** a user sends a POST request without name or graph_dsl
- **THEN** the system returns 422 with field validation errors

### Requirement: Read workflow
The system SHALL provide an API endpoint `GET /api/workflows/{id}` that returns the workflow basic info and current version details.

#### Scenario: Workflow exists
- **WHEN** a user sends a GET request with a valid workflow ID
- **THEN** the system returns 200 with the workflow data including current version's graph_dsl

#### Scenario: Workflow not found
- **WHEN** a user sends a GET request with a non-existent workflow ID
- **THEN** the system returns 404

### Requirement: Update workflow
The system SHALL provide an API endpoint `PUT /api/workflows/{id}` that updates the workflow name and/or creates a new version with updated graph_dsl.

#### Scenario: Update name only
- **WHEN** a user sends a PUT request with only name changed
- **THEN** the system updates the workflow name, returns 200

#### Scenario: Update graph_dsl creates new version
- **WHEN** a user sends a PUT request with updated graph_dsl
- **THEN** the system creates a new WorkflowVersionModel with incremented version number, returns 200

#### Scenario: Invalid graph_dsl on update
- **WHEN** a user sends a PUT request with invalid graph_dsl
- **THEN** the system returns 422 with validation error, no changes persisted

### Requirement: Delete workflow
The system SHALL provide an API endpoint `DELETE /api/workflows/{id}` that soft-deletes the workflow.

#### Scenario: Successful deletion
- **WHEN** a user sends a DELETE request with a valid workflow ID
- **THEN** the system sets deleted_at on the workflow, returns 204

#### Scenario: Workflow not found
- **WHEN** a user sends a DELETE request with a non-existent workflow ID
- **THEN** the system returns 404

### Requirement: List workflows
The system SHALL provide an API endpoint `GET /api/workflows` that returns a paginated list of workflows.

#### Scenario: List with pagination
- **WHEN** a user sends a GET request with page and page_size parameters
- **THEN** the system returns 200 with paginated workflow list

#### Scenario: List excludes deleted workflows
- **WHEN** a user lists workflows
- **THEN** soft-deleted workflows SHALL NOT appear in the results
