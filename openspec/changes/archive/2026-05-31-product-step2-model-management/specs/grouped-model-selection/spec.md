## ADDED Requirements

### Requirement: User-facing models endpoint returns grouped models
The GET /v1/models endpoint SHALL return only enabled chat-type models, grouped by provider with display names.

#### Scenario: List models for agent creation
- **WHEN** user requests GET /v1/models
- **THEN** system returns models grouped by provider, each group showing provider display_name and models with id, display_name, and capabilities

### Requirement: Agent creation uses grouped model selector
The frontend model selection dropdown SHALL display models grouped by provider with friendly names.

#### Scenario: Create agent with model selection
- **WHEN** user opens the create agent page
- **THEN** model dropdown shows providers as groups with display names (e.g., "智谱", "OpenAI"), and models as options within each group
