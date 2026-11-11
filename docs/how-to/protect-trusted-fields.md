# Protect write fields with trusted provenance

A Controller task's model can propose any argument value, including one it
guessed. A **field provenance profile** lets the host declare that a named
field of a write tool — for example `submit_inventory_update.order_ref` — may
only carry a value with a verified trusted source. The platform checks the
source at the governed execution boundary, before the business call. An
unsourced value is never written: it becomes the standard input request or an
explicit stop, and the verdict is recorded as the blocked action's outcome in
the action ledger.

This guide covers the declaration, the legal sources, the verdicts, the
recovery semantics and the support boundary.

## Declare a profile

The profile is trusted host configuration: it lives in the agent version's
frozen configuration under `field_provenance`. Model output, task input and
tool arguments can never declare, widen or satisfy it.

```json
{
  "field_provenance": {
    "version": 1,
    "profile": "synthetic-inventory-order-ref-1",
    "rules": [
      {
        "tool": "submit_inventory_update",
        "field": "order_ref",
        "resource_keys": ["domain", "sku"],
        "sources": {
          "read_receipts": [{"tool": "query_inventory", "field": "order_ref"}],
          "provided_input": true
        },
        "prompt": "Provide the procurement order reference for this update."
      }
    ]
  }
}
```

- `version` is the declaration format; `profile` names this host
  configuration. The platform digests the normalized declaration, so a rule
  rewritten under the same profile name gets a new identity.
- `resource_keys` name the write arguments that identify the resource. A
  source counts only for exactly the same resource values.
- `sources.read_receipts` lists the read tools whose recorded results may
  supply the value, and the result field that carries it. A write tool cannot
  source its own field.
- `sources.provided_input` admits an authorized human answer to this profile's
  own input request.
- Unknown keys, duplicate rules, reserved control keys (`approval_ref`,
  `wait_token`, `agent_child_result`) and rules without any legal source are
  rejected.

**Pinning.** A Run freezes its agent version configuration in its execution
snapshot at admission, so a task keeps the profile it was submitted under, and
tasks submitted before a profile existed stay unprotected. Every accepted
decision of a protected task also pins the profile's version and digest; the
guard admits sources only for decisions taken under that exact pin. An invalid
declaration stops the task before any model call with `field_provenance_invalid`.

The agent versioning API does not capture `field_provenance` yet. Set it
through host-side provisioning of the agent version. A version created through
the API without it is unprotected.

## Legal sources

All sources are read from durable host facts only.

| Source | Counts when |
|---|---|
| Read receipt | A succeeded, non-pending ledger outcome of a declared read tool in the **same Task and Run**, dispatched by an **earlier** accepted decision. The call matches the write's resource keys, the ledger intent matches the accepted action, and the recorded payload matches both its digest and the result released by tool-result governance. |
| Provided input | A `provide_input` revision of the same Task and Run, **pinned by the current decision**. The applied wake receipt must identify that revision and bind the exact consumed request action and contract digest. The recorded request must belong to this profile and match the tool, field and resource. |

These never supply a value:

- model text
- provenance claims inside arguments, such as a `*_source` argument
- tools the rule does not declare
- results produced by the same decision as the write, which covers a correct
  guess made before the result existed
- other workspaces, Tasks or Runs
- other resources
- live supplements
- answers to other requests
- revisions the current decision does not pin, which covers pending, future
  and other-task revisions

A later answer never authorizes an earlier decision.

## Verdicts

The guard runs after permission, policy, approval and the action-ledger claim,
immediately before the business call. Authorization never vouches for where a
value came from, and a sourced value never bypasses authorization.

| Facts | Verdict | Business effect |
|---|---|---|
| Exactly one sourced value, equal to the argument | allow — the business call runs | as the tool does |
| Required value missing and the rule accepts provided input | `input_required` with `{"fields": [...], "prompt": ..., "field_provenance": {binding}}` parks the task in `waiting_input` | none |
| Required value missing and the rule only accepts read receipts | Refusal `unbound` | none |
| Sources disagree, or the argument contradicts the sourced value | refusal `conflict`; task `failed`, `error_category: field_provenance_conflict` | none |
| Missing resource key, malformed source value, a receipt naming another resource, or the call is not the verified action of a decision under this profile | refusal `unbound`; task `failed`, `field_provenance_unbound` | none |
| Decision chain, ledger or input history unreadable or inconsistent | refusal `unavailable`; task `reconciliation_required`, `field_provenance_unavailable` | none |

The refusal envelope is recorded as the blocked action's outcome. It names the
profile, tool, fields, reason and action identity, never a protected or
sourced value. The task's `result.field_provenance` block repeats it. After a
park, answer the request through the task detail page as described in
[Handle human input waits](handle-task-input-waits.md). The model's next write
with the answered value is then verified against that answer.

## Recovery and replay

- Verdicts are action outcomes in the durable ledger. A redelivered or rebuilt
  host replays them instead of re-judging, so a recorded refusal ends the task
  with the same category and a recorded input request does not park twice.
- A completed write executes exactly once. A sourced write whose outcome is
  unknown stays pending reconciliation like any other unknown write.
- The guard is rebuilt per dispatch from durable facts. A wake after a full
  host rebuild verifies the answer exactly like an in-process wake.
- The host records the first governed read projection before returning it to
  the Controller. Replay reapplies tool-result governance, and a receipt can
  supply a protected field only if both projections released it. Business
  tool results cannot issue the host's provenance request or refusal markers.

## Support boundary

- Enforced only for platform-local Controller tasks (`controller_loop`).
  Other task dispatch paths refuse a task whose agent version declares a
  profile, with `field_provenance_unsupported`. Chat sessions, direct gateway
  calls and Runner hosts do not consult the profile.
- Accepted offline with deterministic stand-ins and synthetic inventory tools
  in the `platform_trusted_field` scenario family (see
  [Run the Controller two-host model baseline](run-controller-host-baseline.md)).
  This is not a real-model quality result or a production certification.
  A separate gated family, `platform_trusted_field_model`, re-runs the two
  delivery cases with a real model; it records what the model chose apart
  from what the guard blocked.
- Values are compared as exact strings at top-level argument and result keys.
  Nested paths, normalization and partial matches are not supported.
- Approval runs before the provenance check: an approved write without a source
  is still blocked and becomes an input request (or a refusal).

The implementation is `src/hecate/execution/field_provenance.py`, consumed by
`PlatformGovernance.execute_tool` and the Controller dispatch. Coverage lives
in `tests/test_execution/test_field_provenance.py` and
`tests/scenarios/test_controller_trusted_field.py`.
