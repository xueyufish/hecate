## MODIFIED Requirements

### Requirement: Agent Configurator form layout
The system SHALL provide an `AgentConfigurator` component that displays a tabbed form for configuring an Agent. The form SHALL have 5 tabs: Basic, Knowledge, Tools, Memory, and Advanced. The component SHALL support both create mode (empty form) and edit mode (pre-populated form).

#### Scenario: Create mode displays empty form
- **WHEN** the user navigates to `/agents/new`
- **THEN** the system SHALL display the AgentConfigurator with all fields empty and default values

#### Scenario: Edit mode displays populated form
- **WHEN** the user navigates to `/agents/[id]`
- **THEN** the system SHALL fetch the agent data and display the AgentConfigurator with fields pre-populated

#### Scenario: Tab navigation
- **WHEN** the user clicks a tab
- **THEN** the system SHALL display the corresponding section without losing data in other tabs
