# ADR-037: Agent-Native Execution Model and Graph-Execution Boundary

## Status

Accepted (2026-10-17; fixes the target execution-model responsibilities and supersede
scope recorded in the [evolution plan](../../../docs/refactor/enterprise-agent-platform-evolution-plan.md)
revision #265. This ADR authorizes direction and boundaries only: the Agent Controller
is **not delivered** by this ADR, no default route switch follows from it, and no
production certification is granted. Physical package splits still require named
consumers and dependency evidence.)

## Context

The evolution plan now fixes an agent-native target: open tasks are driven by an
autonomous Agent Controller that does not depend on Pregel; deterministic workflows
keep explicit control rules with Pregel owning graph execution; both consume one shared
durable execution mechanism and one trusted-action pipeline. External runtime
integration (former step8) is removed from the plan.

The operative implementation basis still points the other way. ADR-001/007 established
graph orchestration as the primary paradigm with all multi-agent patterns as graph
templates; ADR-032 made a runtime-emitted TaskDAG the seventh graph pattern; ADR-036
selected the existing Pregel graph as the chat convergence candidate and prohibited
extracting a new harness. The shared execution application service
(`hecate_runtime.execution_service`) is still typed directly against `PregelRuntime`,
and `unified-chat-execution` still names "converge to the unified Pregel runtime" as
its purpose. Following those documents as-is would extend the old direction while the
plan, roadmap, and specs demand the new one.

## Decision

### 1. Six execution responsibilities (target, logical only)

| Component | Core responsibility | State and composition boundary |
|---|---|---|
| Harness | Organize context, Skills, Memory usage, planning and model strategy | Reuses the existing ContextEngine, processors and Middleware; semantic policies are replaceable or removable; owns no authorization or action facts |
| Agent Controller | Run the recoverable decision loop: consume results, user input and child-execution events, propose next work | Accepted decisions, action sets and continuation position must be persisted; waiting releases execution resources; an in-entry memory loop is not a substitute for recovery |
| Dynamic Plan | Express goal decomposition, dependencies, expected artifacts and revisable execution intent | Distinguishes proposals, accepted revisions and actual progress; may execute stepwise or hand structured fragments to a Workflow; no third runtime |
| Workflow Executor | Advance explicit steps, conditions and failure policies; may call models, tools or autonomous Agents | Owns the flow cursor and node state; the first graph implementation reuses Pregel; other flow mechanisms are evaluated only under a named requirement |
| Shared durable execution mechanism | Manage Run/ownership, decision and action identity, Child Runs, reliable wait/wake, control and recovery | Agent Controller and graph execution consume the same operation semantics, keep their own internal state; no parallel ledgers, approvals or task sources of truth |
| Trusted action execution | Validate final parameters, authorization, approval, credentials, budget, idempotency and results | Every governed capability call passes the enforcement point; Harness tool visibility never substitutes for authorization |

Open tasks default to the Agent Controller and SHALL NOT require constructing a
`GraphConfig` or `PregelRuntime`; tasks with explicit control rules use the Workflow
Executor; Pregel carries graph-suitable scheduling, channels, branch-join and graph
state recovery. Graphs may contain cycles, but graph expressiveness is not a reason to
route all autonomous execution through graph compilation.

Agents may call Workflows; Workflows may create Agent Child Runs. Parent and child
states have separate owners linked by stable invocation identity, explicit inputs and
artifact references; a parent advances only after artifact and business-condition
validation. Ordinary LLM nodes do not auto-upgrade to autonomous Agents.

Code, graphs and semantics may each define deterministic workflows or autonomous
Agents. Semantic definitions resolve behavior-affecting ambiguity first, then generate
a scenario-matched representation; unification into a Graph IR is not forced.

### 2. Supersede scope (precise to decision points; history and evidence retained)

