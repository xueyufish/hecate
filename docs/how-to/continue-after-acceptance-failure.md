# Continue a controller run after an acceptance failure

An opt-in continuation policy lets a controller task correct itself when the
declarative delivery gate rejects a final answer. The correction is bounded,
persisted and observable: the rejected answer and its accepted decision stay
immutable, the confirmed business effects are never redone, and the task ends
with the real verdict once the bound is reached.

This is an explicit opt-in. A task without the policy behaves exactly as
before: an acceptance-failed delivery ends the task.

## Declaring the policy

The policy lives in the task's controller options and requires a declared
acceptance standard — corrections are made against a frozen standard, so a
policy without one is refused at submission:

```json
{
  "controller_loop": true,
  "controller": {
    "max_turns": 8,
    "continuation": {"version": 1, "max_continuations": 1}
  },
  "acceptance": {
    "version": 2,
    "required_checks": [
      {"kind": "output_schema", "schema": {"type": "object", "required": ["answer"]}},
      {"kind": "content_contains", "value": "three items"}
    ]
  }
}
```

`max_continuations` is the total number of corrections for the task, across
restarts. It must be a positive integer; booleans, unknown fields and
non-integer values are refused before the task exists. The Runner host accepts
the same block in the run input.

## What may trigger a continuation

Only a delivery whose declared checks are all executable and where at least
one check explicitly failed. The following never continue and keep their real
classification:

- an unverified check (the checker was unavailable or the check kind is not
  supported) — the verdict stays `acceptance_unverified`;
- a delivery without a declared standard — nothing to correct against;
- a cancelled task, a revoked authorization, an exhausted turn budget, an
  expired deadline or a no-progress stop — those stops are terminal;
- a candidate whose recorded decision digest does not match the persisted
  trigger — that is corruption, and the run stops for reconciliation.

There is no model-based evaluator in this first version: only the
deterministic checks of the declared standard can trigger a correction.

## What happens during a continuation

1. The host evaluates the delivered final answer under the frozen standard
   and records the verdict, as it does without the policy.
2. When the verdict is an explicit failure and the bound allows another
   correction, the host persists one **trigger fact** before the next model
   call. The trigger names the rejected candidate's decision identity and
   digest, the standard version and digest, the failed checks, the attempt
   index, the policy digest and the feedback payload. The write is
   consume-once: a dispatch that died after the trigger commit finds the
   stored fact on redelivery and never records a second attempt.
3. The controller re-enters the SAME task and run. The chain replays from
   storage — committed turns cost no model calls and completed actions are
   not re-executed — and the rejected candidate's turn is projected with the
   delivered answer plus one internal control message carrying the stored
   feedback.
4. The corrected answer is evaluated under the same standard. A pass ends the
   task as succeeded; another explicit failure consumes the next attempt
   until the bound is reached, and the task then ends with
   `acceptance_failed`.

The feedback is an internal control message. It is never a human input
revision, never a new user instruction, and it never enters the frozen base
input. The rejected candidate's accepted decision is never rewritten.

## Budgets that survive the correction

A continuation does not reset anything: the durable turn budget, the
accumulated model budget, the task deadline and the no-progress policy all
stay in force. The correction occupies real turns in the same budget. The
no-progress observation window carries across the feedback boundary:
internal control facts never establish progress or reset the streak.

## Observing the trail

The task detail (`GET /api/tasks/{task_id}`) exposes a minimal, read-only
continuation summary: the bound, and per attempt the attempt index, the
rejected candidate's decision identity, the verdict status, the standard
version, the effective turn and the failed checks. Reading it never
evaluates a check, never continues a chain and never moves a task state.

Each consumed attempt also emits a `task_continuation` governance event on
the run's event stream, next to the task's other lifecycle events.

### Reading it on the Task detail page

The Task detail page renders this summary in a dedicated read-only
continuation panel: the recorded bound, the recorded trigger count, and per
attempt the trigger index, the rejected candidate's decision identity and
digest, the verdict status, the standard version, the effective turn and the
failed checks (kind and status only, excluding internal diagnostics). The
panel offers no actions — it never issues a continuation, a resume or a
re-acceptance.

Read the panel with these boundaries:

- The trail is task-level. It does not change when you select a different
  run; the selected run's final delivery/acceptance verdict stays in the
  delivery panel, separate from the historical candidate failures.
- No summary means "no recorded continuation information" — it does not
  imply that the policy is off.
- The trigger count comes from recorded trigger entries. It is not the turn
  count, the cost or the remaining budget, and a recorded trigger does not
  prove that the follow-up model call completed.
- Reaching the recorded bound means no further correction will trigger. It
  is not a task failure. The task's lifecycle state is shown at the top of
  the page; the selected run's delivery/acceptance verdict is a separate fact.
- Missing or unknown values are shown as recorded-unknown, never inferred
  as success. A missing trigger list does not mean zero triggers, and an
  invalid bound cannot establish that the limit was reached.
  The panel shows the projected verdict facts of the failed
  checks, not the internal feedback message itself.

## What this does not do

- It does not re-ask an accepted decision or re-execute a confirmed action.
- It does not extend the task's deadline, turn budget or budget accounting.
- It does not reactivate a task that already reached a terminal state.
- It does not let a cancel or a revocation be overridden by the evaluator:
  once the task moved off `running`, the correction stops.
- It does not grant success on an unverified check, and it does not convert
  an unverified verdict into a correction.
