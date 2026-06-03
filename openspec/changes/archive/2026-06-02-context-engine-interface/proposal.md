## Why

Context management is currently fragmented:
- ConversationService has ContextAssembler, TokenCounter, BudgetManager (high-level)
- EnginePort.context_assemble is pass-through (unused)
- No reusable, testable abstraction for context operations

A ContextEngine ABC provides a clean bottom layer that ConversationService can delegate to, enabling:
- Independent testing of context logic
- Swappable implementations (in-memory, distributed)
- Consistent context handling across both conversation and graph execution paths

## What Changes

- Add `ContextEngine` ABC in `engine/context.py` with methods: `select_messages`, `compress`, `estimate_tokens`
- Add `InMemoryContextEngine` implementation (simple token counting, basic compression)
- P3: Refactor ConversationService to delegate to ContextEngine

## Capabilities

### New Capabilities
- `context-engine`: Pluggable context management interface for message selection, compression, and token estimation

### Modified Capabilities
- None (P2 is interface reservation only)

## Impact

- **New file**: `src/hecate/engine/context.py` (ABC + InMemoryContextEngine)
- **New test**: `tests/test_engine/test_context.py`
- **No breaking changes**: P2 is additive; P3 refactor preserves external interfaces
- **No new dependencies**: Uses only stdlib
