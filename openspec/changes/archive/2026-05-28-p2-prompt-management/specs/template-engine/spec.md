## ADDED Requirements

### Requirement: Render template with variables
The system SHALL render Jinja2 templates with provided variables.

#### Scenario: Simple variable substitution
- **WHEN** a template contains `{{ name }}` and variables = {"name": "Alice"}
- **THEN** the system renders "Hello Alice"

#### Scenario: Missing variable
- **WHEN** a template contains `{{ name }}` but name is not provided
- **THEN** the system renders with empty string or raises validation error

### Requirement: Template validation
The system SHALL validate templates before saving.

#### Scenario: Valid template
- **WHEN** a user saves a valid Jinja2 template
- **THEN** the system accepts it

#### Scenario: Invalid template syntax
- **WHEN** a user saves a template with invalid Jinja2 syntax
- **THEN** the system returns 422 with syntax error details

### Requirement: Sandboxed rendering
The system SHALL use Jinja2 SandboxedEnvironment to prevent code injection.

#### Scenario: Blocked unsafe operations
- **WHEN** a template contains `{{ ''.__class__.__mro__ }}`
- **THEN** the system raises SecurityError

### Requirement: Variable extraction
The system SHALL automatically extract variables from templates.

#### Scenario: Extract variables
- **WHEN** a template contains `{{ name }}` and `{{ age }}`
- **THEN** the system extracts variables = ["name", "age"]
