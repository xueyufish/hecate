## ADDED Requirements

### Requirement: Template customization mode after loading
After loading an orchestration template, the system SHALL enable template customization mode, allowing the user to edit agent roles, add/remove nodes, adjust connections, and modify channel declarations.

#### Scenario: Load template enters customization mode
- **WHEN** the user loads a template from the template picker
- **THEN** the canvas enters customization mode with all editing capabilities enabled

#### Scenario: Customization mode visual indicator
- **WHEN** the canvas is in customization mode
- **THEN** the toolbar SHALL display a "Customizing: {template name}" indicator with a "Save as Workflow" button

### Requirement: Edit agent roles in customized template
The user SHALL be able to modify the system prompt (role description) of any agent node in a customized template.

#### Scenario: Change agent role in template
- **WHEN** the user clicks an agent node in a customized template and changes the role description
- **THEN** the node's `config.system_prompt` SHALL update immediately on the canvas

### Requirement: Add and remove agent nodes in customized template
The user SHALL be able to add new agent nodes to and remove existing agent nodes from a customized template.

#### Scenario: Add agent node to customized template
- **WHEN** the user drags an agent from the palette onto the canvas in customization mode
- **THEN** a new agent node is created and connected to the graph

#### Scenario: Remove agent node from customized template
- **WHEN** the user selects an agent node and presses Delete in customization mode
- **THEN** the agent node and its connected edges SHALL be removed from the canvas

### Requirement: Save customized template as new workflow
The user SHALL be able to save a customized template as a new workflow via a "Save as Workflow" action.

#### Scenario: Save customized template
- **WHEN** the user clicks "Save as Workflow" in customization mode
- **THEN** the system SHALL convert the current canvas state to Graph DSL JSON via `reactFlowToDsl()` and save it as a new workflow

#### Scenario: Save requires workflow name
- **WHEN** the user clicks "Save as Workflow"
- **THEN** a dialog SHALL prompt for a workflow name before saving

### Requirement: Original template remains unmodified
Customizing a template SHALL NOT modify the original template.

#### Scenario: Template picker shows original template
- **WHEN** the user saves a customized template as a new workflow and reopens the template picker
- **THEN** the original template SHALL still appear unmodified in the template list
