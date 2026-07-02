## MODIFIED Requirements

### Requirement: Edge type differentiation in canvas
The system SHALL render edges with 4 distinct visual types: default (solid gray), handoff (dashed purple), conditional (dotted dark amber with label), and fan-out (solid indigo with branch indicators). The previous 2-type system (handoff vs default) is expanded to include conditional and fan-out edge types.

#### Scenario: Handoff edge rendered as dashed purple
- **WHEN** a graph contains an edge with `data.edgeType` set to "handoff" between two agent nodes
- **THEN** the canvas renders the edge as a dashed purple Bezier curve with a "Handoff" label

#### Scenario: Default edge rendered as solid gray
- **WHEN** a graph contains a standard edge (no `data.edgeType` or `data.edgeType` is "default")
- **THEN** the canvas renders the edge as a solid gray Bezier curve

#### Scenario: Conditional edge rendered as dotted with label
- **WHEN** a graph contains an edge with `data.edgeType` set to "conditional"
- **THEN** the canvas renders the edge as a dotted dark amber Bezier curve with the condition key as a label

#### Scenario: Fan-out edge rendered with branch indicators
- **WHEN** an edge originates from a fan-out node
- **THEN** the canvas renders the edge as a solid indigo line with a fork icon indicator

### Requirement: Edge type selection when connecting nodes
The system SHALL allow users to choose the edge type when connecting two nodes via a popover edge type selector with options: Default, Handoff, Conditional. The previous connection dialog with 2 options is replaced with the popover selector supporting 3+ types. Fan-out edges are auto-created when connecting to a fan-out node.

#### Scenario: User creates handoff connection
- **WHEN** the user connects agent node A to agent node B and selects "Handoff" in the edge type selector
- **THEN** the graph DSL stores the edge with `data.edgeType` set to "handoff"

#### Scenario: User creates conditional connection
- **WHEN** the user connects a condition node to an agent node and selects "Conditional" in the edge type selector
- **THEN** the graph DSL stores the edge with `data.edgeType` set to "conditional" and prompts for a condition label

#### Scenario: Connection to fan-out auto-sets type
- **WHEN** the user connects any node to a fan-out node
- **THEN** the edge is automatically created as a fan-out type without showing the selector
