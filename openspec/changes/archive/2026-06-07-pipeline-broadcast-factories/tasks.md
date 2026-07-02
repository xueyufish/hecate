## 1. Sequential Pipeline Factory

- [x] 1.1 Implement `build_sequential_pipeline()` in `src/hecate/engine/templates.py` — accept `stages: list[dict[str, str]]`, `revision_config: dict | None = None`; validate minimum 2 stages, reject duplicate IDs; build AGENT nodes with auto-wired channels (shared `messages` TOPIC + per-stage `{stage_id}_output` LAST_VALUE); build linear edges; return GraphConfig
- [x] 1.2 Add revision loop support to `build_sequential_pipeline()` — when `revision_config` is provided, append CONDITION node after last stage with conditional edge routing to `target_stage` on true and `__end__` on false; add `revision_status` LAST_VALUE channel readable by CONDITION node

## 2. Broadcast Pipeline Factory

- [x] 2.1 Implement `build_broadcast_pipeline()` in `src/hecate/engine/templates.py` — accept `participants: list[dict[str, str]]`, `moderator: dict | None = None`; validate minimum 2 participants, reject duplicate IDs; build AGENT nodes all sharing the same `messages` TOPIC channel (readable + writable); build sequential round-robin edges; return GraphConfig
- [x] 2.2 Add moderator support to `build_broadcast_pipeline()` — when `moderator` is provided, insert moderator AGENT node at both start and end: `__start__` → moderator → participant_0 → ... → participant_{N-1} → moderator → `__end__`

## 3. JSON Templates

- [x] 3.1 Create `src/hecate/data/orchestration_templates/sequential-pipeline.json` — 3-stage researcher→writer→reviewer pipeline with revision loop, following `content-pipeline.json` structure with correct channel wiring
- [x] 3.2 Create `src/hecate/data/orchestration_templates/broadcast-pipeline.json` — 3-participant round-robin broadcast with moderator, demonstrating shared `messages` TOPIC channel pattern

## 4. Tests

- [x] 4.1 Add tests for `build_sequential_pipeline()` in `tests/test_engine/test_pipeline_broadcast_templates.py` — test basic 2-stage pipeline structure (nodes, channels, edges), test 3-stage inter-stage channel wiring (readable/writable), test revision loop (CONDITION node, conditional edge), test validation errors (< 2 stages, duplicate IDs)
- [x] 4.2 Add tests for `build_broadcast_pipeline()` in `tests/test_engine/test_pipeline_broadcast_templates.py` — test basic 3-participant structure (nodes, shared channel, edges), test moderator mode (moderator at start and end), test validation errors (< 2 participants, duplicate IDs)
- [x] 4.3 Add tests for JSON templates — verify `sequential-pipeline.json` and `broadcast-pipeline.json` load and parse correctly via `parse_graph()`, compile without errors, and contain expected node/channel/edge structure

## 5. Verification

- [x] 5.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q` — all green
