## ADDED Requirements

### Requirement: Node positions persist to localStorage
When nodes are moved or the workflow is saved, the system SHALL persist each node's x/y position to localStorage, keyed by `hecate-layout-{workflowId}`.

#### Scenario: Save layout after node drag
- **WHEN** user drags a node to a new position on the canvas
- **THEN** the node positions are saved to localStorage under `hecate-layout-{workflowId}` after the existing 2-second auto-save debounce

#### Scenario: Restore layout on page load
- **WHEN** user opens a workflow that has a saved layout in localStorage
- **THEN** nodes are rendered at their saved positions instead of the default grid layout

### Requirement: Fallback to grid layout when no saved positions exist
When loading a workflow with no localStorage layout data, the system SHALL use the existing grid-based layout formula.

#### Scenario: First time opening a workflow
- **WHEN** user opens a workflow that has never been edited on this device
- **THEN** nodes are positioned using the default grid layout (250px horizontal spacing, 150px vertical spacing)

#### Scenario: Clear localStorage manually
- **WHEN** user clears browser localStorage
- **THEN** on next page load, nodes fall back to the default grid layout

### Requirement: Layout data is independent of DSL
The layout storage SHALL be completely independent of the Graph DSL. The DSL SHALL NOT contain position fields. Layout and DSL are loaded separately and merged at render time.

#### Scenario: DSL contains no position data
- **WHEN** the backend returns a Graph DSL via GET /api/workflows/{id}
- **THEN** the response contains no x/y position fields for nodes
