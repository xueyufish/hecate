## 1. Shared Infrastructure

- [x] 1.1 Create `web/src/components/workflow/channel-selector.tsx` — dual-list multi-select component for channel readable/writable assignment, reads available channels from graph state, supports freeform entry when no channels declared
- [x] 1.2 Create `web/src/components/workflow/edge-type-selector.tsx` — popover component with edge type options (Default, Handoff, Conditional), icons, and conditional label input
- [x] 1.3 Add `reactFlowToDsl()` to `web/src/lib/dsl-bridge.ts` — already exists in codebase, no changes needed

## 2. Agent Node Config Enhancement (1.1.14)

- [x] 2.1 Rewrite agent node section in `config-panel.tsx` — replace single `agent_ref` text input with structured form: agent selector dropdown (fetches `/api/agents`), role description textarea (`system_prompt`), invocation mode radio (`direct`/`tool`), channel selector component (from 1.1), model override text input
- [x] 2.2 Wire agent selector dropdown to `/api/agents` API — populate dropdown with agent names, set `config.agent_ref` on selection, pre-fill when editing existing node
- [x] 2.3 Wire channel selector to graph state — read `state` keys from canvas graph data, pass as available channels to channel-selector component, save selections to `config.channels.readable` and `config.channels.writable`

## 3. Typed Edge Visualization (1.1.16)

- [x] 3.1 Create `ConditionalEdge` custom edge component in `canvas-area.tsx` — dotted dark amber (#d97706) Bezier curve with label at midpoint
- [x] 3.2 Create `FanOutEdge` custom edge component in `canvas-area.tsx` — solid indigo (#6366f1) line with fork icon indicator at source
- [x] 3.3 Update `edgeTypes` registry in `canvas-area.tsx` — register ConditionalEdge and FanOutEdge alongside existing HandoffEdge
- [x] 3.4 Update `typedEdges` mapping in `canvas-area.tsx` — extend edge classification logic to detect conditional (`data.edgeType === "conditional"`) and fan-out (source node is fan-out type) edges
- [x] 3.5 Integrate edge type selector into `handleConnect` — show `EdgeTypeSelector` popover on connect, create edge with selected `data.edgeType`, preserve handoff handle shortcut and fan-out auto-detection
- [x] 3.6 Add edge click handler — on edge click, show context menu with edge type options, update edge type and visual on selection

## 4. Fan-Out/Merge Node Editing (1.1.17)

- [x] 4.1 Add fan-out and merge items to `node-palette.tsx` PALETTE_ITEMS array — Fan Out (GitFork icon, indigo) and Merge (GitMerge icon, slate)
- [x] 4.2 Add fan-out config section to `config-panel.tsx` — branch count selector (2-6), connected branch targets list (auto-synced from edges), visual warning when no branches connected
- [x] 4.3 Add merge config section to `config-panel.tsx` — fan-out source dropdown (lists all fan-out nodes on canvas), output channel text input, visual warning when no fan-out source linked
- [x] 4.4 Add branch count badge to `FanOutNode` in `node-types.tsx` — display "×N" badge showing number of connected branch edges

## 5. Template Customization (1.1.15)

- [x] 5.1 Add customization mode state to canvas store — `isCustomizing` flag, `customizingFrom` template name, toolbar indicator showing "Customizing: {name}"
- [x] 5.2 Update `TemplatePicker` to set customization mode — after `onSelect`, set `isCustomizing=true` and `customizingFrom` to template name, enable canvas editing
- [x] 5.3 Add "Save as Workflow" button to toolbar — shown only in customization mode, opens name dialog, calls `reactFlowToDsl()` to convert canvas to DSL, saves via workflow create API
- [x] 5.4 Verify original template unmodified — ensure template picker still lists original template after customization, no template mutation

## 6. Verification

- [ ] 6.1 Manual test: create agent node → config panel shows all 5 fields → save → reload → values persisted
- [ ] 6.2 Manual test: load template → customize agent roles → add/remove nodes → save as workflow → original template unchanged
- [ ] 6.3 Manual test: connect nodes → edge type selector appears → select each type → visual styles correct
- [ ] 6.4 Manual test: drag fan-out/merge from palette → configure → connect edges → branch count auto-syncs
- [x] 6.5 Run `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/` — backend unchanged, verify no regressions
