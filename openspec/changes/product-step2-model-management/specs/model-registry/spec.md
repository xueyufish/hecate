## ADDED Requirements

### Requirement: Models are stored in database
The system SHALL store discovered models in a model_registry table linked to their provider, with metadata including display name, type, capabilities, and context length.

#### Scenario: Model registry populated on provider creation
- **WHEN** a provider is created and models are discovered
- **THEN** model_registry entries are created with provider_id, model_id, display_name, model_type="chat", capabilities, and is_enabled=true

### Requirement: Admin can list all registered models
The system SHALL return all registered models grouped by provider with their metadata.

#### Scenario: List models grouped by provider
- **WHEN** admin requests GET /api/models
- **THEN** system returns models grouped by provider, each with id, model_id, display_name, model_type, capabilities, max_context, is_enabled

### Requirement: Admin can toggle model enabled status
The system SHALL allow admins to enable or disable individual models.

#### Scenario: Disable a model
- **WHEN** admin calls PUT /api/models/{id} with is_enabled=false
- **THEN** model is disabled and will not appear in user-facing /v1/models

### Requirement: Admin can add custom models
The system SHALL allow admins to manually add models that are not auto-discovered.

#### Scenario: Add custom model
- **WHEN** admin calls POST /api/models with model_id="custom-model", provider_id, display_name
- **THEN** model is created with is_custom=true and appears in the model list
