## ADDED Requirements

### Requirement: Agent palette in workflow canvas
The system SHALL display an agent palette in the workflow canvas sidebar listing all available agents. Users SHALL be able to drag agents from the palette onto the canvas to create AGENT nodes.

#### Scenario: Agent palette displays available agents
- **WHEN** the user opens the workflow canvas editor
- **THEN** the sidebar shows an "Agents" section listing all non-deleted agents with name and mode

#### Scenario: Drag agent to canvas creates AGENT node
- **WHEN** the user drags an agent from the palette onto the canvas
- **THEN** a new AGENT node is created with the agent's ID and name, positioned at the drop location

### Requirement: Edge type differentiation in canvas
The system SHALL render handoff edges differently from standard edges. Handoff edges SHALL use a dashed line style. Standard (invoke-as-tool) edges SHALL use a solid line style.

#### Scenario: Handoff edge rendered as dashed
- **WHEN** a graph contains an edge with `type: "handoff"` between two agent nodes
- **THEN** the canvas renders the edge as a dashed line

#### Scenario: Standard edge rendered as solid
- **WHEN** a graph contains a standard edge (no type or `type: "default"`)
- **THEN** the canvas renders the edge as a solid line

### Requirement: Edge type selection when connecting nodes
The system SHALL allow users to choose the edge type (handoff vs invoke-as-tool) when connecting two agent nodes. The connection dialog SHALL present both options.

#### Scenario: User creates handoff connection
- **WHEN** the user connects agent node A to agent node B and selects "Handoff" in the connection dialog
- **THEN** the graph DSL stores the edge with `type: "handoff"` (rendered as `trigger: "handoff"`)

#### Scenario: User creates invoke-as-tool connection
- **WHEN** the user connects agent node A to agent node B and selects "Invoke as Tool" in the connection dialog
- **THEN** the graph DSL stores the edge without type (standard data flow)

### Requirement: Orchestration template picker
The system SHALL provide a template picker accessible from the workflow canvas toolbar. Users SHALL be able to select a pre-built orchestration template which populates the canvas with the template's graph.

#### Scenario: User loads triage template
- **WHEN** the user opens the template picker and selects "Customer Service Triage"
- **THEN** the canvas is populated with a router agent connected to 3 specialist agents via handoff edges

#### Scenario: Template replaces current canvas
- **WHEN** the user loads a template and the canvas already has nodes
- **THEN** the system prompts for confirmation before replacing the current canvas content

### Requirement: Multi-agent execution visualization
The system SHALL highlight the currently executing agent node during workflow test runs. The canvas SHALL visually distinguish completed, executing, and pending agent nodes.

#### Scenario: Test run highlights executing agent
- **WHEN** a workflow test run is in progress and agent node "specialist" is executing
- **THEN** the "specialist" node is highlighted with a pulsing animation

#### Scenario: Test run shows completed agents
- **WHEN** a workflow test run has completed agent nodes
- **THEN** those nodes show a green checkmark overlay
