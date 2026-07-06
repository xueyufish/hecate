## ADDED Requirements

### Requirement: Router filters candidates by required modalities
The intelligent router SHALL filter model candidates based on the request's required input modalities (e.g., image input requires `modalities.input` containing `"image"`) before applying cost/latency routing strategy.

#### Scenario: Route image input to vision-capable model
- **WHEN** a request contains an image content part and the router has candidates `[gpt-4o (vision: true), text-only-model (vision: false)]`
- **THEN** the router SHALL exclude `text-only-model` from candidates and route to `gpt-4o`

#### Scenario: No capable model available
- **WHEN** a request requires audio input but no candidate model has `modalities.input` containing `"audio"`
- **THEN** the router SHALL return a `NoCapableModelError` listing the required modality

### Requirement: Router considers capability flags for tool-calling requests
The intelligent router SHALL prefer models with `capabilities.tool_call: true` when the request includes tool definitions, avoiding models that cannot execute tools.

#### Scenario: Route tool-calling request to capable model
- **WHEN** a request includes `tools` definitions and the router has candidates with mixed `tool_call` capabilities
- **THEN** the router SHALL filter to only `tool_call: true` candidates before applying cost/latency strategy
