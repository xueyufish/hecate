## 1. Engine Types & Data Model

- [x] 1.1 Add `ChannelAccess` dataclass to `engine/types.py` with `readable: set[str]` and `writable: set[str]` fields
- [x] 1.2 Add `channel_access: dict[str, ChannelAccess]` field to `CompiledGraph` dataclass (default empty dict)
- [x] 1.3 Add `routing_mode: str | None` and `routing_config: dict[str, Any] | None` fields to `NodeConfig` dataclass in `engine/types.py`
- [x] 1.4 Add `RoutingMode` StrEnum in `engine/types.py` with values `CONDITION`, `INTENT`, `DYNAMIC`
- [x] 1.5 Add `IntentPattern` dataclass in `engine/types.py` with `pattern: str` and `target: str` fields

## 2. Graph DSL Schema & Parser

- [x] 2.1 Update `schemas/graph-dsl.schema.json` — add `routing_mode` (enum: condition/intent/dynamic) and `routing_config` (object with intent_patterns, candidate_agents, routing_prompt, allow_repeated_speaker) to CONDITION node config properties
- [x] 2.2 Update `schemas/graph-dsl.schema.json` — add `"dynamic_handoff"` to edge `trigger` enum values
- [x] 2.3 Update `engine/graph_dsl.py` `parse_graph()` to parse `routing_mode` and `routing_config` from CONDITION node config
- [x] 2.4 Update `engine/graph_dsl.py` to validate `routing_mode` values — raise `GraphValidationError` for unknown modes
- [x] 2.5 Add test: parse_graph with intent routing config produces correct NodeConfig
- [x] 2.6 Add test: parse_graph with dynamic routing config produces correct NodeConfig
- [x] 2.7 Add test: parse_graph with invalid routing_mode raises GraphValidationError
- [x] 2.8 Add test: parse_graph with dynamic_handoff trigger produces correct Edge

## 3. Compiler Validation

- [x] 3.1 Add `_validate_channel_access()` method to `GraphCompiler` — iterate nodes, check readable/writable against state channels, log WARNING for mismatches
- [x] 3.2 Add `_validate_routing_config()` method to `GraphCompiler` — validate intent mode has intent_patterns, dynamic mode has candidate_agents, candidate agents reference existing nodes
- [x] 3.3 Populate `CompiledGraph.channel_access` map in `compile()` from node config channels
- [x] 3.4 Integrate both new validation methods into `compile()` pipeline (after existing validations, before CompiledGraph construction)
- [x] 3.5 Add test: compiler warns when node declares readable channel not in state
- [x] 3.6 Add test: compiler warns when node declares writable channel not in state
- [x] 3.7 Add test: compiler raises GraphValidationError for intent mode without intent_patterns
- [x] 3.8 Add test: compiler raises GraphValidationError for dynamic mode without candidate_agents
- [x] 3.9 Add test: compiler raises GraphValidationError for dynamic mode with nonexistent candidate
- [x] 3.10 Add test: compiler populates channel_access map correctly
- [x] 3.11 Add test: compiler accepts valid intent routing config
- [x] 3.12 Add test: compiler accepts valid dynamic routing config

## 4. Runtime Channel Access Enforcement

- [x] 4.1 Add optional `node_id: str | None = None` parameter to `ChannelManager.read()`
- [x] 4.2 Add optional `node_id: str | None = None` parameter to `ChannelManager.write()`
- [x] 4.3 In `ChannelManager.read()` — when `node_id` provided, check against compiled graph's channel_access map and log WARNING for undeclared access
- [x] 4.4 In `ChannelManager.write()` — when `node_id` provided, check against compiled graph's channel_access map and log WARNING for undeclared access
- [x] 4.5 Pass `node_id` from PregelRuntime's node execution loop to ChannelManager calls
- [x] 4.6 Add test: ChannelManager.read() logs warning for undeclared channel access
- [x] 4.7 Add test: ChannelManager.write() logs warning for undeclared channel access
- [x] 4.8 Add test: ChannelManager.read() does not warn when node_id is None
- [x] 4.9 Add test: ChannelManager.read() does not warn for declared channel access

