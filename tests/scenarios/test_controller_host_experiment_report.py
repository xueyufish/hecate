"""Hermetic offline tests for the repeated-trial experiment layer.

Everything runs against the real host assemblies with the deterministic
injected provider — no network. The tests pin the experiment definition
identity, repetition isolation, aggregate denominators and failure
preservation, source and security grouping, unknown cost/review honesty,
and comparison-group drift against the committed history.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import controller_host_runner as runner_driver
from tests.scenarios.baseline import controller_host_spec as spec
from tests.scenarios.baseline import run_controller_host_baseline as collector

pytestmark = pytest.mark.timeout(600)

COMMITTED_BASELINE_RECORDS = sorted(
    (collector.BASELINE_DIR).glob("controller_host_baseline_controller-host-rubric-*.json")
)


@pytest.fixture(scope="module")
def experiment_definition():
    return experiment.build_definition(repetitions=2)


@pytest.fixture(scope="module")
async def experiment_report(tmp_path_factory, experiment_definition):
    root = tmp_path_factory.mktemp("controller-host-experiment")
    return await experiment.run_experiment(
        definition=experiment_definition,
        state_root=root / "state",
        trial_report_dir=root / "records",
    )


def _case_block(report: dict, host: str, case: str) -> dict:
    return next(block for block in report["aggregate"]["cases"] if block["host"] == host and block["case"] == case)


# --- acceptance 1: versioned definition, independent trial identity/state ---


def test_definition_pins_identity_and_criteria(experiment_definition) -> None:
    definition = experiment_definition
    assert experiment.definition_digest(definition) == experiment.definition_digest(copy.deepcopy(definition))
    assert definition["criteria"]["case_criteria"] == experiment.CASE_CRITERIA
    assert definition["definition_version"] == experiment.EXPERIMENT_VERSION
    assert Draft202012Validator(experiment.DEFINITION_SCHEMA).validate(definition) is None
    drifted = copy.deepcopy(definition)
    drifted["sampling"]["max_tokens"] += 1
    with pytest.raises(ValidationError):
        experiment.definition_digest(drifted)


def test_definition_rejects_unpinned_real_mode() -> None:
    with pytest.raises(ValueError, match="pin a concrete model"):
        experiment.build_definition(repetitions=1, source_mode="real_model")
    with pytest.raises(ValueError, match="must not pin a provider model"):
        experiment.build_definition(repetitions=1, model="gpt-4o")


def test_trials_have_independent_identity_and_state_dirs(experiment_report) -> None:
    trials = experiment_report["trials"]
    assert len(trials) == 2
    assert [trial["trial_index"] for trial in trials] == [0, 1]
    assert len({trial["trial_id"] for trial in trials}) == 2
    assert len({trial["state_dir"] for trial in trials}) == 2
    for trial in trials:
        assert trial["status"] == "recorded"
        assert trial["report"] and trial["report_digest"]


# --- acceptance 2: repetition isolation ---


def test_repetitions_reuse_no_task_run_or_side_effect_state(experiment_report) -> None:
    seen_ids: dict[tuple[str, str], set[tuple[str | None, str | None]]] = {}
    for trial in experiment_report["trials"]:
        for run in trial["runs"]:
            key = (run["host"], run["case"])
            pair = (run["task_id"], run["run_id"])
            assert pair not in seen_ids.setdefault(key, set()), "a trial reused a prior Task/Run identity"
            seen_ids[key].add(pair)
    per_case_calls: dict[tuple[str, str], list[int]] = {}
    per_case_side_effects: dict[tuple[str, str], list[int]] = {}
    for trial in experiment_report["trials"]:
        for run in trial["runs"]:
            key = (run["host"], run["case"])
            per_case_calls.setdefault(key, []).append(run["model_calls"])
            per_case_side_effects.setdefault(key, []).append(run["side_effect_count"])
    for key, calls in per_case_calls.items():
        assert len(set(calls)) == 1, f"model-call counter leaked across trials: {key} -> {calls}"
    for (host, case), effects in per_case_side_effects.items():
        if case == "normal":
            assert effects == [1] * len(effects), f"side-effect state accumulated across trials on {host}"


# --- acceptance 3: denominators, completion, failure classification, latency ---


def test_aggregate_denominators_and_completion(experiment_report) -> None:
    denominators = experiment_report["aggregate"]["denominators"]
    assert denominators == {
        "repetitions_requested": 2,
        "trials_recorded": 2,
        "trials_skipped": 0,
        "trials_failed": 0,
    }
    for block in experiment_report["aggregate"]["cases"]:
        assert block["expected"] == 2 and block["attempted"] == 2 and block["recorded"] == 2
        assert block["missing"] == 0 and block["collection_errors"] == 0
        assert block["completion_rate"] == 1.0
        kinds = block["failure_kinds"]
        accounted = (
            block["recorded"]
            + kinds["collection_error"]
            + kinds["missing_from_failed_trial"]
            + kinds["missing_from_skipped_trial"]
        )
        assert accounted == block["expected"], block
    assert experiment_report["aggregate"]["failure_ledger"] == []
    assert experiment_report["_meta"]["status"] == "recorded"


def test_latency_distribution_covers_recorded_samples(experiment_report) -> None:
    for block in experiment_report["aggregate"]["cases"]:
        latency = block["latency_ms"]
        assert latency["samples"] == block["recorded"]
        assert latency["min_ms"] <= latency["p50_ms"] <= latency["p90_ms"] <= latency["max_ms"]


async def test_trial_failure_is_preserved_and_accounted(tmp_path) -> None:
    definition = experiment.build_definition(repetitions=2, hosts=("runner",), cases=("normal",))

    def factory(trial_index: int):
        if trial_index == 0:
            raise RuntimeError("synthetic trial-level infrastructure loss")
        return spec.DeterministicProvider()

    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        provider_factory=factory,
    )
    assert report["_meta"]["status"] == "recorded"
    failed, recorded = report["trials"]
    assert failed["status"] == "failed" and "RuntimeError" in failed["reason"]
    assert failed["report"] and recorded["status"] == "recorded"
    failure_bytes = (tmp_path / "records" / failed["report"]).read_bytes()
    assert hashlib.sha256(failure_bytes).hexdigest() == failed["report_digest"]
    assert json.loads(failure_bytes)["_meta"]["status"] == "failed"
    denominators = report["aggregate"]["denominators"]
    assert denominators["trials_failed"] == 1 and denominators["trials_recorded"] == 1
    ledger = report["aggregate"]["failure_ledger"]
    assert ledger and ledger[0]["kind"] == "trial_failed" and "RuntimeError" in ledger[0]["detail"]
    block = _case_block(report, "runner", "normal")
    assert block["expected"] == 2 and block["attempted"] == 1 and block["missing"] == 1
    assert block["completion_rate"] == 0.5
    assert block["failure_kinds"]["missing_from_failed_trial"] == 1
    Draft202012Validator(experiment.EXPERIMENT_REPORT_SCHEMA).validate(report)


async def test_collection_error_counts_as_failure_not_success(tmp_path, monkeypatch) -> None:
    definition = experiment.build_definition(repetitions=1, hosts=("runner",), cases=("normal",))

    async def lost_driver(host, case, provider, root):
        raise RuntimeError("synthetic host driver loss")

    monkeypatch.setattr(runner_driver, "run_runner_case", lost_driver)
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
    )
    block = _case_block(report, "runner", "normal")
    assert block["collection_errors"] == 1 and block["recorded"] == 0
    assert block["completion_rate"] == 0.0
    kinds = [entry["kind"] for entry in report["aggregate"]["failure_ledger"]]
    assert "collection_error" in kinds
    assert block["criteria_pass"]["by_source"]["deterministic_injection"]["evaluated"] == 0
    Draft202012Validator(experiment.EXPERIMENT_REPORT_SCHEMA).validate(report)


async def test_business_failure_is_recorded_not_filtered(tmp_path) -> None:
    """A mid-loop model outage is a business failure: the run stays recorded,
    its criterion verdict lands in the ledger, and the pass rate drops — it
    is never silently dropped to keep the completion rate flattering."""

    definition = experiment.build_definition(repetitions=1, hosts=("runner",), cases=("normal",))
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        provider_factory=lambda _index: spec.FailAfterProvider(spec.DeterministicProvider(), fail_after=0),
    )
    block = _case_block(report, "runner", "normal")
    assert block["collection_errors"] == 0 and block["recorded"] == 1
    assert block["completion_rate"] == 1.0
    rates = block["criteria_pass"]["by_source"]["deterministic_injection"]
    assert rates == {"evaluated": 1, "passed": 0, "rate": 0.0}
    ledger = report["aggregate"]["failure_ledger"]
    assert [entry["kind"] for entry in ledger] == ["case_criterion_failed"]
    assert report["aggregate"]["security"]["all_passed"] is True
    Draft202012Validator(experiment.EXPERIMENT_REPORT_SCHEMA).validate(report)


async def test_skipped_real_mode_trials_are_preserved(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(collector.REAL_MODE_GATE_ENV, raising=False)
    definition = experiment.build_definition(
        repetitions=2, hosts=("runner",), cases=("normal",), source_mode="real_model", model="zai/test-model"
    )
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
    )
    assert report["_meta"]["status"] == "skipped"
    assert [trial["status"] for trial in report["trials"]] == ["skipped", "skipped"]
    assert all(trial["reason"] for trial in report["trials"])
    assert report["aggregate"]["denominators"]["trials_skipped"] == 2
    block = _case_block(report, "runner", "normal")
    assert block["expected"] == 2 and block["recorded"] == 0 and block["completion_rate"] == 0.0
    assert block["criteria_pass"]["by_source"]["real_model"]["rate"] is None
    ledger_kinds = [entry["kind"] for entry in report["aggregate"]["failure_ledger"]]
    assert ledger_kinds == ["trial_skipped", "trial_skipped"]
    assert report["_meta"]["conclusion"]["real_model_quality"] == "not_available"
    for trial in report["trials"]:
        assert trial["report"], "skipped trials keep their explicit skip record"
        skip_record = json.loads((tmp_path / "records" / trial["report"]).read_text(encoding="utf-8"))
        assert skip_record["_meta"]["status"] == "skipped"
    Draft202012Validator(experiment.EXPERIMENT_REPORT_SCHEMA).validate(report)


# --- acceptance 4: source grouping and separate security assertions ---


def test_hermetic_evidence_stays_deterministic_and_unblended(experiment_report) -> None:
    by_source = experiment_report["aggregate"]["by_source"]
    assert by_source["real_model"]["cases_recorded"] == 0
    assert by_source["deterministic_injection"]["cases_recorded"] == 20
    assert "never a real-model quality result" in by_source["deterministic_injection"]["note"]
    assert experiment_report["_meta"]["conclusion"]["real_model_quality"] == "not_available"
    security = experiment_report["aggregate"]["security"]
    assert security["checks_failed"] == 0 and security["all_passed"] is True
    assert security["checks_evaluable"] > 0
    for block in experiment_report["aggregate"]["cases"]:
        rates = block["criteria_pass"]["by_source"]
        assert rates["real_model"]["evaluated"] == 0 and rates["real_model"]["rate"] is None
        assert rates["deterministic_injection"]["evaluated"] == 2
    assert experiment_report["_meta"]["gate"] is False
    assert experiment_report["_meta"]["business_value"] == "unverified"


async def test_real_and_deterministic_evidence_are_grouped_separately(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-local-test")

    async def run_case(host, case, provider, root):
        source = "real_model" if isinstance(provider, collector.LiteLLMProvider) else "deterministic_injection"
        criterion = experiment.CASE_CRITERIA[case]
        return {
            "host": host,
            "case": case,
            "task_id": f"task-{host}-{case}",
            "run_id": f"run-{host}-{case}",
            "source": source,
            "rounds": [],
            "final_content": None,
            "tool_receipts": [],
            "tool_calls": [],
            "ledger_actions": [],
            "approval": None,
            "rebuild": {"performed": False},
            "post_resume_model_input_has_receipt": None,
            "collection_error": None,
            "stop": {"task_status": "succeeded", "error_category": None, "error": None, "reconciliation_reason": None},
            "summary": {criterion: True, "stop_compliance": True},
            "latency_ms_total": 1.0,
            "model_calls": 1,
            "write_attempts": 0,
            "side_effect_count": 0,
            "usage": {
                "source": "reported",
                "token_total": 10,
                "cost_amount": None,
                "currency": "USD",
                "price_version": "unverified",
            },
        }

    monkeypatch.setattr(collector, "_run_case", run_case)
    definition = experiment.build_definition(repetitions=1, source_mode="real_model", model="test-model")
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
    )
    by_source = report["aggregate"]["by_source"]
    assert by_source["real_model"]["cases_recorded"] == 4
    assert by_source["deterministic_injection"]["cases_recorded"] == 6
    assert report["_meta"]["conclusion"]["real_model_quality"] == "recorded_below"
    normal = _case_block(report, "platform", "normal")
    assert normal["criteria_pass"]["by_source"]["real_model"] == {"evaluated": 1, "passed": 1, "rate": 1.0}
    cancelled = _case_block(report, "platform", "cancelled")
    assert cancelled["criteria_pass"]["by_source"]["real_model"]["evaluated"] == 0
    assert cancelled["criteria_pass"]["by_source"]["deterministic_injection"]["evaluated"] == 1
    usage = report["aggregate"]["usage"]
    assert usage["reported_runs"] == 10 and usage["reported_token_total"] == 100
    Draft202012Validator(experiment.EXPERIMENT_REPORT_SCHEMA).validate(report)


def test_security_failure_is_ledgered_not_averaged_away() -> None:
    definition = experiment.build_definition(repetitions=1)

    def make_run(host: str, case: str, **overrides: object) -> dict:
        return {
            "host": host,
            "case": case,
            "source": "deterministic_injection",
            "collection_error": None,
            "summary": {experiment.CASE_CRITERIA[case]: True, "stop_compliance": True},
            "side_effect_count": 0,
            "write_attempts": 0,
            "latency_ms_total": 1.0,
            "usage": {"source": "unverified", "token_total": None},
            **overrides,
        }

    violating_run = make_run(
        "runner",
        "cancelled",
        side_effect_count=1,
        write_attempts=1,
        summary={experiment.CASE_CRITERIA["cancelled"]: False, "stop_compliance": False},
    )
    trial_records = [
        {
            "trial_id": "t0",
            "trial_index": 0,
            "status": "recorded",
            "runs": [make_run("platform", "normal", side_effect_count=1, write_attempts=1), violating_run],
        }
    ]
    ledger: list[dict] = []
    blocks = experiment._case_blocks(definition, trial_records, ledger)
    security = experiment._security_block(definition, trial_records)

    cancelled = next(block for block in blocks if (block["host"], block["case"]) == ("runner", "cancelled"))
    assert security["all_passed"] is False and security["checks_failed"] == 1
    assert security["failures"][0]["rule"] == "zero_write_cases"
    kinds = [entry["kind"] for entry in ledger if entry["case"] == "cancelled"]
    assert "security_check_failed" in kinds and "case_criterion_failed" in kinds
    # The security verdict is kept in its own block: the quality view still
    # counts the run as recorded evidence and is never blended with security.
    assert cancelled["recorded"] == 1
    assert cancelled["criteria_pass"]["by_source"]["deterministic_injection"]["passed"] == 0
    assert cancelled["failure_kinds"]["security_check_failed"] == 1
    compliant_normal = next(block for block in blocks if (block["host"], block["case"]) == ("platform", "normal"))
    assert compliant_normal["criteria_pass"]["by_source"]["deterministic_injection"]["passed"] == 1


# --- acceptance 5: usage/cost source honesty and manual review records ---


def test_missing_cost_and_usage_stay_unknown(experiment_report) -> None:
    usage = experiment_report["aggregate"]["usage"]
    assert usage["reported_runs"] == 0
    assert usage["reported_token_total"] is None
    assert usage["unverified_runs"] == 20
    cost = experiment_report["aggregate"]["cost"]
    assert cost["status"] == "unknown"
    for trial in experiment_report["trials"]:
        for run in trial["runs"]:
            assert run["usage"]["cost_amount"] is None


def test_review_records_round_trip_and_stay_bounded(experiment_report, tmp_path) -> None:
    records_dir = tmp_path / "records"
    out = experiment.write_experiment_report(experiment_report, records_dir)
    digest_before = experiment.text_digest(out.read_text(encoding="utf-8"))

    experiment.write_review_record(
        out,
        {
            "reviewer": "reviewer-a",
            "verdict": "agree",
            "trials_reviewed": 2,
            "cases_reviewed": 20,
            "duration_ms": 900_000,
            "notes": None,
        },
        out_dir=records_dir,
    )
    experiment.write_review_record(
        out,
        {
            "reviewer": "reviewer-b",
            "verdict": "partial",
            "disagreements": [
                {
                    "trial_id": experiment_report["trials"][0]["trial_id"],
                    "host": "runner",
                    "case": "normal",
                    "reason": "latency outlier",
                }
            ],
        },
        out_dir=records_dir,
    )
    loaded = experiment.load_review_records(out, search_dir=records_dir)
    assert [record["review"]["reviewer"] for record in loaded] == ["reviewer-a", "reviewer-b"]
    summary = experiment.review_summary(loaded)
    assert summary["count"] == 2
    assert summary["verdicts"] == {"agree": 1, "disagree": 0, "partial": 1}
    assert summary["reported_duration_ms_total"] == 900_000
    assert summary["trials_reviewed_total"] == 2

    # A record with nothing collected stays null, never zero.
    only_b = experiment.review_summary(loaded[1:])
    assert only_b["reported_duration_ms_total"] is None
    assert only_b["trials_reviewed_total"] is None
    assert only_b["cases_reviewed_total"] is None

    # Reviews are bound to one digest: another aggregate gets none of them.
    other = copy.deepcopy(experiment_report)
    other["_meta"]["experiment_run_id"] = "other-run"
    other_path = experiment.write_experiment_report(other, records_dir)
    assert experiment.load_review_records(other_path, search_dir=records_dir) == []

    # Reviews never mutate the reviewed record.
    assert experiment.text_digest(out.read_text(encoding="utf-8")) == digest_before
    assert experiment_report["_meta"]["review"]["status"] == "not_performed"

    with pytest.raises(ValidationError):
        experiment.write_review_record(out, {"reviewer": "", "verdict": "agree"}, out_dir=records_dir)
    with pytest.raises(ValidationError):
        experiment.write_review_record(out, {"reviewer": "r", "verdict": "enthusiastic"}, out_dir=records_dir)


def test_review_refuses_non_experiment_records(tmp_path) -> None:
    report = asyncio.run(collector.collect(model="gpt-4o", root=tmp_path))
    single_run_path = tmp_path / "single_run_record.json"
    single_run_path.write_text(json.dumps(report, default=str), encoding="utf-8")
    with pytest.raises(ValueError, match="experiment aggregate"):
        experiment.write_review_record(single_run_path, {"reviewer": "reviewer", "verdict": "agree"}, out_dir=tmp_path)


# --- acceptance 6: history compatibility and comparison-group drift ---


def test_committed_history_records_stay_valid_and_separate() -> None:
    assert COMMITTED_BASELINE_RECORDS, "the committed single-run baseline records must be present"
    for path in COMMITTED_BASELINE_RECORDS:
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["_meta"]["kind"] == "controller_host_baseline"
        with pytest.raises(ValueError, match="experiment records only"):
            experiment.comparison_group(record)
        if record["_meta"]["rubric_version"] == spec.RUBRIC_VERSION:
            Draft202012Validator(spec.REPORT_SCHEMA).validate(record)
        else:
            # A historical rubric record is retained verbatim: it never
            # retroactively acquires the strengthened rubric's contract.
            assert record["_meta"]["rubric_version"] != spec.RUBRIC_VERSION


def test_criteria_or_input_drift_leaves_the_comparison_group(experiment_report) -> None:
    assert experiment.compare_records(experiment_report, experiment_report)["comparable"] is True
    drifted = copy.deepcopy(experiment_report)
    drifted["_meta"]["definition"]["repetitions"] = 3
    drifted["_meta"]["definition_digest"] = experiment.definition_digest(drifted["_meta"]["definition"])
    comparison = experiment.compare_records(experiment_report, drifted)
    assert comparison["comparable"] is False
    assert "different comparison groups" in comparison["reason"]

    scenario_drifted = copy.deepcopy(experiment_report)
    scenario_drifted["_meta"]["definition"]["scenario_digest"] = "f" * 64
    scenario_drifted["_meta"]["definition_digest"] = experiment.definition_digest(
        scenario_drifted["_meta"]["definition"]
    )
    assert experiment.compare_records(experiment_report, scenario_drifted)["comparable"] is False


def test_scenario_digest_tracks_shared_inputs(monkeypatch) -> None:
    before = experiment.scenario_digest()
    monkeypatch.setattr(spec, "MATERIAL", {**spec.MATERIAL, "quantity": 7})
    assert experiment.scenario_digest() != before


def test_aggregate_schema_rejects_tampering(experiment_report) -> None:
    cost_drift = copy.deepcopy(experiment_report)
    cost_drift["aggregate"]["cost"]["status"] = "estimated"
    with pytest.raises(ValidationError):
        Draft202012Validator(experiment.EXPERIMENT_REPORT_SCHEMA).validate(cost_drift)
    version_drift = copy.deepcopy(experiment_report)
    version_drift["_meta"]["experiment_version"] = "controller-host-experiment-0"
    with pytest.raises(ValidationError):
        Draft202012Validator(experiment.EXPERIMENT_REPORT_SCHEMA).validate(version_drift)


def test_comparable_records_produce_deltas_only_inside_the_group(tmp_path) -> None:
    definition = experiment.build_definition(repetitions=1, hosts=("runner",), cases=("normal",))
    base = {
        "_meta": {
            "kind": "controller_host_experiment",
            "definition": definition,
            "definition_digest": experiment.definition_digest(definition),
            "experiment_run_id": "run-a",
        },
        "aggregate": {
            "cases": [
                {
                    "host": "runner",
                    "case": "normal",
                    "completion_rate": 1.0,
                    "criteria_pass": {
                        "by_source": {
                            "real_model": {"rate": 0.5},
                            "deterministic_injection": {"rate": 1.0},
                        }
                    },
                    "latency_ms": {"p50_ms": 100.0},
                }
            ]
        },
    }
    better = copy.deepcopy(base)
    better["_meta"] = {**base["_meta"], "experiment_run_id": "run-b"}
    better["aggregate"]["cases"][0]["criteria_pass"]["by_source"]["real_model"]["rate"] = 1.0
    comparison = experiment.compare_records(base, better)
    assert comparison["comparable"] is True
    delta = comparison["cases"][0]
    assert delta["criteria_rate_delta"]["real_model"] == 0.5
    assert delta["completion_rate_delta"] == 0.0

    drifted = copy.deepcopy(base)
    drifted["_meta"]["definition"]["repetitions"] = 2
    drifted["_meta"]["definition_digest"] = experiment.definition_digest(drifted["_meta"]["definition"])
    drifted["_meta"]["experiment_run_id"] = "run-c"
    assert experiment.compare_records(base, drifted)["comparable"] is False


# --- CLI surface ---


def test_cli_prints_builtin_definition(capsys) -> None:
    from tests.scenarios.baseline import run_controller_host_experiment as cli

    assert cli.main(["--print-default-definition"]) is None
    printed = json.loads(capsys.readouterr().out)
    Draft202012Validator(experiment.DEFINITION_SCHEMA).validate(printed)
    assert printed["repetitions"] == 3 and printed["source_mode"] == "deterministic_injection"


def test_cli_runs_small_definition_and_writes_records(tmp_path) -> None:
    from tests.scenarios.baseline import run_controller_host_experiment as cli

    definition_path = experiment.dump_definition(
        experiment.build_definition(repetitions=1, hosts=("runner",), cases=("normal",)),
        tmp_path / "definition.json",
    )
    out = cli.main(
        [
            "--definition",
            str(definition_path),
            "--state-root",
            str(tmp_path / "state"),
            "--trial-report-dir",
            str(tmp_path / "records"),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    assert out is not None and out.exists()
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["_meta"]["status"] == "recorded"
    Draft202012Validator(experiment.EXPERIMENT_REPORT_SCHEMA).validate(record)
    trials = record["trials"]
    assert len(trials) == 1
    trial_record = json.loads((tmp_path / "records" / trials[0]["report"]).read_text(encoding="utf-8"))
    assert trial_record["_meta"]["status"] == "recorded"
    assert (
        experiment.text_digest((tmp_path / "records" / trials[0]["report"]).read_text(encoding="utf-8"))
        == trials[0]["report_digest"]
    )


@pytest.mark.parametrize(
    "mutation", ["stale_scenario", "missing_criterion", "real_without_model", "hermetic_with_model"]
)
async def test_invalid_definition_fails_before_collection(tmp_path, monkeypatch, mutation) -> None:
    definition = experiment.build_definition(repetitions=1, hosts=("runner",), cases=("normal",))
    if mutation == "stale_scenario":
        definition["scenario_digest"] = "0" * 64
    elif mutation == "missing_criterion":
        definition["criteria"]["case_criteria"] = {"cancelled": "cancel_compliance"}
    elif mutation == "real_without_model":
        definition["source_mode"] = "real_model"
    else:
        definition["model"] = "unexpected-provider"

    async def unexpected_collection(**kwargs):
        pytest.fail("an invalid definition must never reach the collector")

    monkeypatch.setattr(collector, "collect", unexpected_collection)
    with pytest.raises((ValueError, ValidationError)):
        await experiment.run_experiment(
            definition=definition, state_root=tmp_path / "state", trial_report_dir=tmp_path / "records"
        )
    assert not (tmp_path / "records").exists()


def test_scenario_identity_includes_the_scoring_version(monkeypatch) -> None:
    before = experiment.scenario_digest()
    monkeypatch.setattr(spec, "RUBRIC_VERSION", "changed-scoring-rules")
    assert experiment.scenario_digest() != before


def test_failed_samples_remain_in_pass_rate_denominator() -> None:
    definition = experiment.build_definition(repetitions=3, hosts=("runner",), cases=("normal",))
    passing = {
        "host": "runner",
        "case": "normal",
        "source": "deterministic_injection",
        "collection_error": None,
        "summary": {"delivery_compliance": True},
        "side_effect_count": 1,
    }
    lost = {**passing, "collection_error": "driver unavailable"}
    trials = [
        {"trial_id": "t0", "status": "recorded", "runs": [passing]},
        {"trial_id": "t1", "status": "recorded", "runs": [lost]},
        {"trial_id": "t2", "status": "failed", "runs": []},
    ]
    block = experiment._case_blocks(definition, trials, [])[0]
    assert block["criteria_pass"]["by_source"]["deterministic_injection"]["rate"] == pytest.approx(1 / 3)
    security = experiment._security_block(definition, trials)
    assert security["all_passed"] is None
    assert security["checks_missing"] == 2


def test_comparison_rejects_stale_definition_digest(experiment_report) -> None:
    changed = copy.deepcopy(experiment_report)
    changed["_meta"]["definition"]["scenario_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        experiment.compare_records(experiment_report, changed)


def test_failed_attempts_keep_reported_usage_and_latency() -> None:
    definition = experiment.build_definition(repetitions=1, hosts=("runner",), cases=("normal",))
    trials = [
        {
            "trial_id": "t0",
            "status": "recorded",
            "runs": [
                {
                    "host": "runner",
                    "case": "normal",
                    "source": "deterministic_injection",
                    "collection_error": "failed after a metered request",
                    "latency_ms_total": 120000.0,
                    "usage": {"source": "reported", "token_total": 100},
                }
            ],
        }
    ]
    block = experiment._case_blocks(definition, trials, [])[0]
    assert block["latency_ms"]["samples"] == 1
    assert block["latency_ms"]["max_ms"] == 120000.0
    assert block["tokens"]["reported_token_total"] == 100
    assert experiment._usage_summary(trials)["reported_token_total"] == 100


async def test_trial_records_use_exact_bytes_and_exclusive_creation(tmp_path, monkeypatch) -> None:
    definition = experiment.build_definition(repetitions=1, hosts=("runner",), cases=("normal",))
    instant = experiment.datetime.now(experiment.UTC)

    class FixedClock:
        @staticmethod
        def now(_timezone):
            return instant

    class FixedIdentity:
        hex = "a" * 32

    async def collect(**kwargs):
        return {"_meta": {"status": "recorded"}, "runs": []}

    monkeypatch.setattr(experiment, "datetime", FixedClock)
    monkeypatch.setattr(experiment.uuid, "uuid4", FixedIdentity)
    monkeypatch.setattr(collector, "collect", collect)
    report = await experiment.run_experiment(
        definition=definition, state_root=tmp_path / "state", trial_report_dir=tmp_path / "records"
    )
    trial = report["trials"][0]
    raw = (tmp_path / "records" / trial["report"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == trial["report_digest"]
    with pytest.raises(FileExistsError):
        await experiment.run_experiment(
            definition=definition, state_root=tmp_path / "state", trial_report_dir=tmp_path / "records"
        )
    assert (tmp_path / "records" / trial["report"]).read_bytes() == raw


def test_review_digest_binds_exact_file_bytes(experiment_report, tmp_path) -> None:
    report_path = experiment.write_experiment_report(experiment_report, tmp_path)
    report_path.write_bytes(report_path.read_bytes().replace(b"\n", b"\r\n"))
    review_path = experiment.write_review_record(report_path, {"reviewer": "r", "verdict": "agree"}, out_dir=tmp_path)
    review = json.loads(review_path.read_bytes())
    assert review["target"]["report_digest"] == hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert len(experiment.load_review_records(report_path, search_dir=tmp_path)) == 1
    report_path.write_bytes(report_path.read_bytes().replace(b"\r\n", b"\n"))
    assert experiment.load_review_records(report_path, search_dir=tmp_path) == []


async def test_hermetic_factory_cannot_fall_back_to_real_provider(tmp_path, monkeypatch) -> None:
    async def unexpected_collection(**kwargs):
        pytest.fail("a missing injected provider must not reach real collection")

    monkeypatch.setattr(collector, "collect", unexpected_collection)
    report = await experiment.run_experiment(
        definition=experiment.build_definition(repetitions=1, hosts=("runner",), cases=("normal",)),
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        provider_factory=lambda _: None,
    )
    assert report["trials"][0]["status"] == "failed"
    assert "injected provider" in report["trials"][0]["reason"]
