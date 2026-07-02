## ADDED Requirements

### Requirement: HecateError base exception class

The engine SHALL define `HecateError(Exception)` in `engine/errors.py` as the base exception for all Hecate-specific errors. All Hecate exception types SHALL inherit from `HecateError`.

#### Scenario: Catch all Hecate errors

- **WHEN** any Hecate-specific exception is raised
- **THEN** `except HecateError` SHALL catch it

#### Scenario: Backward compatibility with Exception

- **WHEN** code uses `except Exception`
- **THEN** all HecateError subclasses SHALL be caught (HecateError inherits from Exception)

### Requirement: EngineError category for engine runtime errors

The engine SHALL define `EngineError(HecateError)` as the base for engine-specific runtime errors. Subtypes: `MaxSuperstepsError(EngineError)` for graph execution exceeding max supersteps.

#### Scenario: Max supersteps exceeded

- **WHEN** graph execution exceeds `max_supersteps`
- **THEN** PregelRuntime SHALL raise `MaxSuperstepsError` instead of generic `RuntimeError`
- **AND** `except EngineError` SHALL catch it

#### Scenario: Existing RuntimeError code unaffected

- **WHEN** other code raises `RuntimeError` for non-engine conditions
- **THEN** it SHALL NOT be caught by `except EngineError`

### Requirement: GraphValidationError inherits EngineError

`GraphValidationError` SHALL change its inheritance from `Exception` to `EngineError`. The `field` attribute SHALL be preserved.

#### Scenario: Existing GraphValidationError catch still works

- **WHEN** code uses `except GraphValidationError`
- **THEN** it SHALL continue to catch graph validation errors

#### Scenario: Catchable as EngineError

- **WHEN** code uses `except EngineError`
- **THEN** it SHALL catch GraphValidationError instances

#### Scenario: Field attribute preserved

- **WHEN** GraphValidationError is raised with a field parameter
- **THEN** the `field` attribute SHALL be accessible on the exception instance

### Requirement: ChannelError category for channel operation failures

The engine SHALL define `ChannelError(HecateError)` with subtype `ChannelNotFoundError(ChannelError)`. ChannelManager SHALL raise `ChannelNotFoundError` instead of bare `KeyError` when reading from an unregistered channel.

#### Scenario: Read from unregistered channel

- **WHEN** `channel_manager.read("unknown")` is called
- **THEN** `ChannelNotFoundError` SHALL be raised
- **AND** `except ChannelError` SHALL catch it

#### Scenario: ChannelNotFoundError is also catchable as KeyError

- **WHEN** `ChannelNotFoundError` is raised
- **THEN** `except KeyError` SHALL catch it (ChannelNotFoundError inherits from both ChannelError and KeyError)

### Requirement: SecurityError category for guardrail and security failures

The engine SHALL define `SecurityError(HecateError)` with subtype `GuardrailBlockedError(SecurityError)` for guardrail-blocked requests.

#### Scenario: Guardrail blocks request

- **WHEN** a guardrail hook determines a request should be blocked
- **THEN** `GuardrailBlockedError` MAY be raised (alongside the existing return-based GuardrailResult pattern)
- **AND** `except SecurityError` SHALL catch it

#### Scenario: Coexistence with GuardrailResult

- **WHEN** a PreLLMHook returns `GuardrailResult(action=BLOCK)`
- **THEN** the existing return-based pattern SHALL continue to work without raising GuardrailBlockedError

### Requirement: ErrorCategory enum for semantic error classification

The engine SHALL define `ErrorCategory` as a `StrEnum` with members: `LLM_RATE_LIMIT`, `LLM_AUTH`, `LLM_TIMEOUT`, `LLM_CONTEXT_WINDOW`, `TOOL_TIMEOUT`, `TOOL_NOT_FOUND`, `TOOL_EXECUTION`, `ENGINE`, `SECURITY`, `CHANNEL`, `UNKNOWN`.

#### Scenario: String comparison

- **WHEN** `ErrorCategory.LLM_RATE_LIMIT == "llm_rate_limit"`
- **THEN** the comparison evaluates to `True`

#### Scenario: Unknown error category

- **WHEN** an error cannot be classified into any specific category
- **THEN** `ErrorCategory.UNKNOWN` SHALL be returned by the classifier

### Requirement: ErrorClassifier upgraded with isinstance-based classification

The `ErrorClassifier` in `services/validation/retry_policy.py` SHALL be extended with a `classify(error: Exception) -> ErrorCategory` method that uses isinstance checks against provider SDK exception types. The existing `is_retryable(error: str) -> bool` method SHALL be preserved for backward compatibility.

#### Scenario: Classify OpenAI rate limit error

- **WHEN** `classify(openai.RateLimitError(...))` is called
- **THEN** it SHALL return `ErrorCategory.LLM_RATE_LIMIT`

#### Scenario: Classify HecateError subtypes

- **WHEN** `classify(MaxSuperstepsError(...))` is called
- **THEN** it SHALL return `ErrorCategory.ENGINE`

#### Scenario: String fallback for unrecognized errors

- **WHEN** `classify(ValueError("timeout"))` is called
- **THEN** it SHALL fall back to string-based keyword matching
- **AND** return `ErrorCategory.LLM_TIMEOUT` if "timeout" is matched

#### Scenario: is_retryable uses classify

- **WHEN** `is_retryable(error_string)` is called
- **THEN** it SHALL continue to work with string input (backward compatible)
- **AND** `is_retryable_exception(exception)` SHALL use the new classify method
