## ADDED Requirements

### Requirement: Agent Configurator form layout
The system SHALL provide an `AgentConfigurator` component that displays a tabbed form for configuring an Agent. The form SHALL have 4 tabs: Basic, Knowledge, Tools, and Advanced. The component SHALL support both create mode (empty form) and edit mode (pre-populated form).

#### Scenario: Create mode displays empty form
- **WHEN** the user navigates to `/agents/new`
- **THEN** the system SHALL display the AgentConfigurator with all fields empty and default values

#### Scenario: Edit mode displays populated form
- **WHEN** the user navigates to `/agents/[id]`
- **THEN** the system SHALL fetch the agent data and display the AgentConfigurator with fields pre-populated

#### Scenario: Tab navigation
- **WHEN** the user clicks a tab
- **THEN** the system SHALL display the corresponding section without losing data in other tabs

### Requirement: Basic tab fields
The Basic tab SHALL contain: Name (required text input), Persona (textarea for system prompt), Model (grouped dropdown with provider sections), and Mode (select: chat/three_layer).

#### Scenario: Name validation
- **WHEN** the user leaves the Name field empty and submits
- **THEN** the system SHALL display a validation error and prevent submission

#### Scenario: Model selector with provider grouping
- **WHEN** the Model dropdown is opened
- **THEN** the system SHALL display models grouped by provider with availability indicators

#### Scenario: Mode selection
- **WHEN** the user selects a mode
- **THEN** the system SHALL store the selection and use it for agent creation/update

### Requirement: Knowledge tab fields
The Knowledge tab SHALL contain: Knowledge Bases (multi-select from available KBs) and Skills (multi-select from available skills).

#### Scenario: Knowledge base selection
- **WHEN** the user opens the Knowledge Bases selector
- **THEN** the system SHALL display all available knowledge bases and allow multi-select

#### Scenario: Skill selection
- **WHEN** the user opens the Skills selector
- **THEN** the system SHALL display all available skills and allow multi-select

#### Scenario: Empty state when no KBs/skills exist
- **WHEN** there are no knowledge bases or skills configured
- **THEN** the system SHALL display an empty state message with a link to create one

### Requirement: Tools tab fields
The Tools tab SHALL contain: Tools (multi-select from available tools, optionally grouped by category).

#### Scenario: Tool selection
- **WHEN** the user opens the Tools selector
- **THEN** the system SHALL display all available tools and allow multi-select

#### Scenario: Tool deselection
- **WHEN** the user deselects a tool
- **THEN** the system SHALL remove it from the agent's tool list

### Requirement: Advanced tab fields
The Advanced tab SHALL contain: Risk Level (select: LOW/MEDIUM/HIGH), Opening Remarks (optional textarea), Enable Suggestions (toggle, default true), and Mode-specific settings.

#### Scenario: Risk level selection
- **WHEN** the user selects a risk level
- **THEN** the system SHALL store the selection and use it for agent creation/update

#### Scenario: Opening remarks configuration
- **WHEN** the user enters opening remarks text
- **THEN** the system SHALL store the text as the agent's static opening greeting

#### Scenario: Suggestions toggle
- **WHEN** the user toggles "Enable Suggestions" off
- **THEN** the system SHALL set `enable_suggestions` to false for the agent

### Requirement: Form submission
The form SHALL submit all configured fields to the API. In create mode, it SHALL POST to `/api/agents`. In edit mode, it SHALL PUT to `/api/agents/{id}`. On success, it SHALL navigate to the agent detail page.

#### Scenario: Successful creation
- **WHEN** the user fills in required fields and clicks "Create"
- **THEN** the system SHALL POST to `/api/agents` and navigate to `/agents/{new_id}`

#### Scenario: Successful update
- **WHEN** the user modifies fields and clicks "Save"
- **THEN** the system SHALL PUT to `/api/agents/{id}` and show a success message

#### Scenario: Submission error
- **WHEN** the API returns an error
- **THEN** the system SHALL display the error message and keep the form data intact

### Requirement: Data loading for selectors
The system SHALL load available tools, skills, and knowledge bases from the API when the configurator mounts. Loading states SHALL be displayed while data is being fetched.

#### Scenario: Parallel data loading
- **WHEN** the configurator mounts
- **THEN** the system SHALL fetch tools, skills, knowledge bases, and models in parallel

#### Scenario: Loading state
- **WHEN** data is being fetched
- **THEN** the system SHALL display skeleton loaders in the selector fields

#### Scenario: Error state
- **WHEN** data fetching fails
- **THEN** the system SHALL display an error message with a retry button
