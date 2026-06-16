# ADR-001: Graph-First Orchestration with Three-Layer Agent as Preset Template

> **Status**: Accepted

## Context

Hecate needed to determine the basic paradigm for Agent orchestration. The options were fixed layering (like a hardcoded Guard→Plan→Execute pipeline), general-purpose graph (like LangGraph), or code-first (like AutoGen).

## Decision

Adopt **graph orchestration as the primary paradigm**, with the three-layer Agent (Guard→Plan→Sub-Agent) as a preset workflow template — not a hardcoded path.

## Rationale

The three-layer Agent is a special case of fixed graph topology, not a replacement for general-purpose graphs. By making graph the foundation, Hecate supports progressive complexity: users can start with conversation mode (no graph), upgrade to the three-layer Agent template (preset graph), then customize in the visual canvas (arbitrary graph) — each level is backward compatible.

This avoids the limitation seen in systems that hardcode specific orchestration patterns. Users who need a different topology (e.g., pipeline, broadcast, negotiation) can express it as a graph without framework changes.

## Consequences

- The orchestration layer must compile graph definitions before execution
- The three-layer Agent is implemented as a JSON DSL template, not special-cased code
- All multi-agent patterns must be expressible as graph topologies
