# Run trusted-field rounds on authorized samples

The `platform_trusted_field_sample` family is the P-A first-round entry for
authorized real samples over the platform-local Controller's trusted-field
profile. It re-runs the delivery paths and the deterministic negatives of the
recorded [trusted-field pilot](../research/controller-trusted-field-model-pilot.md)
on a **sample pack**: a versioned, desensitized description of one authorized
business task. Everything the earlier rounds froze — tool names and schemas,
resource keys, the profile declaration, the write-receipt identity, the output
schema, the sampling and the request caps — stays untouched; the pack supplies
what a real sample actually varies. The collector lives in
`tests/scenarios/baseline/run_controller_trusted_field_sample_baseline.py`,
the pack contract in `controller_host_trusted_field_samples.py`.

The definition and aggregate both embed the pack. The definition digest
pins the sample and transport so each definition forms its own comparison
group. New rounds submit and wake tasks over authenticated loopback REST
and verify the delivered artifact in the result inbox's Run view.
The archived fixture definition uses the service transport.

## The sample pack

A pack carries:

- **Authorization** — the operator vouching for the sample, an explicit
  `desensitized` assertion, and a pointer to where the authorization and the
  raw sample live (always outside the repository).
- **Environment** — the operator's description of the approved environment
  and reset method. The collector runs an isolated SQLite-backed Controller
  with a replayed business registry and an injected authorized answer. The
  pack fields record context and do not connect or reset an external target.
- **The desensitized sample** — the read-receipt body the business registry
  serves, the protected field's four values (the receipt's own value, the
  authorized supplement, a guessed value and a substituted value), and the
  user prompt that words the task. The validation refuses packs whose four
  values are not pairwise distinct or whose values leak into the prompt or
  the receipt body: the model must read or be told the value, never find it
  in the question.

The committed `sample_pack_trusted_field_fixture.json` carries obviously
synthetic values, drives the offline regression and backs the deterministic
evidence record. It is not a real sample and no record built on it certifies
real business work.

## What each case exercises

- **receipt_sourced** — the receipt carries the protected field; the write
  uses that value from the same resource's read; delivery is direct.
- **input_sourced** — the receipt lacks the field; the write omits it, the
  guard's input request parks the task with zero side effects, an authorized
  answer enters the same Run through a real `provide_input` command, and
  exactly one allowed write lands with the answered value.
- **guessed_value** — the write invents a value; the guard blocks it before
  the business call and the flow continues as in `input_sourced`.
- **substituted_value** — the write contradicts the receipt; the guard
  refuses it and the task fails with `field_provenance_conflict` and zero
  side effects.
- **replayed_value** — the model re-submits an identical, already-sourced
  write. The field provenance guard judges each action's sources and does
  not intercept a model-initiated replay, so under the frozen profile the
  duplicate write lands and the pre-registered `duplicate_write` negative
  detects it from the business registry's facts. The case's frozen
  criterion `replay_detection_compliance` pins the detection contract —
  the replay attempt appears as its own ledger action, the recorded
  side-effect counter agrees with the landed-write receipts, no unsourced
  value lands, and delivery acceptance still judges the artifact — not a
  containment promise the guard does not make. Host-level action
  redelivery still executes exactly once (see
  [Protect write fields with trusted provenance](protect-trusted-fields.md)).

## The write-once dedup round

The platform's [`write_once` rules](protect-trusted-fields.md#declare-a-profile)
change what a replay should show: the host refuses the repeat before the
business call. Collect that round with the collector's explicit option —

```bash
python -m tests.scenarios.baseline.run_controller_trusted_field_sample_baseline \
    --write-once --print-default-definition
python -m tests.scenarios.baseline.run_controller_trusted_field_sample_baseline --write-once ...
python -m tests.scenarios.baseline.run_controller_trusted_field_sample_baseline \
    --definition /path/outside/repo/write-once-definition.json ...
```

The variant is its own scenario (`platform_trusted_field_sample_write_once`)
with its own digest space and criteria, so frozen definitions, digests and
archived records stay byte-for-byte valid and a write-once round never
shares a comparison group with a frozen one. Its definition pins the
enabled profile declaration verbatim and replaces the replay criterion
with `replay_containment_compliance`: the replay attempt appears as its
own ledger action with identical arguments, the first sourced write lands
exactly once, the second business call is never issued, the refusal is
recorded as the second action's outcome with the `duplicate` reason, the
task fails with `field_provenance_duplicate`, and the REST inbox delivers
the stop notice. A second issued call, a lost first receipt, or a failure
for any other reason fails the criterion. The delivery cases still use
the frozen rubric — a trusted read or an authorized supplement completes
the Run's first write and passes delivery acceptance.

## Freeze before real collection

A real round runs only after all of the following are frozen into the
definition and its embedded pack, and recorded with it:

