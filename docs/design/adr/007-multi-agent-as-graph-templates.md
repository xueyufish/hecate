# ADR-007: All Multi-Agent Patterns Unified as Graph Templates

> **Status**: Accepted

## Context

The feature list includes multiple multi-agent orchestration patterns: hierarchical delegation, handoff, pipeline, broadcast, peer selection, expert panel, central controller, and others. The design needed to determine how to unify these within the Graph framework.

## Decision

**All orchestration patterns are pre-compiled Graph templates.** No pattern is hardcoded in the engine.

## Rationale

Every orchestration pattern can be expressed as a graph topology:

- **Hierarchical delegation** = agent node nesting
- **Handoff** = Command(goto)
- **Pipeline** = linear chain
- **Broadcast** = fan-out / fan-in
- **Peer selection** = LLM routing loop
- **Negotiation** = cyclic message exchange

By treating all patterns as graphs, Hecate avoids the complexity of maintaining separate code paths for each pattern. Patterns are progressively added to the template library. Any pattern can be visualized and edited in the canvas — users are not locked into preset topologies.

The `agent` type node is the unified primitive: it references another Agent and maps state from parent to child scope.

## Consequences

- Adding a new pattern means creating a new graph template, not writing engine code
- All patterns inherit Checkpoint, streaming, and interrupt capabilities automatically
- The canvas can render any pattern because they all share the same graph structure
