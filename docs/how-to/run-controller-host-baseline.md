# Run the Controller two-host model baseline

The Controller host baseline records how the same autonomous task behaves on both execution hosts — the platform `controller_loop` dispatch and the standalone runner's controller binding — under one dataset, one tool contract, one core prompt pair, one output schema, one sampling configuration and one scoring rubric. Each host retains its own context scaffolding and tool-result envelope; the complete messages and tool definitions actually sent to the provider are recorded per round. The collector lives in `tests/scenarios/baseline/run_controller_host_baseline.py`; shared fixtures (data, tools, prompts, rubric, report schema) live in `controller_host_spec.py`, the host drivers in `controller_host_platform.py` and `controller_host_runner.py`.

Records land in `tests/scenarios/baselines/` as new timestamped files; previous records are never overwritten. The track is registered under `recorded_tracks` in `tests/scenarios/manifest.yaml`.

## What each case exercises

Every host runs the same case matrix:

- **normal** — the model reads stock, derives the reorder parameters from what it read, submits one governed update, and delivers a structured answer referencing the recorded receipt.
- **approval_resume** — the update requires approval, so the host parks the durable task with the side effect withheld; a decision resumes the original run after host reassembly over the persisted state, completed reads and writes are not repeated, and the recorded read receipt re-enters the model input. The platform reopens its SQL engines/store and resets the in-memory event store; the runner reopens its engine/store and validates the wake reference against its durable local approval authority, including the registered approver and action binding.
- **cancelled** — the platform applies a real `CANCEL` command at the approval park; the runner exercises its real cooperative cancel mid-loop. No write lands on either host.
- **budget_exhausted** — the platform stops through its context-chain token budget (`controller_token_capped`); the runner's durable model-call budget ledger refuses the reservation. Both stop before any model call and any action.
- **unknown_outcome** — a lost tool receipt surfaces each host's real outcome-unknown path: the task converges to `reconciliation_required` with the recorded reason, and no further action executes.

The two hosts classify stops with their own real mechanisms. The rubric asserts each host's actual pre-registered classification (see `EXPECTED_STOPS` in the spec module) rather than forcing a shared vocabulary. Even in real collection mode, stop cases use the deterministic provider and are labeled `deterministic_injection`. Only normal delivery and approval recovery call the selected real provider.

## Run the deterministic matrix (no network)

```bash
python -m tests.scenarios.baseline.run_controller_host_baseline --hermetic
```

This drives both real host assemblies with a deterministic injected provider. It validates the harness end to end — including deriving write parameters from the read receipt — and records the hosts' stop classifications. It is **not** a model-quality result: the record's `source` is `deterministic_injection` and the report says so. The hermetic matrix is also what the report tests in `tests/scenarios/test_controller_host_baseline_report.py` assert against; those tests never touch the network and run in the default CI suite.

## Run real collection (explicit opt-in)

Real provider calls are disabled by default. Enable the gate, select the model, and export the matching provider credential:

```bash
export HECATE_RUN_CONTROLLER_HOST_BASELINE=1
export BASELINE_MODEL=zai/glm-4.5-flash   # or openai/..., anthropic/... — any LiteLLM model name
export ZAI_API_KEY=...                    # credential matching the model prefix
python -m tests.scenarios.baseline.run_controller_host_baseline
```

- Sampling (temperature, token ceiling, timeout, retry count), per-case request/time caps and the latency/token/cost thresholds are pre-registered constants in the collector and its report schema; the schema pins them with `const` so a record cannot drift after the fact. The provider timeout fits within the runner's existing HTTP timeout. Reported cumulative tokens stop further requests at the token limit; unreported usage stays unknown, while the request and elapsed-time caps still apply.
- `--model` overrides `BASELINE_MODEL`; absent both, the collector selects its documented CLI default. `--hosts platform` or `--hosts runner` selects a host in both modes; empty, duplicate and unknown host names are rejected.
- Pacing between real cases is a harness robustness pause for provider rate limits, not a measurement; tune it with `--pacing-seconds`.
- Without the gate or the credential the collector writes an explicit `skipped` record with the reason. A skipped record never reads as a pass.
- The provider adapter converts the provider reply into the hosts' protocols only; it never selects or edits tool calls. Provider-reported usage and model version are recorded verbatim; anything unreported stays `unverified`.
- Real collection is a manual operation: nothing in CI performs network model calls.

## Read a record

