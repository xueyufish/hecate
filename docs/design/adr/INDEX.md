# ADR Index — by Topic

The 29 ADRs in this directory cover every significant architectural decision since Hecate started. They are listed chronologically in the [README](../README.md#architecture-decision-records-adrs). This index provides an alternative **topic-grouped** view to help you find the ADRs relevant to a specific concern.

Each ADR row links to the full document. Use this index as a **map**; read the individual ADRs for the "why" behind each decision.

---

## By topic

### 🏛️ Architecture foundations

The big-picture design that everything else builds on.

| ADR | Title | Topic |
|---|---|---|
| [ADR-001](001-graph-first-orchestration.md) | Graph-First Orchestration with Three-Layer Agent as Preset Template | Engine — orchestration model |
| [ADR-002](002-five-layer-architecture.md) | Five-Layer System Architecture | Top-level layered design |
| [ADR-016](016-platform-spi-architecture.md) | Platform SPI Architecture with 15 Extension Points | Engine — extensibility model |
| [ADR-029](029-trust-tiered-kernel-plugin-architecture.md) | Trust-Tiered Kernel and Plugin Architecture | Minimal kernel, four mounting planes, five isolation tiers |

### ⚙️ Engine runtime

Pregel execution, state, scheduling, async, optimization.

| ADR | Title | Topic |
|---|---|---|
| [ADR-001](001-graph-first-orchestration.md) | Graph-First Orchestration | (also under Architecture) |
| [ADR-003](003-checkpoint-persistence.md) | Checkpoint Persistence with Memory Cache | State persistence |
| [ADR-005](005-progressive-worker-pool.md) | Progressive Worker Pool for Distributed Execution | Worker pool scaling |
| [ADR-016](016-platform-spi-architecture.md) | Platform SPI Architecture | (also under Architecture) |
| [ADR-020](020-async-execution-distributed-state.md) | Asynchronous Execution and Distributed Session State | Distributed execution |

### 🤖 Multi-agent orchestration

Patterns and node types for multi-agent flows.

| ADR | Title | Topic |
|---|---|---|
| [ADR-007](007-multi-agent-as-graph-templates.md) | All Multi-Agent Patterns Unified as Graph Templates | Collaboration patterns |
| [ADR-019](019-visual-workflow-node-types.md) | Visual Workflow Node Types for Event-Driven HITL and Ontology Operations | Node types for HITL |

### 🔌 Protocols and interop

MCP, A2A, plugin systems — how Hecate talks to the outside world.

| ADR | Title | Topic |
|---|---|---|
| [ADR-011](011-a2a-protocol-adoption.md) | A2A Protocol Adoption for Cross-Framework Agent Interoperability | A2A |
| [ADR-012](012-mcp-streamable-http.md) | MCP Streamable HTTP Transport Upgrade | MCP |
| [ADR-016](016-platform-spi-architecture.md) | Platform SPI Architecture | (also under Architecture) |

### 🔒 Security and identity

Guardrails, RBAC, zero trust, multi-tenancy.

| ADR | Title | Topic |
|---|---|---|
| [ADR-008](008-security-via-hooks.md) | Security via Engine-Level Hooks and Plugin Extension Points | Engine-level guardrails |
| [ADR-018](018-zero-trust-identity-architecture.md) | Zero Trust Identity Architecture for Enterprise Agent Access | Auth / multi-tenant |
| [ADR-025](025-enterprise-foundation-enhancement.md) | Enterprise Foundation Enhancement Architecture | Multi-tenancy enhancement |
| [ADR-026](026-security-shield-enhancement.md) | Security Shield Enhancement Architecture | Security deepening |
| [ADR-029](029-trust-tiered-kernel-plugin-architecture.md) | Trust-Tiered Kernel and Plugin Architecture | (also under Architecture & Ecosystem) Plugin trust model / isolation tiers |

### 📚 Knowledge, memory, ontology

RAG, memory levels, ontology, knowledge graph.

| ADR | Title | Topic |
|---|---|---|
| [ADR-006](006-four-level-memory.md) | Four-Level Memory with Progressive Implementation | Memory architecture |
| [ADR-014](014-ontology-action-system.md) | Ontology Action System for Decision Execution | Ontology |
| [ADR-015](015-ontology-augmented-generation.md) | Ontology-Augmented Generation (OAG) | Ontology |
| [ADR-017](017-knowledge-graph-architecture.md) | Knowledge Graph Architecture with GraphStore ABC | Knowledge graph |
| [ADR-024](024-knowledge-memory-enhancement.md) | Knowledge & Memory Enhancement Architecture | RAG enhancement |

### 🎨 Visual canvas and studio

Drag-and-drop UI for designing workflows.

| ADR | Title | Topic |
|---|---|---|
| [ADR-010](010-react-flow-canvas.md) | React Flow Canvas with JSON DSL Bidirectional Sync | Visual canvas choice |
| [ADR-019](019-visual-workflow-node-types.md) | Visual Workflow Node Types | (also under Multi-agent) |

### 🤖 Model hub and LLM integration

100+ LLM providers, routing, cost.

| ADR | Title | Topic |
|---|---|---|
| [ADR-002](002-five-layer-architecture.md) | Five-Layer System Architecture | (also under Architecture) |
| [ADR-022](022-model-hub-enhancement.md) | Model Hub Enhancement Architecture | Model routing / cost |

### 🔧 Tools and skills

Tool system, skill format, plugin architecture.

| ADR | Title | Topic |
|---|---|---|
| [ADR-004](004-skill-system.md) | SKILL.md Format for Skill System | Skill packaging |
| [ADR-012](012-mcp-streamable-http.md) | MCP Streamable HTTP Transport Upgrade | (also under Protocols) |
| [ADR-023](023-tool-platform-enhancement.md) | Tool Platform Enhancement Architecture | Tool platform deepening |

### 📡 Observability and evaluation

Metrics, traces, evaluations, observability deepening.

| ADR | Title | Topic |
|---|---|---|
| [ADR-021](021-ops-center-architecture.md) | Ops Center Architecture | Observability platform |
| [ADR-028](028-observability-evaluation-enhancement.md) | Observability & Evaluation Enhancement Architecture | Observability deepening |

### 🌐 API surface and SDK

Public API design.

| ADR | Title | Topic |
|---|---|---|
| [ADR-009](009-dual-api-design.md) | OpenAI-Compatible API + Management API Dual Track | API surface |

### 🤖 Self-learning and RL

Future-state capabilities (planned, not yet shipped).

| ADR | Title | Topic |
|---|---|---|
| [ADR-013](013-agentic-rl-framework.md) | Agentic RL Framework for Agent Self-Optimization | Future RL framework |

### 🌐 Ecosystem and distribution

Marketplace, plugins, distribution.

| ADR | Title | Topic |
|---|---|---|
| [ADR-027](027-ecosystem-enhancement.md) | Ecosystem Enhancement Architecture | Marketplace, community |
| [ADR-029](029-trust-tiered-kernel-plugin-architecture.md) | Trust-Tiered Kernel and Plugin Architecture | (also under Architecture & Security) Marketplace trust model, isolation tiers, install authority |

---

## By phase

For engineers understanding which ADRs are foundational vs. enhancement: which ADRs are foundational vs. enhancement?

### P1 (Alpha — current)
Foundation + core runtime + protocol adoption:

`ADR-001`, `ADR-002`, `ADR-003`, `ADR-004`, `ADR-005`, `ADR-006`, `ADR-007`, `ADR-008`, `ADR-009`, `ADR-010`, `ADR-011`, `ADR-012`, `ADR-016`, `ADR-018`, `ADR-021`

### P2 (Beta → 1.0 RC)
Multi-agent maturity + observability deepening:

`ADR-019`, `ADR-022`, `ADR-023`, `ADR-024`, `ADR-025`, `ADR-026`, `ADR-028`

### P3+ (Post-1.0)
Future capabilities:

`ADR-013`, `ADR-014`, `ADR-015`, `ADR-017`, `ADR-020`, `ADR-027`

---

## By file size

Quick reference — the shortest and longest ADRs:

| ADR | Lines | Topic |
|---|---|---|
| ADR-004 | ~30 | Skill System |
| ADR-011 | ~50 | A2A Adoption |
| ADR-007 | ~60 | Multi-Agent Templates |
| ... | ... | ... |
| ADR-021 | ~400 | Ops Center |
| ADR-016 | ~300 | Platform SPI |
| ADR-024 | ~280 | Knowledge Enhancement |

(Sizes are approximate.)

---

## ADR template

New ADRs should follow this structure (see any existing ADR for an example):

```markdown
# ADR-NNN: <Title>

> **Status**: Proposed | Accepted | Deprecated | Superseded by ADR-XXX
> **Date**: YYYY-MM-DD

## Context
What is the issue we're seeing that motivates this decision?

## Decision
What is the change we're proposing or have agreed to implement?

## Rationale
Why this approach over alternatives considered.

## Consequences
What becomes easier or harder as a result of this decision.
```

### Status conventions

| Status | Meaning |
|---|---|
| **Proposed** | Under discussion; not yet implemented |
| **Accepted** | Decision made; being or has been implemented |
| **Deprecated** | Decision no longer applies; replaced by a newer ADR (link the replacement) |
| **Superseded by ADR-NNN** | Newer ADR reverses or replaces this one |

---

## How to propose a new ADR

1. Open an issue with the `proposal:adr` tag
2. Use the template above
3. Discuss in the issue until consensus (or maintainer decision)
4. Submit a PR with the ADR file named `NNN-short-name.md`
5. Add the ADR row to the appropriate topic table in this INDEX
6. Update the chronological list in [README](../README.md#architecture-decision-records-adrs)

---

## Related documents

- [README](../README.md) — chronological ADR list + design center overview
- [Positioning](../positioning.md) — why Hecate's architecture makes these decisions
-  — which phase each ADR belongs to
- [A2A Architecture](../a2a-architecture.md) — implements ADR-011
- [Extension SPI Architecture](../extension-architecture.md) — implements ADR-016
- [Multi-Tenancy Architecture](../multi-tenancy-architecture.md) — implements ADR-018 + ADR-025
- [Observability Architecture](../observability-architecture.md) — implements ADR-021 + ADR-028