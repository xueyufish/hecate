## Why

Users building multi-agent workflows on the canvas currently must manually construct graph topologies node-by-node, connecting edges, configuring channels, and setting node types — a process that requires deep understanding of the engine's graph DSL. Enterprise platforms (Coze, CrewAI, Dify) offer pattern-based workflow creation where users select a high-level collaboration pattern (Sequential, Parallel, Handoff, Broadcast, Negotiation, Debate) and the system auto-generates the graph structure. This dramatically lowers the barrier to multi-agent orchestration. The backend already has 9 template builder functions and 8 JSON templates, but there is no unified pattern vocabulary, no pattern classification system, and no API to generate graphs from pattern selection. Now is the right time because the canvas UI (React Flow) and template customization infrastructure are already in place from the p2-canvas-ui-enhancement change.

## What Changes

- Add a `CollaborationPattern` enum to the engine with 6 pattern types: `SEQUENTIAL`, `PARALLEL`, `HANDOFF`, `BROADCAST`, `NEGOTIATION`, `DEBATE`
- Add pattern inference logic (`infer_pattern()`) that analyzes a `GraphConfig` or `CompiledGraph` to detect which pattern it follows
- Add a pattern-to-graph builder (`build_graph_from_pattern()`) that generates a `GraphConfig` from a pattern selection plus configuration parameters (agent count, model, prompts, etc.)
- Add `GET /api/collaboration-patterns` endpoint returning the 6 pattern definitions with descriptions, parameters, and preview metadata
- Add `POST /api/collaboration-patterns/{pattern}/generate` endpoint that accepts pattern parameters and returns a complete Graph DSL JSON
- Enhance `GET /api/orchestration-templates` to include a `pattern_type` field inferred from template structure
- Add 2 missing JSON templates: `negotiation.json` and `debate.json` in `data/orchestration_templates/`
- Build a frontend Pattern Selector component (card grid with 6 patterns) accessible from the canvas toolbar
- Build a Pattern Configuration dialog for parameter input (agent count, models, prompts) after pattern selection
- Integrate pattern-generated graphs with the existing canvas and template customization flow

## Capabilities

### New Capabilities
- `collaboration-pattern-engine`: Backend pattern vocabulary (enum), pattern inference from graph structure, and pattern-to-graph builder. New module `engine/patterns.py` plus API endpoints for pattern listing and graph generation.
- `pattern-selector-ui`: Frontend pattern selector card grid component, pattern configuration dialog, and integration with the canvas workflow page.

### Modified Capabilities
- `orchestration-templates`: Add `pattern_type` field to template listing API response, inferred from template graph structure using the new pattern inference logic.
- `multi-agent-canvas`: Add pattern selector trigger in the canvas toolbar alongside the existing template picker; integrate pattern-generated graphs into the canvas via the existing `dslToReactFlow()` flow.

## Impact

**Backend (engine layer)**:
- New file: `src/hecate/engine/patterns.py` (pattern enum, inference, builder) — zero external deps, consistent with engine layer constraints
- Modified: `src/hecate/api/management/orchestration_templates.py` (add pattern_type to response)
- New file: `src/hecate/api/management/collaboration_patterns.py` (pattern API endpoints)
- New files: `src/hecate/data/orchestration_templates/negotiation.json`, `debate.json`
- No changes to core engine types (types.py), compiler, or Pregel runtime — patterns produce GraphConfig which is already consumable

**Frontend (web)**:
- New component: `web/src/components/workflow/pattern-selector.tsx` (card grid)
- New component: `web/src/components/workflow/pattern-config-dialog.tsx` (parameter form)
- Modified: `web/src/app/(dashboard)/workflows/[id]/page.tsx` (toolbar integration)
- No new dependencies — uses existing React Flow + UI library

**APIs**:
- New: `GET /api/collaboration-patterns` — list patterns
- New: `POST /api/collaboration-patterns/{pattern}/generate` — generate graph from pattern
- Modified: `GET /api/orchestration-templates` — adds `pattern_type` field to items
