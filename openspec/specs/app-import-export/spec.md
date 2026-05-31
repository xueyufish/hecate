## ADDED Requirements

### Requirement: Agent Export Format
The system SHALL define an export JSON format with fields: `version` (string), `exported_at` (ISO timestamp), `agent` (config object), `workflow` (optional Graph DSL), `memory_blocks` (list of block configs).

#### Scenario: Export structure
- **WHEN** an agent is exported
- **THEN** the JSON SHALL contain `version`, `exported_at`, `agent` with all config fields (name, persona, model_config, mode, tools, skills, knowledge_base_ids, risk_level, opening_remarks, enable_suggestions), and optionally `workflow` and `memory_blocks`

### Requirement: Agent Export Endpoint
The system SHALL provide `GET /api/agents/{id}/export` that returns the agent configuration as a downloadable JSON file.

#### Scenario: Export agent
- **WHEN** a user requests `GET /api/agents/{id}/export`
- **THEN** the system SHALL return a JSON file with `Content-Disposition: attachment` header

#### Scenario: Export agent with workflow
- **WHEN** an agent has `mode=workflow` and an associated workflow
- **THEN** the export SHALL include the workflow's Graph DSL in the `workflow` field

#### Scenario: Export agent with memory blocks
- **WHEN** an agent has memory blocks
- **THEN** the export SHALL include the memory blocks in the `memory_blocks` field

#### Scenario: Export non-existent agent
- **WHEN** a user requests export for a non-existent agent
- **THEN** the system SHALL return HTTP 404

### Requirement: Agent Import Endpoint
The system SHALL provide `POST /api/agents/import` that accepts a JSON file and creates a new agent from the exported configuration.

#### Scenario: Import agent
- **WHEN** a user submits `POST /api/agents/import` with a valid export JSON
- **THEN** the system SHALL create a new agent with the exported configuration and return the agent data

#### Scenario: Import with workflow
- **WHEN** the export includes a `workflow` field
- **THEN** the system SHALL create a new workflow and link it to the imported agent

#### Scenario: Import with memory blocks
- **WHEN** the export includes `memory_blocks`
- **THEN** the system SHALL create memory blocks for the new agent

#### Scenario: Import with missing KBs
- **WHEN** the export references KB IDs that don't exist in the target environment
- **THEN** the system SHALL log a warning and import the agent without those KBs

#### Scenario: Import invalid JSON
- **WHEN** the submitted JSON is invalid or missing required fields
- **THEN** the system SHALL return HTTP 422 with a validation error

### Requirement: Frontend Export Button
The agent detail page SHALL display an "Export" button that downloads the agent configuration as a JSON file.

#### Scenario: Export from detail page
- **WHEN** the user clicks "Export" on the agent detail page
- **THEN** the browser SHALL download a JSON file named `{agent-name}.json`

### Requirement: Frontend Import Button
The agents list page SHALL display an "Import Agent" button that opens a file upload dialog. Users SHALL be able to select a JSON file to import.

#### Scenario: Import from list page
- **WHEN** the user clicks "Import Agent" and selects a JSON file
- **THEN** the system SHALL upload the file, create the agent, and navigate to the new agent's detail page

#### Scenario: Import with errors
- **WHEN** the import fails due to invalid JSON or missing required fields
- **THEN** the system SHALL display an error message
