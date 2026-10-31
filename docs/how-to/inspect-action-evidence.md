# Inspect one action's evidence receipt

The Task detail page (`/ops-center/tasks/{task_id}`) lists a Run's recorded actions and, for a selected action, its evidence receipt: the recorded facts, the digest that was checked, and what re-checking it found now. Reading a receipt never re-runs a tool, never calls a model, never claims or completes an action, and never rewrites a stored outcome.

## Where the facts come from

- A receipt is a projection over facts the platform already persists — the action ledger's intent and outcome rows, and the run's accepted decisions. It adds no evidence ledger, no write path and no migration.
- The ledger's outcome is the *latest* recorded outcome for that action, not an immutable historical credential. The receipt states the version it read (`outcome_recorded_at`, `read_at`) so a later re-read is visibly a different reading of the same action.
- An action without a recorded intent has no receipt. Intent and claim records precede execution; their existence alone does not prove the tool ran or its effect occurred. A receipt without a recorded outcome preserves that uncertainty.

## The three questions a receipt keeps apart

| Part | Question | What it may say |
| --- | --- | --- |
| Recorded facts | What was recorded? | Action/Run/Task identity, tool and side-effect class, recovery state, outcome, argument and result digests, when the outcome was recorded |
| Digest coverage | What does a digest cover? | The algorithm (`sha256:canonical-json`) and the object that was hashed (the recorded result payload, or the accepted decision's action arguments) |
| Verification | What can be checked now? | `verified`, `mismatch`, `unverifiable` or `unsupported` |

A `verified` result means only that the stored content still hashes to the recorded value. It is **not** evidence that the business goal passed, and it never upgrades an unverified or unrecorded action.

## Reading the verification status

| Status | Meaning |
| --- | --- |
| `verified` | The stored content re-hashes to the recorded digest. Content equality only |
| `mismatch` | The stored content no longer hashes to the recorded digest — the recorded bytes changed, or the digest was written for different content |
| `unverifiable` | There was nothing recorded to re-hash: no recorded digest, no recorded result, or no accepted decision holding the arguments |
| `unsupported` | The recorded shape cannot be re-checked here (for example content that is not canonical-JSON serializable) |

The result state is reported separately from the verification status:

| Result state | Meaning |
| --- | --- |
| `recorded` | An outcome and its content are on record |
| `missing` | No outcome or locally retained content is available, including legacy and reference-only results; no content comparison can be claimed |
| `unknown` | The outcome is explicitly unknown and the action is pending reconciliation |

## The decision reference

Before linking a decision, the query validates its supported schema and full content digest using the same decoder as replay. Corrupt or unsupported decision records fail the query without publishing a claimed link. For a valid record, the tool-call ID, execution ID, ledger key and tool must all match the dispatched identity derived from the Run, decision step and accepted action key. An action outside that chain is reported as `not_recorded` rather than linked to a plausible neighbour. Argument-digest mismatches are reported separately against the validated decision arguments.

## What the receipt never carries

Raw arguments, result bodies, error bodies, engine session ids, claim tokens, credentials and storage addresses never enter a receipt. A recorded `result_ref` is a pointer, not a capability: it does not authorize a read on its own and is not published.

## Access

Access follows the existing observation boundary: an active workspace, then the Run's and its parent Task's visibility, then the VIEWER floor — all before any ledger access. The action must additionally belong to exactly that Run and Task. Missing, foreign-workspace and soft-deleted resources stay indistinguishable to an unauthorized caller.

## Using the page

- Select an action with **查看证据** to load its receipt. **刷新证据** re-reads the same action. **复制稳定引用** copies a Task page URL containing the Run and encoded action key; opening it selects that Run and loads the same receipt even when list order changes. Every lookup repeats authorization.
- Switching Run clears the selection: an action key from another Run is not resolved against the new one, and a late response from a previously selected action is dropped instead of being rendered over the current one.
- A failed read keeps the panel's other facts intact and says what failed; it never renders an empty receipt as if the action had no evidence.
- The page distinguishes an absent receipt from a failed query. Refreshing the actions also refreshes the selected receipt, so a rejected read removes the previously displayed receipt. Clipboard failure leaves the full reference visible for manual copying.

The deterministic SQL/API and Controller dispatch tests cover the recorded identities, digest checks, read-only behavior and authorization boundary. Page tests cover saved-reference lookup and late responses. These checks do not certify PostgreSQL concurrency, real-model quality or a complete process-failure matrix.