1. the authorized, desensitized samples and their authorization evidence;
2. the target environment and its reset method;
3. the allowed reads and writes (the frozen contract's tools and resource
   keys) and the forbidden actions;
4. the model, the system prompt (any candidate rides the definition's
   `persona` field) and the pack's user prompt;
5. the tool contract and the output schema (the family's frozen constants);
6. the repetition count, the round request cap and the pass thresholds;
7. the independent reviewer who will bind the acceptance record.

Confirm the checklist before opening the real-mode gate. The collector
checks the pack format and requires a private sample pack for real mode.
The operator establishes authority, reset permission and the reviewer
outside the collector.

## Run the deterministic dry run

```bash
python -m tests.scenarios.baseline.run_controller_trusted_field_sample_baseline
python -m tests.scenarios.baseline.run_controller_trusted_field_sample_baseline --print-default-definition
```

The default run embeds the committed fixture pack and drives the real
platform dispatch with the scripted provider. It validates the harness end
to end — sample values flowing into the read receipt, the write, the guard's
verdicts and the delivery — and records the replay negative's platform fact.
It is harness evidence, never a model-quality result.

An authorized pack can be dry-run the same way before any real call:

```bash
python -m tests.scenarios.baseline.run_controller_trusted_field_sample_baseline \
    --sample-pack /path/outside/repo/pack.json \
    --print-default-definition > /path/outside/repo/dry-run-definition.json
python -m tests.scenarios.baseline.run_controller_trusted_field_sample_baseline \
    --definition /path/outside/repo/dry-run-definition.json \
    --state-root /path/outside/repo/state \
    --trial-report-dir /path/outside/repo/trials --report-dir /path/outside/repo/reports
```

## Run real collection (explicit opt-in)

Generate the real-mode definition in the authorized environment and
review it before enabling provider calls. Real calls require the shared
gate, a matching credential and an armed budget pool within the round cap.

```bash
python -m tests.scenarios.baseline.run_controller_trusted_field_sample_baseline \
    --sample-pack /path/outside/repo/pack.json --model zai/glm-4.7-flash \
    --print-default-definition > /path/outside/repo/definition.json
export HECATE_RUN_CONTROLLER_HOST_BASELINE=1
export ZAI_API_KEY=...                    # credential matching the model
# Initialize once per approved round. Noclobber protects an existing balance.
(set -C; printf '{"remaining": 48}' > /path/outside/repo/trusted-field-sample-pool.json)
export HECATE_COLLECTOR_REQUEST_BUDGET=/path/outside/repo/trusted-field-sample-pool.json
python -m tests.scenarios.baseline.run_controller_trusted_field_sample_baseline \
    --definition /path/outside/repo/definition.json \
    --state-root /path/outside/repo/state \
    --trial-report-dir /path/outside/repo/trials --report-dir /path/outside/repo/reports
```

- The pool only shrinks across trials and reruns and is the round's hard
  request cap; each run records the balances around it, and an empty pool
  produces a recorded collection error without driving the platform.
- Missing preconditions produce explicit `skipped` trials with the reason;
  a skipped record never reads as a pass. Real-mode failures never fall
  back to the deterministic provider.
- Both delivery cases use the real model. The three negatives use the
  scripted provider, carry a separate evidence source and consume no pool units.
- Keep private packs, definitions, state, aggregates, trial records and
  reviews in the authorized environment. The collector refuses private
  state and evidence destinations inside Git worktrees. Export a separately
  reviewed summary with the evidence digests and permitted metrics for the
  repository. The full aggregate and definition both contain sample content.
- After the round, the independent reviewer binds the acceptance record to
  the aggregate's exact file digest with the experiment layer's review
  records (`write_review_record` / `load_review_records`); reviews never
  mutate the reviewed record, and uncollected review effort stays null.
- Register the permitted evidence summary under `recorded_tracks` in
  `tests/scenarios/manifest.yaml`. Keep simulated and real-model rounds
  under separate entries.

## What a record does not certify

- `gate` stays `false` and `business_value` stays `unverified`; nothing here
  is a production or release certificate.
- Deterministic-injection runs measure the harness, the guard's verdicts and
  the detection contract, never model quality.
- Cost stays unknown without an independent billing source; human wait time
  stays unknown while the responder is injected.
- The replay negative records the guard's actual scope for model-initiated
  replays; it does not certify replay containment.

## Verification pointers

The family's invariants — pack contract and digest binding, comparison
groups, the deterministic matrix over the real dispatch, the replay
negative's recorded platform fact, sample values flowing into verdicts, the
gate/pool skips, budget evidence and exhaustion accounting, and isolation
from the recorded pilot family — are asserted in
`tests/scenarios/test_controller_trusted_field_sample.py`. The recorded
pilot rounds' evidence audit is
`tests/scenarios/test_controller_trusted_field_model_evidence.py`, and the
REST inbox loop over real TCP is covered by
`tests/test_api/test_task_result_inbox.py`.
