## ADDED Requirements

### Requirement: Admin can create a model provider
The system SHALL allow admins to create a new model provider with name, display name, API key, and optional base URL. Upon creation, the system SHALL automatically discover available models via LiteLLM and populate the model registry.

#### Scenario: Create provider with valid API key
- **WHEN** admin submits provider form with name="zhipu", display_name="智谱", api_key="valid-key"
- **THEN** system creates the provider record, calls LiteLLM to discover models, and creates model_registry entries for all discovered chat models

#### Scenario: Create provider with invalid API key
- **WHEN** admin submits provider form with an invalid API key
- **THEN** system returns 400 error with message "API key validation failed" and no provider is created

### Requirement: Admin can list all model providers
The system SHALL return all providers with their status, model count, and configuration summary.

#### Scenario: List providers
- **WHEN** admin requests GET /api/model-providers
- **THEN** system returns a list of all providers with id, name, display_name, status, model_count, and is_enabled

### Requirement: Admin can update a model provider
The system SHALL allow admins to update provider configuration (display name, API key, base URL, config). If the API key changes, the system SHALL re-discover models.

#### Scenario: Update provider API key
- **WHEN** admin updates a provider's API key to a new valid key
- **THEN** system updates the provider, re-discovers models, and updates the model registry

### Requirement: Admin can delete a model provider
The system SHALL allow admins to delete a provider and all associated model registry entries.

#### Scenario: Delete provider
- **WHEN** admin deletes a provider
- **THEN** system removes the provider and all associated models from model_registry

### Requirement: Admin can test provider connectivity
The system SHALL provide a test endpoint that validates the API key by making a lightweight LLM call.

#### Scenario: Test connectivity with valid key
- **WHEN** admin calls POST /api/model-providers/{id}/test with a valid provider
- **THEN** system returns status="active" and response_time_ms

#### Scenario: Test connectivity with invalid key
- **WHEN** admin calls POST /api/model-providers/{id}/test with an invalid key
- **THEN** system returns status="error" with error_message describing the failure