## 5. Routing Mode Evaluation Engine

- [x] 5.1 Add `evaluate_condition_routing()` function to new `engine/routing.py` — dispatches based on routing_mode
- [x] 5.2 Implement condition mode evaluation (delegates to existing expression evaluator)
- [x] 5.3 Implement intent mode evaluation — iterate intent_patterns, regex match against input, return target on first match
- [x] 5.4 Implement intent mode LLM fallback — call `EnginePort.llm_invoke()` with routing_prompt when no pattern matches
- [x] 5.5 Implement dynamic mode evaluation — call `EnginePort.llm_invoke()` with candidate_agents list and routing_prompt
- [x] 5.6 Implement dynamic mode `allow_repeated_speaker` — filter last speaker from candidates before LLM call
- [x] 5.7 Implement dynamic mode response validation — check LLM response against candidate_agents, fall back to "default" target
- [x] 5.8 Integrate routing evaluation into PregelRuntime's condition node execution path
- [x] 5.9 Add test: intent mode with matching pattern returns correct target
- [x] 5.10 Add test: intent mode with no pattern match and LLM fallback returns LLM-classified target
- [x] 5.11 Add test: intent mode with no pattern match and no routing_prompt returns "default"
- [x] 5.12 Add test: dynamic mode returns valid agent from LLM response
- [x] 5.13 Add test: dynamic mode invalid LLM response falls back to "default"
- [x] 5.14 Add test: dynamic mode allow_repeated_speaker=false excludes last speaker

## 6. Dynamic Handoff Support

- [x] 6.1 Update `_validate_handoff_edges()` in compiler to also validate `dynamic_handoff` trigger edges
- [x] 6.2 Update handoff tool injection logic — when edge trigger is `dynamic_handoff`, inject `handoff_to_agent` with multiple target candidates
- [x] 6.3 Update handoff tool execution — validate target against allowed candidate list, return error for invalid targets
- [x] 6.4 Add test: dynamic handoff edge triggers tool injection with multiple targets
- [x] 6.5 Add test: dynamic handoff invalid target returns error
- [x] 6.6 Add test: dynamic handoff cycle detection works

## 7. Frontend — Channel Access Summary

- [x] 7.1 Add channel access summary section to agent node config panel in `web/src/components/workflow/config-panel.tsx`
- [x] 7.2 Display readable/writable channels grouped by type with broadcast participation highlights
- [x] 7.3 Show "No channel access configured" message for nodes without channel declarations
- [x] 7.4 Add broadcast icon for TOPIC channels shared with other agents

## 8. Frontend — Routing Mode Config Panel

- [x] 8.1 Add routing mode selector (Condition/Intent/Dynamic) to condition node config panel
- [x] 8.2 Implement Intent mode UI — intent pattern rows (add/remove) with regex input and target node selector
- [x] 8.3 Implement Intent mode UI — optional routing prompt textarea
- [x] 8.4 Implement Dynamic mode UI — candidate agents multi-select, routing prompt textarea, allow_repeated_speaker toggle
- [x] 8.5 Persist routing_mode and routing_config to graph DSL node data on change
- [x] 8.6 Condition mode (default) shows existing expression field only

## 9. Frontend — Dynamic Handoff Edge

- [x] 9.1 Add "Dynamic Handoff" option to edge type selector in `edge-type-selector.tsx`
- [x] 9.2 Render dynamic handoff edges with distinct style (dashed purple + sparkle icon)
- [x] 9.3 Support multi-target selection when creating dynamic handoff edges

## 10. Verification

- [x] 10.1 Run `ruff check src/hecate/ tests/` — 0 errors
- [x] 10.2 Run `ruff format --check src/ tests/` — 0 errors
- [x] 10.3 Run `mypy src/` — 0 errors
- [x] 10.4 Run `python -m pytest tests/test_engine/ -q` — all pass (495 passed)
- [x] 10.5 Run `npx tsc --noEmit` in web/ — 0 new errors (1 pre-existing in dsl-bridge.test.ts)
