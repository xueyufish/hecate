## Why

The current Graph DSL supports 6 node types (CONVERSATION, TOOL_CALL, CONDITION, AGENT, KNOWLEDGE_RETRIEVAL, VARIABLE_SET) but lacks two essential patterns for deterministic multi-step pipelines:

1. **Parallel execution (Fan-out/Fan-in)** — there is no way to express "run A, B, C simultaneously and merge their results". The Pregel runtime already supports executing multiple nodes per superstep (via `current_nodes` list), but no node type or edge semantics formalize parallel dispatch and result aggregation.

2. **Deterministic conditional branching** — while CONDITION nodes exist, they rely on expression evaluation against channel state. There is no first-class support for variable-based If/Else routing that evaluates a channel value against a threshold or matches against a set of keys (e.g., `score > 80 → approve`, otherwise → reject). The current `_route` key mechanism only supports true/false string branching.

3. **Template coverage** — only 3 orchestration templates exist (Router, Pipeline, Supervisor). Common patterns like parallel processing and iterative refinement loops are missing.

These gaps were identified through competitive analysis against Versatile (AgentArts), Dify, n8n, and Coze, all of which support parallel branches and variable-based conditional routing natively.

## What Changes

- Add `FAN_OUT` node type to `NodeType` enum — a dispatch node that splits execution into multiple parallel branches, each writing to isolated sub-channels.
- Add `MERGE` node type to `NodeType` enum — an aggregation node that collects results from all parallel branches and combines them into a unified output.
- Enhance `CONDITION` node semantics to support multi-key routing (not just true/false) — e.g., `{"high": "node_a", "medium": "node_b", "low": "node_c"}`.
- Update `PregelRuntime.execute()` to dispatch FAN_OUT branches concurrently via `asyncio.gather`, then collect results at MERGE nodes.
- Update `GraphCompiler` to validate FAN_OUT/MERGE pairs (every FAN_OUT must have a matching MERGE).
- Update `schemas/graph-dsl.schema.json` to include the new node types.
- Add 3 new JSON templates: `fan-out-pipeline.json`, `conditional-pipeline.json`, `reflection-loop.json`.
- Add corresponding Python factory functions in `templates.py`.

## Capabilities

### New Capabilities
- `fan-out-merge`: Parallel dispatch (FAN_OUT) and result aggregation (MERGE) node types with concurrent execution in PregelRuntime
- `multi-route-condition`: Enhanced CONDITION node supporting multi-key conditional routing beyond true/false branching

### Modified Capabilities
- `engine-types`: Add FAN_OUT and MERGE to NodeType enum; update ChannelType semantics for fan-out sub-channels
- `graph-dsl`: Update JSON Schema to include new node types; update compiler to validate FAN_OUT/MERGE pair constraints
- `pregel-runtime`: Implement concurrent dispatch for FAN_OUT branches via asyncio.gather; add MERGE aggregation logic
- `orchestration-templates`: Add 3 new templates (fan-out-pipeline, conditional-pipeline, reflection-loop)

## Impact

**Engine layer** (`src/hecate/engine/`):
- `types.py` — 2 new NodeType enum values (FAN_OUT, MERGE)
- `compiler.py` — new validation for FAN_OUT/MERGE structural constraints
- `pregel.py` — concurrent dispatch logic for FAN_OUT, aggregation logic for MERGE
- `templates.py` — 3 new factory functions
- `graph_dsl.py` — no changes (already handles string enum parsing via NodeType)

**Schema** (`schemas/`):
- `graph-dsl.schema.json` — add "fan-out" and "merge" to node type enum

**Data** (`src/hecate/data/orchestration_templates/`):
- 3 new JSON template files

**Tests** (`tests/`):
- New test file for FAN_OUT/MERGE execution
- New test file for multi-key CONDITION routing
- Tests for 3 new templates

**API/Services**: No changes — existing orchestration-templates API already discovers templates from the data directory.

**Breaking changes**: None — all new node types are additive. Existing graphs continue to work unchanged.
