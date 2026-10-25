# Cancel a task from the ops console

The Task detail page (`/ops-center/tasks/{task_id}`) can request task cancellation and show the server-recorded receipt. Cancellation is a governed control command, not a guaranteed stop.

## What the page does

- Reads the task's durable revision (`revision` in the task detail response). A task with no durable record shows no revision, and the page refuses to submit a cancel in that case — it never guesses a version.
- Sends `POST /api/tasks/{task_id}/commands` with `kind: "cancel"`, a stable `command_id` minted for the operation, and the `expected_revision` read from the page. The `/cancel` shorthand endpoint is not used because it cannot carry these fields.
- Requires an explicit confirmation before the request leaves the browser. While a submission is in flight, repeat clicks cannot start a second one.

## Receipts and retry

The response is a command receipt, recorded verbatim:

| State | Meaning |
| --- | --- |
| `requested` | The platform recorded the command; the executor has not acknowledged or applied it |
| `acknowledged` | The executor received the command; this does not prove it took effect |
| `applied` | The executor reports the command took effect |
| `rejected` | The command was refused (task already terminal, revision mismatch, or unsupported capability) |
| `expired` | The command lapsed before taking effect |

- Queued or waiting tasks usually converge to `applied` and become `cancelled`. A running execution has no cooperative abort channel: its receipt stays `requested` and the task may run to its natural outcome. Issuing a cancel never promises that a remote host can be force-stopped.
- An HTTP success — even an `applied` receipt — does not rewrite the task lifecycle shown on the page. The lifecycle badge changes only when the re-queried task detail reports the new state.
- If the submission fails at the network level (result unknown) or the server rejects it (for example `403` for a missing editor role), retrying reuses the same `command_id` and the same request body, so the server replays one command instead of minting a second.
- A `rejected` receipt caused by a stale revision (`expected task revision no longer matches`) tells you to refresh the page; the page never re-sends automatically with a different revision.
- The confirmation pins its displayed revision. Refreshing the task during confirmation or before a retry cannot change that operation's request body; a new version requires a separately confirmed operation. If the refreshed task is terminal or has no revision, confirmation and retry are unavailable.
- After a submission the page refreshes the task detail and the command receipt list. If that refresh fails, the already-received receipt stays on screen under a refresh-failure banner — a failed refresh is not a failed submission and must not trigger a new command.

## Server-side boundaries

- Write authorization is decided by the server (`EDITOR` role in the task's workspace); the page neither hides nor derives write permission from the browser session.
- Cancellation of a terminal task is refused by the server; the page disables submission for `succeeded`, `failed`, and `cancelled` tasks accordingly.
- The command-receipt list (`GET /api/tasks/{task_id}/commands`) shows recorded states read-only. Only the single-command read performs lazy expiry; the console does not call it.

For the API contract behind this surface, see [Task control commands](../refactor/enterprise-agent-platform-evolution-plan.md) (step6/step14 sections) and the OpenAPI schema under `src/hecate/contracts/openapi/`.
