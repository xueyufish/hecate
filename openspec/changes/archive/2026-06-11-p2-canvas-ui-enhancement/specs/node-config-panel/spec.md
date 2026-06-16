## MODIFIED Requirements

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
