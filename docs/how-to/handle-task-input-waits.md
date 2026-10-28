# Handle human input waits on controller tasks

A controller-loop task can park in `waiting_input` when its governed tool
returns the explicit input-request contract — a structured result
`{"status": "input_required", "contract": {...}}` (never guessed from
text). The platform persists the wait, a workspace EDITOR submits the
missing fields from the task detail page, and the same Task/Run continues
from its accepted-decision chain with the input as a durable revision.
This guide covers the operator-visible flow and the support boundaries.

## The closed loop

1. **Request**: the agent calls a tool whose business API needs more
   information. The API answers `input_required` with a contract (e.g.
   `{"fields": ["region"]}`); no side effect has been performed. The
   dispatcher records that answer as the action's real outcome and parks
   the task (`waiting_input`, single-use `wait_token`, optional expiry).
2. **Submit**: on the task detail page (Ops Center → Tasks → detail), the
   补充输入 panel shows the request summary — the tool, the declared
   fields, and the validity hint. An EDITOR enters a JSON object whose
   keys answer the declared fields and confirms. The page issues
   `POST /api/tasks/{id}/commands` with `kind: "provide_input"`, a stable
   `command_id`, the page-read `expected_revision`, the payload
   (schema ref `urn:hecate:task-user-input/0` — a JSON object), and
   the wait token in `detail_ns`.
3. **Receipt**: the command receipt states what happened to the command —
   `applied` means the platform accepted the input and recorded a durable
   input revision; it never means the model has consumed the input or
   that the task succeeded. Refresh the task and the command list for the
   real state; a failed refresh keeps the received receipt on screen.
4. **Continuation**: the wake re-dispatches the SAME task/run. Committed
   decisions replay from storage with zero model calls, the input
   revision folds in at its causal turn boundary, and the model decides
   the next step with the provided fields visible. The wake acknowledges
   the parked action's durable execution identity in the same transaction
   as the input revision. Replay suppresses only that exact settled
   request; a fresh action with identical arguments can park again.
   A crash after outcome persistence but before parking recovers the wait
   from the ledger without repeating the tool or model call.

## What is writable, what is read-only

The panel is writable only for a plain human input wait: `waiting_input`
+ `wake_kind: provide_input` + unconsumed token + a supported `kind: input`
contract with a tool and required fields + a usable task revision. Expired,
malformed and workflow child-result waits render read-only with a reason:

- `waiting_approval` waits are approval waits; they advance only through
  the verified approval resume, not the input form.
- A wait whose contract carries `await_task_ref` is a workflow
  child-result wait; the server rejects `provide_input` for it outright —
  only the trusted callback path (which verifies the child's recorded
  facts) may advance it. Caller-supplied `callback_digest` metadata is
  rejected and cannot authorize a wake.
- A consumed token means the input was already accepted and the task is
  continuing; the panel says so and offers no form.
- Terminal tasks and tasks without a wait show no submission surface.

Authorization is server-side: the command gate requires EDITOR (VIEWER
and unauthenticated callers get 403 regardless of any token), and the
token is transport, not authority. Retries after an unknown network
result reuse the same command id and body, so the server replays one
command; changing the input or entering a new wait uses a new command
identity. The full request, including the original wait token, stays frozen
across retry; a later wait can start another operation after a receipt.
Revision conflicts reject the command without consuming the
token — refresh and resubmit against the current wait.

## Contract rules

- The tool declares its input request: `status: "input_required"` plus a
  `contract` object. The supported `contract.fields` is a nonempty array of
  unique, nonblank strings (`approval_ref` remains reserved control data);
  optional `contract.prompt` is plain text. A
  wake payload missing any required field is rejected and the token stays
  consumable. Unstructured `input_required`-ish results are business
  feedback, never a parkable wait.
- Results whose outcome is unknown (a write whose effect cannot be
  confirmed) route to reconciliation, never to an input wait.
- Input revisions are immutable and ordered by causal turn boundary;
  restarts replay them without re-asking accepted decisions or redoing
  confirmed actions. Turn budgets, accumulated budgets and deadlines are
  never reset by a wake.

## Operator checklist

- Task stuck in `waiting_input` past its validity: the page shows the
  declared expiry hint; an expired token rejects further wakes —
  cancel the task or re-drive it with a fresh request.
- Repeated `applied` receipts but the task re-parks: the tool asked
  again (a new input request). Each wait has its own token; the command
  list shows one applied receipt per accepted input.
- The panel is read-only but the task is `waiting_input`: check the
  reason line — consumed (already continuing), approval wait, or
  workflow child wait (callback-only).
