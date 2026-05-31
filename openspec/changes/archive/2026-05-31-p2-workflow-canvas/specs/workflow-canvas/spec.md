## ADDED Requirements

### Requirement: Canvas renders a directed graph of nodes and edges
The system SHALL render a visual canvas where each DSL node appears as a draggable card with an icon and label, and each DSL edge appears as a directed line between source and target nodes.

#### Scenario: Render a workflow with 3 connected nodes
- **WHEN** a workflow is opened in the canvas editor
- **THEN** all nodes from the graph DSL are rendered as draggable cards at their stored positions, and all edges are rendered as directed lines

#### Scenario: Empty workflow shows entry point
- **WHEN** a new empty workflow is created
- **THEN** the canvas shows a single `__start__` entry node that cannot be deleted

### Requirement: Users can add nodes from a palette
The system SHALL provide a node palette sidebar listing all available node types. Dragging a node type from the palette onto the canvas SHALL create a new node of that type.

#### Scenario: Drag LLM node from palette
- **WHEN** user drags "LLM Call" from the node palette onto the canvas
- **THEN** a new node of type `conversation` appears at the drop position with a generated unique ID and default config

#### Scenario: Drag Condition node from palette
- **WHEN** user drags "Condition" from the node palette onto the canvas
- **THEN** a new node of type `condition` appears with a default expression field

### Requirement: Users can connect nodes with edges
The system SHALL allow users to draw directed edges between nodes by dragging from a source node's output handle to a target node's input handle.

#### Scenario: Connect two nodes
- **WHEN** user drags from node A's output handle to node B's input handle
- **THEN** a directed edge from A to B is created and displayed on the canvas

#### Scenario: Prevent self-loops
- **WHEN** user attempts to connect a node's output to its own input
- **THEN** the connection is rejected and no edge is created

### Requirement: Canvas supports zoom, pan, and minimap
The system SHALL provide zoom controls (+/-/fit), mouse wheel zoom, click-drag panning, and a minimap overview in the bottom-right corner.

#### Scenario: Zoom to fit
- **WHEN** user clicks the "Fit View" button
- **THEN** the canvas zooms and pans so all nodes are visible within the viewport

### Requirement: Canvas state persists per workflow version
Node positions and viewport state SHALL be stored as part of the graph DSL metadata so reopening a workflow restores the visual layout.

#### Scenario: Reopen workflow restores layout
- **WHEN** user closes and reopens a workflow
- **THEN** all nodes appear at their last saved positions with the same viewport zoom and pan
