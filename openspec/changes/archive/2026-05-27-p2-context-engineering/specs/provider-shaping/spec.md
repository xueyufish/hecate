## ADDED Requirements

### Requirement: Provider Strategy shapes context for target LLM
The system SHALL apply a provider-specific strategy to the assembled context before passing it to the LLM service, adapting message format, tool definitions, and system prompt structure to the target provider's preferences.

#### Scenario: OpenAI provider strategy
- **WHEN** the target model starts with "gpt-" and the context contains a system message longer than 2000 tokens
- **THEN** the OpenAI strategy SHALL truncate the system message to 2000 tokens and append a note indicating truncation

#### Scenario: Anthropic provider strategy
- **WHEN** the target model starts with "claude-" and the context contains tool definitions
- **THEN** the Anthropic strategy SHALL format tool definitions using Anthropic's native tool format (input_schema instead of parameters) if different from OpenAI format

#### Scenario: Unknown provider uses default strategy
- **WHEN** the target model does not match any known provider prefix
- **THEN** the default strategy SHALL pass the context through unchanged

### Requirement: Strategy selection by model name
The system SHALL automatically select the appropriate provider strategy based on the model identifier.

#### Scenario: Model name mapping
- **WHEN** the model is "gpt-4o"
- **THEN** the system SHALL select `OpenAIStrategy`

#### Scenario: Model name mapping for Claude
- **WHEN** the model is "claude-3-5-sonnet-20241022"
- **THEN** the system SHALL select `AnthropicStrategy`

#### Scenario: Fallback to default
- **WHEN** the model is "qwen-plus" and no Qwen-specific strategy is registered
- **THEN** the system SHALL select `DefaultStrategy`

### Requirement: Provider-specific system message handling
The system SHALL adapt the system message construction based on provider requirements.

#### Scenario: Anthropic system message as top-level parameter
- **WHEN** the target provider is Anthropic
- **THEN** the strategy SHALL extract the system message from the messages array and ensure it is passed as the top-level `system` parameter (Anthropic API convention)

#### Scenario: OpenAI system message stays in messages array
- **WHEN** the target provider is OpenAI
- **THEN** the strategy SHALL keep the system message as the first element in the messages array

### Requirement: Provider strategy is extensible
The system SHALL allow registration of custom provider strategies without modifying existing code.

#### Scenario: Registering a custom strategy
- **WHEN** a user registers a custom strategy for model prefix "deepseek-"
- **THEN** subsequent calls with a model starting with "deepseek-" SHALL use the custom strategy instead of the default
