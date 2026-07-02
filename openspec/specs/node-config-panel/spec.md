## ADDED Requirements

### Requirement: Node selection activates ConfigPanel in right-side panel
When a user clicks a node on the workflow canvas, the system SHALL display the ConfigPanel component in the right-side panel (300px width), populated with that node's current configuration. For agent nodes, the panel SHALL display the enhanced structured form (agent selector, role description, invocation mode, channel selector, model override) instead of the previous single `agent_ref` text input.

#### Scenario: Click a node to open configuration
- **WHEN** user clicks a conversation node on the canvas
- **THEN** the right-side panel displays ConfigPanel with the node's model and system_prompt fields pre-filled

#### Scenario: Click an agent node to open enhanced configuration
- **WHEN** user clicks an agent node on the canvas
- **THEN** the right-side panel displays ConfigPanel with the agent structured form: agent selector dropdown, role description textarea, invocation mode radio, channel selector, and model override input, all pre-filled with the node's current config values

#### Scenario: Click canvas background to deselect
- **WHEN** user clicks empty canvas area (no node)
- **THEN** the right-side panel displays placeholder text "Select a node to configure" and the selected node state is cleared

### Requirement: ConfigPanel edits propagate to canvas and auto-save
When a user edits a node property in ConfigPanel, the change SHALL update the node data on the canvas and trigger the existing auto-save mechanism (2-second debounce to API).

#### Scenario: Edit model name in conversation node
- **WHEN** user changes the model field from "gpt-4o" to "gpt-4o-mini" in ConfigPanel
- **THEN** the node's data.config.model updates on the canvas, and after 2 seconds the change is saved to the backend via PUT /api/workflows/{id}

### Requirement: Right-side panel width is 300px
The right-side panel SHALL have a fixed width of 300px, matching the ConfigPanel component's built-in width.

#### Scenario: Panel width consistency
- **WHEN** the workflow editor page loads
- **THEN** the right-side panel renders at exactly 300px width

### Requirement: Test-run results remain in bottom panel
The right-side panel SHALL be used exclusively for node configuration editing. Test-run input form and execution results SHALL remain in the existing bottom panel and right-side result display area.

#### Scenario: Test-run result display after clicking a node
- **WHEN** user has a test-run result and clicks a node to configure it
- **THEN** the right-side panel shows ConfigPanel, and test-run results remain accessible in the bottom panel
