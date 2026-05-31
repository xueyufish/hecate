## ADDED Requirements

### Requirement: Provider status change triggers agent warning
When a provider's status changes to "error" or "inactive", the system SHALL identify agents using models from that provider and flag them for attention.

#### Scenario: Provider goes offline
- **WHEN** a provider's status changes to "error"
- **THEN** agents using models from that provider show a warning indicator in the agent list

### Requirement: .env fallback for development
The system SHALL continue to support API keys from environment variables as a fallback when no database providers are configured.

#### Scenario: No database providers configured
- **WHEN** no providers exist in the database
- **THEN** /v1/models falls back to LiteLLM get_valid_models() using env var API keys
