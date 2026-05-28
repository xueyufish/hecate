## ADDED Requirements

### Requirement: Validate LLM output against schema
The system SHALL validate LLM responses against expected output schemas.

#### Scenario: Valid JSON output
- **WHEN** LLM returns valid JSON matching schema
- **THEN** the system accepts the response

#### Scenario: Invalid JSON output
- **WHEN** LLM returns invalid JSON
- **THEN** the system logs error and requests retry with error context

### Requirement: Auto-repair common format errors
The system SHALL attempt to auto-repair common LLM output format errors.

#### Scenario: Missing quotes
- **WHEN** LLM returns `{name: "Alice"}` (missing quotes on key)
- **THEN** the system auto-repairs to `{"name": "Alice"}`

#### Scenario: Trailing comma
- **WHEN** LLM returns `{"name": "Alice",}` (trailing comma)
- **THEN** the system auto-repairs to `{"name": "Alice"}`
