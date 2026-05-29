## ADDED Requirements

### Requirement: Provider has configurable timeout, retry, and rate limit
Each provider SHALL store optional configuration for timeout (seconds), max_retries, and rate_limit_rpm in a JSON config field.

#### Scenario: Create provider with custom config
- **WHEN** admin creates a provider with config={"timeout": 60, "max_retries": 5}
- **THEN** config is stored and used when calling models through this provider

#### Scenario: Default config values
- **WHEN** admin creates a provider without specifying config
- **THEN** system applies defaults: timeout=30, max_retries=3, rate_limit_rpm=60
