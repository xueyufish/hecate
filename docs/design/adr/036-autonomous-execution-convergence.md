# ADR-036: Autonomous execution convergence

## Status

Accepted for the bounded compatibility fixes and continued evidence collection.
Real-model observations are **recorded** for `zai/glm-4.5-flash`. The original rubric v3
record is retained; the [rubric v4 observation](../../../tests/scenarios/baselines/autonomous_multiturn_autonomous-execution-rubric-4_20261016T083041343667Z.json)
adds actual model inputs/outputs, usage, tool receipts and approval events. It exposed
lost tool context during engine output repair. A deterministic reproduction and bounded
fix retain that context without dispatching tools again; a
[targeted follow-up](../../../tests/scenarios/baselines/autonomous_multiturn_rubric-4_repair_context_20261016T084148839574Z.json)
records valid artifacts and approval continuation. Some latency thresholds still fail,
and cost remains unverified; observations are not a statistical quality certificate.
Real TCP recovery now verifies the prior receipt in the recovery model's first input,
with persisted PostgreSQL facts and no duplicate tool execution. The
[review](../../../openspec/changes/archive/2026-10-16-step5e-real-model-baseline/review.md) records its scoped
ownership and legacy boundaries. This ADR does not authorize a default route switch or
production certification.

## Context

The [mechanism matrix](../execution-mechanism-matrix.md) records the actual behavior of
chat's direct loop, the Pregel Agent graph, deterministic Workflow and standalone Runner.
The original claim that an unknown receipt automatically stopped a live graph was incorrect:
ToolWorker persisted the unknown receipt but still returned an error tool message. Receipt-based
recovery alone did not prevent the current model turn from continuing.

Both chat paths now stop on unknown outcomes and budget refusal, validate declared final output,
and check cancellation at model boundaries. Repairing structured output disables tools instead of
restarting a graph. Workflow callbacks use committed child result facts and atomically persist step
receipts with the parent's wake. Runner retains its declared fixed-plan preview semantics.

Evidence is [HTTP/SSE parity](../../../tests/scenarios/test_s12_autonomous_multiturn.py),
[replay and stop checks](../../../tests/test_channel/test_loop_stop_semantics.py), and
[persistent Workflow contracts](../../../tests/test_execution/test_workflow_subtask_contract.py).
A scripted provider supports deterministic safety assertions; it does not establish real-model quality.

## Decision

Keep the existing mechanisms and G3 routing while closing demonstrated safety and compatibility
defects. Reuse the existing Pregel graph as the candidate for later chat convergence because its
checkpoint/event recovery is already shared with Workflow. Final default-routing approval requires
the remaining G3 and real-model evidence; it cannot follow from this ADR alone.

Do not extract a new harness or package, remove Pregel, or invent a mandatory step5f. Internal
component changes must be justified by measured behavior and maintenance costs, not package counts.

## Alternatives

| Direction | Necessary changes | Dependencies | Recovery complexity | Maintenance cost | Governance boundary |
|---|---|---|---|---|---|
| Reuse Pregel | Complete G3 entry/evidence mapping and real-model comparison; retain the direct rollback path during migration | Existing shared assembly, checkpoints, event log and verified port | Existing durable recovery, with live unknown-result termination corrected here | Duplicate chat adapters remain during migration; no new execution kernel | Production port and ToolWorker govern graph effects; direct registry governs rollback |
| Make direct loop the permanent default | Add durable chat lifecycle and recovery to the direct path | Claims plus additional state/event persistence work | Supplied history alone cannot certify arbitrary restart or trimmed-history recovery | Low immediate migration cost, but durable recovery would be duplicated | Governed registry remains the chat effect boundary |
| Extract a shared lightweight harness | New component, chat/Runner migrations and compatibility certification | A demonstrated shared consumer contract and independently verified host lifecycle | Revalidate state ownership, unknown receipts and approval recovery on every host | Highest initial change surface; long-term benefit is unmeasured | Must preserve the current authorization, claim and evidence seams |

No performance or operational-cost advantage is asserted for any option without a measured run.

## Runner sub-decision

Runner remains fixed-plan for its already supported preview/governance profiles. Model-driven
Runner selection is a remaining requirement for independent autonomous consumption; platform chat
parity cannot satisfy that requirement. A later focused implementation should first evaluate reuse
of the shared execution assembly and compare it with a host-local loop under the same task, budget,
approval and recovery boundaries. That implementation is outside this change's declared scope.

## Consequences

- HTTP callers receive explicit terminal categories; declared schema streaming buffers final content.
- Formatting retries cannot start tools. Already-started parallel tools are drained before stop delivery.
- Workflow output is accepted from persistent child records; forged caller summaries cannot supply artifacts.
- Durable approval waiting on the durable task path (typed park signal, pending
  authority binding, decision-gated resume wake, dispatch-time one-time consumption,
  real worker-subprocess restart) is delivered by
  `step5e-durable-approval-continuation`; the chat path keeps synchronous approval.
  The workflow composition (step-declared child approval waiting, system-execution
  snapshot tenancy derived from the verified agent principal, rejection convergence
  through the verified callback with durable outbox retry, separate parent/child process restart) is delivered by
  `step5e-workflow-agent-continuation`.
  The reviewed task path retains the original approval reference and stable server action
  ID across consumption-before-claim crashes; new actions require new decisions. PostgreSQL
  authority time revalidates restarted clocks without extending expiry. Evidence uses
  trusted service-level decisions and worker restarts, not new approval HTTP/UI or offline
  authority. See the [reviewed observation](../../../tests/scenarios/baselines/durable_approval_restart_20261016T170954390Z.json).
  Broader model-quality coverage, Runner selection, business value and managed deployment
  certification remain open. The
  [review](../../../openspec/changes/archive/2026-10-12-builtin-autonomous-execution/review.md)
  records validation and limitations. step5e remains a partial technical delivery until its gates pass.
