## ADDED Requirements

### Requirement: Generate synthetic training environments
The system SHALL generate synthetic training environments for agent improvement.

#### Scenario: Generate environment for capability
- **WHEN** a capability gap is identified
- **THEN** the system generates a synthetic environment to practice that capability

### Requirement: Validate environment difficulty
The system SHALL validate that synthetic environments have appropriate difficulty.

#### Scenario: Balance difficulty
- **WHEN** an environment is generated
- **THEN** the system validates target success rate is 20-60%
