## 1. Exception Hierarchy Definition

- [x] 1.1 Create `src/hecate/engine/errors.py` with `HecateError(Exception)` base class
- [x] 1.2 Define `EngineError(HecateError)` with subtype `MaxSuperstepsError(EngineError)`
- [x] 1.3 Define `ChannelError(HecateError)` with subtype `ChannelNotFoundError(ChannelError, KeyError)`
- [x] 1.4 Define `SecurityError(HecateError)` with subtype `GuardrailBlockedError(SecurityError)`
- [x] 1.5 Define `ErrorCategory(StrEnum)` with all members (LLM_RATE_LIMIT, LLM_AUTH, LLM_TIMEOUT, LLM_CONTEXT_WINDOW, TOOL_TIMEOUT, TOOL_NOT_FOUND, TOOL_EXECUTION, ENGINE, SECURITY, CHANNEL, UNKNOWN)
- [x] 1.6 Full docstrings on all classes (module, public classes)

## 2. GraphValidationError Migration

- [x] 2.1 Change `GraphValidationError` in `engine/graph_dsl.py` to inherit from `EngineError` instead of `Exception`
- [x] 2.2 Move import: `from hecate.engine.errors import EngineError` (keep GraphValidationError in graph_dsl.py for import compatibility)
- [x] 2.3 Preserve `field` attribute and `__init__` signature
- [x] 2.4 Verify all existing tests pass (GraphValidationError still catchable as Exception and GraphValidationError)

## 3. Engine Error Replacements

- [x] 3.1 In `engine/pregel.py`: replace `raise RuntimeError(...)` for max supersteps with `raise MaxSuperstepsError(...)`
- [x] 3.2 In `engine/channel.py`: replace `raise KeyError(...)` for unregistered channel read with `raise ChannelNotFoundError(...)`
- [x] 3.3 Import `MaxSuperstepsError` and `ChannelNotFoundError` from `hecate.engine.errors`

## 4. ErrorClassifier Upgrade

- [x] 4.1 Add `classify(error: Exception) -> ErrorCategory` method to ErrorClassifier using isinstance checks
- [x] 4.2 Implement isinstance mapping for HecateError subtypes (EngineError→ENGINE, ChannelError→CHANNEL, SecurityError→SECURITY)
- [x] 4.3 Implement isinstance mapping for provider SDK exceptions (openai.RateLimitError→LLM_RATE_LIMIT, openai.AuthenticationError→LLM_AUTH, etc.) with try/except ImportError guard
- [x] 4.4 Implement string-based fallback for unrecognized exceptions using existing keyword lists
- [x] 4.5 Add `is_retryable_exception(error: Exception) -> bool` that uses classify() instead of string matching
- [x] 4.6 Preserve existing `is_retryable(error: str) -> bool` for backward compatibility
- [x] 4.7 Import ErrorCategory from hecate.engine.errors

## 5. Tests

- [x] 5.1 Test HecateError is catchable as Exception
- [x] 5.2 Test EngineError subtypes (MaxSuperstepsError raised and caught)
- [x] 5.3 Test ChannelNotFoundError is catchable as both ChannelError and KeyError
- [x] 5.4 Test GraphValidationError inherits EngineError (catchable as EngineError, HecateError, Exception, GraphValidationError)
- [x] 5.5 Test GuardrailBlockedError inherits SecurityError
- [x] 5.6 Test ErrorCategory enum string comparison
- [x] 5.7 Test ErrorClassifier.classify with HecateError subtypes
- [x] 5.8 Test ErrorClassifier.classify with provider exception types (mock or real)
- [x] 5.9 Test ErrorClassifier.classify string fallback for unrecognized exceptions
- [x] 5.10 Test ErrorClassifier.is_retryable_exception for retryable categories
- [x] 5.11 Test ErrorClassifier.is_retryable (string) backward compatibility
- [x] 5.12 Test PregelRuntime raises MaxSuperstepsError (not RuntimeError)
- [x] 5.13 Test ChannelManager raises ChannelNotFoundError (not KeyError)

## 6. Documentation

- [x] 6.1 Update AGENTS.md: add engine/errors.py to key files table if applicable
- [x] 6.2 Verify no engine layer violations (errors.py must not import from services/ or models/)
