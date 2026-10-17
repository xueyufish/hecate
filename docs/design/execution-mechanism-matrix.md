# Execution mechanism matrix

This is the maintained behavior of the direct chat loop (DL), Pregel Agent graph (PG),
deterministic Workflow (WF), and standalone Runner (RN). Update affected cells whenever
execution semantics change. Decisions live in [ADR-036](adr/036-autonomous-execution-convergence.md).
A deterministic responder proves protocol and safety behavior; real model quality needs a
separate recorded run. Runner model selection and managed deployment certification remain open.

| Axis | DL | PG | WF | RN |
|---|---|---|---|---|
| Model invocation | Metered `GovernedModelService`, cancellation checked before each call including output repair ([chat](../../src/hecate/channel/api/v1/chat.py), [governance](../../src/hecate/core/composition/governance.py)) | Structured provider calls through the production port; the same budget authority and chat cancellation wrapper ([port](../../src/hecate/core/composition/runtime_port_adapter.py)) | Each child owns its model calls; parent follows explicit steps ([dispatcher](../../src/hecate/execution/task_dispatcher.py)) | Configured endpoint/stub worker; profile budget gate ([engine](../../packages/hecate-runner/src/hecate_runner/engine.py)) |
| Tool selection / feedback | Model chooses tools; decided receipts replay before new authorization/dispatch. Identity includes session, tool-turn ordinal, arguments and history digest; old call-id keys are read ([chat](../../src/hecate/channel/api/v1/chat.py), [replay tests](../../tests/test_channel/test_loop_stop_semantics.py)) | Model chooses tools; ToolWorker persists receipts. Unknown outcomes terminate before error feedback enters history ([ToolWorker](../../packages/hecate-runtime/src/hecate_runtime/workers/tool_worker.py), [parity](../../tests/scenarios/test_s12_autonomous_multiturn.py)) | Explicit child sequence; autonomous child execution remains inside the child ([adapter](../../src/hecate/execution/workflow_child.py)) | Fixed tool plan; does not prove model selection ([engine](../../packages/hecate-runner/src/hecate_runner/engine.py)) |
| Structured output | Optional validated `output_schema` on both HTTP request shapes; final answer repair is bounded and tools are disabled; declared streaming content is held until valid ([chat](../../src/hecate/channel/api/v1/chat.py), [agents](../../src/hecate/channel/api/v1/agents.py)) | Same entry validation; repairs only the final model answer, never restarts a tool graph ([chat](../../src/hecate/channel/api/v1/chat.py), [parity](../../tests/scenarios/test_s12_autonomous_multiturn.py)) | Step contract reaches the child; parent validates committed output/artifacts before advancing ([callback](../../src/hecate/execution/task_control.py), [contract tests](../../tests/test_execution/test_workflow_subtask_contract.py)) | Tool output schema validation; no certified autonomous final answer ([engine](../../packages/hecate-runner/src/hecate_runner/engine.py)) |
| Budget / stop | Distinct `needs_review`, `budget_exhausted`, `iteration_limit`, `cancelled`, `invalid_output`; unknown replay is terminal ([stop tests](../../tests/test_channel/test_loop_stop_semantics.py)) | Budget refusal and graph limit become the same chat stop categories; terminal conditions bypass retry; already started tool calls drain before propagation ([retry](../../packages/hecate-runtime/src/hecate_runtime/retry.py), [parity](../../tests/scenarios/test_s12_autonomous_multiturn.py)) | Child execution/validation failures have distinct recorded sources; parent applies `fail` or reconciliation policy. Unknown effects remain reconciliation cases ([dispatcher](../../src/hecate/execution/task_dispatcher.py)) | Profile budget refusal and unknown-result reconciliation ([engine](../../packages/hecate-runner/src/hecate_runner/engine.py)) |
| Approval wait | Synchronous approval callback; a chat request is not a durable approval wait ([chat](../../src/hecate/channel/api/v1/chat.py)) | ToolWorker approval callback; graph interrupt/resume is separately supported ([ToolWorker](../../packages/hecate-runtime/src/hecate_runtime/workers/tool_worker.py)). Durable tasks: a policy-required approval parks the task (`WAITING_APPROVAL` + pending authority binding), `resume` wakes only on a persisted approved decision, the parked attempt continues natively on the same run/session and the binding is consumed once at dispatch ([approval wait](../../src/hecate/execution/approval_wait.py), [dispatcher](../../src/hecate/execution/task_dispatcher.py), [restart e2e](../../tests/scenarios/test_durable_approval_restart.py)) | Durable child wait and verified callback; callback cannot supply approval authority ([task control](../../src/hecate/execution/task_control.py)). step5e: a step may declare `approval_wait` — the child parks on a policy-required approval while the parent holds its wait, rejection converges the parent through the verified callback and durable outbox retry by the declared failure policy, and system children persist snapshots under an agent-principal derived tenant key ([combo tests](../../tests/test_execution/test_workflow_approval_combo.py), [restart e2e](../../tests/scenarios/test_workflow_approval_restart.py)) | Local approval authority, durable wait and single-use wake grant ([SC03](../../tests/scenarios/test_sc03_local_governance.py)) |
| Persistence | Governance claims persist; chat itself has no engine log. Pending tool history can replay decided receipts; arbitrary trimmed-history recovery is not certified ([governance](../../src/hecate/core/composition/governance.py), [replay tests](../../tests/test_channel/test_loop_stop_semantics.py)) | Checkpoints and event log, including unknown receipts ([service](../../src/hecate/studio/workflows/execution_service.py), [ToolWorker](../../packages/hecate-runtime/src/hecate_runtime/workers/tool_worker.py)) | Step callbacks and token consumption commit with parent wake; child submissions use stable step idempotency keys ([task control](../../src/hecate/execution/task_control.py), [restart tests](../../tests/test_execution/test_workflow_subtask_contract.py)) | Local durable task, ledger and evidence; production scope follows profile certification ([SC03](../../tests/scenarios/test_sc03_local_governance.py)) |
| Cancellation | Request disconnect checked before each model call; running effects drain, no forced rollback ([chat](../../src/hecate/channel/api/v1/chat.py)) | Chat wrapper checks the same boundary; engine cancellation/interrupt mechanisms remain available ([chat](../../src/hecate/channel/api/v1/chat.py)) | Commands handled by durable task control ([task control](../../src/hecate/execution/task_control.py)) | Cooperative dispatch-boundary cancellation ([engine](../../packages/hecate-runner/src/hecate_runner/engine.py)) |
| Error events | Sync stop response or terminal SSE error with `[DONE]`; no engine event stream ([chat](../../src/hecate/channel/api/v1/chat.py)) | Engine receipts and error events, mapped to the same chat categories ([ToolWorker](../../packages/hecate-runtime/src/hecate_runtime/workers/tool_worker.py), [chat](../../src/hecate/channel/api/v1/chat.py)) | Durable state, command receipts, callbacks and failure source ([task control](../../src/hecate/execution/task_control.py)) | Local evidence, action ledger and run events ([engine](../../packages/hecate-runner/src/hecate_runner/engine.py)) |

