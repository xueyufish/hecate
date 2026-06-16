## Why

The canvas workflow editor is functionally minimal — agent nodes only expose an `agent_ref` text field, edges only differentiate handoff vs default, templates load as read-only one-shot graphs, and fan-out/merge nodes cannot be created or edited interactively. These gaps make the canvas unusable for real multi-agent orchestration design. Competing platforms (Coze, AgentArts, Dify) provide rich node configuration, visual edge typing, template customization, and interactive branch editing. Closing these gaps is the critical P2 milestone.

## What Changes

- **Agent Node Config Enhancement (1.1.14)**: Expand the agent node config panel beyond `agent_ref` to include: role description, invocation mode selector (direct/tool), readable/writable channel multi-select, and model override. Replace the current plain text input with a structured form fetching agents from the API and channels from the graph DSL state declaration.
- **Template Customization (1.1.15)**: After loading an orchestration template, allow users to edit agent roles (system prompts), add/remove agent nodes, adjust connections, modify channel declarations, and save the modified graph as a new workflow. Currently template loading is one-shot with no edit capability.
- **Typed Edge Visualization (1.1.16)**: Add visual differentiation for 4 edge types: default (solid gray), handoff (dashed purple — already exists), conditional (dotted with label), fan-out (multi-target with branch indicators). Add an edge type selector in the connection interaction. Currently only handoff has custom rendering.
- **Fan-Out/Merge Node Editing (1.1.17)**: Allow users to create fan-out and merge nodes from the node palette, configure branch targets in fan-out nodes, link merge nodes to their fan-out source, and edit the output channel. Currently these nodes only appear when loaded from templates and cannot be created or configured interactively.

## Capabilities

### New Capabilities
- `agent-node-config`: Rich agent node configuration panel with role description, invocation mode, channel selection, and model override
- `template-customization`: Edit orchestration templates after loading — modify agent roles, add/remove nodes, adjust connections, save as new workflow
- `typed-edge-visualization`: Visual edge type differentiation (default, handoff, conditional, fan-out) with edge type selector on connect
- `fan-out-merge-editing`: Interactive creation and configuration of fan-out and merge nodes in the canvas

### Modified Capabilities
- `node-config-panel`: Agent node section expanded from single `agent_ref` field to full structured form — existing config panel structure preserved, agent section replaced
- `fan-out-merge-visual`: Fan-out and merge nodes added to node palette for interactive creation — currently palette-excluded, now draggable with configuration support
- `multi-agent-canvas`: Edge type selection extended beyond handoff to include conditional and fan-out types — existing handoff rendering preserved, new types added alongside

## Impact

**Frontend (web/src/components/workflow/)**:
- `config-panel.tsx`: Agent section rewritten with structured form (agent selector, role description, invocation mode, channels, model override)
- `canvas-area.tsx`: Edge rendering expanded — new edge components for conditional and fan-out types, edge type selector on connect
- `node-palette.tsx`: Add fan-out and merge as draggable items
- `node-types.tsx`: Add config badges to fan-out/merge nodes (branch count, source link)
- `template-picker.tsx`: Add "Customize" mode after template load — enable canvas editing instead of read-only

**Frontend new files**:
- `web/src/lib/dsl-bridge.ts` enhancements: template-to-canvas round-trip with edit tracking
- `web/src/components/workflow/edge-type-selector.tsx`: Edge type selection dialog on connect
- `web/src/components/workflow/channel-selector.tsx`: Multi-select component for channel read/write config

**Backend**: Minimal changes — existing Graph DSL schema already supports all node types, edge types, channel config, and invocation modes. No schema changes needed. API may need a workflow save endpoint if not present.

**Dependencies**: No new npm packages required. Uses existing React Flow custom edge/node APIs, existing shadcn/ui components, existing API client.
