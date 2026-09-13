## Purpose

Behavior contract for the multi-agent central controller node: intent-driven routing of user turns to sub-workflows based on the intent recognition engine, with start/default/end workflow assignment, session-intent drift re-routing, isolated sub-workflow execution, and routing decision events for visualization.

## Requirements

### Requirement: Controller node type and configuration

The graph DSL SHALL support a CONTROLLER node type whose configuration contains: a required intent package reference (id, optionally with a pinned published version, defaulting to the latest published version), a mapping from package categories to route targets (sub-workflow or agent node references), a start workflow target, a required default workflow target, and an end workflow designation, plus a global-intent configuration (enabled flag and optional goal hint). Validation SHALL reject configurations whose package reference is unresolvable, whose mapping references categories absent from the referenced package version, or whose referenced route targets are not declared nodes; the default workflow target SHALL be mandatory.

#### Scenario: Valid controller config parses

- **WHEN** a graph declares a CONTROLLER node with a published package reference, a mapping of two existing categories to two declared agent nodes, and a default workflow
- **THEN** the graph SHALL validate and the parsed node SHALL preserve the mapping, start/default/end assignments, and global-intent configuration

#### Scenario: Category not in referenced package rejected

- **WHEN** the mapping references a category that does not exist in the referenced package version
- **THEN** validation SHALL fail naming the unknown category

#### Scenario: Missing default workflow rejected

- **WHEN** a CONTROLLER node declares no default workflow target
- **THEN** validation SHALL fail indicating the default workflow is required

### Requirement: Per-turn recognition and routing

On each user turn, the controller SHALL invoke the recognition engine with evidence from its referenced package. An atomic intent matching a mapped category SHALL route the turn to that category's target. A workflow intent (multi-turn task) matching a mapped category SHALL route to the corresponding multi-step target. No match on either level SHALL route the turn to the default workflow.

#### Scenario: Atomic intent routes to mapped target

- **WHEN** a user turn is recognized with atomic label "billing" and the mapping routes "billing" to a billing sub-workflow
- **THEN** the turn SHALL execute the billing sub-workflow

#### Scenario: Workflow intent routes multi-step task

- **WHEN** accumulated turns form a workflow intent labeled "quarterly-report-pipeline" that maps to a report sub-workflow
- **THEN** the turn SHALL route to the report sub-workflow with the workflow-intent context

#### Scenario: No match falls back to default workflow

- **WHEN** recognition produces no label matching any mapped category
- **THEN** the turn SHALL route to the configured default workflow

### Requirement: Session-intent drift re-routing

The controller SHALL use the session intent to keep routing sticky across turns of the same goal. Re-routing SHALL occur only when recognition reports an explicit goal shift. While the session intent is stable, the controller SHALL NOT re-route mid-goal turns away from the active target.

#### Scenario: Sticky routing during one goal

- **WHEN** consecutive turns classify to atomic categories different from the session goal but no explicit goal shift is detected
- **THEN** the controller SHALL route per atomic mapping without resetting the session goal or re-assigning the overall route

#### Scenario: Explicit shift re-routes

- **WHEN** a turn is recognized as an explicit goal shift to a new category with a mapping
- **THEN** the controller SHALL update the session intent and route subsequent turns to the new target

### Requirement: Isolated sub-workflow execution

Sub-workflow execution from the controller SHALL follow the same isolation semantics as agent-node sub-graph invocation: explicit input and output channel mappings, a distinct child session for the sub-workflow, and failure authority limited to the worker error contract. Unregistered parent channels in the input mapping SHALL fail validation before execution.

#### Scenario: Child session isolation

- **WHEN** the controller dispatches a sub-workflow
- **THEN** the sub-workflow SHALL run in a distinct child session whose long-term memory writes are keyed by the child session, and only mapped outputs SHALL fold back into the parent

#### Scenario: Unregistered input channel rejected

- **WHEN** the controller's input mapping references a parent channel that is not registered
- **THEN** validation SHALL fail before execution

### Requirement: Controller routing events

Each routing decision SHALL emit an additive event capturing: the selected target, the triggering intent level and label, confidence, decision source, cache-hit flag, the evidence version reference, and the current session intent state. The events SHALL be consumable by the canvas and trace views for orchestration visualization.

#### Scenario: Routing decision event per turn

- **WHEN** the controller routes a turn to any target (mapped or default)
- **THEN** exactly one routing event SHALL be appended with the selected target, triggering level and label, source, and session intent state

#### Scenario: Default routing is observable

- **WHEN** a turn falls back to the default workflow
- **THEN** the event SHALL record the default target with the recognition result that produced no match
