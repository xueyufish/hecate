## ADDED Requirements

### Requirement: JSON structured logging
The system SHALL output logs in JSON format for machine parsing.

#### Scenario: Structured log output
- **WHEN** a log event occurs
- **THEN** the system outputs JSON with timestamp, level, message, context

### Requirement: Log context enrichment
The system SHALL enrich logs with request context (session_id, agent_id, user_id).

#### Scenario: Enriched log
- **WHEN** a log is generated during a request
- **THEN** the log includes session_id, agent_id, user_id
