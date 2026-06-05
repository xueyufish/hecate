## ADDED Requirements

### Requirement: Fan-out and merge node types defined in frontend type system
The frontend NodeTypeSchema SHALL include "fan-out" and "merge" as valid node type enum values, matching the backend DSL schema.

#### Scenario: DSL with fan-out node loads without error
- **WHEN** a Graph DSL containing a node with type "fan-out" is loaded via dslToReactFlow
- **THEN** the node is rendered on the canvas without errors and without being dropped

### Requirement: Fan-out and merge nodes render with distinct visual components
Fan-out nodes SHALL display with a distinct icon and color (e.g., fork icon, indigo). Merge nodes SHALL display with a distinct icon and color (e.g., merge icon, slate).

#### Scenario: Fan-out node visual rendering
- **WHEN** a fan-out node is loaded from DSL onto the canvas
- **THEN** it renders with an indigo background, fork-like icon, and label "Fan Out"

#### Scenario: Merge node visual rendering
- **WHEN** a merge node is loaded from DSL onto the canvas
- **THEN** it renders with a slate background, merge icon, and label "Merge"

### Requirement: Fan-out and merge are not in the node palette
The NodePalette component SHALL NOT include fan-out or merge as draggable items. These node types can only appear when loaded from an existing DSL.

#### Scenario: Node palette contents
- **WHEN** the workflow editor loads
- **THEN** the node palette shows exactly 6 items: Conversation, Condition, Tool Call, Agent, Knowledge Retrieval, Variable Set

### Requirement: DSL bridge maps fan-out and merge labels
The NODE_TYPE_LABELS map in dsl-bridge.ts SHALL include entries for "fan-out" → "Fan Out" and "merge" → "Merge".

#### Scenario: Fan-out node label from DSL
- **WHEN** dslToReactFlow processes a node with type "fan-out"
- **THEN** the node's label defaults to "Fan Out" if no system_prompt or other label source exists