## Evidence and remaining gates

- [S12](../../tests/scenarios/test_s12_autonomous_multiturn.py) compares actual HTTP/SSE direct
  and Pregel paths with the same feedback-driven synthetic task.
- [Workflow contract tests](../../tests/test_execution/test_workflow_subtask_contract.py)
  exercise committed child facts, forged/duplicate/late callbacks and fresh dispatcher instances.
- [Recorded comparison runner](../../tests/scenarios/baseline/run_autonomous_baseline.py)
  calls the actual DL and PG mechanisms with a real provider and synthetic tool boundaries.
  [Original record](../../tests/scenarios/baselines/autonomous_multiturn_baseline.json) is
  retained. The [rubric v4 observation](../../tests/scenarios/baselines/autonomous_multiturn_autonomous-execution-rubric-4_20261016T083041343667Z.json)
  records both actual mechanisms with `zai/glm-4.5-flash`, including model inputs/outputs,
  per-round usage, tool receipts and approval events. Stop-injection cases converge correctly;
  initial engine artifact failures exposed missing tool context in schema repair. The
  [targeted follow-up](../../tests/scenarios/baselines/autonomous_multiturn_rubric-4_repair_context_20261016T084148839574Z.json)
  preserves that context and records correct artifacts and approval continuation. Some latency
  thresholds remain exceeded; cost and supplier pricing remain unknown. Failed observations
  remain in the original files, and a bounded follow-up is not a new full comparison.
- [Real-model disconnect acceptance](../../tests/scenarios/test_real_model_disconnect.py)
  uses the same TCP fixture for deterministic and actual providers. The
  [PostgreSQL observation](../../tests/scenarios/baselines/real_model_disconnect_v1_20261016T083351993460Z.json)
  verifies cancelled convergence, a unique persisted receipt/terminal, the real receipt in the
  first recovery input and a correct structured answer without repeated execution. The
  [SQLite observation](../../tests/scenarios/baselines/real_model_disconnect_v1_20261016T083647368984Z.json)
  proves in-process recovery only; its events/snapshots are in memory. Automatic history restore
  requires a service-verified same-user/same-agent snapshot; legacy ownership is not inferred.
  Explicit client histories remain authoritative. Real-provider tests require manual opt-in.
- Independent Runner model selection, managed profiles and business outcomes remain
  unverified. These gates prevent claiming step5e production completion.

- [Durable approval restart acceptance](../../tests/scenarios/test_durable_approval_restart.py)
  covers opt-in platform tasks with PostgreSQL events, snapshots and authority. Real worker
  processes verify ordinary resume, replay of completed writes, a crash after approval
  consumption before action claim, and a new decision for the same parameters under a new
  action ID. The original approval reference and server action identity remain unchanged;
  the first resumed model input must contain real receipts. Online database time revalidates
  process clock anchors without extending expiry. The [reviewed real-model observation](../../tests/scenarios/baselines/durable_approval_restart_20261016T170954390Z.json)
  records actual model inputs/outputs and the original consumed binding. The earlier
  observation is retained; failure paths now also write their partial observations.
  Decision injection uses the trusted service boundary; new enterprise approval HTTP/UI,
  control-plane restart, offline clock trust and arbitrary fault combinations are not certified.

The workflow approval/restart real-model observation retains a schema-validation failure: the child resumed and the protected effect executed once, but its final text was not JSON and the parent correctly refused to advance. A successful real-model composition remains open; see the [composition review](../../openspec/changes/archive/2026-10-17-step5e-workflow-agent-continuation/review.md).
