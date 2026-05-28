## ADDED Requirements

### Requirement: Configurable retry policy
The system SHALL support configurable retry policies for tool execution.

#### Scenario: Exponential backoff
- **WHEN** a tool fails with a retryable error
- **THEN** the system retries with exponential backoff (1s, 2s, 4s, ...)

#### Scenario: Max retries exceeded
- **WHEN** a tool fails after max retries
- **THEN** the system returns error result

### Requirement: Error classification
The system SHALL classify errors as retryable or non-retryable.

#### Scenario: Retryable error (network timeout)
- **WHEN** a tool fails with network timeout
- **THEN** the system retries

#### Scenario: Non-retryable error (invalid input)
- **WHEN** a tool fails with invalid input error
- **THEN** the system does not retry

### Requirement: Circuit breaker
The system SHALL implement circuit breaker pattern for tools with high failure rates.

#### Scenario: Circuit opens
- **WHEN** a tool fails 5 times in 1 minute
- **THEN** the circuit opens and subsequent calls fail immediately for 30 seconds

#### Scenario: Circuit half-open
- **WHEN** the circuit is open and 30 seconds pass
- **THEN** the circuit allows one test call
