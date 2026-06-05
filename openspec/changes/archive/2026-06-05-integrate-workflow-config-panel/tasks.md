## 1. Fan-out/Merge Visual Support

- [x] 1.1 Add "fan-out" and "merge" to `NodeTypeSchema` enum in `web/src/lib/workflow-types.ts`
- [x] 1.2 Add FanOutNode and MergeNode components to `web/src/components/workflow/node-types.tsx` with distinct icons and colors
- [x] 1.3 Add "fan-out" → "Fan Out" and "merge" → "Merge" to `NODE_TYPE_LABELS` in `web/src/lib/dsl-bridge.ts`
- [x] 1.4 Add FanOutNode and MergeNode entries to `nodeTypeComponents` map in `node-types.tsx`

## 2. Node Click Interaction

- [x] 2.1 Add `onNodeClick` prop to `CanvasAreaProps` in `web/src/components/workflow/canvas-area.tsx` and wire it to ReactFlow's `onNodeClick` event
- [x] 2.2 Add `selectedNodeId` state to `page.tsx` and pass `onNodeClick` handler to CanvasArea
- [x] 2.3 Add click-on-canvas-deselect handler via ReactFlow's `onPaneClick` in canvas-area.tsx

## 3. ConfigPanel Integration

- [x] 3.1 Import ConfigPanel in `page.tsx`
- [x] 3.2 Replace the right-side panel (w-[280px]) with w-[300px] dynamic panel: ConfigPanel when node selected, placeholder text when nothing selected
- [x] 3.3 Implement `handleConfigUpdate` callback in `page.tsx` that updates node data and triggers `scheduleSave`
- [x] 3.4 Remove or relocate Input Form from right-side panel (keep as toolbar button or collapsible section)

## 4. Layout Persistence

- [x] 4.1 Create layout save helper: serialize node positions to localStorage key `hecate-layout-{workflowId}`
- [x] 4.2 Integrate layout save into existing `scheduleSave` callback in `page.tsx`
- [x] 4.3 Modify `dslToReactFlow` or add post-load merge step: if localStorage layout exists for this workflowId, apply saved positions to nodes; otherwise use default grid
- [ ] 4.4 Test round-trip: create workflow, move nodes, reload page, verify positions restored

## 5. Verification

- [x] 5.1 Run `npm run build` in web/ — zero errors
- [x] 5.2 Run `npm test` in web/ — all tests pass
- [x] 5.3 Run `ruff check src/hecate/ tests/` — no Python changes expected, verify clean
- [ ] 5.4 Manual verification: open workflow editor, select node, edit config, save, reload, verify persistence
