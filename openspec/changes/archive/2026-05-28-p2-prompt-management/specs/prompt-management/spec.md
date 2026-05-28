## ADDED Requirements

### Requirement: Create prompt
The system SHALL provide an API endpoint `POST /api/prompts` that creates a new prompt with initial version.

#### Scenario: Successful creation
- **WHEN** a user sends a POST request with name, template, and variables
- **THEN** the system creates a PromptModel and PromptVersionModel (version=1), returns 201

### Requirement: Read prompt
The system SHALL provide `GET /api/prompts/{id}` that returns the prompt with current version.

#### Scenario: Prompt exists
- **WHEN** a user sends a GET request with valid prompt ID
- **THEN** the system returns 200 with prompt data including template and variables

### Requirement: Update prompt
The system SHALL provide `PUT /api/prompts/{id}` that updates the prompt and creates a new version.

#### Scenario: Update creates new version
- **WHEN** a user sends a PUT request with updated template
- **THEN** the system creates a new version with incremented version number

### Requirement: Delete prompt
The system SHALL provide `DELETE /api/prompts/{id}` that soft-deletes the prompt.

#### Scenario: Successful deletion
- **WHEN** a user sends a DELETE request
- **THEN** the system sets deleted_at, returns 204

### Requirement: List prompts
The system SHALL provide `GET /api/prompts` that returns paginated prompts.

#### Scenario: List with pagination
- **WHEN** a user sends a GET request with page and page_size
- **THEN** the system returns paginated prompt list

### Requirement: Version management
The system SHALL provide version management endpoints for prompts.

#### Scenario: List versions
- **WHEN** a user sends GET /api/prompts/{id}/versions
- **THEN** the system returns all versions ordered by version number

#### Scenario: Rollback to version
- **WHEN** a user sends POST /api/prompts/{id}/rollback/{version}
- **THEN** the system creates a new version with target version's template

### Requirement: Label deployment
The system SHALL support labels (production/staging/development) for prompt deployment.

#### Scenario: Get prompt by label
- **WHEN** a user sends GET /api/prompts/by-label/production
- **THEN** the system returns the prompt with the "production" label
