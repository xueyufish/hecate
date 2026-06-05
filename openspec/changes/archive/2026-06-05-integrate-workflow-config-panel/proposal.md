## Why

The workflow canvas editor has a fully-built `ConfigPanel` component (6 node-type forms) and `CanvasArea` component, but they are not connected. Users can drag nodes onto the canvas but cannot configure any node properties (model, system_prompt, tool_name, etc.). Additionally, fan-out/merge node types exist in the backend DSL schema but have no frontend rendering, and node layout positions are lost on every page reload because the DSL does not persist visual coordinates.

## What Changes

- Integrate `ConfigPanel` into the workflow editor's right-side panel, activated when a node is selected on the canvas
- Add `onNodeClick` callback to `CanvasArea` and `selectedNodeId` state to `page.tsx`
- Right-side panel width unified to 300px, showing ConfigPanel when a node is selected, placeholder text when nothing is selected
- Test-run results remain in the bottom panel (no mode switching in right panel)
- Persist node layout positions to localStorage (keyed by workflowId), independent of the semantic DSL
- Add fan-out and merge node types to `NodeTypeSchema`, `node-types.tsx`, and `dsl-bridge.ts` as visual-only (no palette entry, no config form) — full creation/editing deferred to P3
- Restore layout from localStorage on DSL load, fall back to auto-grid layout for new workflows

## Capabilities

### New Capabilities

- `node-config-panel`: Right-side panel integration for editing node properties when a node is selected on the canvas
- `node-layout-persistence`: Persist and restore ReactFlow node positions in localStorage, separate from semantic DSL
- `fan-out-merge-visual`: Visual-only rendering support for fan-out and merge node types (no creation/editing)

### Modified Capabilities

## Impact

- **Frontend files**: `page.tsx` (editor page), `canvas-area.tsx` (add onNodeClick), `node-types.tsx` (add FanOutNode/MergeNode), `dsl-bridge.ts` (add labels), `workflow-types.ts` (add enum values)
- **No backend changes**: All changes are frontend-only; DSL schema and API endpoints unchanged
- **No breaking changes**: Existing workflows load and render identically; new localStorage key is additive
