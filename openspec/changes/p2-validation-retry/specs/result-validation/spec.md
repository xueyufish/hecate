## ADDED Requirements

### Requirement: Validate tool output against schema
The system SHALL validate tool execution results against a JSON Schema.

#### Scenario: Valid output
- **WHEN** a tool returns output matching its schema
- **THEN** the system accepts the result

#### Scenario: Invalid output
- **WHEN** a tool returns output not matching its schema
- **THEN** the system logs the validation error and returns error result

### Requirement: Custom validation rules
The system SHALL support custom validation rules beyond JSON Schema.

#### Scenario: Custom rule passes
- **WHEN** a custom validation rule is defined and the output passes
- **THEN** the system accepts the result

#### Scenario: Custom rule fails
- **WHEN** a custom validation rule is defined and the output fails
- **THEN** the system returns error with rule details
