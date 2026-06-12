## ADDED Requirements

### Requirement: Agent node config panel with structured form
The agent node config panel SHALL display a structured form with the following fields: agent selector (dropdown), role description (textarea), invocation mode (radio), channel selector (dual-list), and model override (text input).

#### Scenario: Agent node config panel renders all fields
- **WHEN** the user clicks an agent node on the canvas
- **THEN** the config panel displays: agent selector dropdown, role description textarea, invocation mode radio (direct/tool), channel selector with readable/writable lists, and model override text input

#### Scenario: Agent selector populates from API
- **WHEN** the user opens the agent node config panel
- **THEN** the agent selector dropdown SHALL fetch available agents from `/api/agents` and display them by name

#### Scenario: Agent selector sets agent_ref
- **WHEN** the user selects an agent from the dropdown
- **THEN** the node's `config.agent_ref` SHALL be set to the selected agent's ID

### Requirement: Role description field maps to system_prompt
The agent node config panel SHALL include a role description textarea that maps to the node's `config.system_prompt` field.

#### Scenario: Role description updates system_prompt
- **WHEN** the user enters "You are a research analyst" in the role description field
- **THEN** the node's `config.system_prompt` SHALL be updated to "You are a research analyst"

### Requirement: Invocation mode selector
The agent node config panel SHALL include an invocation mode radio selector with options "Direct" and "Tool", mapping to `config.invocation_mode` values "direct" and "tool" respectively.

#### Scenario: Select direct invocation mode
- **WHEN** the user selects "Direct" in the invocation mode radio
- **THEN** the node's `config.invocation_mode` SHALL be set to "direct"

#### Scenario: Select tool invocation mode
- **WHEN** the user selects "Tool" in the invocation mode radio
- **THEN** the node's `config.invocation_mode` SHALL be set to "tool"

### Requirement: Channel selector with readable/writable dual-list
The agent node config panel SHALL include a channel selector component that reads available channels from the graph's `state` declaration and allows the user to assign channels as readable, writable, both, or neither for the agent.

#### Scenario: Channel selector shows graph state channels
- **WHEN** the user opens the channel selector for an agent node in a graph with state channels "messages", "research_data", and "analysis_results"
- **THEN** the selector SHALL display all three channels as options

#### Scenario: Assign readable channels
- **WHEN** the user moves "messages" and "research_data" to the readable list
- **THEN** the node's `config.channels.readable` SHALL be set to ["messages", "research_data"]

#### Scenario: Assign writable channels
- **WHEN** the user moves "messages" and "analysis_results" to the writable list
- **THEN** the node's `config.channels.writable` SHALL be set to ["messages", "analysis_results"]

#### Scenario: Empty graph shows channel hint
- **WHEN** the graph has no state channels declared
- **THEN** the channel selector SHALL display a hint "Add channels in graph settings" and allow freeform channel name entry

### Requirement: Model override field
The agent node config panel SHALL include a model override text input that sets `config.model` for the agent node.

#### Scenario: Model override sets config.model
- **WHEN** the user enters "gpt-4o-mini" in the model override field
- **THEN** the node's `config.model` SHALL be set to "gpt-4o-mini"

#### Scenario: Empty model override removes override
- **WHEN** the user clears the model override field
- **THEN** the node's `config.model` SHALL be removed from the config
