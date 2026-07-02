## ADDED Requirements

### Requirement: Pattern selector card grid
The system SHALL display a pattern selector as a card grid dialog accessible from the workflow canvas toolbar. Each of the 6 patterns (Sequential, Parallel, Handoff, Broadcast, Negotiation, Debate) SHALL be shown as a card with an icon, name, short description, and a mini topological preview.

#### Scenario: Pattern selector opened from toolbar
- **WHEN** the user clicks the "Patterns" button in the canvas toolbar
- **THEN** a dialog SHALL open showing 6 pattern cards arranged in a 3×2 grid layout

#### Scenario: Each pattern card shows metadata
- **WHEN** the pattern selector dialog is displayed
- **THEN** each card SHALL show a pattern icon, pattern name, one-line description, and a mini graph preview (simplified node-edge diagram)

#### Scenario: Pattern card indicates node count
- **WHEN** the user views the "Parallel" pattern card
- **THEN** the card SHALL display the estimated minimum node count (e.g., "5+ nodes")

### Requirement: Pattern configuration dialog
After selecting a pattern, the system SHALL show a configuration dialog with pattern-specific parameter fields. The dialog SHALL validate inputs before enabling the "Generate" button.

#### Scenario: Sequential pattern configuration
- **WHEN** the user selects the "Sequential" pattern
- **THEN** a configuration dialog SHALL appear with fields: workflow name (text), stages (dynamic list where each stage has: name, model dropdown, system prompt textarea), and an "Add Stage" button

#### Scenario: Parallel pattern configuration
- **WHEN** the user selects the "Parallel" pattern
- **THEN** the configuration dialog SHALL show fields: coordinator (name, model, prompt), workers (dynamic list with add/remove), aggregator (name, model, prompt)

#### Scenario: Handoff pattern configuration
- **WHEN** the user selects the "Handoff" pattern
- **THEN** the configuration dialog SHALL show fields: router (name, model, prompt), specialists (dynamic list with add/remove)

#### Scenario: Minimum validation before generate
- **WHEN** the user has not filled in all required fields for a pattern
- **THEN** the "Generate" button SHALL be disabled and missing fields SHALL show validation errors

### Requirement: Pattern selection generates canvas graph
When the user completes pattern configuration and clicks "Generate", the system SHALL call the pattern generation API, convert the resulting Graph DSL JSON to React Flow elements via `dslToReactFlow()`, and populate the canvas.

#### Scenario: Generate and populate canvas
- **WHEN** the user fills in a 3-stage sequential pattern and clicks "Generate"
- **THEN** the system SHALL call `POST /api/collaboration-patterns/sequential/generate`, receive the Graph DSL, convert it via `dslToReactFlow()`, and replace the canvas with the generated nodes and edges

#### Scenario: Canvas replaces existing content with confirmation
- **WHEN** the user generates a pattern and the canvas already has nodes
- **THEN** the system SHALL show a confirmation dialog before replacing existing canvas content

#### Scenario: Enter customization mode after generation
- **WHEN** the pattern graph is loaded onto the canvas
- **THEN** the canvas SHALL enter template customization mode (same as template loading), enabling full editing and "Save as Workflow"

### Requirement: Pattern selector alongside template picker
The pattern selector SHALL be accessible as a separate toolbar action alongside the existing template picker, clearly differentiated as "Start from Pattern" vs "Load Template".

#### Scenario: Both options visible in toolbar
- **WHEN** the user views the canvas toolbar
- **THEN** both "Patterns" and "Templates" buttons SHALL be visible with distinct icons and labels
