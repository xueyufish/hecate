## Context

Hecate's engine layer provides all primitives needed for multi-agent orchestration: TOPIC channels (append-reduce message accumulation), LAST_VALUE channels (single-value state), sequential edge resolution in PregelRuntime (one node per super-step for linear graphs), FAN_OUT/MERGE for parallel dispatch, and AgentWorker for agent nodes.

Five factory functions exist in `engine/templates.py`: `build_chat_graph()`, `build_three_layer_graph()`, `build_fan_out_pipeline()`, `build_conditional_pipeline()`, `build_reflection_loop()`. Each constructs a `GraphConfig` with manually wired channels (readable/writable per node), edges, and state declarations.

Six JSON templates exist in `data/orchestration_templates/`: chat, three-layer, fan-out, conditional, reflection, and content-pipeline. The content-pipeline template (`content-pipeline.json`) demonstrates a sequential researcher→writer→reviewer pattern with manual channel wiring, but it is a single hardcoded use case.

The gap: developers wanting a generic sequential pipeline or broadcast pattern must understand channel wiring semantics and construct Graph DSL dicts manually. This is a DX problem, not an engine capability gap.

## Goals / Non-Goals

**Goals:**
- Provide `build_sequential_pipeline(stages=[...])` that auto-wires channels for linear A→B→C→D workflows
- Provide `build_broadcast_pipeline(participants=[...])` that creates shared-channel round-robin graphs
- Add JSON templates for both patterns to the orchestration-templates catalog
- Follow existing factory function conventions exactly (return `GraphConfig`, use same parameter patterns)

**Non-Goals:**
- New engine primitives — all needed primitives (TOPIC, LAST_VALUE, AGENT nodes, CONDITION nodes, sequential edge resolution) already exist
- Compiler changes — no new validation rules or compilation paths
- Typed stage input/output contracts (e.g., Pydantic models for inter-stage data) — future enhancement
- Dynamic pipeline construction at runtime — factories produce static GraphConfig
- Streaming support changes — PregelRuntime streaming works as-is

## Decisions

### D1: Sequential pipeline uses dedicated per-stage LAST_VALUE channels

**Decision**: Each stage gets a `{stage_id}_output` LAST_VALUE channel. Stage N writes to `{stage_id}_output`, stage N+1 reads from `{stage_id}_output` (the previous stage's output channel). The shared `messages` TOPIC channel accumulates all agent interactions.

**Alternatives considered**:
- Single shared LAST_VALUE channel for inter-stage data → conflates outputs, no way for stage N+2 to reference stage N's output without parsing messages
- No inter-stage channels, rely solely on messages TOPIC → agents must parse message history to extract structured data

**Rationale**: Matches `content-pipeline.json` pattern (research_data, draft, review_status) and `build_reflection_loop()` pattern (draft, quality_status). Explicit per-stage channels make data flow visible and debuggable.

### D2: Broadcast pipeline uses sequential round-robin, not concurrent dispatch

**Decision**: Broadcast participants execute sequentially in a fixed order, all sharing the same `messages` TOPIC channel. Each participant sees all previous messages (from the TOPIC's append-reduce behavior) and appends its own response.

**Alternatives considered**:
- Concurrent execution (all agents in same super-step) → agents cannot see each other's responses within the same step; requires MERGE semantics; loses the conversational "discussion" quality
- True pub/sub with real-time message visibility → would require new engine primitives for message broadcasting within a super-step

**Rationale**: Matches AutoGen's `RoundRobinGroupChat` and AgentScope's `MsgHub` with sequential execution. The shared TOPIC channel naturally accumulates all messages. This is the most useful broadcast pattern for practical multi-agent discussions.

### D3: Revision loop is optional in sequential pipeline

**Decision**: `build_sequential_pipeline()` accepts an optional `revision_config` parameter. When provided, a CONDITION node and revision loop are appended after the last stage. When omitted, the pipeline is strictly linear.

**Rationale**: The `content-pipeline.json` template shows a revision loop (reviewer → check_revision → writer), but many pipelines are purely linear (ETL-style). Making it optional covers both cases without requiring separate factory functions.

### D4: Broadcast supports configurable turn limits via max_supersteps

**Decision**: The broadcast factory sets `GraphConfig` metadata with a `max_turns` value that maps to PregelRuntime's `max_supersteps` parameter. No new termination condition primitive needed.

**Rationale**: PregelRuntime already has `max_supersteps` as a safety limit. For broadcast round-robin, the number of turns = number of participants × rounds. The caller can configure this via the runtime, not the graph config.

### D5: Factory function signatures follow existing patterns

**Decision**: Use `TypedDict` for stage/participant definitions instead of Pydantic models, matching the existing `NodeConfig.config` dict pattern. Each stage/participant is a plain dict with `id`, `model`, `system_prompt` keys.

**Rationale**: Consistency with existing factories. No new dependency on Pydantic in the engine layer. The `NodeConfig.config` field is already `dict[str, Any]`.

## Risks / Trade-offs

- **[Generic pipeline may not fit all use cases]** → Mitigation: Factories produce standard `GraphConfig` that users can modify after construction. Not a locked-in abstraction.
- **[Per-stage channels create channel proliferation for long pipelines]** → Mitigation: Acceptable for typical 3-7 stage pipelines. If needed in the future, a "compact" mode could use a single `pipeline_state` dict channel.
- **[Broadcast round-robin assumes fixed order]** → Mitigation: Matches AutoGen/CrewAI conventions. Dynamic ordering (e.g., LLM-selected speaker) is a separate feature.
- **[No typed data contracts between stages]** → Mitigation: Intentionally out of scope. Stages communicate via channels, not typed interfaces. Can add Pydantic contracts later as a separate enhancement.
