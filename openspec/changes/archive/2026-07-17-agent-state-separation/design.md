## Context

Hecate's current per-session state lives in an ephemeral `execution_context` dict created fresh each `WorkflowExecutionService.execute()` call. When the process exits, all working state is lost — conversation buffers, compressed summaries, permission caches, and tool/task sub-contexts. Only durable storage (DB messages, CheckpointStore for graph execution, AgentEnvironment filesystem) survives.

This is a gap compared to competitor platforms:
- **AgentScope 2.0**: `AgentState` (Pydantic) with `AgentStateStore` (InMemory/File/Redis/MySQL/OSS)
- **Claude Code**: `SessionStore` adapter (S3/Redis/Postgres) with dual-write architecture
- **Bedrock AgentCore**: Managed session storage (per-session filesystem, 14-day retention)
- **Codex**: Rollout system (JSONL + SQLite index, append-only)

The `execution_context` already contains the seeds of AgentState: `session_id`, `context_engine`, `environment_root`. The gap is structure, persistence, and lifecycle management.

## Goals / Non-Goals

**Goals:**
- Introduce `AgentState` as a structured Pydantic model representing per-session working state
- Define `AgentStateStore` ABC for pluggable state persistence
- Implement `InMemoryStateStore` for single-machine use and testing
- Integrate state load/save lifecycle into `WorkflowExecutionService`
- Populate `environment_root` from `EnvironmentManager` into AgentState automatically

**Non-Goals (deferred to later features):**
- Redis/Postgres state store backends (→ 13.4a Distributed Session State Store)
- Compressed summary implementation (→ ContextEngine enhancement)
- REST API for state inspection (→ internal service-layer only)
- Multi-tenant state isolation (→ 10.5 Tenant Isolation)
- State serialization format optimization (JSON via Pydantic is sufficient for MVP)

## Decisions

### D1: AgentStateStore in services/ (not engine/)

**Decision**: Place `AgentStateStore` ABC and implementations in `src/hecate/services/state/`.

**Rationale**: The engine layer cannot import from services/ (layering rule). AgentState needs to reference `AgentEnvironment` (in services/) and will be consumed by `WorkflowExecutionService` (in services/). Placing it in services/ avoids layering violations. The engine's `CheckpointStore` (graph-level) is orthogonal and stays in engine/.

**Alternative considered**: Place ABC in `engine/ports.py`. Rejected because it would require the engine to know about services-layer concepts (EnvironmentManager).

### D2: AgentState as execution_context field (not replacement)

**Decision**: AgentState is injected into `execution_context["_agent_state"]`. Workers access it indirectly through `execution_context`.

**Rationale**: Minimal change to existing Worker interface. Workers already receive `execution_context` as a dict — adding a key is non-breaking. Replacing `execution_context` entirely would require changes to every Worker's `execute()` signature.

**Alternative considered**: Make AgentState the new `execution_context` type. Rejected because it changes the Worker contract and is a larger refactor for MVP.

### D3: Summary field reserved, not implemented

**Decision**: AgentState includes a `summary: str` field that is always empty in MVP. ContextEngine enhancement will populate it later.

**Rationale**: The field exists in the data model (so consumers can start coding against it), but no compression logic is implemented yet. This avoids coupling AgentState to ContextEngine internals prematurely.

### D4: Per-call write strategy

**Decision**: AgentState is loaded at `execute()` entry and saved at `execute()` exit (once per call). Not saved on every message or channel write.

**Rationale**: Matches AgentScope's pattern. Reduces store pressure. The call is the natural atomic unit — if the process crashes mid-call, the previous state is still valid (the call will be retried).

**Alternative considered**: Save on every superstep (like LangGraph). Rejected because it adds latency and complexity for MVP.

### D5: Pydantic BaseModel for serialization

**Decision**: AgentState extends Pydantic `BaseModel` with `model_dump()` / `model_validate()` for serialization.

**Rationale**: Consistent with AgentScope's approach. Pydantic handles type validation, JSON serialization, and schema evolution. Already available as a project dependency.

### D6: asyncio.Lock for concurrent access safety

**Decision**: InMemoryStateStore uses `asyncio.Lock` per session_id to prevent concurrent writes.

**Rationale**: Multiple coroutines may access the same session (e.g., streaming + background save). The lock is per-key, not global, so different sessions can be saved concurrently.

## Risks / Trade-offs

| Risk | Mitigation |
|------|-----------|
| InMemoryStateStore loses state on process restart | Expected for MVP. Redis/Postgres backends deferred to 13.4a. Documented limitation. |
| AgentState size growth (large context lists) | Mitigated by future ContextEngine compression. MVP has no size limit — will add when needed. |
| Concurrent save conflicts | asyncio.Lock prevents corruption. Not distributed-safe — acceptable for single-process MVP. |
| execution_context dict mutation leaks state | AgentState is a Pydantic model (copy semantics). Mutations in Workers don't affect the stored snapshot until explicit save. |

## Migration Plan

No migration needed — this is purely additive. Existing `execution_context` behavior is unchanged. AgentState is optional: if no `AgentStateStore` is provided, `WorkflowExecutionService` behaves exactly as before.

## Open Questions

- **State size monitoring**: Should we add a warning when AgentState exceeds a threshold? (Defer to observability phase)
- **Garbage collection of old sessions**: InMemoryStateStore grows unbounded. Add TTL eviction? (Defer to EnvironmentManager's TTL pattern)
