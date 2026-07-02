## ADDED Requirements

### Requirement: Admin can test model with custom prompt
The system SHALL provide a model debugging endpoint that sends a test prompt to a selected model and returns the response.

#### Scenario: Test model with valid parameters
- **WHEN** admin calls POST /api/models/test with model_id, prompt, temperature=0.7, max_tokens=100
- **THEN** system calls the model via llm_service.chat() and returns the response content, model used, and token usage

#### Scenario: Test model with invalid model
- **WHEN** admin calls POST /api/models/test with a model that is not available
- **THEN** system returns 400 error with the LiteLLM error message

### Requirement: Model debugging UI provides parameter controls
The frontend SHALL provide a testing panel with model selection, prompt input, parameter sliders, and response display.

#### Scenario: Use model debugging panel
- **WHEN** admin opens the model debugging page
- **THEN** page shows: model dropdown (grouped by provider), prompt textarea, temperature slider (0-2), max_tokens input, "Test" button, and response area
