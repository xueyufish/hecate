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

## What a record does not certify

- `gate` stays `false` and `business_value` stays `unverified` on every record; nothing here is a production or release certificate.
- Deterministic-injection runs measure the harness and the hosts' stop classifications, never model quality.
- The approval case rebuilds each host **in process over a persisted SQLite state**. The record says so explicitly; it is not a PostgreSQL or cross-process recovery certification.
- Cost is unknown unless a provider independently bills and reports it; the record keeps it unknown rather than estimating.
- Historical rubric records are retained unchanged. Their original verdicts do not acquire the strengthened rubric's contract, approval-authority or replay checks retroactively.

## Verification pointers

Report invariants (schema, coverage, cost honesty, threshold drift, approval and stop compliance, provider-outage preservation) are asserted in `tests/scenarios/test_controller_host_baseline_report.py`. The host assemblies themselves are covered by `tests/test_execution/test_controller_loop_dispatch.py` (platform) and `packages/hecate-runner/tests/test_controller_loop.py` plus `test_controller_continuation.py` (runner).
