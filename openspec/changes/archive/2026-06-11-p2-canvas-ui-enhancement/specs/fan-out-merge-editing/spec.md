## ADDED Requirements

### Requirement: Fan-out and merge nodes in node palette
The NodePalette component SHALL include fan-out and merge as draggable items, allowing users to create these nodes interactively.

#### Scenario: Fan-out in node palette
- **WHEN** the workflow editor loads
- **THEN** the node palette SHALL include a "Fan Out" item with the fork icon and indigo color

#### Scenario: Merge in node palette
- **WHEN** the workflow editor loads
- **THEN** the node palette SHALL include a "Merge" item with the merge icon and slate color

#### Scenario: Drag fan-out to canvas
- **WHEN** the user drags the "Fan Out" item from the palette onto the canvas
- **THEN** a new fan-out node is created at the drop location with a default branch count of 2

#### Scenario: Drag merge to canvas
- **WHEN** the user drags the "Merge" item from the palette onto the canvas
- **THEN** a new merge node is created at the drop location

### Requirement: Fan-out node branch configuration
When a fan-out node is selected, the config panel SHALL display a branch count selector (2-6) and list the connected branch target nodes.

#### Scenario: Configure branch count
- **WHEN** the user changes the branch count from 2 to 3 in the fan-out config panel
- **THEN** the fan-out node's `config.branches` SHALL be updated to accommodate 3 branch targets

#### Scenario: Branch targets listed
- **WHEN** the user selects a fan-out node with edges to nodes "analyst_a", "analyst_b", "analyst_c"
- **THEN** the config panel SHALL list these target nodes and update `config.branches` to ["analyst_a", "analyst_b", "analyst_c"]

#### Scenario: Branch targets auto-synced from edges
- **WHEN** the user connects a new edge from the fan-out node to a target node
- **THEN** the fan-out config panel SHALL automatically update the branch list to include the new target

### Requirement: Merge node source configuration
When a merge node is selected, the config panel SHALL display a fan-out source selector and an output channel field.

#### Scenario: Configure fan-out source
- **WHEN** the user selects a merge node and chooses "fanout" from the fan-out source dropdown
- **THEN** the node's `config.fan_out_source` SHALL be set to "fanout"

#### Scenario: Configure output channel
- **WHEN** the user enters "analysis_results" in the output channel field
- **THEN** the node's `config.output_channel` SHALL be set to "analysis_results"

#### Scenario: Fan-out source dropdown lists available fan-out nodes
- **WHEN** the user opens the fan-out source dropdown for a merge node
- **THEN** the dropdown SHALL list all fan-out nodes currently on the canvas

### Requirement: Fan-out/merge validation warnings
The config panel SHALL display visual warnings when fan-out or merge nodes have invalid configurations.

#### Scenario: Fan-out with no branches warning
- **WHEN** a fan-out node has no connected branch edges
- **THEN** the config panel SHALL display a warning "No branches connected. Connect edges to target nodes."

#### Scenario: Merge with no fan-out source warning
- **WHEN** a merge node has no `config.fan_out_source` set
- **THEN** the config panel SHALL display a warning "No fan-out source linked. Select a fan-out node as source."

### Requirement: Fan-out visual badge on canvas
Fan-out nodes SHALL display a badge showing the number of connected branches.

#### Scenario: Fan-out branch count badge
- **WHEN** a fan-out node has 3 connected branch edges
- **THEN** the node SHALL display a badge "×3" indicating 3 branches
