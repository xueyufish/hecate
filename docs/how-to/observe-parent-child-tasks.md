# Observe parent-child task relationships

A controller task can delegate work to child tasks through the controlled `agent_children` mechanism. The task detail page shows the recorded relationship facts for both sides of that relationship in the task-scoped **父子调用（任务级）** panel. Everything in the panel is a recorded fact read from the existing authorized task detail GET (`GET /api/tasks/{task_id}`); the panel issues no request of its own and offers no command.

## What the parent side shows

- The accepted-call count (`agent_children.accepted_calls`). A recorded `0` is a real fact and is shown as such — it is different from the summary being absent.
- When the task currently has a recorded agent-child wait, its consumption summary (`agent_children.pending`): the call alias, the call identity, the child task reference and whether the callback was consumed.
- When no wait is currently bound, the panel says so explicitly. The API summarizes only the current wait; it promises no call history, and the panel claims none.
- The child-execution budget lines (`agent_child_budget_used`, `workflow_budget_used`) — see below.

## What the child side shows

- The delegation stamp the task was accepted under (`agent_child_parent`): the call alias, the call identity and the parent task reference, with a back-to-parent navigation link.

## The four facts that stay separate

1. **A call was accepted** — the count and the child-side stamp.
2. **The callback was consumed** — `pending.consumed`. This is a consumption fact only: it does not mean the child task succeeded and does not mean the parent's goal passed.
3. **The child task's lifecycle** — never shown on the parent. The parent's detail does not carry the child's state; use the child task link to open the child's own detail page, which queries the child with its own authorization.
4. **Delivery and acceptance** — each task's own run panels; a relationship says nothing about either verdict.

Never infer a child's outcome from the parent's wait state: an unconsumed wait may belong to a child that is still running or already finished.

## What the budget lines mean

The **子执行累计预算（已记入父任务）** block always renders two independent lines — Agent 子调用 (`agent_child_budget_used`) and Workflow Controller 子步骤 (`workflow_budget_used`) — so an old task reads 未记录 instead of silently looking like zero.

- Each line is the parent's persisted, cumulative spend for that child-execution kind: agent-child usage is folded in when the child's wake callback is consumed; workflow usage is folded in when a step's verified callback is consumed, and only steps with a declared budget slice meter.
- A recorded `0` is a real fact and is shown as `0`. 未记录 means the counter was never recorded (a pre-feature task, or no metered child execution yet) — it is not zero, and the two counters handle their missing states independently.
- The figures are already consumed accounting entries on the parent. They are not a real-time balance, do not cover the task's full usage (parent-side model calls are not child-execution spend), and are not a verified bill. The child task's own detail remains the place to read the child's outcome.

## Navigation and reference validation

Relationship references are recorded data echoed by the API. A local link is generated only when the reference re-validates locally: the right `kind`, the supported platform issuer (`hecate`) on both the reference and the task detail being viewed, and a UUID id. Matching external issuer domains do not map external identifiers to local tasks. Anything else renders as recorded text with an explicit note instead of a guessed link. A relationship reference never grants access: child and parent details stay behind the platform's workspace/role read gate, and opening the link is an ordinary authorized navigation, not a delegation of permission.

## What the panel does not do

- It never issues a POST: no human input, resume, cancel or callback submission. The existing agent-child wait stays read-only.
- It shows no child status and no call history. The budget lines are parent-side recorded totals only; they say nothing about any single child call or step.
- Absence of the summaries means "no recorded relationship information" — possibly a task from before the feature — and never "delegation is disabled".
- The section is task-scoped and stays put when the selected Run changes; run-scoped verdicts remain in the run panels.

## Verification pointers

Component behavior (fact branches, consumption states, reference-validation matrix) is pinned in `web/src/__tests__/task-child-observation-panel.test.tsx`; page integration (navigation, run-switch stability, stale refresh, late-response isolation, no writes) in `web/src/__tests__/task-detail-child-observation.test.tsx`. The served fields are covered by `tests/test_execution/test_agent_child_run.py` and the read boundary by `tests/test_api/test_task_observation_access_boundary.py`.
