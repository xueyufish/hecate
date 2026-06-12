## 1. Engine Pattern Module

- [x] 1.1 Create `src/hecate/engine/patterns.py` with `CollaborationPattern` StrEnum (6 values: SEQUENTIAL, PARALLEL, HANDOFF, BROADCAST, NEGOTIATION, DEBATE)
- [x] 1.2 Implement `infer_pattern(config: GraphConfig) -> CollaborationPattern | None` with structural heuristic rules for all 6 patterns
- [x] 1.3 Implement `build_graph_from_pattern(pattern: CollaborationPattern, config: dict) -> GraphConfig` that delegates to existing `build_*` functions in `templates.py`
- [x] 1.4 Write unit tests for `CollaborationPattern` enum values and string representations
- [x] 1.5 Write unit tests for `infer_pattern()` — test each pattern detection scenario using existing template GraphConfigs
- [x] 1.6 Write unit tests for `build_graph_from_pattern()` — test each pattern generates valid GraphConfig with correct topology

## 2. JSON Template Gaps

- [x] 2.1 Create `src/hecate/data/orchestration_templates/negotiation.json` — proposer → responder → condition (agreement check) → loop or end, category "negotiation"
- [x] 2.2 Create `src/hecate/data/orchestration_templates/debate.json` — debater_a → debater_b alternating with round counter, optional judge, category "debate"
- [x] 2.3 Verify both new templates load correctly via `GET /api/orchestration-templates` and `GET /api/orchestration-templates/{id}`

## 3. Pattern API Endpoints

- [x] 3.1 Create `src/hecate/api/management/collaboration_patterns.py` with router
- [x] 3.2 Implement `GET /api/collaboration-patterns` — return 6 pattern definitions with id, name, description, parameters (JSON Schema), and preview metadata
- [x] 3.3 Implement `POST /api/collaboration-patterns/{pattern}/generate` — validate pattern enum, call `build_graph_from_pattern()`, return Graph DSL JSON
- [x] 3.4 Register collaboration_patterns router in the main app router setup
- [x] 3.5 Enhance `GET /api/orchestration-templates` to include `pattern_type` field inferred via `infer_pattern()` for each template
- [x] 3.6 Write API tests for `GET /api/collaboration-patterns` — verify 6 items returned with correct metadata
- [x] 3.7 Write API tests for `POST /api/collaboration-patterns/{pattern}/generate` — test valid generation for each pattern and error cases (invalid pattern, missing params)
- [x] 3.8 Write API test verifying `pattern_type` field in template listing matches expected patterns

## 4. Frontend Pattern Selector Component

- [x] 4.1 Create `web/src/components/workflow/pattern-selector.tsx` — modal dialog with 3×2 card grid for 6 patterns, each card showing icon, name, description, and mini topology preview
- [x] 4.2 Add pattern metadata types to `web/src/lib/workflow-types.ts` — `CollaborationPattern`, `PatternDefinition`, `PatternGenerateRequest` interfaces
- [x] 4.3 Add API client functions to fetch pattern list and generate graph — uses `api.get()` / `api.post()` directly in components

## 5. Frontend Pattern Configuration Dialog

- [x] 5.1 Create `web/src/components/workflow/pattern-config-dialog.tsx` — dynamic configuration form rendered per pattern, with "Generate" button
- [x] 5.2 Implement dynamic stage/worker list UI for patterns that need variable-length lists (Sequential stages, Parallel workers, Handoff specialists)

## 6. Canvas Integration

- [x] 6.1 Add "Patterns" toolbar button to workflow canvas page (`web/src/app/(dashboard)/workflows/[id]/page.tsx`) alongside existing "Templates" button
- [x] 6.2 Wire pattern selection flow: click Patterns → open selector → select pattern → open config → generate → `dslToReactFlow()` → populate canvas
- [ ] 6.3 Add confirmation dialog when generating pattern replaces existing canvas nodes
- [x] 6.4 Auto-enter template customization mode after pattern generation (reuse existing `isCustomizing` state)
- [x] 6.5 Verify TypeScript compilation passes with 0 new errors

## 7. Verification

- [x] 7.1 Run `ruff check src/hecate/ tests/` — 0 errors
- [x] 7.2 Run `ruff format --check src/ tests/` — passes
- [x] 7.3 Run `mypy src/` — 0 errors
- [x] 7.4 Run `python -m pytest tests/test_engine/test_patterns.py tests/test_api/test_collaboration_patterns.py` — 32/32 tests pass
- [x] 7.5 Run `npx tsc --noEmit` in web/ — 0 new errors (1 pre-existing in dsl-bridge test)
