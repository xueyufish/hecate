## Context

The workflow canvas editor (`web/src/components/workflow/`) is a React Flow-based visual graph editor. It currently supports:

- **6 node types** rendered via `nodeTypeComponents` in `node-types.tsx`: conversation, condition, tool-call, agent, knowledge-retrieval, variable-set. Fan-out and merge exist as components but are excluded from the node palette.
- **Edge types**: Only handoff has custom rendering (dashed purple). All other edges use React Flow's default Bezier.
- **Config panel** (`config-panel.tsx`): Renders per-node-type forms. Agent nodes only expose `agent_ref` as a text input.
- **Template picker** (`template-picker.tsx`): Loads templates from `/api/orchestration-templates`, populates canvas as read-only one-shot.
- **Node palette** (`node-palette.tsx`): 6 draggable items. No fan-out or merge.
- **DSL bridge**: `dsl-bridge.ts` converts between Graph DSL JSON and React Flow node/edge arrays. Currently one-directional (DSL → canvas).

The backend Graph DSL schema (`schemas/graph-dsl.schema.json`) already supports: agent `invocation_mode` (direct/tool), `channels` (readable/writable), fan-out `branches`, merge `fan_out_source`/`output_channel`, edge `type: "handoff"`, and conditional edge targets (dict mapping). No backend changes needed — this is purely a frontend enhancement.

## Goals / Non-Goals

**Goals:**
- Enable rich agent node configuration matching the full Graph DSL schema capabilities
- Allow template customization after loading — edit, modify, save as new workflow
- Provide visual edge type differentiation for 4 distinct edge types
- Enable interactive fan-out/merge node creation and configuration
- All changes are frontend-only with no backend API modifications

**Non-Goals:**
- Backend Graph DSL schema changes (already supports all needed fields)
- Real-time collaboration / multi-user editing
- Undo/redo system (can be added later)
- Workflow version history (covered by existing `workflow-version-publish` spec)
- Canvas auto-layout algorithms (manual positioning only)
- Execution visualization during test runs (already in `multi-agent-canvas` spec)

## Decisions

### D1: Config panel uses field-per-section layout with API-backed selectors

**Decision**: Replace the agent node's single `agent_ref` text input with a structured form containing: (1) Agent selector (dropdown fetching from `/api/agents`), (2) Role description (textarea for `system_prompt`), (3) Invocation mode (radio: direct/tool), (4) Channel selector (dual-list multi-select from graph's `state` keys), (5) Model override (text input).

**Rationale**: The Graph DSL already supports all these fields in `node.config`. The config panel just needs to expose them. Using API-backed selectors ensures the user picks valid agent IDs and channel names rather than typing freeform.

**Alternative considered**: Keep freeform text inputs and validate on save — rejected because dropdowns prevent errors and match the UX of Coze/AgentArts.

### D2: Template customization via edit mode flag, not clone-and-edit

**Decision**: After loading a template, set an `isCustomizing` flag in canvas state. This flag enables all canvas editing (add/remove nodes, edit configs, adjust connections). The "Save" action calls the workflow create API with the modified graph. Original template is never modified.

**Rationale**: Templates are read-only assets. Customization creates a new workflow derived from the template. This matches the "Save As" mental model users expect.

**Alternative considered**: Clone template JSON then edit the clone — equivalent behavior, but the flag approach is simpler to implement since the canvas is already in edit mode by default; we just need to prevent accidental template overwrite.

### D3: Edge type selector as connection-line popover

**Decision**: When the user drags a connection from a source handle, show a small popover near the mouse with edge type options: Default, Handoff, Conditional. The selection determines the edge's `data.edgeType` and visual style. Fan-out edges are auto-created when connecting to a fan-out node (no manual selection needed).

**Rationale**: React Flow's `onConnect` event fires after the connection is made. We intercept it to show the type selector before finalizing. This matches the UX pattern of FigJam/Miro connection type selection.

**Alternative considered**: Pre-select edge type in a toolbar — rejected because it requires mode-switching which is less intuitive than contextual selection.

### D4: Fan-out and merge added to node palette with structured config

**Decision**: Add fan-out and merge to the node palette. On drop, fan-out nodes prompt for branch count (2-6). Branch targets are configured by connecting edges from the fan-out node. Merge nodes require linking to a fan-out source and specifying an output channel.

**Rationale**: The current `fan-out-merge-visual` spec excludes these from the palette because they "can only appear when loaded from an existing DSL." This change reverses that decision — interactive creation is needed for real workflow design.

**Alternative considered**: Auto-insert fan-out/merge pairs when the user drags a parallel pattern — too magical, hard to predict intent. Simple node-by-node creation is more predictable.

### D5: Channel selector reads from graph state declaration

**Decision**: The channel multi-select in agent node config reads available channels from the graph's `state` declaration (the top-level `state` object in Graph DSL). Users can select which channels an agent reads from and writes to.

**Rationale**: Channels are defined at the graph level, not per-node. The selector must show all declared channels and let the user pick a subset for each agent's readable/writable lists. This matches the DSL schema where `channels.readable` and `channels.writable` reference top-level state keys.

### D6: Round-trip DSL bridge for template customization

**Decision**: Enhance `dsl-bridge.ts` with a `reactFlowToDsl()` function that converts canvas nodes/edges back to Graph DSL JSON. This enables saving customized workflows.

**Rationale**: Currently only `dslToReactFlow()` exists (one-directional). Template customization requires converting the edited canvas state back to DSL for saving. The function must handle: node type mapping, config extraction, edge type reconstruction, and state channel discovery from node configs.

## Risks / Trade-offs

**[Edge type selector UX complexity]** → The popover-on-connect pattern may feel unfamiliar. Mitigation: Keep the popover minimal (3 options with icons), default to "Default" type, and ensure the existing handoff handle behavior continues to work.

**[Channel selector depends on graph state]** → If no channels are declared (new empty graph), the selector shows nothing. Mitigation: Show a hint "Add channels in graph settings" and allow freeform channel name entry as fallback.

**[Fan-out/merge config validation]** → Invalid fan-out/merge configurations (e.g., merge without fan-out source) will fail at compile time, not edit time. Mitigation: Add visual warnings in the config panel when fan-out has no branches or merge has no source link.

**[Template customization state management]** → Adding `isCustomizing` flag and edit tracking to canvas state. Mitigation: Use the existing Zustand store pattern — add a `customizingFrom` field to track template origin.
