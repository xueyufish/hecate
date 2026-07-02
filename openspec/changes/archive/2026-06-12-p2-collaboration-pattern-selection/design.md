## Context

Hecate's engine layer has 9 template builder functions in `engine/templates.py` that generate `GraphConfig` instances for common patterns: chat, three-layer, fan-out, conditional, reflection, sequential, broadcast, negotiation, and debate. The `data/orchestration_templates/` directory contains 8 JSON template files loaded by the API. The canvas frontend (React Flow) already supports template customization mode, `dslToReactFlow()` for loading templates, and `reactFlowToDsl()` for saving.

**Current state**: No unified pattern vocabulary exists. Templates use free-form `category` strings ("pipeline", "broadcast", "delegation", "customer-service", "content"). There is no `PatternType` enum, no pattern inference, and no API to generate graphs from a pattern selection. Two patterns (negotiation, debate) have builder functions in templates.py but lack JSON template files.

**Constraints**:
- Engine layer has zero external deps (only `jsonschema` exception)
- Canvas page uses `useState` (no Zustand store)
- Graph DSL schema is versioned at `schemas/graph-dsl.schema.json`
- API templates are loaded from static JSON files (not database)

## Goals / Non-Goals

**Goals:**
- Provide a canonical `CollaborationPattern` enum as the single source of truth for 6 pattern types
- Enable backend pattern inference from any `GraphConfig`
- Enable graph generation from pattern selection with configurable parameters
- Expose pattern listing and generation via REST API
- Provide a frontend pattern selector with card grid UI and configuration dialog
- Fill the 2 missing JSON template gaps (negotiation, debate)
- Enhance existing template API to include inferred `pattern_type`

**Non-Goals:**
- Persisting user-created patterns to database (future P3+)
- Visual graph editor for modifying pattern templates before generation
- Pattern composition (combining multiple patterns into one graph)
- Custom pattern creation by users
- Changes to the Pregel runtime, compiler, or core engine types (GraphConfig, Edge, etc.)

## Decisions

### D1: New `engine/patterns.py` module (not extending templates.py)

**Decision**: Create a new `engine/patterns.py` module for `CollaborationPattern` enum, `infer_pattern()`, and `build_graph_from_pattern()`.

**Rationale**: `templates.py` is a collection of factory functions (9 functions, 964 lines). Pattern classification and inference are fundamentally different concerns — one produces graphs, the other analyzes them. Separating them keeps `templates.py` focused on graph construction and makes the pattern system independently testable. The builder function delegates to existing template functions where applicable.

**Alternatives considered**:
- Adding to `types.py`: Rejected — `types.py` is pure data definitions (dataclasses/enums with no logic)
- Adding to `templates.py`: Rejected — would mix graph construction with graph analysis; file is already 964 lines

### D2: Pattern builder delegates to existing template functions

**Decision**: `build_graph_from_pattern()` SHALL delegate to the appropriate `build_*` function from `templates.py` (e.g., `build_sequential_pipeline()` for SEQUENTIAL). It acts as a facade that normalizes the parameter interface.

**Rationale**: Avoids duplicating graph construction logic. The 9 template functions are well-tested and encode the correct graph topologies. The pattern builder translates the unified parameter schema to function-specific arguments.

### D3: Pattern inference uses structural heuristics (not ML)

**Decision**: `infer_pattern()` uses deterministic structural rules based on node types, edge triggers, and channel configurations.

**Rationale**: The patterns have clear structural signatures — FAN_OUT/MERGE nodes → parallel, handoff triggers → handoff, shared TOPIC → broadcast, loop with condition → negotiation/debate. No ML or fuzzy matching needed.

### D4: Pattern selector as dialog (not inline panel)

**Decision**: The pattern selector opens as a modal dialog with a 3×2 card grid, followed by a second-step configuration dialog.

**Rationale**: Matches the existing template picker interaction pattern (dialog overlay). A 2-step flow (select → configure) is cleaner than cramming everything into one panel. Consistent with Coze and Dify's template/pattern selectors.

### D5: No Graph DSL schema changes

**Decision**: Do NOT add a `pattern_type` field to the Graph DSL JSON Schema. Pattern type is inferred at runtime, not stored in the graph definition.

**Rationale**: Keeping pattern inference separate from the DSL avoids schema versioning complexity and keeps graph definitions portable. A graph generated from a pattern is indistinguishable from a hand-built graph — both are valid Graph DSL.

**Alternatives considered**:
- Adding optional `pattern_type` to schema: Rejected — adds metadata that can become stale if the graph is edited; inference is more reliable

### D6: New API file for pattern endpoints

**Decision**: Create `api/management/collaboration_patterns.py` for the pattern listing and generation endpoints, separate from `orchestration_templates.py`.

**Rationale**: Templates are static JSON files; patterns are a dynamic generation system. Different concerns, different endpoints, different caching strategies. Templates may eventually move to database storage (P3+), while patterns will remain engine-layer logic.

## Risks / Trade-offs

**[Pattern inference accuracy]** → Heuristic rules may misclassify edge-case graphs (e.g., a graph with FAN_OUT and handoff edges). **Mitigation**: `infer_pattern()` returns `None` for ambiguous graphs; the template API gracefully handles `null` pattern types.

**[Builder parameter explosion]** → 6 patterns with different parameters make the API surface complex. **Mitigation**: Each pattern has a well-defined JSON Schema for its parameters; the frontend dynamically renders fields based on the schema returned by `GET /api/collaboration-patterns`.

**[Frontend dialog complexity]** → 6 different configuration forms could lead to code duplication. **Mitigation**: Use a dynamic form renderer driven by the pattern parameter schema from the API; only the "stages/workers list" UI needs custom components.

**[Template catalog growing beyond 8]** → Adding negotiation.json and debate.json brings catalog to 10 templates. **Mitigation**: Templates are lightweight JSON files; no performance concern at this scale.
