## Why

The engine supports all primitives for sequential pipelines and broadcast patterns (TOPIC channels, sequential edge resolution, FAN_OUT/MERGE, AgentWorker), but developers must manually construct Graph DSL dicts with correct channel wiring for every node. Two common multi-agent patterns — linear sequential pipelines (CrewAI `Process.sequential`, AgentScope `sequential_pipeline`) and shared-channel broadcast (AgentScope `MsgHub`, AutoGen `RoundRobinGroupChat`) — lack first-class factory functions, forcing users to understand low-level channel semantics to express simple multi-step workflows.

A `content-pipeline.json` template exists for the researcher→writer→reviewer pattern, but it is a single hardcoded use case. The engine needs generic, parameterized factory functions that accept a list of stages (or participants) and produce correctly wired Graph DSL automatically.

## What Changes

- Add `build_sequential_pipeline()` factory function to `engine/templates.py` — accepts a list of stage definitions and produces a linear A→B→C→... graph with auto-wired TOPIC + LAST_VALUE channels, optional revision loop, and optional quality gate.
- Add `build_broadcast_pipeline()` factory function to `engine/templates.py` — accepts a list of participant definitions and produces a sequential round-robin graph where all participants share the same TOPIC channel, with optional turn limits and termination conditions.
- Add JSON templates: `sequential-pipeline.json` and `broadcast-pipeline.json` to `data/orchestration_templates/`.
- Update `orchestration-templates` API responses with new template metadata.

## Capabilities

### New Capabilities
- `sequential-pipeline`: Factory function and template for deterministic multi-step sequential pipelines with auto-wired channels, optional revision loops, and inter-stage data flow
- `broadcast-pipeline`: Factory function and template for shared-channel broadcast patterns with sequential round-robin execution and shared message visibility

### Modified Capabilities
- `orchestration-templates`: Add sequential-pipeline and broadcast-pipeline to the template catalog; update list endpoint metadata

## Impact

**Engine layer** (`src/hecate/engine/`):
- `templates.py` — 2 new factory functions (~120 lines each)
- No changes to types.py, compiler.py, pregel.py, or graph_dsl.py — all primitives exist

**Data** (`src/hecate/data/orchestration_templates/`):
- 2 new JSON template files

**Tests** (`tests/`):
- New test cases for both factory functions (Graph DSL structure validation, channel wiring verification)
- No integration test changes (engine-layer only)

**API/Services**: No code changes — existing orchestration-templates API auto-discovers new JSON files from the data directory.

**Breaking changes**: None — purely additive.
