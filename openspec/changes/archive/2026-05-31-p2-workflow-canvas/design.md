## Context

Hecate's execution engine is complete: Graph DSL JSON Schema defines 4 node types (conversation, tool-call, condition, agent) and 4 channel types; `compiler.py` compiles DSL into a validated graph; `PregelRuntime` executes it with channels, checkpoints, and interrupt support. WorkflowModel + WorkflowVersionModel ORM models exist with versioning support. The existing workflow CRUD API (`api/management/workflows.py`) handles basic create/update/delete but has no frontend.

The gap: users must write raw JSON DSL to create workflows. There is no visual editor, no way to configure nodes without understanding the JSON schema, and no way to test a workflow without deploying an Agent.

## Goals / Non-Goals

**Goals:**
- Provide a React Flow-based visual DAG editor where users drag nodes from a palette, connect them with edges, and configure each node via a side panel
- Support all 4 existing DSL node types + 2 new utility types (knowledge-retrieval, variable-set)
- Bidirectional conversion: visual state ↔ Graph DSL JSON so users can edit visually or via JSON
- Test run: execute a workflow with sample input and show per-node execution status
- Integrate into the existing dashboard (sidebar entry, consistent styling)

**Non-Goals:**
- Real-time collaborative editing (single-user only for now)
- Custom node type creation by users (only built-in types)
- Sub-workflow nesting in the canvas (subgraph nodes reference existing workflows by ID)
- Undo/redo beyond browser defaults
- Mobile responsive canvas (desktop only)
- Workflow scheduling / cron triggers (P3)
- Version diff visualization (just list versions)

## Decisions

### D1: React Flow v12 (`@xyflow/react`) as canvas library

**Choice**: `@xyflow/react` (React Flow v12)
**Alternatives considered**: elkjs (layout-only), dagre (layout-only), draw2d (Canvas-based), custom Canvas/SVG
**Rationale**: React Flow is the most mature React DAG library (25k+ GitHub stars), provides drag/drop, zoom/pan, minimap, custom nodes/edges out of the box. Used by Langflow, Coze, and Dify for similar use cases. v12 has first-class Next.js App Router support.

### D2: Client-side DSL serialization

**Choice**: Graph DSL ↔ React Flow conversion happens entirely in the browser
**Alternatives considered**: Server-side conversion endpoint
**Rationale**: The DSL schema is simple (nodes dict + edges array). Client-side conversion avoids round-trips, enables instant validation feedback, and keeps the canvas responsive. The existing `graph-dsl.schema.json` can be used with `zod` for validation.

### D3: Workflow API extends existing endpoints

**Choice**: Add test-run and validation endpoints to the existing workflow API
**Alternatives considered**: Separate workflow-execution service
**Rationale**: The existing `api/management/workflows.py` already has CRUD. We add `POST /api/workflows/{id}/validate` (dry-run compile), `POST /api/workflows/{id}/test-run` (execute with input), and `GET /api/workflows/{id}/runs` (list runs). No new service layer needed — the Pregel runtime is invoked directly through EnginePort.

### D4: Node configuration via side panel, not inline

**Choice**: Click a node → right side panel opens with a form for that node's config
**Alternatives considered**: Inline editing (double-click to edit), modal dialog
**Rationale**: Side panel gives more space for complex configs (model selector, tool picker, prompt editor), doesn't obstruct the canvas, and is consistent with how Coze/Dify handle node configuration.

### D5: New node types via config, not engine changes

**Choice**: Add `knowledge-retrieval` and `variable-set` as new DSL node types by extending the JSON Schema and adding handlers in the engine's worker dispatch
**Rationale**: The engine already dispatches by `node.type`. Adding new types is a clean extension. Knowledge retrieval wraps the existing RAG pipeline. Variable-set writes to channels without LLM invocation.

## Risks / Trade-offs

- **[React Flow bundle size ~200KB]** → Acceptable for desktop-only dashboard. Use dynamic import (`next/dynamic`) to avoid impacting initial page load.
- **[Graph DSL schema evolution]** → Adding new node types requires updating `graph-dsl.schema.json`. The compiler and runtime must handle unknown node types gracefully (log warning, skip) so old workflows remain executable.
- **[Test-run resource consumption]** → Executing workflows with real LLM calls in test mode could be expensive. Mitigation: test-run uses a configurable mock mode that logs prompts without calling LLMs, real mode requires explicit opt-in.
- **[Canvas performance with 50+ nodes]** → React Flow handles up to ~500 nodes well. Beyond that, virtualization is needed. For P2, 50-node limit is reasonable. Log a warning when approaching the limit.