- `_meta` — the frozen identity: source (`real_model` or `deterministic_injection`), model, sampling, dataset/prompt/tool/output-schema digests, source HEAD and dirty status, collector-source digests, pre-registered thresholds, and `gate: false`. Cost is always `cost_threshold_conclusion: "unverified"`.
- `_meta.collection` — three separated statements: what the collector completed, whether real-model collection happened (and its per-case quality fields when it did), and what the deterministic-injection runs cover.
- `runs[]` — one entry per host and case: Task/Run identities, per-round model inputs and choices, tool receipts, stable ledger action keys and argument digests, approval decisions, the host's stop classification, rebuild facts, usage, and the shared rubric's verdicts. Inspect each run's source when reading a mixed real/deterministic record.

## Repeated trials: the experiment layer

One single-shot record is one sample. `tests/scenarios/baseline/run_controller_host_experiment.py` wraps the collector in a **versioned experiment definition** that pins — behind a content digest — the scenario identity (data, prompts, tools, output schema, expected stops), the host and case matrix, the model configuration, the repetition count, the resource caps, the scoring rubric version, and the pre-declared per-case criteria and security rules. Every run of a definition produces an aggregate record plus one immutable trial record per repetition.

```bash
# deterministic-injection repetition matrix (no network), built-in definition
python -m tests.scenarios.baseline.run_controller_host_experiment
# real-model repetition: pin the model in a definition file, then
python -m tests.scenarios.baseline.run_controller_host_experiment --definition my-experiment.json
# inspect the pinned fields before running
python -m tests.scenarios.baseline.run_controller_host_experiment --print-default-definition
```

Properties that make the records comparable and auditable:

- **Isolation** — every trial gets its own identity (`trial_id`) and state directory and calls the single-shot collector, so no Task/Run identity, model-call counter or business side-effect state carries across trials. Trial work dirs live under `--state-root` and are transient; the trial records land beside the aggregate in `tests/scenarios/baselines/`.
- **Denominators and failures** — the aggregate keeps explicit denominators (repetitions requested, trials recorded/skipped/failed), per-host-and-case completion rates against those denominators, latency distributions, and a failure ledger. Per-source pass rates use the scheduled repetitions for that source, including missing or failed samples; if none can be evaluated, the rate stays unknown. Skipped real-mode trials, collection errors, criterion failures and trial-level infrastructure losses are all recorded separately. Available latency and reported usage from failed attempts remain in the summaries.
- **Source grouping** — real-model and deterministic-injection evidence are counted in separate `by_source` blocks and separate pass rates; deterministic injection is labeled harness evidence, never model quality.
- **Security apart from quality** — the pre-declared security rules (side-effect ceiling per case, zero writes on stop cases) are evaluated per recorded run into their own `security` block, including expected and missing check counts. A known failure makes `all_passed` false; missing evidence leaves it unknown until every scheduled check passes. No quality average can offset a security failure.
- **Unknown stays unknown** — token totals are summed only over runs whose usage the provider reported; cost has no billing source and stays `unknown`; manual review effort (count, duration) is `null` until someone records it. Nothing is zero-filled.
- **Review records** — manual review is written as a separate immutable file bound to the aggregate's exact file digest (`write_review_record` / `load_review_records` / `review_summary`); reviews never mutate the reviewed record.
- **Comparison groups** — the definition digest is the group identity (`compare_records`). Comparisons verify that each digest matches its embedded definition. Any criteria or input change re-keys the digest, and cross-digest comparisons return `comparable: false` instead of silent deltas. Collection rejects a stale scenario/scoring digest, missing case criteria or an unpinned real model before calling a provider. Historical single-run records are retained unchanged and are not experiment records.

New reports use `controller-host-experiment-2` to distinguish the corrected denominators, security coverage and scoring identity from historical experiment reports. Existing records remain unchanged. Trial files, including trial-level failure records, use exclusive creation and byte-level digests; reviews bind the aggregate's exact bytes, including its line endings.

Real-model repeated trials inherit the single-shot real-mode gate (`HECATE_RUN_CONTROLLER_HOST_BASELINE=1`) and matching provider credential checks; missing either produces explicit skipped trials. Authorized business samples and a cost budget must be agreed before manually enabling real collection; the collector does not verify those approvals. The current task remains a synthetic inventory fixture even when a real model executes it, so that run cannot certify real business work.

## Clarification scenarios: the platform_clarification experiment

`tests/scenarios/baseline/run_controller_clarification_baseline.py` registers a second experiment scenario, `platform_clarification`, on the same versioned definition/aggregate machinery. It runs a **paired** synthetic-inventory probe on the real platform dispatch: the two cases share one dataset, one tool contract, one prompt pair and one output schema, and differ only in whether the read receipt already carries the required procurement order reference (`order_ref`).

