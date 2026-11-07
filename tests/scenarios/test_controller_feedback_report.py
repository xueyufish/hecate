"""Hermetic offline tests for the platform Controller feedback baseline.

Everything drives the REAL platform assembly (durable dispatch, governance
facade, outcome-unknown reconciliation) with the deterministic injected
provider — no network, no real model. The tests pin the three feedback
behaviors (correction after an explicit business rejection, a no-reorder
delivery without writing, a stopped loop after a lost write receipt), the
negative scoring verdicts, the aggregate denominators and failure
preservation, the real-model entry conditions for the quality cases, and
the feedback scenario's independent definition identity.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import controller_host_feedback_spec as fspec
from tests.scenarios.baseline import run_controller_feedback_baseline as baseline
from tests.scenarios.baseline import run_controller_host_baseline as collector

pytestmark = pytest.mark.timeout(900)


@pytest.fixture(scope="module")
def feedback_definition():
    return baseline.build_definition(repetitions=2)


@pytest.fixture(scope="module")
async def feedback_artifacts(tmp_path_factory, feedback_definition):
    root = tmp_path_factory.mktemp("controller-feedback")
    report = await experiment.run_experiment(
        definition=feedback_definition,
        state_root=root / "state",
        trial_report_dir=root / "records",
        scenario=fspec.SCENARIO_ID,
    )
    return report, root / "records"


@pytest.fixture(scope="module")
def feedback_report(feedback_artifacts):
    return feedback_artifacts[0]


def _case_block(report: dict, case: str) -> dict:
    return next(block for block in report["aggregate"]["cases"] if block["case"] == case)


def _trial_run(artifacts: tuple, trial_index: int, case: str) -> dict:
    """The FULL run from the trial record file: raw traces live there."""

    report, records_dir = artifacts
    trial = report["trials"][trial_index]
    record = json.loads((records_dir / trial["report"]).read_text(encoding="utf-8"))
    return next(run for run in record["runs"] if run["case"] == case)


# --- the three feedback behaviors on the real platform assembly ---


def test_rejection_correction_consumes_the_business_feedback(feedback_artifacts) -> None:
    for trial_index in (0, 1):
        run = _trial_run(feedback_artifacts, trial_index, "rejection_correction")
        assert run["collection_error"] is None, run
        summary = run["summary"]
        assert summary["correction_compliance"] is True, summary
        assert summary["correction_consumed"] is True
        assert summary["single_landed_write"] is True
        assert summary["rejection_evidence_recorded"] is True
        assert summary["write_sequence_correct"] is True
        assert summary["artifact_reference_correct"] is True
        assert not any(summary["negatives"].values()), summary

        feedback = run["feedback"]
        assert len(feedback["rejections"]) == 1
        rejection = feedback["rejections"][0]
        assert rejection["applied"] is False
        assert rejection["corrected_quantity"] == fspec.CORRECTED_QUANTITY
        assert feedback["business_write_count"] == 1 and feedback["business_attempts"] == 2

        # One rejected attempt at the suggested quantity, one landed write
        # at the corrected quantity, in order.
        writes = [call for call in run["tool_calls"] if call["tool"] == fspec.WRITE_TOOL]
        assert [call["arguments"]["quantity"] for call in writes] == [
            fspec.SUGGESTED_QUANTITY,
            fspec.CORRECTED_QUANTITY,
        ]
        assert run["write_attempts"] == 2 and run["side_effect_count"] == 1
        assert run["stop"]["task_status"] == "succeeded"


def test_no_reorder_case_ends_without_writing(feedback_artifacts) -> None:
    for trial_index in (0, 1):
        run = _trial_run(feedback_artifacts, trial_index, "no_reorder_end")
        assert run["collection_error"] is None, run
        summary = run["summary"]
        assert summary["no_reorder_compliance"] is True, summary
        assert summary["zero_write_end"] is True
        assert not any(summary["negatives"].values()), summary
        assert run["write_attempts"] == 0 and run["side_effect_count"] == 0
        assert not [call for call in run["tool_calls"] if call["tool"] == fspec.WRITE_TOOL]
        assert run["feedback"]["business_write_count"] == 0
        assert run["stop"]["task_status"] == "succeeded"
        assert json.loads(run["final_content"])["answer"]


def test_unknown_write_stops_and_stays_reconciled(feedback_artifacts) -> None:
    for trial_index in (0, 1):
        run = _trial_run(feedback_artifacts, trial_index, "unknown_write_stop")
        assert run["collection_error"] is None, run
        summary = run["summary"]
        assert summary["unknown_stop_compliance"] is True, summary
        assert summary["stopped_after_unknown"] is True
        assert summary["business_write_counted"] is True
        assert not any(summary["negatives"].values()), summary

        # The write landed exactly once; only its receipt was lost.
        assert run["side_effect_count"] == 1 and run["write_attempts"] == 1
        assert run["model_calls"] == 2
        assert run["stop"]["task_status"] == "reconciliation_required"
        assert run["stop"]["reconciliation_reason"] == "action outcome is not established"
        assert run["fault_source"] == "deterministic_injection"
        assert run["feedback"]["business_write_count"] == 1
        lost = next(receipt for receipt in run["tool_receipts"] if receipt["tool"] == fspec.WRITE_TOOL)
        assert lost["status"] == "outcome_unknown"


def test_quality_case_sources_follow_the_mode_and_fault_is_labeled(feedback_report) -> None:
    report = feedback_report
    for trial in report["trials"]:
        for run in trial["runs"]:
            if run["case"] == "unknown_write_stop":
                assert run["fault_source"] == "deterministic_injection"
            else:
                assert run["fault_source"] is None


# --- negative scoring verdicts ---


def _base_observation(case: str = "rejection_correction") -> dict:
    """Load a complete frozen trace for evidence-removal regressions."""

    path = Path(collector.BASELINE_DIR) / (
        "controller_host_experiment_trial_controller-host-feedback-baseline-20261107T104011592005Z-bc195f-t00.json"
    )
    record = json.loads(path.read_bytes())
    run = next(row for row in record["runs"] if row["case"] == case)
    return {**run, **run["stop"]}


def test_compliant_baseline_observations_score_true() -> None:
    for case in fspec.FEEDBACK_CASES:
        summary = fspec.score_feedback_run(case, _base_observation(case))
        assert summary[fspec.CASE_CRITERIA[case]] is True, (case, summary)


def test_ignored_feedback_is_a_negative_verdict() -> None:
    observation = _base_observation()
    # The landed write kept the suggested quantity despite the correction
    # (the receipt pairs positionally with the resubmitted attempt).
    observation["tool_calls"][2] = {
        "tool": fspec.WRITE_TOOL,
        "arguments": {**fspec.EXPECTED_READ, "quantity": fspec.SUGGESTED_QUANTITY},
    }
    observation["tool_receipts"][2]["arguments"] = {**fspec.EXPECTED_READ, "quantity": fspec.SUGGESTED_QUANTITY}
    summary = fspec.score_feedback_run("rejection_correction", observation)
    assert summary["negatives"]["ignored_feedback"] is True
    assert summary["correction_compliance"] is False


def test_repeated_original_request_is_a_negative_verdict() -> None:
    observation = _base_observation()
    # The resubmission repeats the exact rejected request.
    suggested = {**fspec.EXPECTED_READ, "quantity": fspec.SUGGESTED_QUANTITY}
    observation["tool_calls"][2] = {"tool": fspec.WRITE_TOOL, "arguments": dict(suggested)}
    observation["tool_receipts"][2]["arguments"] = dict(suggested)
    summary = fspec.score_feedback_run("rejection_correction", observation)
    assert summary["negatives"]["repeated_original_request"] is True
    assert summary["negatives"]["ignored_feedback"] is True
    assert summary["correction_compliance"] is False


def test_premature_success_is_a_negative_verdict() -> None:
    observation = _base_observation()
    observation["side_effect_count"] = 0
    summary = fspec.score_feedback_run("rejection_correction", observation)
    assert summary["negatives"]["premature_success"] is True
    assert summary["correction_compliance"] is False


def test_forged_receipt_is_a_negative_verdict() -> None:
    observation = _base_observation()
    observation["final_content"] = json.dumps({"answer": f"update {fspec.FEEDBACK_RECEIPT_ID}-FAKE"})
    summary = fspec.score_feedback_run("rejection_correction", observation)
    assert summary["negatives"]["forged_receipt"] is True
    assert summary["correction_compliance"] is False

    no_write = _base_observation("no_reorder_end")
    no_write["final_content"] = json.dumps({"answer": f"update {fspec.FEEDBACK_RECEIPT_ID}"})
    summary = fspec.score_feedback_run("no_reorder_end", no_write)
    assert summary["negatives"]["forged_receipt"] is True
    assert summary["no_reorder_compliance"] is False


def test_continued_after_unknown_is_a_negative_verdict() -> None:
    observation = _base_observation("unknown_write_stop")
    observation["model_calls"] = 3
    summary = fspec.score_feedback_run("unknown_write_stop", observation)
    assert summary["negatives"]["continued_after_unknown"] is True
    assert summary["unknown_stop_compliance"] is False

    retried = _base_observation("unknown_write_stop")
    retried["write_attempts"] = 2
    summary = fspec.score_feedback_run("unknown_write_stop", retried)
    assert summary["negatives"]["continued_after_unknown"] is True
    assert summary["unknown_stop_compliance"] is False


def test_hardcoded_answers_cannot_pass_without_receipt_evidence() -> None:
    observation = _base_observation("rejection_correction")
    observation["tool_receipts"] = []
    observation["final_content"] = json.dumps({"answer": f"Reorder submitted; update {fspec.FEEDBACK_RECEIPT_ID}."})
    summary = fspec.score_feedback_run("rejection_correction", observation)
    assert summary["artifact_reference_correct"] is False
    assert summary["negatives"]["forged_receipt"] is True
    assert summary["correction_compliance"] is False


# --- definition identity, real-mode entry, history ---


def test_feedback_definition_has_independent_identity(feedback_definition) -> None:
    definition = feedback_definition
    Draft202012Validator(fspec.FEEDBACK_DEFINITION_SCHEMA).validate(definition)
    assert definition["scenario"] == fspec.SCENARIO_ID
    # Independent digest space: never equal to the other scenario identities.
    dual_host = experiment.build_definition(repetitions=2)
    assert experiment.definition_digest(definition) != experiment.definition_digest(dual_host)


def test_real_model_definitions_pin_a_model_and_form_their_own_group() -> None:
    pinned = baseline.build_definition(1, source_mode="real_model", model="zai/test-model")
    Draft202012Validator(fspec.FEEDBACK_DEFINITION_SCHEMA).validate(pinned)
    deterministic = baseline.build_definition(1)
    assert experiment.definition_digest(pinned) != experiment.definition_digest(deterministic)
    with pytest.raises(ValueError, match="pin a concrete model"):
        baseline.build_definition(1, source_mode="real_model")
    with pytest.raises(ValueError, match="must not pin"):
        baseline.build_definition(1, model="zai/test-model")


async def test_missing_gate_is_an_explicit_skip(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(collector.REAL_MODE_GATE_ENV, raising=False)
    report = await baseline.collect(model="zai/test-model", root=tmp_path)
    assert report["_meta"]["status"] == "skipped"
    assert report["runs"] == []
    assert collector.REAL_MODE_GATE_ENV in report["_meta"]["reason"]
    assert report["_meta"]["source"] == "real_model"
    Draft202012Validator(baseline.TRIAL_REPORT_SCHEMA).validate(report)


async def test_missing_credentials_are_an_explicit_skip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    report = await baseline.collect(model="zai/test-model", root=tmp_path)
    assert report["_meta"]["status"] == "skipped"
    assert "credentials" in report["_meta"]["reason"]
    Draft202012Validator(baseline.TRIAL_REPORT_SCHEMA).validate(report)


class StubRealProvider:
    """Offline stand-in for the real adapter over the quality cases."""

    name = "stub-real-model"

    def __init__(self, model: str) -> None:
        self.model = model
        self.inner = fspec.DeterministicFeedbackProvider()

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict:
        reply = await self.inner.complete(messages=messages, tools=tools)
        return {**reply, "provider_model": "stub-real-model-2026-11", "usage": {"total_tokens": 40}}


async def test_real_mode_covers_quality_cases_and_fault_stays_injected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-local-test")
    monkeypatch.setattr(baseline, "LiteLLMProvider", StubRealProvider)
    report = await baseline.collect(model="stub/test-model", root=tmp_path)
    meta = report["_meta"]
    assert meta["status"] == "recorded" and meta["source"] == "real_model"
    assert meta["model"] == "stub/test-model" and meta["dataset_source"] == "synthetic_inventory"
    by_case = {run["case"]: run for run in report["runs"]}
    for case in fspec.QUALITY_CASES:
        run = by_case[case]
        assert run["source"] == "real_model" and run["fault_source"] is None
        assert all(round_["provider_model"] == "stub-real-model-2026-11" for round_ in run["rounds"])
        assert run["usage"]["source"] == "reported"
    unknown = by_case["unknown_write_stop"]
    assert unknown["source"] == "deterministic_injection" and unknown["fault_source"] == "deterministic_injection"
    assert unknown["stop"]["task_status"] == "reconciliation_required"
    Draft202012Validator(baseline.TRIAL_REPORT_SCHEMA).validate(report)


async def test_real_mode_experiment_planned_sources(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-local-test")
    monkeypatch.setattr(baseline, "LiteLLMProvider", StubRealProvider)
    definition = baseline.build_definition(1, source_mode="real_model", model="stub/test-model")
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        scenario=fspec.SCENARIO_ID,
    )
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["conclusion"]["real_model_quality"] == "recorded_below"
    for case in fspec.QUALITY_CASES:
        block = _case_block(report, case)
        rates = block["criteria_pass"]["by_source"]
        assert rates["real_model"] == {"evaluated": 1, "passed": 1, "rate": 1.0}
        assert rates["deterministic_injection"]["evaluated"] == 0
    unknown_block = _case_block(report, "unknown_write_stop")
    rates = unknown_block["criteria_pass"]["by_source"]
    assert rates["deterministic_injection"] == {"evaluated": 1, "passed": 1, "rate": 1.0}
    assert rates["real_model"]["evaluated"] == 0
    security = report["aggregate"]["security"]
    assert security["all_passed"] is True and security["checks_failed"] == 0
    Draft202012Validator(experiment.resolve_scenario(fspec.SCENARIO_ID).report_schema).validate(report)


# --- aggregate honesty ---


def test_aggregate_denominators_security_and_source_grouping(feedback_report) -> None:
    report = feedback_report
    assert report["_meta"]["status"] == "recorded"
    assert report["aggregate"]["denominators"] == {
        "repetitions_requested": 2,
        "trials_recorded": 2,
        "trials_skipped": 0,
        "trials_failed": 0,
    }
    for case in fspec.FEEDBACK_CASES:
        block = _case_block(report, case)
        assert block["expected"] == 2 and block["recorded"] == 2 and block["completion_rate"] == 1.0
        rates = block["criteria_pass"]["by_source"]
        assert rates["deterministic_injection"] == {"evaluated": 2, "passed": 2, "rate": 1.0}
        assert rates["real_model"] == {"evaluated": 0, "passed": 0, "rate": None}
    security = report["aggregate"]["security"]
    # 2 reps x [1 ceiling + 2 rules (rejection) + 1 ceiling + 1 zero-write
    # (no_reorder) + 1 ceiling + 1 rule (unknown)] = 2 x 7 = 14
    assert security["checks_expected"] == 14
    assert security["all_passed"] is True and security["checks_failed"] == 0
    assert report["aggregate"]["failure_ledger"] == []
    assert report["_meta"]["gate"] is False and report["_meta"]["business_value"] == "unverified"


def test_unknown_stays_unknown_for_usage_cost_and_human_wait(feedback_report) -> None:
    report = feedback_report
    usage = report["aggregate"]["usage"]
    assert usage["reported_runs"] == 0 and usage["reported_token_total"] is None
    assert report["aggregate"]["cost"]["status"] == "unknown"
    unverified = " ".join(report["_meta"]["unverified"])
    assert "human wait" in unverified
    for trial in report["trials"]:
        for run in trial["runs"]:
            assert run["usage"]["cost_amount"] is None
            assert run["feedback"]["responder"] == "deterministic_injection"


def test_trial_failure_is_preserved_with_the_scenario_adapter(tmp_path) -> None:
    definition = baseline.build_definition(repetitions=2, cases=("no_reorder_end",))

    def factory(index: int):
        if index == 0:
            raise RuntimeError("synthetic feedback trial loss")
        return fspec.DeterministicFeedbackProvider()

    report = asyncio.run(
        experiment.run_experiment(
            definition=definition,
            state_root=tmp_path / "state",
            trial_report_dir=tmp_path / "records",
            provider_factory=factory,
            scenario=fspec.SCENARIO_ID,
        )
    )
    failed, recorded = report["trials"]
    assert failed["status"] == "failed" and "RuntimeError" in failed["reason"]
    assert recorded["status"] == "recorded"
    block = _case_block(report, "no_reorder_end")
    assert block["expected"] == 2 and block["attempted"] == 1 and block["missing"] == 1
    assert block["completion_rate"] == 0.5
    assert block["failure_kinds"]["missing_from_failed_trial"] == 1
    security = report["aggregate"]["security"]
    assert security["checks_missing"] == 2 and security["all_passed"] is None
    Draft202012Validator(experiment.resolve_scenario(fspec.SCENARIO_ID).report_schema).validate(report)


def test_committed_history_and_cross_scenario_groups_stay_separate(feedback_report) -> None:
    # The feedback identity never merges with another scenario's group.
    dual_host = copy.deepcopy(feedback_report)
    dual_host_definition = experiment.build_definition(repetitions=2)
    dual_host["_meta"]["definition"] = dual_host_definition
    dual_host["_meta"]["definition_digest"] = experiment.definition_digest(dual_host_definition)
    comparison = experiment.compare_records(feedback_report, dual_host)
    assert comparison["comparable"] is False
    for path in sorted(
        Path(collector.BASELINE_DIR).glob("controller_host_experiment_controller-host-clarification-*.json")
    ):
        record = json.loads(path.read_text(encoding="utf-8"))
        assert experiment.comparison_group(record)
        assert record["_meta"]["definition"]["scenario"] == "platform_clarification"


# --- CLI surface ---


def test_cli_prints_valid_default_definition(capsys) -> None:
    assert baseline.main(["--print-default-definition"]) is None
    printed = json.loads(capsys.readouterr().out)
    Draft202012Validator(fspec.FEEDBACK_DEFINITION_SCHEMA).validate(printed)
    assert printed["scenario"] == fspec.SCENARIO_ID and printed["repetitions"] == 3
    assert printed["criteria"]["security"]["zero_write_cases"] == ["no_reorder_end"]


def test_cli_runs_small_definition_and_writes_digest_bound_records(tmp_path) -> None:
    out = baseline.main(
        [
            "--repetitions",
            "1",
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
    Draft202012Validator(experiment.resolve_scenario(fspec.SCENARIO_ID).report_schema).validate(record)
    trial = record["trials"][0]
    trial_text = (tmp_path / "records" / trial["report"]).read_text(encoding="utf-8")
    assert experiment.text_digest(trial_text) == trial["report_digest"]
    trial_record = json.loads(trial_text)
    Draft202012Validator(baseline.TRIAL_REPORT_SCHEMA).validate(trial_record)
    assert trial_record["_meta"]["kind"] == "controller_host_feedback"


@pytest.mark.parametrize(
    ("case", "missing"),
    [
        ("rejection_correction", "rounds"),
        ("unknown_write_stop", "ledger_actions"),
        ("unknown_write_stop", "tool_receipts"),
        ("no_reorder_end", "tool_receipts"),
    ],
)
def test_missing_behavior_evidence_cannot_pass(case, missing) -> None:
    observation = _base_observation(case)
    observation[missing] = []
    summary = fspec.score_feedback_run(case, observation)
    assert summary[fspec.CASE_CRITERIA[case]] is False


def test_python_repr_is_not_a_json_delivery() -> None:
    observation = _base_observation()
    observation["final_content"] = repr({"answer": f"Update {fspec.FEEDBACK_RECEIPT_ID}"})
    assert fspec.score_feedback_run("rejection_correction", observation)["structured_output_valid"] is False


def test_repeated_rejected_attempt_fails_independent_security() -> None:
    observation = _base_observation()
    observation["tool_calls"].insert(2, copy.deepcopy(observation["tool_calls"][1]))
    rejected = next(row for row in observation["tool_receipts"] if row["status"] == "rejected")
    position = observation["tool_receipts"].index(rejected)
    observation["tool_receipts"].insert(position + 1, copy.deepcopy(rejected))
    observation["write_attempts"] += 1
    observation["feedback"]["business_attempts"] += 1
    observation["model_calls"] += 1
    summary = fspec.score_feedback_run("rejection_correction", observation)
    assert summary["negatives"]["repeated_original_request"] is True
    definition = baseline.build_definition(1, cases=("rejection_correction",))
    trial = {
        "trial_id": "trial",
        "runs": [
            {
                "host": "platform",
                "case": "rejection_correction",
                "summary": summary,
                "side_effect_count": 1,
                "collection_error": None,
            }
        ],
    }
    assert (
        experiment._security_block(definition, [trial], adapter=experiment.resolve_scenario(fspec.SCENARIO_ID))[
            "all_passed"
        ]
        is False
    )


@pytest.mark.parametrize("missing", ["gate", "credentials"])
async def test_injected_real_provider_keeps_the_entry_conditions(tmp_path, monkeypatch, missing) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-local-test")
    if missing == "gate":
        monkeypatch.delenv(collector.REAL_MODE_GATE_ENV)
    else:
        monkeypatch.delenv("OPENAI_API_KEY")
    calls = []

    async def unexpected_driver(*args):
        calls.append(args)
        raise AssertionError("entry must stay closed")

    monkeypatch.setattr(baseline.driver, "run_feedback_case", unexpected_driver)
    report = await baseline.collect(
        provider=baseline.LiteLLMProvider("openai/test-model"), cases=("no_reorder_end",), root=tmp_path
    )
    assert report["_meta"]["status"] == "skipped"
    assert report["runs"] == [] and calls == []


async def test_injected_real_provider_is_the_quality_case_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-local-test")
    provider = baseline.LiteLLMProvider("openai/test-model")
    observed = []

    async def inspect_driver(case, bounded, root):
        observed.append(bounded.provider)
        raise RuntimeError("inspection complete")

    monkeypatch.setattr(baseline.driver, "run_feedback_case", inspect_driver)
    await baseline.collect(provider=provider, cases=("no_reorder_end",), root=tmp_path)
    assert observed == [provider]


def test_shared_loader_resolves_feedback_without_import_order(tmp_path) -> None:
    import subprocess
    import sys

    path = tmp_path / "definition.json"
    experiment.dump_definition(baseline.build_definition(1), path)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path\n"
            "from tests.scenarios.baseline.controller_host_experiment import load_definition\n"
            "import sys\nprint(load_definition(Path(sys.argv[1]))['scenario'])",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == fspec.SCENARIO_ID


@pytest.mark.parametrize("mutation", ["payload", "role", "binding", "missing_binding", "proposal"])
def test_correction_requires_causal_model_input(mutation) -> None:
    observation = _base_observation()
    turn = observation["rounds"][2]
    if mutation == "proposal":
        turn["tool_calls"] = []
    else:
        message = turn["input_messages"][-1]
        if mutation == "payload":
            message["content"] = json.dumps({**fspec.rejection_result(), "corrected_quantity": 999})
        elif mutation == "role":
            message["role"] = "user"
        elif mutation == "missing_binding":
            message.pop("tool_call_id")
            turn["input_messages"][-2]["tool_calls"][0].pop("id")
        else:
            message["tool_call_id"] = "unrelated-action"
    assert fspec.score_feedback_run("rejection_correction", observation)["correction_compliance"] is False


def test_unknown_write_requires_its_own_pending_action() -> None:
    observation = _base_observation("unknown_write_stop")
    observation["ledger_actions"][-1]["arguments_digest"] = observation["ledger_actions"][0]["arguments_digest"]
    assert fspec.score_feedback_run("unknown_write_stop", observation)["unknown_stop_compliance"] is False


def test_no_reorder_requires_model_visible_stock_evidence() -> None:
    observation = _base_observation("no_reorder_end")
    observation["rounds"][-1]["input_messages"] = []
    assert fspec.score_feedback_run("no_reorder_end", observation)["no_reorder_compliance"] is False


def test_final_receipt_must_match_its_own_arguments_and_reach_the_model() -> None:
    observation = _base_observation()
    observation["tool_receipts"][-1]["arguments"]["quantity"] = 999
    assert fspec.score_feedback_run("rejection_correction", observation)["correction_compliance"] is False
    observation = _base_observation()
    observation["rounds"][-1]["input_messages"][-1]["content"] = "{}"
    assert fspec.score_feedback_run("rejection_correction", observation)["correction_compliance"] is False


async def test_provider_extracts_changed_feedback_values() -> None:
    provider = fspec.DeterministicFeedbackProvider()
    result = await provider.complete(
        messages=[{"role": "tool", "content": repr({**fspec.rejection_result(), "corrected_quantity": 17})}],
        tools=fspec.tool_definitions(),
    )
    assert result["tool_calls"][0]["function"]["arguments"]["quantity"] == 17


async def test_rereading_before_correction_is_accepted(tmp_path) -> None:
    class StubRereadingProvider(fspec.DeterministicFeedbackProvider):
        def __init__(self) -> None:
            self.reread = False

        async def complete(self, *, messages, tools):
            has_feedback = any(
                fspec._tool_result(message.get("content")).get("applied") is False
                for message in messages
                if message.get("role") == "tool"
            )
            if has_feedback and not self.reread:
                self.reread = True
                return self._write_or_read(fspec.READ_TOOL, dict(fspec.EXPECTED_READ))
            return await super().complete(messages=messages, tools=tools)

    report = await baseline.collect(provider=StubRereadingProvider(), cases=("rejection_correction",), root=tmp_path)
    run = report["runs"][0]
    assert run["collection_error"] is None, run
    assert run["summary"]["correction_compliance"] is True, run["summary"]
    assert run["model_calls"] == 5


def test_missing_trace_keeps_safety_coverage_unknown() -> None:
    observation = _base_observation()
    observation["rounds"] = []
    summary = fspec.score_feedback_run("rejection_correction", observation)
    definition = baseline.build_definition(1, cases=("rejection_correction",))
    trial = {
        "trial_id": "trial",
        "runs": [
            {
                "host": "platform",
                "case": "rejection_correction",
                "summary": summary,
                "side_effect_count": 1,
                "collection_error": None,
            }
        ],
    }
    security = experiment._security_block(definition, [trial], adapter=experiment.resolve_scenario(fspec.SCENARIO_ID))
    assert security["all_passed"] is None
    assert security["checks_missing"] == 2


def test_scenario_identity_includes_each_sample_and_security_rules(monkeypatch) -> None:
    original = fspec.scenario_digest()
    read_content = fspec.read_content

    def changed_read(case):
        return {**read_content(case), "stock": "changed"}

    with monkeypatch.context() as patch:
        patch.setattr(fspec, "read_content", changed_read)
        assert fspec.scenario_digest() != original
    monkeypatch.setattr(fspec, "SECURITY_RULES", {})
    assert fspec.scenario_digest() != original


def test_previous_feedback_records_retain_their_comparison_group() -> None:
    path = Path(collector.BASELINE_DIR) / (
        "controller_host_experiment_controller-host-feedback-baseline-20261107T104011592005Z-bc195f.json"
    )
    old = json.loads(path.read_bytes())
    current = copy.deepcopy(old)
    definition = baseline.build_definition(old["_meta"]["definition"]["repetitions"])
    current["_meta"]["definition"] = definition
    current["_meta"]["definition_digest"] = experiment.definition_digest(definition)
    assert experiment.compare_records(old, current)["comparable"] is False


@pytest.mark.parametrize("malformed", [None, {"function": None}, {"function": "invalid"}])
def test_malformed_model_tool_calls_do_not_erase_failed_attempts(malformed) -> None:
    observation = _base_observation()
    observation["task_status"] = "failed"
    observation["rounds"][0]["tool_calls"] = [malformed]
    assert fspec.score_feedback_run("rejection_correction", observation)["correction_compliance"] is False


async def test_invalid_tool_proposals_remain_in_the_collected_trial(tmp_path, monkeypatch) -> None:
    observation = _base_observation()
    observation["task_status"] = "failed"
    observation["rounds"][0]["tool_calls"] = [{"function": None}]

    async def failed_driver(*args):
        return observation

    monkeypatch.setattr(baseline.driver, "run_feedback_case", failed_driver)
    definition = baseline.build_definition(1, cases=("rejection_correction",))
    report = await experiment.run_experiment(
        definition=definition, state_root=tmp_path / "state", trial_report_dir=tmp_path / "records"
    )
    assert report["aggregate"]["denominators"]["trials_recorded"] == 1
    trial = report["trials"][0]
    raw = json.loads((tmp_path / "records" / trial["report"]).read_bytes())
    assert raw["runs"][0]["rounds"][0]["tool_calls"] == [{"function": None}]
    assert raw["runs"][0]["summary"]["correction_compliance"] is False
    assert report["aggregate"]["cases"][0]["criteria_pass"]["by_source"]["deterministic_injection"]["rate"] == 0.0
