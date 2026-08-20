# ADR-032: Dynamic Orchestration via Runtime-Emitted TaskDAG

## Status

Accepted (2026-08-17; extends ADR-001 graph-first, ADR-007 multi-agent-as-graph-templates, ADR-030 log-as-truth; seventh multi-agent collaboration pattern.)

## Context

Hecate's `CollaborationPattern` enum defines six static multi-agent patterns (sequential, parallel, handoff, broadcast, negotiation, debate), all pre-compiled at design time (`src/hecate/engine/patterns.py:29`). The 2026-08-14 industry survey (`docs/research/2026-08-competitor-analysis.md`) and the subsequent deep-dive against Magentic-One (arXiv 2411.04468), Open Multi-Agent v1.14, DeerFlow subagent contract (`backend/packages/harness/deerflow/subagents/AGENTS.md`), Deep Agents interpreters, AgentScope team/plan tools, and dsh source analysis converged on a clear gap: the industry has moved to **runtime-emitted task DAGs** (LLM plans the workflow at execution time). None of Hecate's six patterns supports this — even the dynamic-handoff edge (one-step dynamic) cannot emit a whole DAG.

The dependency chain (per `docs/features/roadmap.md` Sprint 7 ordering): 1.3.19 Event-Sourced Execution State has shipped, exposing the `SUBGRAPH_START/END` reference pair and log-as-truth substrate that this ADR consumes. 1.3.4 fail-closed approval is sequenced in parallel. No new persistence, no new protocol, no new execution primitive is required — only new graph-node semantics on top of the existing Pregel runtime.

## Decision

Introduce the seventh pattern, **DYNAMIC**, as a runtime-emitted sub-graph. The data flow:

```
                +-----------------------+
goal, roster --> |   COORDINATOR node    | -- emit TaskDAG -->
                +-----------------------+     compile sub-graph
                          |                   run in child session
                          v
                  _plan / _ledger /        <-- channels (LogPolicy default-in,
                  _synthesis_buffer           fold-to-version replayable)
                          |
                          v
                  ORCHESTRATOR_DECISION  +  ORCHESTRATOR_EVALUATION events
                  (plan revision history)    (typed blocker, additive stop_reason)
```

The LLM writes a *suggested* TaskDAG; a deterministic executor materialises it into a runnable `GraphConfig`; a separate child session executes it; the coordinator folds outputs back into a synthesis channel. Failures, replans, and capped budgets are visible in the event log via the new `ORCHESTRATOR_DECISION` / `ORCHESTRATOR_EVALUATION` event types. **The plan is a first-class log citizen from the moment it is written** — no new persistence is required.

### Two implementation choices, rejected

* **code-as-plan** (Deep Agents interpreters; Claude Code dynamic workflows). The plan is JS or Python code dispatched via a sandbox. Rejected because Hecate is multi-tenant: serialisable data is auditable; sandboxed code is not. Also: Pregel already gives deterministic loops/branches for free, so a code runtime is unnecessary.
- **let the LLM write `GraphConfig` directly.** Rejected because LLM-authored DSL is brittle (id collisions, channel naming). The executor materialises node ids (`task_<task_id>_<revision>`) and channel ids (`<task_id>.<expected_output>`); the LLM only expresses intent.

### Five design pillars (cited; decisions in the design doc)

1. **Magentic double-loop as a graph** — outer-loop replan, inner-loop executor + evaluator five-question stall counter. Source: arXiv 2411.04468 §4.1 (task ledger / progress ledger / stall counter ≤ 2).
2. **Three-axis budget + additive `stop_reason`** — `max_iterations` / `stall_limit` / `max_total_tasks` / `max_concurrent` / `token_budget`. Capped partial outputs surface a model-visible guidance string so the planner does not mistake them for clean completions. Source: DeerFlow `subagents/AGENTS.md` (Phase 2 stop_reason design lesson).
3. **Benefit-based delegation rubric** — coordinator system prompt embeds a public `BENEFIT_BASED_DELEGATION_RUBRIC` constant, snapshot-tested. Source: DeerFlow `lead_agent/prompt.py` + `test_subagent_routing_prompt.py`.
4. **Five-isolation contract** — independent `child_session_id`, sub-session memory isolation, explicit input channel mapping, explicit output back-write (no leakage of `messages`), failure authority limited to `WorkerResult.error` and the status contract (no output-text guessing). Source: DeerFlow `subagents/AGENTS.md` 5 isolation properties + dsh `Mech.51 composeFrom` (no re-resolving parent presets).
5. **Fail-closed pre-dispatch validation** — OMA v1.14 `validateTaskRequirements`. `validate_task_requirements(dag, roster)` rejects cycles, unsatisfiable roster, capability gaps, and dangling references before any worker dispatches.

## Consequences

- **Public surface**: new `NodeType.COORDINATOR`, new `CollaborationPattern.DYNAMIC`, new `TaskDAG` Pydantic schema, new `CoordinatorWorker`, new `build_dynamic_orchestration_executor` template, two new `EventType` values, new `orchestrator_validator` module.
- **No new persistence**: plans go through existing channels; `LogPolicy` already defaults-in the new event types.
- **No new worker**: `CoordinatorWorker` reuses the existing `PregelRuntime` for the sub-graph, the existing `ChannelManager` for isolation, and the existing `EventStore` for observability. The executor template is the only new compilation step.
- **No breakage of existing patterns**: `infer_pattern()` adds a new top-priority branch (any COORDINATOR node → DYNAMIC). `build_graph_from_pattern` raises `NotImplementedError` for DYNAMIC since dynamic graphs cannot be statically constructed.
- **Five deferrals (registered in `docs/features/feature-catalog.md` 2026-08-17 scope-freeze)**:
  - Advanced orchestration follow-up: full consensus proposer→judge, append-only `PlanPatch` repair API, async orchestration + mid-flight steering, plan-freeze artifact + exact replay.
  - UI companion follow-up: pattern-selector seventh entry, canvas `COORDINATOR` node, replay coordinator-card expansion.
- **Naming**: `ORCHESTRATOR_DECISION` and `ORCHESTRATOR_EVALUATION` follow ADR-030 §1 additive EventType contract; unknown verdict values fall back to `"stalled"` (DeerFlow compatibility).

#### References

- ADR-001 — Graph-first orchestration
- ADR-007 — Multi-agent patterns unified as graph templates
- ADR-030 — Event-sourced execution state (log-as-truth)
- `openspec/changes/dynamic-orchestration/{proposal,design}.md`
- `openspec/changes/dynamic-orchestration/specs/dynamic-orchestration/spec.md`
- `src/hecate/engine/dynamic_types.py` — public schema
- `src/hecate/engine/orchestrator_validator.py` — fail-closed validation
- `src/hecate/engine/workers/coordinator_worker.py` — Magentic double-loop
- `src/hecate/engine/templates.py` — executor template (`build_dynamic_orchestration_executor`)
- `tests/test_engine/test_coordinator_prompt.py` — rubric snapshot
- `tests/test_engine/test_dynamic_orchestration.py` — end-to-end coverage
- `tests/test_engine/test_patterns.py` — DYNAMIC enum + `infer_pattern` recognition