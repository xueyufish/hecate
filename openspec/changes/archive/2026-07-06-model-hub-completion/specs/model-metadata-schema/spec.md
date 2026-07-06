## ADDED Requirements

### Requirement: ModelRegistryModel stores structured model metadata
The system SHALL add a `model_metadata` JSON column to `ModelRegistryModel` containing `modalities` (input/output arrays), `capabilities` (boolean flags), and `limits` (context/output integers).

#### Scenario: Multi-modal model metadata
- **WHEN** a model like GPT-4o is registered with `model_metadata: {modalities: {input: ["text", "image", "audio"], output: ["text"]}, capabilities: {reasoning: true, tool_call: true, vision: true}, limits: {context: 128000, output: 16384}}`
- **THEN** the system SHALL store and serve this metadata for routing, catalog display, and capability filtering

#### Scenario: Text-only model metadata
- **WHEN** a model like text-embedding-ada-002 is registered with `model_metadata: {modalities: {input: ["text"], output: ["embedding"]}, capabilities: {}, limits: {context: 8192}}`
- **THEN** the system SHALL correctly identify it as an embedding model not suitable for chat

### Requirement: System migrates existing model_type to model_metadata
The system SHALL populate `model_metadata` for existing rows during migration based on the current `model_type` value, using conservative defaults.

#### Scenario: Migrate chat models
- **WHEN** an existing model has `model_type = "chat"`
- **THEN** migration SHALL set `model_metadata = {modalities: {input: ["text"], output: ["text"]}, capabilities: {tool_call: false}, limits: {}}`

#### Scenario: Migrate embedding models
- **WHEN** an existing model has `model_type = "embedding"`
- **THEN** migration SHALL set `model_metadata = {modalities: {input: ["text"], output: ["embedding"]}, capabilities: {}, limits: {}}`

### Requirement: System provides backward-compatible model_type accessor
The system SHALL compute `model_type` from `model_metadata` for backward compatibility with existing code that reads the `model_type` field.

#### Scenario: Derive chat type from metadata
- **WHEN** `model_metadata.modalities.output` contains `"text"` and `input` contains `"text"` only
- **THEN** the computed `model_type` SHALL be `"chat"`

#### Scenario: Derive embedding type from metadata
- **WHEN** `model_metadata.modalities.output` contains `"embedding"`
- **THEN** the computed `model_type` SHALL be `"embedding"`

### Requirement: Catalog displays capability badges from model_metadata
The system SHALL render capability badges (vision, tool_call, reasoning, streaming) in the Model Catalog UI based on `model_metadata.capabilities` and `model_metadata.modalities`.

#### Scenario: Vision badge for multi-modal model
- **WHEN** a model has `capabilities.vision: true` or `modalities.input` includes `"image"`
- **THEN** the catalog SHALL display a vision capability badge

#### Scenario: Context window display
- **WHEN** a model has `limits.context: 128000`
- **THEN** the catalog SHALL display "128K context" badge
