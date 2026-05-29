## Why

Hecate has a fully functional execution engine (Graph DSL → Compiler → Pregel Runtime), but workflows can only be created by writing raw JSON DSL. Users need a visual drag-and-drop canvas to design, configure, and test workflows — this is the core differentiator for P2 "好用" phase. Without it, the engine is inaccessible to non-developers.

## What Changes

- **React Flow canvas editor** — drag-and-drop DAG editor for building workflow graphs visually, with node palette, edge drawing, zoom/pan, minimap
- **Workflow node type library** — 6 built-in node types (LLM Call, Condition, Tool Call, Knowledge Retrieval, Sub-Agent, Variable Set) each with a configuration panel
- **Workflow CRUD API** — REST endpoints for create/read/update/delete/list workflows with versioning (already have WorkflowModel + WorkflowVersionModel ORM)
- **Graph DSL serializer** — bidirectional conversion between React Flow node/edge state and the existing Graph DSL JSON Schema
- **Workflow test run** — ability to trigger a workflow execution with sample input, view per-node execution status and output in real-time
- **Frontend workflow pages** — workflow list, canvas editor, version history, test run panel integrated into the dashboard sidebar

## Capabilities

### New Capabilities

- `workflow-canvas`: Visual drag-and-drop DAG editor with React Flow, node palette, edge drawing, and canvas controls
- `workflow-node-types`: Built-in node type definitions (LLM, Condition, Tool, Knowledge Retrieval, Sub-Agent, Variable) with configuration panels
- `workflow-api`: REST API for workflow CRUD, versioning, and test execution
- `workflow-dsl-bridge`: Bidirectional conversion between React Flow visual state and Graph DSL JSON
- `workflow-test-run`: Trigger workflow execution with sample input and view per-node results

### Modified Capabilities

_(none — all existing specs are for P1 engine internals which remain unchanged)_

## Impact

- **Frontend**: New pages under `web/src/app/(dashboard)/workflows/`, new dependency on `reactflow` (or `@xyflow/react`)
- **Backend**: New route file `api/management/workflows.py`, extend existing `api/management/` patterns
- **Models**: Reuse existing `WorkflowModel` + `WorkflowVersionModel` (no schema changes)
- **Engine**: No changes — canvas produces Graph DSL that feeds into existing `graph_dsl.parse_graph()` → `compiler.compile()` → `PregelRuntime`
- **Dependencies**: `@xyflow/react` (React Flow v12), `zod` for frontend DSL validation
