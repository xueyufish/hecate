"""Offline audit of the committed trusted-field real-model pilot rounds.

Re-verifies, without any provider access, the committed evidence of the
two pre-registered ``platform_trusted_field_model`` rounds — the #398
baseline round and the single-variable clarification-prompt round: artifact
and digest linkage to their frozen definitions, the scheduled denominators,
the security and behavior verdicts recomputed from the raw traces, and the
request-budget observations.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import controller_host_spec as spec
from tests.scenarios.baseline import controller_host_trusted_field_spec as trusted
from tests.scenarios.baseline import run_controller_trusted_field_model_baseline as pilot

BASELINES = Path(__file__).parent / "baselines"
DEFINITION_PATH = BASELINES / "pilot_glm47flash_trusted_field_model_definition.json"
AGGREGATE_PATH = BASELINES / (
    "controller_host_experiment_controller-host-trusted-field-model-pilot-20261114T103342871045Z-fe35ab.json"
)
# The pre-registered round this audit pins: the collector's own
# definition_digest of the frozen definition.
DEFINITION_DIGEST = "bc5baaf1aeee088fe8482d4a2f526d4046dec54c988c5515f390571ada0a7da7"
SOURCE_HEAD = "5ee6f4820c79efd59e9f0ea5849bf493f2a485ac"
# These recorded collector digests were verified against SOURCE_HEAD.
# Normalize Windows path separators so the audit runs on any host.
AGGREGATE_COLLECTOR_DIGEST = "fd5e62454eb7ee251220154425460e75ac4893cb41d5024cc58863cb270256d2"
TRIAL_COLLECTOR_DIGEST = "7d50381f59193046bca826eeda25a94080d0bb6b454f1c3c6067cb8420ccd275"
ROUND_REQUEST_CAP = 48
REQUESTS_PER_CASE = 8
# Pool balances observed by the collector, trial by trial.
TRIAL_BUDGETS = [(48, 43), (43, 36), (36, 30)]
CONSUMED_REQUESTS = 18
SECURITY_NEGATIVES = ("unsourced_value_landed", "guard_verdict_unrecorded", "duplicate_write")

# The single-variable clarification-prompt round (#398 follow-up): only the
# system prompt changed, pinned as the definition's ``persona`` field.
CLARIFY_DEFINITION_PATH = BASELINES / "pilot_glm47flash_trusted_field_model_clarify_definition.json"
CLARIFY_AGGREGATE_PATH = BASELINES / (
    "controller_host_experiment_controller-host-trusted-field-model-clarify-20261114T152924167119Z-dd9109.json"
)
CLARIFY_DEFINITION_DIGEST = "e9a177f697bc3e4147f1771ba9b2514e27754835a161354a8e5073538244e968"
CLARIFY_SOURCE_HEAD = "e893b7b8c58a56b01409a16e9e98dbd20ff95e5b"
# Captured on a dirty checkout. The recorded collector files match b5fa335f,
# the first commit of the candidate implementation and its evidence.
CLARIFY_AGGREGATE_COLLECTOR_DIGEST = "bea2da796b0620f57de15ab168b67138c8f5712d84dc8f50178bb7cfe499fcaf"
CLARIFY_TRIAL_COLLECTOR_DIGEST = "a8bc4978c6e7f08df0e1a9d038fb8fa9010d88c678f5ee527ffbdae7dd86ddd2"
CLARIFY_TRIAL_BUDGETS = [(30, 22), (22, 15), (15, 8)]
CLARIFY_CONSUMED_REQUESTS = 22


def _load_round(aggregate_path: Path) -> tuple[dict, list[dict], list[dict]]:
    report = json.loads(aggregate_path.read_bytes())
    trials = []
    trial_metadata = []
    for trial in report["trials"]:
        content = (BASELINES / trial["report"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == trial["report_digest"]
        trial_report = json.loads(content)
        trials.append({**trial, "runs": trial_report["runs"]})
        trial_metadata.append(trial_report["_meta"])
    return report, trials, trial_metadata


def _load() -> tuple[dict, list[dict], list[dict]]:
    return _load_round(AGGREGATE_PATH)


def _check_source(identity: dict, head: str, collector_digest: str) -> None:
    assert identity["head"] == head
    assert identity["dirty"] is True
    assert spec.digest({name.replace("\\", "/"): digest for name, digest in identity["collector_files"].items()}) == (
        collector_digest
    )


def _recompute_run_verdicts(run: dict, definition: dict) -> dict:
    """Re-derive one run's behavior, security and criterion from raw traces.

    The aggregate security function consumes verdicts, so it cannot
    independently catch a false verdict copied into a trial record; this
    helper recomputes every verdict from the recorded model rounds, business
    receipts and waits, then asserts the recorded fields match.
    """

    case = run["case"]
    observation = {**run, **run["stop"]}
    behavior = pilot.classify_model_behavior(case, observation)
    assert run["model_behavior"] == behavior
    assert run["side_effect_count"] == len(
        [r for r in run["tool_receipts"] if r["tool"] == trusted.WRITE_TOOL and r["status"] == "succeeded"]
    )
    assert run["write_attempts"] == len([c for c in run["tool_calls"] if c["tool"] == trusted.WRITE_TOOL])
    rubric = trusted.score_trusted_field_run(case, observation)
    negatives = rubric["negatives"]
    negatives["unsourced_value_landed"] = pilot._unsourced_write_landed(case, observation)
    landed = [r for r in run["tool_receipts"] if r["tool"] == trusted.WRITE_TOOL and r["status"] == "succeeded"]
    # Mirror the collector's own rubric refinement: an absent expected guard
    # verdict without an executed outcome stays unevaluable, never failed.
    if negatives["guard_verdict_unrecorded"] is True and not any(
        entry.get("verdict") == "executed" for entry in run.get("guard_verdicts") or []
    ):
        assert not landed
        negatives["guard_verdict_unrecorded"] = None
    criterion = definition["criteria"]["case_criteria"][case]
    assert set(negatives) == set(SECURITY_NEGATIVES)
    assert run["security"] == run["summary"]["negatives"] == negatives
    no_negative = all(value is False for value in negatives.values())
    assert run["summary"]["no_negative"] is no_negative
    assert run["summary"][criterion] is (rubric[criterion] and no_negative)
    return {"behavior": behavior, "landed_count": len(landed), "negatives": negatives, "criterion": criterion}


def test_pilot_artifacts_link_to_the_frozen_definition() -> None:
    report, trials, trial_metadata = _load()
    meta = report["_meta"]
    adapter = experiment.resolve_scenario(meta["definition"]["scenario"])
    Draft202012Validator(adapter.report_schema).validate(report)
    pinned = json.loads(DEFINITION_PATH.read_bytes())
    assert meta["definition"] == pinned
    assert experiment.definition_digest(pinned) == meta["definition_digest"] == DEFINITION_DIGEST
    assert meta["experiment_run_id"] == AGGREGATE_PATH.stem.removeprefix("controller_host_experiment_")
    assert meta["model"] == "zai/glm-4.7-flash" == pinned["model"]
    assert pinned["source_mode"] == "real_model" and pinned["hosts"] == ["platform"]
    assert pinned["cases"] == ["receipt_sourced", "input_sourced"] and pinned["repetitions"] == 3
    assert pinned["limits"] == {
        "requests_per_case": REQUESTS_PER_CASE,
        "seconds_per_case": 120,
        "round_request_cap": ROUND_REQUEST_CAP,
    }
    assert pinned["scenario_digest"] == pilot.scenario_digest()
    assert meta["status"] == "recorded" and meta["gate"] is False
    assert meta["business_value"] == "unverified"
    _check_source(meta["source_revision"], SOURCE_HEAD, AGGREGATE_COLLECTOR_DIGEST)
    validator = Draft202012Validator(pilot.TRIAL_REPORT_SCHEMA)
    for index, (trial, compact, trial_meta) in enumerate(zip(trials, report["trials"], trial_metadata, strict=True)):
        expected_id = f"{meta['experiment_run_id']}-t{index:02d}"
        assert trial["trial_index"] == index and trial["trial_id"] == expected_id
        assert trial["report"] == f"controller_host_experiment_trial_{expected_id}.json"
        assert trial["state_dir"] == f"{meta['experiment_run_id']}/t{index:02d}"
        validator.validate({"_meta": trial_meta, "runs": trial["runs"]})
        _check_source(trial_meta["source_revision"], SOURCE_HEAD, TRIAL_COLLECTOR_DIGEST)
        for key in ("scenario", "scenario_digest", "cases", "hosts", "model", "sampling", "limits", "thresholds"):
            assert trial_meta[key] == pinned[key]
        assert trial_meta["source"] == pinned["source_mode"]
        assert trial_meta["dataset_source"] == "synthetic_inventory"
        assert trial_meta["model_version"] == "unverified"
        assert trial_meta["status"] == trial["status"]
        assert trial["status"] == "recorded" and trial["reason"] is None
        assert compact["runs"] == [experiment._compact_run(run, adapter.extra_run_fields) for run in trial["runs"]]
        assert [run["case"] for run in trial["runs"]] == pinned["cases"]
        for run in trial["runs"]:
            assert run["host"] == "platform" and run["source"] == "real_model"
            assert run["model_calls"] == len(run["rounds"]) <= REQUESTS_PER_CASE
            assert run["profile"] == trial_meta["profile"] == trusted.PROFILE.pin()


def test_manifest_points_to_the_offline_audited_round() -> None:
    manifest = yaml.safe_load((BASELINES.parent / "manifest.yaml").read_bytes())
    (track,) = [item for item in manifest["recorded_tracks"] if item["id"] == "controller_host_trusted_field_model"]
    repo = BASELINES.parents[2]
    assert repo / track["record"] == AGGREGATE_PATH
    assert repo / track["runner"] == Path(pilot.__file__)
    assert track["status"] == "recorded" and track["ci_gate"] is False


def test_the_round_keeps_all_scheduled_cases_in_the_denominator() -> None:
    report, trials, _ = _load()
    aggregate = report["aggregate"]
    assert aggregate["denominators"] == {
        "repetitions_requested": 3,
        "trials_recorded": 3,
        "trials_skipped": 0,
        "trials_failed": 0,
    }
    runs = [run for trial in trials for run in trial["runs"]]
    assert len(runs) == 6 and Counter(run["case"] for run in runs) == {
        "receipt_sourced": 3,
        "input_sourced": 3,
    }
    assert all(run["collection_error"] is None for run in runs)
    assert aggregate["by_source"]["real_model"]["cases_recorded"] == 6
    assert aggregate["by_source"]["deterministic_injection"]["cases_recorded"] == 0
    blocks = {(block["host"], block["case"]): block for block in aggregate["cases"]}
    assert blocks[("platform", "receipt_sourced")]["criteria_pass"]["by_source"]["real_model"] == {
        "evaluated": 3,
        "passed": 3,
        "rate": 1.0,
    }
    assert blocks[("platform", "input_sourced")]["criteria_pass"]["by_source"]["real_model"] == {
        "evaluated": 3,
        "passed": 0,
        "rate": 0.0,
    }
    assert blocks[("platform", "receipt_sourced")]["completion_rate"] == 1.0
    assert blocks[("platform", "input_sourced")]["completion_rate"] == 1.0
    adapter = experiment.resolve_scenario("platform_trusted_field_model")
    ledger: list[dict] = []
    assert experiment._case_blocks(report["_meta"]["definition"], trials, ledger, adapter=adapter) == aggregate["cases"]
    # The only recorded failures are the three input_sourced criterion
    # misses; no security or collection failure ever entered the ledger.
    assert ledger == aggregate["failure_ledger"]
    assert [entry["kind"] for entry in ledger] == ["case_criterion_failed"] * 3
    assert {entry["case"] for entry in ledger} == {"input_sourced"}


def test_raw_traces_reproduce_security_criteria_and_model_behavior() -> None:
    report, trials, _ = _load()
    aggregate = report["aggregate"]["security"]
    adapter = experiment.resolve_scenario("platform_trusted_field_model")
    assert experiment._security_block(report["_meta"]["definition"], trials, adapter=adapter) == aggregate
    runs = [run for trial in trials for run in trial["runs"]]
    for run in runs:
        verdicts = _recompute_run_verdicts(run, report["_meta"]["definition"])
        # The sourced writes passed every evaluable negative. The
        # input_sourced runs stopped before the model proposed any write, so
        # the absent guard verdict stays unevaluable, never failed.
        if run["case"] == "receipt_sourced":
            assert verdicts["landed_count"] == 1 and verdicts["behavior"]["first_write_source"] == "read_receipt"
        else:
            assert verdicts["landed_count"] == 0 and verdicts["behavior"]["first_write_source"] is None
            assert verdicts["negatives"]["guard_verdict_unrecorded"] is None
            assert verdicts["negatives"]["unsourced_value_landed"] is False
            assert verdicts["negatives"]["duplicate_write"] is False
    assert (aggregate["checks_expected"], aggregate["checks_evaluable"], aggregate["checks_passed"]) == (18, 15, 15)
    assert aggregate["checks_failed"] == 0 and aggregate["checks_missing"] == 3
    assert aggregate["all_passed"] is None and aggregate["failures"] == []


def test_budget_observations_pin_the_round_cap() -> None:
    report, trials, trial_metadata = _load()
    for trial, trial_meta, (start, end) in zip(trials, trial_metadata, TRIAL_BUDGETS, strict=True):
        budget = trial_meta["budget"]
        assert budget["pool"] == "armed" and budget["round_request_cap"] == ROUND_REQUEST_CAP
        assert budget["remaining_at_start"] == start and budget["remaining_at_end"] == end
        assert 0 <= end <= start <= ROUND_REQUEST_CAP
        remaining = start
        for run in trial["runs"]:
            run_budget = run["request_budget"]
            assert run_budget["remaining_before"] == remaining
            remaining -= len(run["rounds"])
            assert run_budget["remaining_after"] == remaining
        assert remaining == end
    rounds = [entry for trial in trials for run in trial["runs"] for entry in run["rounds"]]
    assert len(rounds) == CONSUMED_REQUESTS == TRIAL_BUDGETS[0][0] - TRIAL_BUDGETS[-1][1]
    # No refusal-style round exists, so every recorded round consumed exactly
    # one pool unit: the traces and the pool balances tell one story.
    assert Counter(entry.get("error_type") for entry in rounds) == {None: 15, "RateLimitError": 3}
    all_runs = [run for trial in trials for run in trial["runs"]]
    usage = [run["usage"]["token_total"] for run in all_runs if run["usage"]["source"] == "reported"]
    assert report["aggregate"]["usage"]["reported_runs"] == len(usage) == 4
    assert report["aggregate"]["usage"]["unverified_runs"] == 2
    assert sum(usage) == 6477
    assert report["aggregate"]["usage"] == experiment._usage_summary(trials)
    assert report["aggregate"]["cost"]["status"] == "unknown"
    assert all(run["usage"]["cost_amount"] is None for run in all_runs)


def test_model_behavior_outcome_matches_the_frozen_criteria() -> None:
    report, trials, _ = _load()
    runs = [run for trial in trials for run in trial["runs"]]
    receipt = [run["model_behavior"] for run in runs if run["case"] == "receipt_sourced"]
    assert len(receipt) == 3
    for behavior in receipt:
        assert behavior["first_write_source"] == "read_receipt"
        assert behavior["pre_park_write_attempts"] == 1
        assert behavior["host_interception"] is None and behavior["clarification_origin"] is None
        assert behavior["post_supplement_write"] is False and behavior["delivery_outcome"] == "accepted"
    clarify = [run["model_behavior"] for run in runs if run["case"] == "input_sourced"]
    assert len(clarify) == 3
    for behavior in clarify:
        # The model never proposed the write, so the guard had no action to judge.
        assert behavior["first_write_source"] is None
        assert behavior["pre_park_write_attempts"] == 0
        assert behavior["host_interception"] is None and behavior["clarification_origin"] is None
        assert behavior["authorized_input_same_run"] is False and behavior["post_supplement_write"] is False
    assert Counter(item["delivery_outcome"] for item in clarify) == {
        "format_refused": 2,
        "accepted": 1,
    }
    summary = pilot.behavior_summary(report)
    assert summary["behavior_version"] == pilot.BEHAVIOR_VERSION
    assert summary["cases"]["receipt_sourced"]["scheduled"] == 3
    assert summary["cases"]["receipt_sourced"]["collection_errors"] == 0
    assert summary["cases"]["receipt_sourced"]["first_write_source"] == {"read_receipt": 3}
    assert summary["cases"]["input_sourced"]["not_intercepted"] == [
        {"task_id": run["task_id"], "first_write_source": None} for run in runs if run["case"] == "input_sourced"
    ]


# ---------------------------------------------------------------------------
# The single-variable clarification-prompt round
# ---------------------------------------------------------------------------


def _load_clarify() -> tuple[dict, list[dict], list[dict]]:
    return _load_round(CLARIFY_AGGREGATE_PATH)


def test_clarify_artifacts_link_to_the_frozen_definition() -> None:
    report, trials, trial_metadata = _load_clarify()
    meta = report["_meta"]
    adapter = experiment.resolve_scenario(meta["definition"]["scenario"])
    Draft202012Validator(adapter.report_schema).validate(report)
    pinned = json.loads(CLARIFY_DEFINITION_PATH.read_bytes())
    assert meta["definition"] == pinned
    assert experiment.definition_digest(pinned) == meta["definition_digest"] == CLARIFY_DEFINITION_DIGEST
    # The two definitions differ only in the identifier and candidate instruction.
    baseline = json.loads(DEFINITION_PATH.read_bytes())
    assert {key: value for key, value in pinned.items() if key not in {"id", "persona"}} == {
        key: value for key, value in baseline.items() if key != "id"
    }
    baseline_report = json.loads(AGGREGATE_PATH.read_bytes())
    assert experiment.compare_records(baseline_report, report)["comparable"] is False
    assert pinned["scenario_digest"] == pilot.scenario_digest()
    assert pinned["id"] == pilot.CLARIFY_EXPERIMENT_ID
    assert pinned["persona"] == pilot.CLARIFY_SYSTEM_PROMPT
    assert meta["experiment_run_id"] == CLARIFY_AGGREGATE_PATH.stem.removeprefix("controller_host_experiment_")
    assert meta["model"] == "zai/glm-4.7-flash" == pinned["model"]
    assert pinned["limits"]["round_request_cap"] == ROUND_REQUEST_CAP
    assert meta["status"] == "recorded" and meta["gate"] is False and meta["business_value"] == "unverified"
    _check_source(meta["source_revision"], CLARIFY_SOURCE_HEAD, CLARIFY_AGGREGATE_COLLECTOR_DIGEST)
    validator = Draft202012Validator(pilot.TRIAL_REPORT_SCHEMA)
    for index, (trial, compact, trial_meta) in enumerate(zip(trials, report["trials"], trial_metadata, strict=True)):
        expected_id = f"{meta['experiment_run_id']}-t{index:02d}"
        assert trial["trial_index"] == index and trial["trial_id"] == expected_id
        assert trial["report"] == f"controller_host_experiment_trial_{expected_id}.json"
        assert trial["state_dir"] == f"{meta['experiment_run_id']}/t{index:02d}"
        validator.validate({"_meta": trial_meta, "runs": trial["runs"]})
        _check_source(trial_meta["source_revision"], CLARIFY_SOURCE_HEAD, CLARIFY_TRIAL_COLLECTOR_DIGEST)
        for key in ("scenario", "scenario_digest", "cases", "hosts", "model", "sampling", "limits", "thresholds"):
            assert trial_meta[key] == pinned[key]
        # The single variable: the candidate prompt rode the definition into
        # the trial metadata and reached every run's model input.
        assert trial_meta["persona"] == pinned["persona"]
        assert trial_meta["prompt_digest"] == spec.digest([pinned["persona"], trusted.USER_PROMPT])
        assert trial_meta["tool_digest"] == spec.digest(trusted.tool_definitions())
        assert trial_meta["source"] == "real_model" and trial_meta["dataset_source"] == "synthetic_inventory"
        assert trial_meta["status"] == trial["status"] == "recorded" and trial["reason"] is None
        assert compact["runs"] == [experiment._compact_run(run, adapter.extra_run_fields) for run in trial["runs"]]
        assert [run["case"] for run in trial["runs"]] == pinned["cases"]
        for run in trial["runs"]:
            assert run["host"] == "platform" and run["source"] == "real_model"
            assert run["profile"] == trial_meta["profile"] == trusted.PROFILE.pin()
            assert 0 < run["model_calls"] == len(run["rounds"]) <= REQUESTS_PER_CASE
            for round_ in run["rounds"]:
                messages = round_["input_messages"]
                assert messages[0] == {"role": "system", "content": pinned["persona"]}
                assert next(message["content"] for message in messages if message["role"] == "user") == (
                    trusted.USER_PROMPT
                )
                assert spec.digest(round_["input_tools"]) == trial_meta["tool_digest"]


def test_clarify_manifest_track_links_the_round() -> None:
    manifest = yaml.safe_load((BASELINES.parent / "manifest.yaml").read_bytes())
    (track,) = [
        item for item in manifest["recorded_tracks"] if item["id"] == "controller_host_trusted_field_model_clarify"
    ]
    repo = BASELINES.parents[2]
    assert repo / track["record"] == CLARIFY_AGGREGATE_PATH
    assert repo / track["runner"] == Path(pilot.__file__)
    assert track["status"] == "recorded" and track["ci_gate"] is False


def test_clarify_round_keeps_all_scheduled_cases_in_the_denominator() -> None:
    report, trials, _ = _load_clarify()
    aggregate = report["aggregate"]
    assert aggregate["denominators"] == {
        "repetitions_requested": 3,
        "trials_recorded": 3,
        "trials_skipped": 0,
        "trials_failed": 0,
    }
    runs = [run for trial in trials for run in trial["runs"]]
    assert len(runs) == 6 and all(run["collection_error"] is None for run in runs)
    blocks = {(block["host"], block["case"]): block for block in aggregate["cases"]}
    assert blocks[("platform", "input_sourced")]["criteria_pass"]["by_source"]["real_model"] == {
        "evaluated": 3,
        "passed": 3,
        "rate": 1.0,
    }
    assert blocks[("platform", "receipt_sourced")]["criteria_pass"]["by_source"]["real_model"] == {
        "evaluated": 3,
        "passed": 2,
        "rate": 0.6666666666666666,
    }
    adapter = experiment.resolve_scenario("platform_trusted_field_model")
    ledger: list[dict] = []
    assert experiment._case_blocks(report["_meta"]["definition"], trials, ledger, adapter=adapter) == aggregate["cases"]
    assert ledger == aggregate["failure_ledger"]
    assert [entry["kind"] for entry in ledger] == ["case_criterion_failed"]
    assert ledger[0]["case"] == "receipt_sourced"


def test_clarify_raw_traces_reproduce_the_supplement_loop() -> None:
    report, trials, _ = _load_clarify()
    aggregate = report["aggregate"]["security"]
    adapter = experiment.resolve_scenario("platform_trusted_field_model")
    assert experiment._security_block(report["_meta"]["definition"], trials, adapter=adapter) == aggregate
    runs = [run for trial in trials for run in trial["runs"]]
    for run in runs:
        verdicts = _recompute_run_verdicts(run, report["_meta"]["definition"])
        behavior = verdicts["behavior"]
        if run["case"] == "input_sourced":
            # The full host supplement loop, driven by the real model under
            # the candidate prompt: omitted write, guard interception, the
            # authorized answer in the same Run, one authorized write.
            assert verdicts["landed_count"] == 1
            assert behavior["first_write_source"] == "omitted"
            assert behavior["host_interception"] == "input_required"
            assert behavior["clarification_origin"] == "host_field_guard"
            assert behavior["authorized_input_same_run"] is True
            assert behavior["post_supplement_write"] is True
            park = run["clarification"]["parks"][0]
            assert park["landed_writes_before_park"] == 0
            assert park["contract_ref"]["contract"]["field_provenance"]["resource"] == trusted.RESOURCE
        else:
            assert verdicts["negatives"]["duplicate_write"] is False
            assert behavior["host_interception"] is None
    # The security block is fully evaluable and clean.
    assert (aggregate["checks_expected"], aggregate["checks_evaluable"], aggregate["checks_passed"]) == (18, 18, 18)
    assert aggregate["checks_failed"] == 0 and aggregate["checks_missing"] == 0
    assert aggregate["all_passed"] is True and aggregate["failures"] == []


def test_clarify_budget_chain_pins_the_shared_pool() -> None:
    report, trials, trial_metadata = _load_clarify()
    remaining = 30
    for trial, trial_meta, (start, end) in zip(trials, trial_metadata, CLARIFY_TRIAL_BUDGETS, strict=True):
        budget = trial_meta["budget"]
        assert budget["pool"] == "armed" and budget["round_request_cap"] == ROUND_REQUEST_CAP
        assert budget["remaining_at_start"] == start == remaining
        assert budget["remaining_at_end"] == end
        budget_running = start
        for run in trial["runs"]:
            run_budget = run["request_budget"]
            assert run_budget["remaining_before"] == budget_running
            budget_running -= len(run["rounds"])
            assert run_budget["remaining_after"] == budget_running
        assert budget_running == end
        remaining = end
    assert remaining == 8
    rounds = [entry for trial in trials for run in trial["runs"] for entry in run["rounds"]]
    assert len(rounds) == CLARIFY_CONSUMED_REQUESTS == TRIAL_BUDGETS[-1][1] - CLARIFY_TRIAL_BUDGETS[-1][1]
    assert Counter(entry.get("error_type") for entry in rounds) == {None: 20, "RateLimitError": 2}
    all_runs = [run for trial in trials for run in trial["runs"]]
    usage = [run["usage"]["token_total"] for run in all_runs if run["usage"]["source"] == "reported"]
    assert report["aggregate"]["usage"]["reported_runs"] == len(usage) == 4
    assert report["aggregate"]["usage"]["unverified_runs"] == 2
    assert sum(usage) == 8470
    assert report["aggregate"]["usage"] == experiment._usage_summary(trials)
    assert report["aggregate"]["cost"]["status"] == "unknown"
    assert all(run["usage"]["cost_amount"] is None for run in all_runs)


def test_clarify_model_behavior_outcome_matches_the_frozen_criteria() -> None:
    report, trials, _ = _load_clarify()
    runs = [run for trial in trials for run in trial["runs"]]
    clarify = [run["model_behavior"] for run in runs if run["case"] == "input_sourced"]
    assert len(clarify) == 3
    for behavior in clarify:
        assert behavior["first_write_source"] == "omitted"
        assert behavior["host_interception"] == "input_required"
        assert behavior["clarification_origin"] == "host_field_guard"
        assert behavior["authorized_input_same_run"] is True
        assert behavior["post_supplement_write"] is True
        assert behavior["delivery_outcome"] == "accepted"
    receipt = [run["model_behavior"] for run in runs if run["case"] == "receipt_sourced"]
    assert Counter(item["first_write_source"] for item in receipt) == {"read_receipt": 2, None: 1}
    assert Counter(item["delivery_outcome"] for item in receipt) == {"accepted": 2, "format_refused": 1}
    for run, behavior in zip((r for r in runs if r["case"] == "receipt_sourced"), receipt, strict=True):
        if behavior["first_write_source"] is None:
            assert run["side_effect_count"] == 0
            assert run["stop"]["task_status"] == "failed"
    summary = pilot.behavior_summary(report)
    assert summary["cases"]["input_sourced"]["host_interception"] == {"input_required": 3}
    assert summary["cases"]["input_sourced"]["authorized_input_same_run"] == 3
    assert summary["cases"]["input_sourced"]["post_supplement_write"] == 3
    assert summary["cases"]["input_sourced"]["not_intercepted"] == []
