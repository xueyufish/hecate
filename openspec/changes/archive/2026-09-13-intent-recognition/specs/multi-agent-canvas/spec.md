## ADDED Requirements

### Requirement: Controller node on canvas

The canvas SHALL support the CONTROLLER node type as a first-class node with a dedicated config panel. The panel SHALL provide: an intent package picker listing the workspace's packages and their published versions (with an optional version pin, defaulting to latest published); an intent-to-workflow mapping editor whose rows are rendered from the referenced package version's categories, each row mapping a category to a route target selected from the graph's workflow/agent nodes; start workflow, default workflow, and end workflow assignment selectors; and a global-intent section with an enable toggle and an optional goal hint. Mapping rows SHALL refresh when a different package version is selected. The canvas SHALL visualize the intent-to-workflow mapping as edges or an equivalent visual indication from the controller node to the mapped targets.

#### Scenario: Controller node rendered and configured

- **WHEN** the user adds a CONTROLLER node to the canvas and selects a published package in the config panel
- **THEN** the panel SHALL show the mapping editor with one row per package category, and route-target selectors limited to nodes declared in the graph

#### Scenario: Start/default/end assignment persisted

- **WHEN** the user assigns start, default, and end workflows in the panel
- **THEN** the graph DSL node config SHALL carry the package reference, category mappings, and the three workflow assignments

#### Scenario: Mapping rows follow the selected package version

- **WHEN** the user switches the package version pin from "v1" (3 categories) to "v2" (5 categories)
- **THEN** the mapping editor SHALL render rows for v2's categories, preserving existing mappings whose categories still exist and flagging removed categories for reassignment

#### Scenario: Validation errors surfaced in panel

- **WHEN** the user saves a controller config whose default workflow is unset or whose mapping references an undeclared node
- **THEN** the canvas SHALL surface the validation error on the offending field and SHALL NOT persist an invalid config

#### Scenario: Mapping visualized on canvas

- **WHEN** a controller node has categories mapped to two agent nodes and a default workflow
- **THEN** the canvas SHALL visually distinguish the category-mapped edges from the default-workflow edge
