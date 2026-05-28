## ADDED Requirements

### Requirement: Complete trace hierarchy
The system SHALL support Trace → Span → Generation hierarchy in LangFuse.

#### Scenario: Create trace
- **WHEN** a conversation starts
- **THEN** the system creates a LangFuse Trace

#### Scenario: Create span
- **WHEN** a tool is executed
- **THEN** the system creates a Span within the Trace

### Requirement: Cost attribution
The system SHALL track token costs per user, agent, and session.

#### Scenario: Track cost per user
- **WHEN** a user makes API calls
- **THEN** the system tracks total token cost for that user
