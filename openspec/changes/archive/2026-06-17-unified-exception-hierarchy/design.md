## Context

Hecate's engine layer currently uses generic Python exceptions (ValueError, KeyError, RuntimeError) with no domain-specific types. The existing `ErrorClassifier` in `services/validation/retry_policy.py` classifies errors via string keyword matching (checking for "timeout", "rate limit", "429" etc. in error messages).

The original spec for 1.3.5g called for a full hierarchy: `HecateError → LLMError/ToolError/EngineError/SecurityError/ChannelError` with ~15 exception subtypes. However, research across 10 platforms shows this approach is not industry practice:

- **0/10 platforms** wrap provider exceptions into their own LLMError tree
- OpenAI SDK uses status-code-based hierarchy (RateLimitError=429, AuthenticationError=401)
- LiteLLM extends OpenAI's hierarchy directly
- LangChain uses dual-inheritance mapping (e.g., `OpenAIContextOverflowError(openai.BadRequestError, ContextOverflowError)`)
- Google ADK lets Gemini errors pass through unwrapped, uses `ToolErrorType` enum for semantics
- LangGraph defines only graph-specific errors (GraphRecursionError, NodeTimeoutError, InvalidUpdateError)

## Goals / Non-Goals

**Goals:**
- Define `HecateError` base class with Hecate-specific exception categories
- Replace generic exceptions (RuntimeError, KeyError) with typed Hecate exceptions
- Define `ErrorCategory` enum for semantic error classification
- Upgrade `ErrorClassifier` to support isinstance-based matching alongside string fallback
- Maintain full backward compatibility — all existing except blocks continue to work

**Non-Goals:**
- LLMError/ToolError exception tree (deferred — industry consensus is to let provider exceptions pass through)
- Dual-inheritance provider mapping (P4 — LangChain pattern, requires provider-specific classes)
- Framework-Level Auto-Retry (1.3.5h — separate change, depends on this one)
- Platform-Level Tool Gating (1.3.5f — independent)
- Refactoring API layer error handling (separate change)

## Decisions

### D1: Only define Hecate-specific exceptions, not LLM/Tool wrappers

**Choice**: Define `HecateError → EngineError/ChannelError/SecurityError` only. Do not create LLMError or ToolError exception trees.

**Rationale**: 10-platform research shows zero platforms wrap provider exceptions. Hecate uses LiteLLM which already inherits OpenAI's typed exceptions (RateLimitError, AuthenticationError, etc.). Wrapping them in `LLMRateLimitError` adds a layer with no value — `isinstance(e, openai.RateLimitError)` already works.

**Alternatives considered**:
- Full tree (spec original): Rejected — no industry precedent, engine layer can't import provider SDKs (layer constraint)
- Dual-inheritance (LangChain pattern): Deferred to P4 — requires provider-specific mapping classes in services layer

### D2: ErrorCategory enum replaces LLMError/ToolError for classification

**Choice**: Define `ErrorCategory` StrEnum with semantic categories for all error sources (LLM, Tool, Engine, Security, Channel). `ErrorClassifier.classify()` returns `ErrorCategory` instead of bool.

**Rationale**: Google ADK's `ToolErrorType` enum proves that semantic categorization via enum is sufficient for retry decisions, observability, and error reporting. It avoids the complexity of a full exception tree while providing the same classification power.

### D3: ErrorClassifier upgraded in-place, backward compatible

**Choice**: Extend existing `ErrorClassifier` in `services/validation/retry_policy.py` with new `classify(error) -> ErrorCategory` method. Existing `is_retryable(error_string)` method preserved with string fallback.

**Rationale**: ErrorClassifier is already used by RetryPolicy and CircuitBreaker. The upgrade adds isinstance checks before falling back to string matching. No breaking change to existing callers.

### D4: GraphValidationError inheritance change

**Choice**: Change `GraphValidationError(Exception)` to `GraphValidationError(EngineError)`.

**Rationale**: EngineError inherits from HecateError inherits from Exception. Python's exception handling checks inheritance chains, so `except GraphValidationError` and `except Exception` both continue to work. The only behavioral change: `except EngineError` now also catches GraphValidationError (desired).

### D5: MaxSuperstepsError and ChannelNotFoundError replace generic exceptions

**Choice**: Replace `raise RuntimeError(...)` in pregel.py with `raise MaxSuperstepsError(...)`. Replace `raise KeyError(...)` in channel.py with `raise ChannelNotFoundError(...)`.

**Rationale**: LangGraph uses `GraphRecursionError(RecursionError)` and `InvalidUpdateError` for similar situations. Typed exceptions allow API layer to catch specific engine errors for appropriate HTTP status mapping.

### D6: GuardrailBlockedError vs existing GuardrailAction.BLOCK

**Choice**: Define `GuardrailBlockedError(SecurityError)` as an optional exception that can be raised by guardrail hooks, while preserving the existing `GuardrailResult(action=BLOCK)` return-based pattern.

**Rationale**: The current return-based pattern (PreLLMHook returns GuardrailResult with action=BLOCK) is the primary mechanism. GuardrailBlockedError provides an alternative for code paths that prefer exception-based control flow. Both patterns coexist.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| GraphValidationError inheritance change could break isinstance checks | Python inheritance chain: GraphValidationError → EngineError → HecateError → Exception. All existing except blocks work. |
| ErrorClassifier isinstance checks require importing provider SDKs | Classifier lives in services/ layer (not engine/), so importing openai/litellm is allowed. |
| ErrorCategory enum may not cover all edge cases | UNKNOWN category as fallback. String-based matching preserved for unrecognized errors. |
| GuardrailBlockedError duplicates GuardrailAction.BLOCK | Both coexist by design — return-based for hooks, exception-based for direct calls. |
