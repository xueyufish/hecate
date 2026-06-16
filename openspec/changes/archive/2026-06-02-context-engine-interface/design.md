## Context

Hecate has two execution paths:
1. **Conversation path**: API → ConversationService → LLM (direct)
2. **Graph path**: PregelRuntime → AgentWorker → EnginePort → ConversationService → LLM

Both paths need context management (message selection, compression, token estimation). Currently:
- ConversationService has its own context logic (ContextAssembler, TokenCounter, etc.)
- EnginePort.context_assemble is pass-through
- No reusable abstraction exists

## Goals / Non-Goals

**Goals:**
- Define `ContextEngine` ABC with three core methods
- Provide `InMemoryContextEngine` for testing and single-machine deployment
- Position as engine-internal ABC (not on EnginePort)
- Prepare for P3 refactor where ConversationService delegates to ContextEngine

**Non-Goals:**
- Refactoring ConversationService (P3)
- Distributed context engine (P4+)
- Replacing EnginePort.context_assemble (it becomes a call-through to ContextEngine)

## Decisions

### D1: ContextEngine is engine-internal, not on EnginePort

**Choice**: Create `engine/context.py` parallel to `engine/eventstore.py`.

**Rationale**: ContextEngine is an implementation detail of how context is managed. EnginePort.context_assemble remains the public API; it delegates to ContextEngine internally.

### D2: Three-method interface

**Choice**:
- `select_messages(history: list[dict], budget: int) -> list[dict]` — Select which messages to include
- `compress(messages: list[dict]) -> list[dict]` — Compress messages (summarize, truncate)
- `estimate_tokens(messages: list[dict]) -> int` — Estimate token count

**Rationale**: These are the three fundamental context operations. Higher-level logic (phase detection, prioritization) belongs in ConversationService.

### D3: InMemoryContextEngine uses simple heuristics

**Choice**: Default implementation uses:
- `select_messages`: Keep last N messages that fit budget
- `compress`: Truncate oldest messages beyond threshold
- `estimate_tokens`: Simple character-based estimation (4 chars ≈ 1 token)

**Rationale**: Good enough for testing and single-machine. Real implementation (P3+) can use tiktoken or provider-specific counting.

### D4: ConversationService integration deferred to P3

**Choice**: P2 only defines the ABC and InMemory implementation. P3 refactors ConversationService.

**Rationale**: Avoids breaking existing code. Allows testing the interface before committing to integration.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| ContextEngine duplicates ConversationService logic | P3 refactor will consolidate; P2 is just interface reservation |
| Simple token estimation may be inaccurate | Good enough for budget checks; P3 can add tiktoken |
| Engine-internal ABC adds complexity | Follows established pattern (EventStore, SchedulerStrategy, EvictionPolicy) |

## Open Questions

None — follows established P2 interface reservation pattern.
