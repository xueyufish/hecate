## ADDED Requirements

### Requirement: List workflow versions
The system SHALL provide an API endpoint `GET /api/workflows/{id}/versions` that returns all versions of a workflow ordered by version number.

#### Scenario: List versions
- **WHEN** a user sends a GET request for a workflow's versions
- **THEN** the system returns 200 with a list of all versions, each containing version number, graph_dsl, change_summary, and created_at

#### Scenario: Workflow not found
- **WHEN** a user sends a GET request for versions of a non-existent workflow
- **THEN** the system returns 404

### Requirement: Get specific version
The system SHALL provide an API endpoint `GET /api/workflows/{id}/versions/{version}` that returns a specific version's details.

#### Scenario: Version exists
- **WHEN** a user sends a GET request for a specific version
- **THEN** the system returns 200 with the version's full graph_dsl and compiled_graph

#### Scenario: Version not found
- **WHEN** a user sends a GET request for a non-existent version
- **THEN** the system returns 404

### Requirement: Rollback to version
The system SHALL provide an API endpoint `POST /api/workflows/{id}/rollback/{version}` that creates a new version with the graph_dsl from the specified version.

#### Scenario: Successful rollback
- **WHEN** a user sends a POST request to rollback to version 2
- **THEN** the system creates a new version (e.g., version 5) with version 2's graph_dsl, sets it as current, returns 200

#### Scenario: Rollback to non-existent version
- **WHEN** a user sends a POST request to rollback to a non-existent version
- **THEN** the system returns 404

### Requirement: Version auto-increment
The system SHALL automatically increment version numbers when creating new versions.

#### Scenario: Sequential version numbers
- **WHEN** a workflow has versions 1, 2, 3 and a new version is created
- **THEN** the new version SHALL have version number 4

#### Scenario: Version number from initial creation
- **WHEN** a workflow is created for the first time
- **THEN** the initial version SHALL have version number 1

### Requirement: Change summary
The system SHALL accept an optional change_summary when creating new versions.

#### Scenario: Summary provided
- **WHEN** a user provides change_summary when updating graph_dsl
- **THEN** the new version SHALL store the change_summary

#### Scenario: Summary omitted
- **WHEN** a user does not provide change_summary
- **THEN** the new version SHALL have an empty change_summary
