## ADDED Requirements

### Requirement: Execute tool in sandbox
The system SHALL execute tool calls in isolated Docker containers.

#### Scenario: Sandbox execution
- **WHEN** a tool is configured for sandbox execution
- **THEN** the system creates a Docker container and executes the tool inside it

#### Scenario: Resource limits
- **WHEN** executing in sandbox
- **THEN** the system enforces CPU, memory, and network limits

### Requirement: Sandbox lifecycle management
The system SHALL manage sandbox container lifecycle (create, use, destroy).

#### Scenario: Auto-destroy after timeout
- **WHEN** a sandbox execution exceeds timeout
- **THEN** the system destroys the container and returns timeout error
