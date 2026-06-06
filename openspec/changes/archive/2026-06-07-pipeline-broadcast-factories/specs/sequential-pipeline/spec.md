## ADDED Requirements

### Requirement: Sequential pipeline factory function
The system SHALL provide a `build_sequential_pipeline()` factory function in `engine/templates.py` that accepts a list of stage definitions and returns a `GraphConfig` representing a linear sequential pipeline with auto-wired channels.

#### Scenario: Basic two-stage pipeline
- **WHEN** `build_sequential_pipeline(stages=[{"id": "researcher", "model": "gpt-4o", "system_prompt": "You are a researcher."}, {"id": "writer", "model": "gpt-4o", "system_prompt": "You are a writer."}])` is called
- **THEN** the returned GraphConfig SHALL contain 2 AGENT nodes (researcher, writer), a shared `messages` TOPIC channel readable and writable by both stages, and a `researcher_output` LAST_VALUE channel writable by researcher and readable by writer

#### Scenario: Three-stage pipeline with inter-stage data flow
- **WHEN** `build_sequential_pipeline(stages=[{"id": "a", ...}, {"id": "b", ...}, {"id": "c", ...}])` is called
- **THEN** stage A SHALL write to channels `messages` and `a_output`, stage B SHALL read from `messages` and `a_output` and write to `messages` and `b_output`, stage C SHALL read from `messages` and `b_output` and write to `messages`

#### Scenario: Pipeline edge connectivity
- **WHEN** a sequential pipeline is created with N stages
- **THEN** edges SHALL form a linear chain: `__start__` → stage_0 → stage_1 → ... → stage_{N-1} → `__end__`, and the `entry` field SHALL be set to stage_0

#### Scenario: Pipeline channel auto-wiring
- **WHEN** a sequential pipeline is created
- **THEN** the `messages` TOPIC channel SHALL be readable and writable by ALL stages, and each stage N SHALL have a dedicated `{stage_id}_output` LAST_VALUE channel that is writable by stage N and readable by stage N+1 (if it exists)

### Requirement: Sequential pipeline with revision loop
The system SHALL support an optional revision configuration in `build_sequential_pipeline()` that appends a CONDITION node and creates a revision loop from the last stage back to a designated revision target.

#### Scenario: Pipeline with revision loop enabled
- **WHEN** `build_sequential_pipeline(stages=[...], revision_config={"expression": "quality == 'needs_revision'", "target_stage": "writer"})` is called
- **THEN** the graph SHALL contain a CONDITION node after the last stage with a conditional edge routing to `target_stage` when the expression evaluates true, and to `__end__` when false

#### Scenario: Pipeline without revision loop
- **WHEN** `build_sequential_pipeline(stages=[...])` is called without `revision_config`
- **THEN** the graph SHALL contain no CONDITION nodes and SHALL be strictly linear from first stage to `__end__`

### Requirement: Sequential pipeline stage validation
The system SHALL validate stage definitions when building a sequential pipeline.

#### Scenario: Minimum stage count
- **WHEN** `build_sequential_pipeline(stages=[single_stage])` is called with fewer than 2 stages
- **THEN** the function SHALL raise `ValueError` with a descriptive message

#### Scenario: Duplicate stage IDs rejected
- **WHEN** `build_sequential_pipeline(stages=[{"id": "agent", ...}, {"id": "agent", ...}])` is called with duplicate stage IDs
- **THEN** the function SHALL raise `ValueError`

### Requirement: Sequential pipeline JSON template
The system SHALL include a `sequential-pipeline.json` template file in `data/orchestration_templates/` demonstrating a 3-stage researcher→writer→reviewer pipeline with a revision loop.

#### Scenario: Template loads successfully
- **WHEN** the sequential-pipeline template is loaded via the orchestration-templates API
- **THEN** the template SHALL contain 3 AGENT nodes, 1 CONDITION node, a `messages` TOPIC channel, and per-stage LAST_VALUE channels with correct readable/writable wiring
