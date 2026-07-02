## 1. Engine Types

- [x] 1.1 Add `FAN_OUT` and `MERGE` enum values to `NodeType` in `src/hecate/engine/types.py`
- [x] 1.2 Update `schemas/graph-dsl.schema.json` node type enum to include `"fan-out"` and `"merge"`
- [x] 1.3 Update `schemas/graph-dsl.schema.json` node config properties to include fan-out-specific fields (`branches`, `fan_out_source`, `output_channel`)

## 2. Graph Compiler

- [x] 2.1 Add `_validate_fan_out_merge()` method to `GraphCompiler` in `src/hecate/engine/compiler.py` — validate every FAN_OUT has a reachable MERGE downstream and every MERGE has an upstream FAN_OUT
- [x] 2.2 Call `_validate_fan_out_merge()` in `compile()` after existing validation stages
- [x] 2.3 Add tests for compiler validation: FAN_OUT without MERGE raises error, MERGE without FAN_OUT raises error, valid FAN_OUT/MERGE pair passes

## 3. Multi-Key Condition Routing

- [x] 3.1 Update `_resolve_next_nodes()` in `src/hecate/engine/pregel.py` to support multi-key routing with "default" and "false" fallback chain
- [x] 3.2 Update `_resolve_next_nodes_after_interrupt()` with same multi-key fallback logic
- [x] 3.3 Add tests for multi-key routing: exact match, default fallback, legacy false fallback, backward-compatible true/false

## 4. FAN_OUT / MERGE Runtime Execution

- [x] 4.1 Add `_dispatch_fan_out()` method to `PregelRuntime` in `src/hecate/engine/pregel.py` — reads FAN_OUT config branches, creates sub-channels, dispatches all branch workers via `asyncio.gather`, returns list of WorkerResults
- [x] 4.2 Add `_execute_merge()` method to `PregelRuntime` — reads all branch sub-channels, aggregates into dict, writes to output channel
- [x] 4.3 Update `execute()` superstep loop to detect FAN_OUT nodes and call `_dispatch_fan_out()` instead of normal sequential dispatch
- [x] 4.4 Update `execute()` to handle MERGE node execution by calling `_execute_merge()`
- [x] 4.5 Add tests for FAN_OUT execution: 3 branches run concurrently, results collected in sub-channels, MERGE aggregates correctly
- [x] 4.6 Add test for FAN_OUT branch failure propagation — one branch fails, entire fan-out fails
- [x] 4.7 Add test for FAN_OUT + interrupt interaction — not supported, should raise clear error

## 5. Orchestration Templates

- [x] 5.1 Create `src/hecate/data/orchestration_templates/fan-out-pipeline.json` — researcher → fanout → [analyst_a, analyst_b, analyst_c] → merge → summarizer
- [x] 5.2 Create `src/hecate/data/orchestration_templates/conditional-pipeline.json` — classifier → check_category → {finance, tech, legal} routing
- [x] 5.3 Create `src/hecate/data/orchestration_templates/reflection-loop.json` — drafter → reviewer → check_quality → {needs_improvement: reviser, approved: __end__}, reviser → reviewer loop
- [x] 5.4 Add Python factory functions in `src/hecate/engine/templates.py`: `build_fan_out_pipeline()`, `build_conditional_pipeline()`, `build_reflection_loop()`
- [x] 5.5 Add tests for each template: verify parse_graph succeeds, verify compile succeeds, verify correct node/edge structure

## 6. Integration Verification

- [x] 6.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 6.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 6.3 Run `mypy src/` — zero errors
- [x] 6.4 Run `python -m pytest tests/ -q` — all tests pass
