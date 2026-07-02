# ADR-019: Visual Workflow Node Types for Event-Driven HITL and Ontology Operations

> **Status**: Proposed
> **Date**: 2026-07-01

## Context

Hecate's visual canvas (React Flow) supports 8 abstract node types — `llm`, `code`, `condition`, `tool`, `agent`, `subgraph`, `input`, `output` — and relies on the engine-level `interrupt()` API for human-in-the-loop (HITL) workflows. While this abstraction is powerful for developers, competitive analysis of 14 platforms (Dify, Coze, Qianfan, Huawei AgentArts, Palantir AIP, openJiuwen, AgentScope, LangGraph, and others) revealed a pattern: enterprise-grade agent platforms provide higher-level visual node types that map directly to common business scenarios, reducing the gap between "what the engine can do" and "what the user sees on the canvas."

Specifically, three categories of high-level node types are missing:

1. **HITL nodes** — `interrupt()` is a code-level API, not a drag-and-drop node. Enterprise workflows need visible "Human Approval" or "Form Input" nodes on the canvas.
2. **Trigger nodes** — Workflows start from an implicit `__start__`. Event-driven architecture needs explicit entry points (webhook, schedule, event) visible on the canvas.
3. **Ontology operation nodes** — Knowledge Graph CRUD operations require wrapping as generic Tool nodes. Ontology-based development (à la Palantir OSDK) needs dedicated entity-aware node types.

## Decision

Add **three categories of high-level visual node types** (4 features) that compile to existing engine primitives, complemented by an integrated development view:

### 1. Human Input / Form Node (1.1.24)

A first-class canvas node type for structured human-in-the-loop interaction:

- **Visual form designer**: Text fields, dropdowns, date pickers, file upload, multi-select components
- **Approval routing**: Configure approve/reject/modify branches with different downstream edges
- **Engine mapping**: Compiles to `interrupt({ type: "form", schema: ... })` at the engine level — no new engine primitive needed
- **Use cases**: Expense approval, content review, compliance check, data validation

### 2. Trigger Node (1.1.25)

Explicit entry-point node types for event-driven workflows:

| Trigger Type | Source | Engine Mapping |
|-------------|--------|----------------|
| Webhook Trigger | HTTP POST to `/api/workflows/{id}/trigger` | Creates new Session with webhook payload as initial state |
| Schedule Trigger | Cron expression via Scheduled Tasks (13.9) | Creates new Session at cron intervals |
| Event Trigger | EventStore subscription | Creates new Session when matching event arrives |
| MCP Trigger | MCP resource change notification | Creates new Session when MCP server reports resource update |

Replaces implicit `__start__` with explicit, configurable trigger nodes visible on the canvas. Multiple triggers can coexist on the same workflow — a workflow might be both webhook-triggerable and schedule-triggerable.

### 3. Object CRUD Node (1.1.26)

Ontology-aware canvas node types for Knowledge Graph operations:

| Node Type | GraphStore Operation | Ontology Integration |
|-----------|---------------------|---------------------|
| Create Entity | `add_entities()` | Property autocomplete from entity type schema |
| Update Entity | `add_entities()` (upsert) | Schema validation before write |
| Delete Entity | `delete_entities()` | Referential integrity check |
| Query Entity | `search_entities()` / Cypher | Template query builder from schema |
| Create Relation | `add_relations()` | Relation type autocomplete from ontology |
| Traverse Subgraph | `get_neighbors()` | Visual preview of traversal depth |

Each node maps to `GraphStore` ABC operations via `EnginePort.knowledge_query()`. Parameters are type-safe — bound to ontology schema definitions at compile time.

### 4. Side-by-side Chat + Canvas (1.1.27)

An integrated development view where the workflow canvas and a real-time chat preview are displayed side-by-side:

- Left half: Canvas with drag-and-drop graph editing
- Right half: Live chat with the agent being developed
- Active node highlighting: Shows which graph node generated each response segment
- Inline state inspection: Session state, tool call results, channel values visible alongside chat
- No page switching required — edit the graph and test simultaneously

## Rationale

### Why High-Level Node Types?

The 8 abstract node types (`llm`, `code`, `condition`, `tool`, `agent`, `subgraph`, `input`, `output`) are the correct engine-level abstraction — they provide maximum flexibility for arbitrary graph topologies. However, they are too low-level for business analysts and citizen developers who need to model enterprise workflows visually.

High-level visual node types compile down to the same engine primitives:

```
Human Input / Form Node → interrupt() + Command(resume) wrapper
Trigger Node → __start__ + external trigger source mapping
Object CRUD Node → Tool node + GraphStore-specific tool binding
```

This maintains engine-level simplicity (no new node types in the Graph DSL) while adding canvas-level expressiveness for common patterns.

### Why Not Add New Graph DSL Node Types?

Adding `human_input`, `trigger`, `object_crud` as new Graph DSL node types would:
- Violate the engine's zero-dependency principle (Form Node needs UI components, Object CRUD needs GraphStore)
- Increase compiler complexity (new node types need validation, optimization, and execution support)
- Create coupling between the visual canvas and the execution engine

Instead, these node types exist only in the visual canvas layer. The canvas-to-DSL compilation step translates them into existing primitives before the compiler processes them. This is the same approach used for collaboration patterns (ADR-007) — the 6 patterns are canvas-level templates that compile to graph topologies.

### Side-by-side Chat + Canvas

This is a UX enhancement, not an engine feature. It requires:
- Split-pane layout in the Studio frontend
- WebSocket connection for streaming chat alongside canvas state
- Active node tracking (which superstep is currently executing)
- No backend changes — uses existing streaming and execution APIs

## Consequences

- Features 1.1.24 (Human Input/Form Node) and 1.1.25 (Trigger Node) are P3 (Sprint 5) — they depend only on existing `interrupt()` and Scheduled Tasks/Webhooks
- Feature 1.1.26 (Object CRUD Node) is P4 (Sprint 6) — it depends on Knowledge Graph (3.5.1-3.5.3, Sprint 5)
- Feature 1.1.27 (Side-by-side Chat+Canvas) is P4 (Sprint 6) — it depends on Execution State Visualization (1.1.23, Sprint 6)
- The Graph DSL schema (`graph-dsl.schema.json`) does not change — no new node types at the engine level
- The canvas-to-DSL compilation step gains new translation rules for high-level node types
- Existing workflows are unaffected — the new node types are additive
