## Why

Hecate's engine layer uses generic Python exceptions (ValueError, KeyError, RuntimeError) with no way to distinguish error source at the exception level. The existing `ErrorClassifier` in `services/validation/retry_policy.py` classifies errors by string keyword matching — fragile and indirect. API layer error handling relies on ad-hoc if-else string inspection rather than structured exception types.

Research across 10 platforms (OpenAI SDK, LiteLLM, LangChain, LangGraph, Google ADK, IBM watsonx, Salesforce, Huawei, AutoGen, CrewAI) shows that **no platform wraps provider exceptions into their own LLMError/ToolError tree**. All let provider SDK exceptions pass through. The industry consensus is: define your own domain-specific errors only (engine, channel, security), use an error category enum for classification, and upgrade the classifier to use isinstance checks.

## What Changes

- Define `HecateError(Exception)` as the base exception for all Hecate-specific errors in a new `engine/errors.py`
- Define three Hecate-specific exception categories: `EngineError`, `ChannelError`, `SecurityError` with subtypes
- Change `GraphValidationError` inheritance from `Exception` to `EngineError` (backward compatible — EngineError inherits from HecateError inherits from Exception)
- Define `ErrorCategory` StrEnum with semantic categories (LLM_RATE_LIMIT, LLM_AUTH, LLM_TIMEOUT, TOOL_TIMEOUT, ENGINE, SECURITY, etc.)
- Upgrade `ErrorClassifier` to support isinstance-based type matching with provider SDK exceptions (openai.RateLimitError, etc.) while preserving string-based fallback
- Update PregelRuntime's `MaxSupersteps` error from generic `RuntimeError` to `MaxSuperstepsError(EngineError)`

## Capabilities

### New Capabilities

- `exception-hierarchy`: HecateError base class with EngineError/ChannelError/SecurityError subtypes, ErrorCategory enum, and upgraded ErrorClassifier

### Modified Capabilities

(none — existing specs for guardrail-hook, engine-types, channel-registry remain unchanged; error classification is additive)

## Impact

- **New file**: `src/hecate/engine/errors.py` — exception hierarchy + ErrorCategory enum
- **Modified**: `src/hecate/services/validation/retry_policy.py` — ErrorClassifier upgraded with isinstance support
- **Modified**: `src/hecate/engine/graph_dsl.py` — GraphValidationError inherits EngineError
- **Modified**: `src/hecate/engine/pregel.py` — MaxSuperstepsError replaces RuntimeError
- **Modified**: `src/hecate/engine/channel.py` — ChannelNotFoundError replaces bare KeyError
- **Tests**: New tests for exception hierarchy, ErrorCategory classification, ErrorClassifier isinstance matching
- **No breaking changes**: All existing except blocks continue to work (HecateError inherits Exception)
