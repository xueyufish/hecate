## ADDED Requirements

### Requirement: Model catalog aggregation service
The system SHALL define `CatalogService` in `model_hub/catalog_service.py` that aggregates ModelRegistryModel, ModelProviderModel, and ModelPricingModel into a unified catalog view with computed fields.

#### Scenario: List catalog entries
- **WHEN** `list_models()` is called with optional filters (provider, capability, model_type, min_context, max_cost)
- **THEN** the service SHALL return a list of catalog entries, each containing model_id, display_name, provider_name, capabilities, max_context, effective_pricing, and provider_status

#### Scenario: Get single catalog entry
- **WHEN** `get_model(model_id)` is called
- **THEN** the service SHALL return a detailed catalog entry with all metadata including pricing history, capability badges, and provider information

#### Scenario: Search models by capability
- **WHEN** `search_models(capabilities=["vision", "function_calling"])` is called
- **THEN** the service SHALL filter ModelRegistryModel where the `capabilities` JSON field contains all requested capabilities

#### Scenario: Compare models
- **WHEN** `compare_models(model_ids=["gpt-4o", "claude-3.5-sonnet"])` is called
- **THEN** the service SHALL return a comparison matrix with pricing, context window, capabilities, and provider for each model side-by-side

### Requirement: Model catalog REST API
The system SHALL expose `/api/models/catalog` endpoints for browsing, searching, and comparing models.

#### Scenario: List catalog with pagination
- **WHEN** GET `/api/models/catalog?page=1&page_size=20&provider=openai&capability=vision` is received
- **THEN** the system SHALL return paginated catalog entries matching the filters with total count

#### Scenario: Get model details
- **WHEN** GET `/api/models/catalog/{model_id}` is received
- **THEN** the system SHALL return the full catalog entry with pricing history and provider details

#### Scenario: Compare models
- **WHEN** GET `/api/models/catalog/compare?model_ids=gpt-4o,claude-3.5-sonnet` is received
- **THEN** the system SHALL return a comparison matrix

#### Scenario: Filter by pricing tier
- **WHEN** GET `/api/models/catalog?max_input_price=0.01` is received
- **THEN** the system SHALL return only models with effective input pricing at or below the threshold

### Requirement: Capability badges
The system SHALL compute capability badges from ModelRegistryModel.capabilities JSON field and present them as structured tags in the catalog.

#### Scenario: Vision-capable model
- **WHEN** a model has `{"vision": true}` in its capabilities JSON
- **THEN** the catalog entry SHALL include `"capability_badges": ["vision"]`

#### Scenario: Multi-capability model
- **WHEN** a model has `{"vision": true, "function_calling": true, "streaming": true}` in capabilities
- **THEN** the catalog entry SHALL include `"capability_badges": ["vision", "function_calling", "streaming"]`

### Requirement: Effective pricing computation
The system SHALL compute effective pricing for each catalog entry by querying ModelPricingModel for the currently active pricing record.

#### Scenario: Model with active pricing
- **WHEN** a model has a pricing entry where `effective_from <= now < effective_until` (or effective_until is NULL)
- **THEN** the catalog entry SHALL include `effective_pricing: {input_per_1k, output_per_1k, currency}`

#### Scenario: Model without pricing
- **WHEN** a model has no matching pricing entry
- **THEN** the catalog entry SHALL include `effective_pricing: null` and `has_pricing: false`
