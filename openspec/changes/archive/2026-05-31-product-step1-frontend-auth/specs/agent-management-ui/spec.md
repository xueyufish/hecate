## ADDED Requirements

### Requirement: Agent list page
The system SHALL display all agents owned by the current user in a list with name, model, and status.

#### Scenario: View agent list
- **WHEN** user navigates to the Agents page
- **THEN** system shows a list of agents with name, model name, creation date, and a "Create Agent" button

#### Scenario: Empty state
- **WHEN** user has no agents
- **THEN** system shows an empty state with a prompt to create the first agent

### Requirement: Create agent
The system SHALL allow users to create a new agent with name, description, model selection, and system prompt.

#### Scenario: Successful creation
- **WHEN** user fills in agent name, selects a model, optionally sets system prompt, and clicks Create
- **THEN** system creates the agent via API and redirects to the agent detail page

#### Scenario: Model selection
- **WHEN** user opens model selector
- **THEN** system shows available models fetched from `GET /v1/models`

### Requirement: Agent detail and configuration
The system SHALL allow users to view and edit agent configuration including tools and knowledge bases.

#### Scenario: View agent config
- **WHEN** user opens an agent's detail page
- **THEN** system shows current config: name, description, model, system prompt, bound tools, bound knowledge bases

#### Scenario: Bind tools
- **WHEN** user opens tool binding section
- **THEN** system shows available tools (from `GET /api/tools`) with toggle to bind/unbind each tool

#### Scenario: Bind knowledge bases
- **WHEN** user opens knowledge base binding section
- **THEN** system shows available knowledge bases (from `GET /api/knowledge-bases`) with toggle to bind/unbind

### Requirement: Delete agent
The system SHALL allow users to delete an agent with confirmation.

#### Scenario: Delete with confirmation
- **WHEN** user clicks delete and confirms
- **THEN** system deletes the agent and redirects to agent list
