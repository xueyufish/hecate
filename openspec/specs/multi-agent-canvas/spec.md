## Purpose

Multi-agent canvas provides the UI for visualizing and interacting with agent workflow graphs, including agent palette, edge visualization, template picker, pattern selector, and execution visualization.
## Requirements
### Requirement: Agent palette in workflow canvas
The system SHALL display an agent palette in the workflow canvas sidebar listing all available agents. Users SHALL be able to drag agents from the palette onto the canvas to create AGENT nodes.

#### Scenario: Agent palette displays available agents
- **WHEN** the user opens the workflow canvas editor
- **THEN** the sidebar shows an "Agents" section listing all non-deleted agents with name and mode

#### Scenario: Drag agent to canvas creates AGENT node
- **WHEN** the user drags an agent from the palette onto the canvas
- **THEN** a new AGENT node is created with the agent's ID and name, positioned at the drop location

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

### Requirement: Orchestration template picker
The system SHALL provide a template picker accessible from the workflow canvas toolbar. Users SHALL be able to select a pre-built orchestration template which populates the canvas with the template's graph. Additionally, a pattern selector SHALL be provided as a separate toolbar action offering 6 collaboration patterns that auto-generate graph structures.

#### Scenario: User loads triage template
- **WHEN** the user opens the template picker and selects "Customer Service Triage"
- **THEN** the canvas is populated with a router agent connected to 3 specialist agents via handoff edges

#### Scenario: Template replaces current canvas
- **WHEN** the user loads a template and the canvas already has nodes
- **THEN** the system prompts for confirmation before replacing the current canvas content

#### Scenario: Pattern selector accessible from toolbar
- **WHEN** the user clicks the "Patterns" button in the canvas toolbar
- **THEN** a pattern selector dialog opens showing 6 collaboration patterns as selectable cards

### Requirement: Multi-agent execution visualization
The system SHALL highlight the currently executing agent node during workflow test runs. The canvas SHALL visually distinguish completed, executing, and pending agent nodes.

#### Scenario: Test run highlights executing agent
- **WHEN** a workflow test run is in progress and agent node "specialist" is executing
- **THEN** the "specialist" node is highlighted with a pulsing animation

#### Scenario: Test run shows completed agents
- **WHEN** a workflow test run has completed agent nodes
- **THEN** those nodes show a green checkmark overlay

