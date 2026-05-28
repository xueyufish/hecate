## ADDED Requirements

### Requirement: Inject constraints into system prompt
The system SHALL inject relevant constraint rules into the system prompt before LLM calls.

#### Scenario: Inject constraints
- **WHEN** context is assembled for a conversation
- **THEN** the system appends relevant constraint rules to the system prompt

### Requirement: Constraint priority
The system SHALL prioritize constraints when multiple apply.

#### Scenario: Multiple constraints
- **WHEN** multiple constraints apply to a situation
- **THEN** the system applies them in priority order
