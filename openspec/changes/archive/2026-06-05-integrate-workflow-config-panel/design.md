## Context

The workflow canvas editor (`workflows/[id]/page.tsx`) has a three-column layout: left palette (200px), center canvas, right panel (280px). The right panel currently shows test-run input form and node execution results, but **no node configuration editing**.

A fully-built `ConfigPanel` component exists (`components/workflow/config-panel.tsx`, 217 lines) with forms for all 6 node types (conversation, condition, tool-call, agent, knowledge-retrieval, variable-set). It accepts `{ node, onUpdate, onClose }` props but is never imported or rendered.

The backend DSL schema defines 8 node types. The frontend only handles 6 — fan-out and merge have no type definitions, visual components, or config forms.

Node positions are calculated with a fixed grid formula on every DSL load, so any manual layout adjustments are lost on refresh.

## Goals / Non-Goals

**Goals:**
- Users can click a node on the canvas to open its configuration in the right-side panel
- ConfigPanel edits propagate to the canvas and trigger auto-save
- Node layout positions persist across page reloads (localStorage, keyed by workflowId)
- Fan-out and merge nodes render visually when present in a loaded DSL (no creation/editing)

**Non-Goals:**
- Fan-out/merge node creation in the palette or configuration forms (deferred to P3)
- Test-run result display in the right panel (stays in bottom panel)
- DSL schema changes (backend unchanged)
- i18n or accessibility improvements

## Decisions

### Decision 1: Right-side panel shows ConfigPanel when node is selected

**Choice**: Dynamic right panel — ConfigPanel when node selected, placeholder when nothing selected.

**Alternatives considered**:
- AgentArts-style inline node expansion (no side panel): Rejected because Hecate nodes have rich config (model, prompt, channels) that needs more space than inline expansion provides
- Floating/drawer overlay: Rejected because it adds complexity without clear benefit over a dedicated panel

**Rationale**: The right panel already exists at 280px. Widening to 300px (matching ConfigPanel's existing `w-[300px]`) gives a natural home for node editing without new UI patterns.

### Decision 2: Test-run results stay in the bottom panel

**Choice**: Right panel is exclusively for node editing. Test-run input form and results remain at the bottom.

**Rationale**: Editing and test-run viewing are mutually exclusive workflows. Separating them keeps the right panel logic simple (two states: node selected / nothing selected) and avoids mode confusion.

### Decision 3: Layout persistence uses localStorage, not DSL

**Choice**: Store ReactFlow node positions in `localStorage` keyed by `hecate-layout-${workflowId}`. DSL remains purely semantic (no position fields).

**Alternatives considered**:
- Add `position` to DSL schema: Rejected because it pollutes the semantic model with view-layer data
- Backend API field: Rejected because layout is a frontend concern; avoids API changes
- Auto-layout with dagre/elkjs: Considered for fallback only; user manual adjustments should persist

**Rationale**: Clean separation of concerns. The DSL describes what the workflow does; localStorage describes how it looks. No backend changes needed.

### Decision 4: Fan-out/merge as visual-only node types

**Choice**: Add type enum values, visual components, and DSL bridge labels. No palette entry, no ConfigPanel form.

**Rationale**: If a workflow DSL contains fan-out/merge nodes (e.g., created via API), the frontend should render them instead of silently dropping them. Full creation/editing UI requires interaction design for branch wiring that should not be rushed.

## Risks / Trade-offs

- **localStorage size limits**: Workflow layouts are small (<10KB per workflow), so hitting the ~5MB limit would require thousands of workflows. → Acceptable for now; can migrate to IndexedDB or backend API in P3 if needed.
- **Fan-out/merge visual-only gap**: Users cannot create these nodes from the UI, only via API. → Documented as P3 TODO in feature-catalog.md.
- **ConfigPanel width increase (280→300px)**: Slightly reduces canvas space. → Minimal impact; 20px difference is barely noticeable.