| Prior decision | Superseded / limited point | Points that remain in force |
|---|---|---|
| ADR-001 graph-first orchestration | "Graph orchestration as the primary paradigm" as the default premise for all orchestration | Graphs as the expression and execution mechanism for Workflows; the three-layer Agent template |
| ADR-007 multi-agent as graph templates | "All orchestration patterns are pre-compiled Graph templates" as a universal claim | The six static patterns plus DYNAMIC remain delivered and usable as structured strategies |
| ADR-032 dynamic orchestration | TaskDAG→sub-graph as the only dynamic-orchestration shape; "No new persistence" applied to reliable-execution tasks | Fail-closed validation, five-isolation contract, budgets and event semantics (delivered) |
| ADR-036 autonomous execution convergence | "Reuse the existing Pregel graph as the candidate for later chat convergence"; "Do not extract a new harness" | Dual-path stop semantics, structured-output validation, durable approval continuation, real-model baseline evidence; G3 evidence gate and no-default-flip from ADRs alone |
| ADR-034 / ADR-035 | None — no decision point superseded | Governance semantics, single-writer state ownership, package direction all remain; only their "built-in Runtime = Pregel" phrasing and step8 certification references are updated |

### 3. ADR-032 premise review

"No new persistence" holds for graph observability but not for reliable execution:
the Coordinator's channel-based plan history (`_plan` channel and
`ORCHESTRATOR_DECISION` events) is not the persisted accepted-decision semantics the
plan requires. Accepted decisions, accepted plan revisions and continuation positions
are committed by their Controller/Workflow owners through the shared durable
execution mechanism (step6 work); that mechanism owns persistence and recovery
operations, not plan meaning or a parallel business-state authority. Tasks that declare
reliable execution or resume must not claim reliable plan recovery from channel-only
history. ADR-032's code-as-plan rejection was scoped to that graph strategy.
Multi-tenancy requires explicit authorization, isolation and evidence; serialized
plans alone do not prove auditability, and program representations are not inherently
unauditable. Code, graphical and semantic definitions remain independent of execution
freedom. Controlled program execution still requires a separately verified isolation
profile and is not a first-delivery prerequisite.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Keep Pregel as the universal convergence target | The accepted architecture separates autonomous decisions from graph scheduling; existing comparisons cover bounded legacy paths and do not measure a new Controller. Quality, latency, cost and recovery comparisons remain implementation/acceptance work |
| Rewrite ADR-036 in place | ADR-036 carries accepted delivery evidence (rubric v4 baseline, durable approval continuation, workflow composition review); a partial supersede preserves the evidence chain |
| Extract the Harness/Controller packages now | Physical packaging requires named consumers and dependency evidence; this ADR fixes logical responsibilities only (plan §1 "architecture vs implementation boundary") |

## Consequences

- The chat convergence target becomes the shared execution application service and its
  unified Task/Run, action, approval and event semantics (see the
  `unified-chat-execution` delta); the `PregelRuntime`-typed request of the shared
  execution service is an implementation status to be lifted, not a target contract.
- DYNAMIC/TaskDAG orchestration is scoped as a structured execution strategy
  (see the `dynamic-orchestration` delta); open tasks do not require emitting a full
  DAG or compiling a graph.
- Positioning, architecture, engine-design and the Runtime `AGENTS.md` record the
  target-vs-current split; the Runtime self-sufficiency invariant (no imports of
  platform domains, durable or Runner) is unchanged and extends to future Controller
  code.
- No runtime behavior, route, schema or default switch changes follow from this ADR.
  Agent Controller delivery and its acceptance remain gated on step5e/6/7 slices; new
  Runtime extension points still require a second implementation or a named consumer.
- Existing contracts, pilots and evidence from the removed external-runtime work
  (former step8) are retained as history; reviving that scope requires a new proposal.

## References

- [Evolution plan](../../../docs/refactor/enterprise-agent-platform-evolution-plan.md) — §1 responsibility table, §5 step2 migration list
- [ADR-001](001-graph-first-orchestration.md), [ADR-007](007-multi-agent-as-graph-templates.md), [ADR-032](032-dynamic-orchestration.md), [ADR-036](036-autonomous-execution-convergence.md), [ADR-034](034-platform-governance-semantics.md), [ADR-035](035-standalone-execution-package-direction.md)
- [Execution mechanism matrix](../execution-mechanism-matrix.md) — current measured behavior
- Deltas: `openspec/changes/archive/2026-10-17-step2-agent-native-baseline-alignment/specs/{unified-chat-execution,dynamic-orchestration}/spec.md`
