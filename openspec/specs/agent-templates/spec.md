## ADDED Requirements

### Requirement: Agent Template Schema
The system SHALL define an agent template schema with fields: name, description, category, preview (icon, tags), and agent configuration (persona, model_config, tools, skills, knowledge_base_ids, risk_level, opening_remarks, enable_suggestions, memory_blocks).

#### Scenario: Template structure
- **WHEN** a template JSON file is loaded
- **THEN** it SHALL contain metadata (name, description, category, preview) and agent configuration matching AgentCreateSchema

### Requirement: Template List API
The system SHALL provide `GET /api/agent-templates` that returns a list of available templates with metadata (id, name, description, category, preview).

#### Scenario: List templates
- **WHEN** a user requests `GET /api/agent-templates`
- **THEN** the system SHALL return all built-in templates with their metadata

### Requirement: Template Detail API
The system SHALL provide `GET /api/agent-templates/{id}` that returns the full template configuration.

#### Scenario: Get template details
- **WHEN** a user requests `GET /api/agent-templates/{id}`
- **THEN** the system SHALL return the full template including agent configuration

#### Scenario: Template not found
- **WHEN** a user requests a non-existent template
- **THEN** the system SHALL return HTTP 404

### Requirement: Template Instantiation
The system SHALL provide `POST /api/agent-templates/{id}/instantiate` that validates the template configuration and returns it for agent creation.

#### Scenario: Instantiate template
- **WHEN** a user requests `POST /api/agent-templates/{id}/instantiate`
- **THEN** the system SHALL validate KB IDs exist and return the template config ready for `POST /api/agents`

#### Scenario: Invalid KB IDs in template
- **WHEN** a template references KB IDs that don't exist
- **THEN** the system SHALL return HTTP 422 with the invalid KB IDs listed

### Requirement: Built-in Templates
The system SHALL include 5 built-in templates:
1. **Customer Service** — persona for customer support, tools for ticket lookup
2. **Code Review** — persona for code analysis, tools for file reading
3. **Research Assistant** — persona for research, tools for web search
4. **Content Writer** — persona for writing, tools for content generation
5. **Data Analyst** — persona for data analysis, tools for data processing

#### Scenario: Built-in templates available
- **WHEN** the system starts
- **THEN** all 5 built-in templates SHALL be available via the API

### Requirement: Frontend Template Picker
The agent creation page SHALL display a "From Template" button that opens a template picker dialog. Users SHALL be able to browse templates by category and select one to pre-fill the agent creation form.

#### Scenario: Open template picker
- **WHEN** the user clicks "From Template" on the agent creation page
- **THEN** the system SHALL display a dialog with template categories and previews

#### Scenario: Select template
- **WHEN** the user selects a template
- **THEN** the system SHALL close the dialog and pre-fill the agent creation form with the template's configuration

#### Scenario: Cancel template selection
- **WHEN** the user closes the template picker without selecting
- **THEN** the form SHALL remain unchanged
