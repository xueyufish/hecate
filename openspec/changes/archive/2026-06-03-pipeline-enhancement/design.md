## Context

Hecate's engine layer provides a Graph DSL with 6 node types and a Pregel/BSP runtime that executes graphs in superstep cycles. The runtime already supports executing multiple nodes per superstep (`current_nodes` is a list, iterated in a `for` loop), but there is no formal mechanism for parallel dispatch and result aggregation.

The existing CONDITION node type supports boolean branching via the `_route` key in channel updates, limited to `{"true": "...", "false": "..."}` dict targets. Multi-key routing (e.g., routing by score ranges or category labels) requires expressing each case as a separate condition node, which is cumbersome.

Three JSON templates exist in `src/hecate/data/orchestration_templates/` and are served by the orchestration-templates API.

## Goals / Non-Goals

**Goals:**
- Add FAN_OUT node type that dispatches to N parallel branches concurrently
- Add MERGE node type that collects results from all fan-out branches
- Enhance CONDITION node to support multi-key routing (more than true/false)
- Implement concurrent branch execution in PregelRuntime via `asyncio.gather`
- Add compiler validation for FAN_OUT/MERGE structural constraints
- Add 3 new orchestration templates demonstrating the new patterns
- Maintain backward compatibility — existing graphs work unchanged

**Non-Goals:**
- Variable aggregation functions (sum, average, etc.) at MERGE nodes — keep it simple: MERGE collects all branch outputs into a dict
- Nested fan-out (fan-out within a fan-out branch) — out of scope for this change
- Dynamic fan-out (number of branches determined at runtime) — branches are defined at graph definition time
- Frontend Canvas changes for visual fan-out rendering — that belongs to the canvas feature
- NL2Workflow generation of these patterns — that is P4 feature 1.1.11

## Decisions

### Decision 1: FAN_OUT and MERGE as new node types (not edge semantics)

**Options considered:**
- A. New node types `FAN_OUT` and `MERGE` with standard edges connecting branches
- B. Edge-level parallel semantics (a special edge type that triggers parallel execution)
- C. A single `PARALLEL` node type that internally manages sub-graphs

**Choice: A — Separate FAN_OUT and MERGE node types**

Rationale:
- Aligns with how Versatile, n8n, and Dify model parallel execution (split node + merge node)
- Works naturally with the existing edge model — FAN_OUT has N outgoing edges to branch nodes, branch nodes have edges to MERGE
- Compiler can validate structural constraints (every FAN_OUT must have a MERGE downstream)
- Keeps PregelRuntime changes minimal — the runtime just needs to identify FAN_OUT nodes and dispatch their branches concurrently

### Decision 2: Channel isolation for parallel branches

**Approach:** Each FAN_OUT branch writes to a branch-scoped sub-channel (e.g., `_fanout_{node_id}_{branch_index}`), and the MERGE node reads all sub-channels and combines them into a dict on a single output channel.

**Rationale:** This prevents parallel branches from overwriting each other's channel state. The existing `ChannelManager.write()` with TOPIC channels would silently interleave results, which is incorrect for deterministic pipelines.

**Implementation:**
- FAN_OUT node config specifies `branches`: list of branch node IDs
- The runtime creates a temporary sub-channel per branch (type LAST_VALUE)
- Each branch worker writes to its sub-channel
- MERGE node reads all sub-channels and outputs a dict `{branch_id: result}` to the output channel

### Decision 3: Multi-key CONDITION via enhanced edge target dict

**Approach:** Allow CONDITION nodes to write any string value to `_route` (not just "true"/"false"), and allow edge targets to have arbitrary string keys matching those values.

**Current behavior:**
```python
route_key = str(result.channel_updates.get("_route", "true"))
target = edge.target.get(route_key, edge.target.get("false"))
```

**Enhanced behavior:**
```python
route_key = str(result.channel_updates.get("_route", ""))
# Try exact match first, then fall back to "default" key
target = edge.target.get(route_key) or edge.target.get("default")
```

This is backward-compatible — existing true/false routing still works because "true" and "false" are valid dict keys. The fallback changes from `edge.target.get("false")` to `edge.target.get("default")`, but we'll support both for backward compatibility.

### Decision 4: FAN_OUT execution model in PregelRuntime

**Approach:** When the runtime encounters a FAN_OUT node, it:
1. Reads the FAN_OUT node config to get the list of branch node IDs
2. Dispatches all branch workers concurrently via `asyncio.gather`
3. Collects all WorkerResults
4. Advances to the MERGE node (resolved via edge lookup)
5. MERGE reads all branch sub-channels and outputs aggregated result

The FAN_OUT node itself is a "virtual" node — it doesn't execute a worker, it just triggers parallel dispatch of its branch nodes. This is important: the FAN_OUT config contains `branches` (list of node IDs), not model/prompt settings.

### Decision 5: Template design for 3 new templates

**fan-out-pipeline.json:** Research → Fan-out to 3 analyst agents → Merge → Summary
```
researcher → fanout → [analyst_a, analyst_b, analyst_c] → merge → summarizer → __end__
```

**conditional-pipeline.json:** Input → Classify → Route by category → Process → __end__
```
classifier → check_category → {finance: finance_agent, tech: tech_agent, legal: legal_agent} → __end__
```

**reflection-loop.json:** Draft → Review → (check quality) → Revise or Finish
```
drafter → reviewer → check_quality → {needs_improvement: reviser, approved: __end__}
reviser → reviewer (loop)
```

## Risks / Trade-offs

**[Risk] Parallel branch failure** → If one branch fails, the entire fan-out fails. This is the simplest semantics and matches n8n/Dify behavior. Partial success (continue with available results) can be added later as an opt-in config.

**[Risk] Sub-channel namespace collision** → `_fanout_{node_id}_{branch_index}` naming could collide if node IDs contain underscores. Mitigation: use a unique separator like `__fanout__` and validate node IDs don't contain double underscores.

**[Risk] Backward compatibility of CONDITION fallback** → Changing `edge.target.get("false")` to `edge.target.get("default")` could break existing graphs that only have "true"/"false" keys. Mitigation: support both fallbacks — try the route key first, then "default", then "false" for backward compatibility.

**[Trade-off] No nested fan-out** → This limits expressiveness but significantly reduces implementation complexity. Nested fan-out can be added in a future iteration if needed.

**[Trade-off] Branch count fixed at graph definition time** → Dynamic fan-out (e.g., "fan out to N agents based on input") requires a different architecture. This is out of scope but the FAN_OUT config can be extended later with a `dynamic_branch_source` field.
