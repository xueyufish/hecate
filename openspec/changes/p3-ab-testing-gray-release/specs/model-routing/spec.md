## ADDED Requirements

### Requirement: Route by rules
The system SHALL route LLM calls based on configurable rules (cost, latency, capability).

#### Scenario: Cost-aware routing
- **WHEN** a request has no specific model requirement
- **THEN** the system routes to the cheapest available model

#### Scenario: Latency-aware routing
- **WHEN** a request requires low latency
- **THEN** the system routes to the fastest available model
