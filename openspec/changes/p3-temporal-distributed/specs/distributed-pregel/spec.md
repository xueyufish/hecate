## ADDED Requirements

### Requirement: DistributedPregelWorkflow wraps Pregel execution
The system SHALL provide a Temporal Workflow that wraps Pregel BSP execution for distributed scenarios.

#### Scenario: Execute graph as Temporal Workflow
- **WHEN** a user triggers graph execution in distributed mode
- **THEN** the system starts a Temporal Workflow that executes supersteps as Activities

#### Scenario: Continue-As-New for long workflows
- **WHEN** a workflow has executed 10 supersteps
- **THEN** the system uses Continue-As-New to reset workflow history

### Requirement: Cross-node checkpoint persistence
The system SHALL persist checkpoints to PostgreSQL after each superstep.

#### Scenario: Checkpoint after superstep
- **WHEN** a superstep completes
- **THEN** the system saves checkpoint to PostgreSQL via Temporal Activity

#### Scenario: Resume from checkpoint
- **WHEN** a workflow is restarted after failure
- **THEN** the system loads the latest checkpoint and resumes execution
