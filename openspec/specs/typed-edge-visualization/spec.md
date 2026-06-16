## ADDED Requirements

### Requirement: Default edge type rendered as solid gray Bezier
Default edges (no type or `type: "default"`) SHALL render as solid gray Bezier curves, matching React Flow's default rendering.

#### Scenario: Default edge visual
- **WHEN** an edge has no `data.edgeType` or `data.edgeType` is "default"
- **THEN** the edge SHALL render as a solid gray (#94a3b8) Bezier curve with no label

### Requirement: Handoff edge type rendered as dashed purple
Handoff edges SHALL render as dashed purple Bezier curves with a "Handoff" label. This replaces the existing handoff rendering.

#### Scenario: Handoff edge visual
- **WHEN** an edge has `data.edgeType` set to "handoff"
- **THEN** the edge SHALL render as a dashed purple (#8b5cf6) Bezier curve with a "Handoff" label at the midpoint

### Requirement: Conditional edge type rendered as dotted with label
Conditional edges SHALL render as dotted dark amber Bezier curves with the condition key displayed as a label.

#### Scenario: Conditional edge visual
- **WHEN** an edge has `data.edgeType` set to "conditional" and `data.label` set to "finance"
- **THEN** the edge SHALL render as a dotted dark amber (#d97706) Bezier curve with a "finance" label at the midpoint

#### Scenario: Conditional edge without label
- **WHEN** an edge has `data.edgeType` set to "conditional" and no `data.label`
- **THEN** the edge SHALL render as a dotted dark amber Bezier curve with a "Condition" label at the midpoint

### Requirement: Fan-out edge type rendered with branch indicators
Fan-out edges SHALL render as solid indigo lines with small arrow indicators showing parallel branching.

#### Scenario: Fan-out edge visual
- **WHEN** an edge originates from a fan-out node
- **THEN** the edge SHALL render as a solid indigo (#6366f1) line with a small fork icon at the source

### Requirement: Edge type selector on connect
When the user creates a connection between two nodes, the system SHALL present an edge type selector with options: Default, Handoff, Conditional.

#### Scenario: Show edge type selector
- **WHEN** the user drags a connection from node A to node B
- **THEN** a popover SHALL appear near the connection point with options: Default (solid), Handoff (dashed), Conditional (dotted)

#### Scenario: Select default edge type
- **WHEN** the user selects "Default" in the edge type selector
- **THEN** the edge SHALL be created with `data.edgeType` set to "default"

#### Scenario: Select handoff edge type
- **WHEN** the user selects "Handoff" in the edge type selector
- **THEN** the edge SHALL be created with `data.edgeType` set to "handoff" and rendered as dashed purple

#### Scenario: Select conditional edge type
- **WHEN** the user selects "Conditional" in the edge type selector
- **THEN** the edge SHALL be created with `data.edgeType` set to "conditional" and the user SHALL be prompted to enter a condition label

#### Scenario: Handoff handle shortcut preserved
- **WHEN** the user connects from an agent node's "handoff" source handle
- **THEN** the edge SHALL automatically be created as handoff type without showing the selector

#### Scenario: Connection to fan-out node auto-sets type
- **WHEN** the user connects any node to a fan-out node
- **THEN** the edge SHALL automatically be created with fan-out edge type without showing the selector

### Requirement: Edge type changeable after creation
The user SHALL be able to change an existing edge's type by clicking the edge and selecting a new type.

#### Scenario: Change edge type via context menu
- **WHEN** the user clicks an existing edge
- **THEN** a context menu SHALL appear with edge type options: Default, Handoff, Conditional
- **THEN** selecting a new type SHALL update the edge's visual style and `data.edgeType`
