## MODIFIED Requirements

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
