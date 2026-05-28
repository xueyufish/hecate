## ADDED Requirements

### Requirement: LLM-driven task routing
The system SHALL use LLM to analyze task descriptions and route to appropriate agents.

#### Scenario: Route to specialist
- **WHEN** a task requires specific expertise
- **THEN** the system routes to an agent with matching skills

#### Scenario: Fallback to default
- **WHEN** LLM cannot determine best agent
- **THEN** the system uses default routing rules

### Requirement: Load-aware allocation
The system SHALL consider agent load when allocating tasks.

#### Scenario: Prefer idle agent
- **WHEN** multiple agents can handle a task
- **THEN** the system prefers the agent with lowest current load