- **clarify_missing** — the receipt lacks the field, so the compliant provider calls the governed write tool without it; the tool declares the explicit `input_required` contract (zero side effects so far) and the task parks in the real `waiting_input` state. The driver issues a real `provide_input` command through `TaskControlService`, the durable revision is acknowledged against the parked action's identity, the same Task/Run resumes, the model input carries the platform's `provided_input` supplement, and the follow-up write uses the provided value verbatim before the artifact references the actual receipt.
- **field_complete** — the receipt carries the field; the write goes out with the receipt-derived value and delivery is direct. Zero clarifications is itself an assertion: any park on this side scores `invalid_clarification`.

Per-run scoring keeps the honest verdicts separate from quality: parks, wait durations (harness time around the injected responder — never a human wait), durable input revisions, side effects before the request, and negative verdicts for a guessed field, a premature write, a duplicate write, an invalid clarification and a forged receipt. The independent security block evaluates guessed input and pre-input side effects alongside the write ceiling, preserving known violations even when later collection fails. The rubric checks the same Run across the park and wake, the exact request/write sequence, and a successful write receipt referenced in the final answer. Missing evidence stays unevaluable. The scenario uses `controller-host-clarification-rubric-2`; earlier records retain their original rubric and comparison group.

The default run is the deterministic-injection matrix. Real-model collection is a manual, operator-gated choice: pass `--model <litellm-name>` (or a definition file pinning `source_mode: real_model` with a concrete model) and export the documented gate plus the matching provider credential. Real-mode runs reuse the dual-host adapter (`LiteLLMProvider` with the pre-registered sampling and per-case request/token bounds), label each run's model source as `real_model`, keep the business-sample source (`dataset_source: synthetic_inventory`) and the human responder (`deterministic_injection`) labeled separately, and **never fall back to the deterministic provider** — a model error, timeout or invalid output is recorded as a failed or reconciliation-stopped run inside the denominators. Missing gate or credentials produce explicit skipped trials with the reason. A real_model definition has its own digest, so its records never enter a deterministic comparison group; real human wait times and costs stay unknown without an independent source. The same gate and credential checks apply when a caller supplies an existing real provider directly. Case deadlines retain the cancelled provider request in the round trace. A pinned definition owns its model and repetitions: the CLI rejects combining `--definition` with `--model` or an explicit `--repetitions`, so a conflicting budget cannot be silently ignored.

```bash
python -m tests.scenarios.baseline.run_controller_clarification_baseline            # repeated experiment (deterministic)
python -m tests.scenarios.baseline.run_controller_clarification_baseline --print-default-definition
export HECATE_RUN_CONTROLLER_HOST_BASELINE=1
export BASELINE_MODEL=zai/glm-4.5-flash && export ZAI_API_KEY=...
python -m tests.scenarios.baseline.run_controller_clarification_baseline --model zai/glm-4.5-flash
```

## What a record does not certify

- `gate` stays `false` and `business_value` stays `unverified` on every record; nothing here is a production or release certificate.
- Deterministic-injection runs measure the harness and the hosts' stop classifications, never model quality.
- The approval case rebuilds each host **in process over a persisted SQLite state**. The record says so explicitly; it is not a PostgreSQL or cross-process recovery certification.
- Cost is unknown unless a provider independently bills and reports it; the record keeps it unknown rather than estimating.
- Historical rubric records are retained unchanged. Their original verdicts do not acquire the strengthened rubric's contract, approval-authority or replay checks retroactively.

## Verification pointers

Report invariants (schema, coverage, cost honesty, threshold drift, approval and stop compliance, provider-outage preservation) are asserted in `tests/scenarios/test_controller_host_baseline_report.py`. The experiment layer's invariants (definition pinning, repetition isolation, denominators and failure preservation, source and security grouping, unknown cost/review honesty, comparison-group drift, history compatibility) are asserted in `tests/scenarios/test_controller_host_experiment_report.py`, and the track is registered as `controller_host_experiment` in `tests/scenarios/manifest.yaml`. The clarification scenario's invariants (paired identity, park/wake/resume behavior, negative verdicts, deferred real-model boundary) are asserted in `tests/scenarios/test_controller_clarification_report.py` under the `controller_host_clarification` recorded track. The host assemblies themselves are covered by `tests/test_execution/test_controller_loop_dispatch.py` (platform) and `packages/hecate-runner/tests/test_controller_loop.py` plus `test_controller_continuation.py` (runner).
