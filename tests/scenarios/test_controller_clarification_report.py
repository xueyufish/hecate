"""Hermetic offline tests for the platform Controller clarification baseline.

Everything drives the REAL platform assembly (durable dispatch, governance
facade, waiting_input park, real provide_input command, same-run resume)
with the deterministic injected provider — no network, no real model. The
tests pin the paired behavior (clarify only when the field is unknown,
direct delivery when it is known), the negative scoring verdicts, the
aggregate denominators and failure preservation, the unknown-stays-unknown
boundaries, and the clarification scenario's independent definition
identity inside the shared experiment layer. The real-model entry is
verified with offline provider stand-ins patched over the dual-host
adapter seam: the gated path, the run-level source labels, the failure
windows and the absence of any deterministic fallback.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tests.scenarios.baseline import controller_host_clarification_spec as cspec
from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import run_controller_clarification_baseline as baseline
from tests.scenarios.baseline import run_controller_host_baseline as collector

pytestmark = pytest.mark.timeout(600)


@pytest.fixture(scope="module")
def clarification_definition():
    return baseline.build_definition(repetitions=2)


@pytest.fixture(scope="module")
async def clarification_artifacts(tmp_path_factory, clarification_definition):
    root = tmp_path_factory.mktemp("controller-clarification")
    report = await experiment.run_experiment(
        definition=clarification_definition,
        state_root=root / "state",
        trial_report_dir=root / "records",
        scenario=cspec.SCENARIO_ID,
    )
    return report, root / "records"


@pytest.fixture(scope="module")
def clarification_report(clarification_artifacts):
    return clarification_artifacts[0]


def _case_block(report: dict, case: str) -> dict:
    return next(block for block in report["aggregate"]["cases"] if block["case"] == case)


def _trial_run(artifacts: tuple, trial_index: int, case: str) -> dict:
    """The FULL run from the trial record file: raw traces live there, not
    in the aggregate's verdict projection."""

    report, records_dir = artifacts
    trial = report["trials"][trial_index]
    record = json.loads((records_dir / trial["report"]).read_text(encoding="utf-8"))
    return next(run for run in record["runs"] if run["case"] == case)


# --- paired behavior on the real platform assembly ---


def test_clarify_case_parks_once_and_delivers_with_provided_field(clarification_artifacts) -> None:
    for trial_index in (0, 1):
        run = _trial_run(clarification_artifacts, trial_index, "clarify_missing")
        assert run["collection_error"] is None, run
        assert all(turn["input_tools"] == cspec.tool_definitions() for turn in run["rounds"])
        summary = run["summary"]
        assert summary["clarify_deliver_compliance"] is True, summary
        assert summary["same_run"] is True
        assert run["run_count"] == 1
        assert run["run_id"] == run["clarification"]["parks"][0]["run_id"]
        assert summary["clarification_contract_exact"] is True
        assert summary["zero_side_effect_before_request"] is True
        assert summary["supplement_visible_to_model"] is True
        assert summary["input_revision_recorded"] is True
        assert summary["provided_field_used_verbatim"] is True
        assert summary["artifact_reference_correct"] is True
        assert not any(summary["negatives"].values()), summary

        clarification = run["clarification"]
        assert clarification["park_count"] == 1
        park = clarification["parks"][0]
        assert park["wake_kind"] == "provide_input"
        assert park["contract_ref"]["tool"] == cspec.WRITE_TOOL
        assert park["contract_ref"]["contract"]["fields"] == [cspec.REQUIRED_FIELD]
        assert park["side_effects_before_park"] == 0
        assert park["acknowledged"] is True
        assert park["wait_ms_harness"] >= 0
        assert clarification["responder"] == "deterministic_injection"
        assert clarification["input_revisions"] == [{cspec.REQUIRED_FIELD: cspec.PROVIDED_ORDER_REF}]

        # The request and the resumed write, in order, on the governed tool.
        writes = [call for call in run["tool_calls"] if call["tool"] == cspec.WRITE_TOOL]
        assert [call["arguments"].get(cspec.REQUIRED_FIELD) for call in writes] == [None, cspec.PROVIDED_ORDER_REF]
        assert run["side_effect_count"] == 1 and run["write_attempts"] == 2
        assert run["stop"]["task_status"] == "succeeded"
        assert run["model_calls"] == 4


def test_complete_case_delivers_directly_with_zero_clarifications(clarification_artifacts) -> None:
    for trial_index in (0, 1):
        run = _trial_run(clarification_artifacts, trial_index, "field_complete")
        assert run["collection_error"] is None, run
        assert all(turn["input_tools"] == cspec.tool_definitions() for turn in run["rounds"])
        summary = run["summary"]
        assert summary["direct_deliver_compliance"] is True, summary
        assert summary["zero_clarification"] is True
        assert summary["provided_field_used_verbatim"] is True
        assert not any(summary["negatives"].values()), summary

        assert run["clarification"]["park_count"] == 0
        assert run["clarification"]["parks"] == []
        writes = [call for call in run["tool_calls"] if call["tool"] == cspec.WRITE_TOOL]
        assert len(writes) == 1 and writes[0]["arguments"] == cspec.EXPECTED_PROVIDED_WRITE
        assert run["side_effect_count"] == 1 and run["write_attempts"] == 1
        assert run["stop"]["task_status"] == "succeeded"
        assert run["model_calls"] == 3


def test_paired_cases_differ_only_in_field_availability(clarification_artifacts) -> None:
    first = _trial_run(clarification_artifacts, 0, "clarify_missing")
    second = _trial_run(clarification_artifacts, 0, "field_complete")
    # Same prompt, same tools, same receipt except the paired field.
    assert first["rounds"][0]["input_messages"] == second["rounds"][0]["input_messages"]
    read_first = next(receipt for receipt in first["tool_receipts"] if receipt["tool"] == cspec.READ_TOOL)
    read_second = next(receipt for receipt in second["tool_receipts"] if receipt["tool"] == cspec.READ_TOOL)
    difference = {key for key in set(read_first["content"]) ^ set(read_second["content"])}
    assert difference == {cspec.REQUIRED_FIELD}
    assert read_second["content"][cspec.REQUIRED_FIELD] == cspec.PROVIDED_ORDER_REF


# --- negative scoring verdicts ---


def _base_observation(case: str = "clarify_missing") -> dict:
    return {
        "task_status": "succeeded",
        "run_id": "run-1",
        "run_count": 1,
        "error_category": None,
        "tool_calls": [
            {"tool": cspec.READ_TOOL, "arguments": cspec.EXPECTED_READ},
            {"tool": cspec.WRITE_TOOL, "arguments": dict(cspec.WRITE_BASE)},
            {"tool": cspec.WRITE_TOOL, "arguments": dict(cspec.EXPECTED_PROVIDED_WRITE)},
        ],
        "final_content": json.dumps(
            {"answer": f"submitted {cspec.PROVIDED_ORDER_REF}; update {cspec.CLARIFY_RECEIPT_ID}"}
        ),
        "side_effect_count": 1,
        "write_attempts": 2,
        "tool_receipts": [
            {
                "tool": cspec.WRITE_TOOL,
                "status": "succeeded",
                "arguments": dict(cspec.EXPECTED_PROVIDED_WRITE),
                "content": {"status": "ok", "update_id": cspec.CLARIFY_RECEIPT_ID},
            }
        ],
        "clarification": {
            "parks": [
                {
                    "run_id": "run-1",
                    "wake_kind": "provide_input",
                    "wake_state": "applied",
                    "contract_ref": {
                        "tool": cspec.WRITE_TOOL,
                        "execution_id": "exec-1",
                        "contract": {"fields": [cspec.REQUIRED_FIELD]},
                    },
                    "consumed": False,
                    "acknowledged": True,
                    "side_effects_before_park": 0,
                    "write_attempts_before_park": 1,
                    "wait_ms_harness": 12.0,
                }
            ],
            "park_count": 1,
            "input_revisions": [{cspec.REQUIRED_FIELD: cspec.PROVIDED_ORDER_REF}],
            "supplement_visible_in_model_input": True,
            "responder": "deterministic_injection",
        },
    }


def test_compliant_baseline_observation_scores_true() -> None:
    summary = cspec.score_clarification_run("clarify_missing", _base_observation())
    assert summary["clarify_deliver_compliance"] is True


def test_guessed_field_is_a_negative_verdict() -> None:
    observation = _base_observation()
    observation["tool_calls"][2] = {
        "tool": cspec.WRITE_TOOL,
        "arguments": {**cspec.EXPECTED_PROVIDED_WRITE, cspec.REQUIRED_FIELD: "GUESSED-123"},
    }
    summary = cspec.score_clarification_run("clarify_missing", observation)
    assert summary["negatives"]["guessed_field"] is True
    assert summary["clarify_deliver_compliance"] is False


def test_premature_write_is_a_negative_verdict() -> None:
    observation = _base_observation()
    observation["clarification"]["parks"][0]["side_effects_before_park"] = 1
    summary = cspec.score_clarification_run("clarify_missing", observation)
    assert summary["negatives"]["premature_write"] is True
    assert summary["clarify_deliver_compliance"] is False


def test_duplicate_write_is_a_negative_verdict() -> None:
    observation = _base_observation()
    observation["side_effect_count"] = 2
    summary = cspec.score_clarification_run("clarify_missing", observation)
    assert summary["negatives"]["duplicate_write"] is True
    assert summary["clarify_deliver_compliance"] is False


def test_invalid_clarification_is_a_negative_verdict() -> None:
    drifted_contract = _base_observation()
    drifted_contract["clarification"]["parks"][0]["contract_ref"]["contract"]["fields"] = ["tier"]
    summary = cspec.score_clarification_run("clarify_missing", drifted_contract)
    assert summary["negatives"]["invalid_clarification"] is True
    assert summary["clarify_deliver_compliance"] is False

    unacknowledged = _base_observation()
    unacknowledged["clarification"]["parks"][0]["acknowledged"] = False
    assert cspec.score_clarification_run("clarify_missing", unacknowledged)["clarify_deliver_compliance"] is False

    parked_when_known = _base_observation("field_complete")
    parked_when_known["clarification"]["park_count"] = 1
    summary = cspec.score_clarification_run("field_complete", parked_when_known)
    assert summary["negatives"]["invalid_clarification"] is True
    assert summary["direct_deliver_compliance"] is False


def test_forged_receipt_is_a_negative_verdict() -> None:
    observation = _base_observation()
    observation["final_content"] = json.dumps({"answer": f"update {cspec.CLARIFY_RECEIPT_ID}-FAKE"})
    summary = cspec.score_clarification_run("clarify_missing", observation)
    assert summary["negatives"]["forged_receipt"] is True
    assert summary["clarify_deliver_compliance"] is False

    no_receipt = _base_observation()
    no_receipt["final_content"] = json.dumps({"answer": "done"})
    assert cspec.score_clarification_run("clarify_missing", no_receipt)["clarify_deliver_compliance"] is False


# --- definition identity and scenario isolation ---


def test_clarification_definition_has_independent_identity(clarification_definition) -> None:
    definition = clarification_definition
    Draft202012Validator(cspec.CLARIFICATION_DEFINITION_SCHEMA).validate(definition)
    assert definition["scenario"] == cspec.SCENARIO_ID
    assert definition["source_mode"] == "deterministic_injection"
    assert definition["criteria"]["case_criteria"] == cspec.CASE_CRITERIA
    # Independent digest space: never equal to the dual-host matrix identity.
    dual_host = experiment.build_definition(repetitions=2)
    assert experiment.definition_digest(definition) != experiment.definition_digest(dual_host)


def test_real_model_definitions_pin_a_model_and_form_their_own_group() -> None:
    pinned = baseline.build_definition(1, source_mode="real_model", model="zai/test-model")
    Draft202012Validator(cspec.CLARIFICATION_DEFINITION_SCHEMA).validate(pinned)
    deterministic = baseline.build_definition(1)
    # The two modes never share a comparison group.
    assert experiment.definition_digest(pinned) != experiment.definition_digest(deterministic)
    with pytest.raises(ValueError, match="pin a concrete model"):
        baseline.build_definition(1, source_mode="real_model")
    with pytest.raises(ValueError, match="must not pin"):
        baseline.build_definition(1, model="zai/test-model")


async def test_unpinned_real_model_definition_is_rejected(tmp_path) -> None:
    definition = baseline.build_definition(1)
    definition["source_mode"] = "real_model"
    with pytest.raises(ValidationError):
        await experiment.run_experiment(
            definition=definition,
            state_root=tmp_path / "state",
            trial_report_dir=tmp_path / "records",
            scenario=cspec.SCENARIO_ID,
        )


async def test_scenario_field_is_enforced_against_the_adapter(tmp_path) -> None:
    definition = baseline.build_definition(1)
    dual_host_definition = {**definition, "scenario": experiment.DEFAULT_SCENARIO}
    with pytest.raises(ValueError, match="scenario"):
        await experiment.run_experiment(
            definition=dual_host_definition,
            state_root=tmp_path / "state",
            trial_report_dir=tmp_path / "records",
            scenario=cspec.SCENARIO_ID,
        )


async def test_missing_gate_is_an_explicit_skip(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(collector.REAL_MODE_GATE_ENV, raising=False)
    report = await baseline.collect(model="zai/test-model", root=tmp_path)
    assert report["_meta"]["status"] == "skipped"
    assert report["runs"] == []
    assert collector.REAL_MODE_GATE_ENV in report["_meta"]["reason"]
    assert report["_meta"]["source"] == "real_model"
    assert report["_meta"]["model"] == "zai/test-model"
    Draft202012Validator(baseline.TRIAL_REPORT_SCHEMA).validate(report)


async def test_missing_credentials_are_an_explicit_skip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    report = await baseline.collect(model="zai/test-model", root=tmp_path)
    assert report["_meta"]["status"] == "skipped"
    assert "credentials" in report["_meta"]["reason"]
    Draft202012Validator(baseline.TRIAL_REPORT_SCHEMA).validate(report)


def test_cross_scenario_comparison_is_refused(clarification_report) -> None:
    dual_host = copy.deepcopy(clarification_report)
    dual_host_definition = experiment.build_definition(repetitions=2)
    dual_host["_meta"]["definition"] = dual_host_definition
    dual_host["_meta"]["definition_digest"] = experiment.definition_digest(dual_host_definition)
    comparison = experiment.compare_records(clarification_report, dual_host)
    assert comparison["comparable"] is False


# --- aggregate honesty ---


def test_aggregate_denominators_security_and_source_grouping(clarification_report) -> None:
    report = clarification_report
    assert report["_meta"]["status"] == "recorded"
    assert report["aggregate"]["denominators"] == {
        "repetitions_requested": 2,
        "trials_recorded": 2,
        "trials_skipped": 0,
        "trials_failed": 0,
    }
    for case in cspec.CLARIFICATION_CASES:
        block = _case_block(report, case)
        assert block["expected"] == 2 and block["recorded"] == 2 and block["completion_rate"] == 1.0
        rates = block["criteria_pass"]["by_source"]
        assert rates["deterministic_injection"] == {"evaluated": 2, "passed": 2, "rate": 1.0}
        assert rates["real_model"] == {"evaluated": 0, "passed": 0, "rate": None}
    security = report["aggregate"]["security"]
    assert security["all_passed"] is True and security["checks_failed"] == 0
    assert security["checks_evaluable"] == security["checks_expected"] == 10
    assert report["aggregate"]["failure_ledger"] == []
    assert report["_meta"]["gate"] is False and report["_meta"]["business_value"] == "unverified"


def test_unknown_stays_unknown_for_usage_cost_and_human_wait(clarification_report) -> None:
    report = clarification_report
    usage = report["aggregate"]["usage"]
    assert usage["reported_runs"] == 0 and usage["reported_token_total"] is None
    assert report["aggregate"]["cost"]["status"] == "unknown"
    unverified = " ".join(report["_meta"]["unverified"])
    assert "human wait" in unverified
    assert "real-model" in unverified
    for trial in report["trials"]:
        for run in trial["runs"]:
            assert run["usage"]["cost_amount"] is None
            assert run["clarification"]["responder"] == "deterministic_injection"


def test_trial_failure_is_preserved_with_the_scenario_adapter(tmp_path) -> None:
    definition = baseline.build_definition(repetitions=2, cases=("clarify_missing",))

    def factory(index: int):
        if index == 0:
            raise RuntimeError("synthetic clarification trial loss")
        return cspec.DeterministicClarificationProvider()

    report = asyncio.run(
        experiment.run_experiment(
            definition=definition,
            state_root=tmp_path / "state",
            trial_report_dir=tmp_path / "records",
            provider_factory=factory,
            scenario=cspec.SCENARIO_ID,
        )
    )
    failed, recorded = report["trials"]
    assert failed["status"] == "failed" and "RuntimeError" in failed["reason"]
    assert recorded["status"] == "recorded"
    block = _case_block(report, "clarify_missing")
    assert block["expected"] == 2 and block["attempted"] == 1 and block["missing"] == 1
    assert block["completion_rate"] == 0.5
    assert block["failure_kinds"]["missing_from_failed_trial"] == 1
    security = report["aggregate"]["security"]
    assert security["checks_missing"] == 3 and security["all_passed"] is None
    Draft202012Validator(experiment.resolve_scenario(cspec.SCENARIO_ID).report_schema).validate(report)


def test_aggregate_schema_rejects_tampering(clarification_report) -> None:
    schema = experiment.resolve_scenario(cspec.SCENARIO_ID).report_schema
    version_drift = copy.deepcopy(clarification_report)
    version_drift["_meta"]["experiment_version"] = "controller-host-experiment-0"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(version_drift)
    scenario_drift = copy.deepcopy(clarification_report)
    scenario_drift["_meta"]["definition"]["scenario"] = experiment.DEFAULT_SCENARIO
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(scenario_drift)


# --- CLI surface ---


def test_cli_prints_valid_default_definition(capsys) -> None:
    assert baseline.main(["--print-default-definition"]) is None
    printed = json.loads(capsys.readouterr().out)
    Draft202012Validator(cspec.CLARIFICATION_DEFINITION_SCHEMA).validate(printed)
    assert printed["scenario"] == cspec.SCENARIO_ID and printed["repetitions"] == 3


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
    Draft202012Validator(experiment.resolve_scenario(cspec.SCENARIO_ID).report_schema).validate(record)
    trial = record["trials"][0]
    trial_text = (tmp_path / "records" / trial["report"]).read_text(encoding="utf-8")
    assert experiment.text_digest(trial_text) == trial["report_digest"]
    trial_record = json.loads(trial_text)
    Draft202012Validator(baseline.TRIAL_REPORT_SCHEMA).validate(trial_record)
    assert trial_record["_meta"]["kind"] == "controller_host_clarification"


def test_dual_host_history_and_identity_untouched() -> None:
    # The dual-host matrix keeps its own identity, criteria and schemas.
    assert experiment.DEFAULT_SCENARIO == "controller_host_matrix"
    assert experiment.CASE_CRITERIA["normal"] == "delivery_compliance"
    dual_host = experiment.build_definition(repetitions=1)
    Draft202012Validator(experiment.DEFINITION_SCHEMA).validate(dual_host)
    assert "scenario" not in dual_host


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(run_id="replacement-run"),
        lambda row: row.update(run_count=2),
        lambda row: row["clarification"]["parks"][0].pop("side_effects_before_park"),
        lambda row: row["clarification"]["parks"][0].update(wake_kind="approval"),
        lambda row: row["clarification"]["parks"][0].update(wake_state="rejected"),
        lambda row: row["clarification"]["parks"][0]["contract_ref"].pop("execution_id"),
        lambda row: row["tool_calls"].insert(1, {"tool": cspec.WRITE_TOOL, "arguments": dict(cspec.WRITE_BASE)}),
        lambda row: row["tool_receipts"][0].update(status="input_required"),
        lambda row: row["tool_receipts"][0].update(tool=cspec.READ_TOOL),
        lambda row: row.update(final_content=json.dumps({"answer": "done", "debug": cspec.CLARIFY_RECEIPT_ID})),
    ],
)
def test_incomplete_or_inconsistent_evidence_cannot_pass(mutation) -> None:
    observation = _base_observation()
    mutation(observation)
    assert cspec.score_clarification_run("clarify_missing", observation)["clarify_deliver_compliance"] is False


def test_even_a_correct_guess_before_input_is_rejected() -> None:
    observation = _base_observation()
    observation["tool_calls"][1]["arguments"][cspec.REQUIRED_FIELD] = cspec.PROVIDED_ORDER_REF
    summary = cspec.score_clarification_run("clarify_missing", observation)
    assert summary["negatives"]["guessed_field"] is True
    assert summary["clarify_deliver_compliance"] is False


@pytest.mark.parametrize("collection_error", [None, "lost final observation"])
def test_premature_write_fails_independent_security_even_after_collection_loss(collection_error) -> None:
    observation = _base_observation()
    observation["clarification"]["parks"][0]["side_effects_before_park"] = 1
    summary = cspec.score_clarification_run("clarify_missing", observation)
    definition = baseline.build_definition(1, cases=("clarify_missing",))
    trial = {
        "trial_id": "trial-1",
        "status": "recorded",
        "runs": [
            {
                "host": "platform",
                "case": "clarify_missing",
                "summary": summary,
                "side_effect_count": 1,
                "collection_error": collection_error,
            }
        ],
    }
    security = experiment._security_block(definition, [trial], adapter=experiment.resolve_scenario(cspec.SCENARIO_ID))
    assert security["all_passed"] is False
    assert security["checks_failed"] == 1
    assert security["failures"][0]["rule"] == "premature_write"
    ledger = []
    cases = experiment._case_blocks(definition, [trial], ledger)
    assert cases[0]["failure_kinds"]["security_check_failed"] == 1
    assert any(entry["kind"] == "security_check_failed" for entry in ledger)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row["criteria"]["case_criteria"].update(clarify_missing="direct_deliver_compliance"),
        lambda row: row["criteria"]["security"].update(zero_write_cases=["clarify_missing"]),
    ],
)
async def test_clarification_definition_rejects_changed_scoring_rules(tmp_path, mutation) -> None:
    definition = baseline.build_definition(1)
    mutation(definition)
    with pytest.raises(ValidationError):
        await experiment.run_experiment(
            definition=definition, state_root=tmp_path / "state", trial_report_dir=tmp_path / "records"
        )


async def test_deterministic_provider_uses_received_fields_and_ignores_prompt_markers() -> None:
    provider = cspec.DeterministicClarificationProvider()
    prompt = [{"role": "user", "content": f"provided_input {cspec.PROVIDED_ORDER_REF} {cspec.CLARIFY_RECEIPT_ID}"}]
    result = await provider.complete(messages=prompt, tools=cspec.tool_definitions())
    assert result["tool_calls"][0]["function"]["name"] == cspec.READ_TOOL
    messages = [
        *prompt,
        {"role": "tool", "content": json.dumps({**cspec.read_content("clarify_missing"), "quantity": 7})},
        {"role": "user", "content": json.dumps({"provided_input": {cspec.REQUIRED_FIELD: "PO-CHANGED"}})},
    ]
    result = await provider.complete(messages=messages, tools=cspec.tool_definitions())
    assert result["tool_calls"][0]["function"]["arguments"] == {
        **cspec.WRITE_BASE,
        "quantity": 7,
        cspec.REQUIRED_FIELD: "PO-CHANGED",
    }


def test_shared_loader_resolves_clarification_in_a_fresh_process(tmp_path) -> None:
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
    assert result.stdout.strip() == cspec.SCENARIO_ID


async def test_default_scenario_resolves_the_current_collector(tmp_path, monkeypatch) -> None:
    from tests.scenarios.baseline import run_controller_host_baseline as collector

    calls = []

    async def collect(**kwargs):
        calls.append(kwargs)
        return {"_meta": {"status": "recorded"}, "runs": []}

    monkeypatch.setattr(collector, "collect", collect)
    await experiment.run_experiment(
        definition=experiment.build_definition(repetitions=1, hosts=("platform",), cases=("normal",)),
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
    )
    assert len(calls) == 1


async def test_deterministic_provider_accepts_live_tool_repr() -> None:
    provider = cspec.DeterministicClarificationProvider()
    result = await provider.complete(
        messages=[{"role": "tool", "content": repr(cspec.read_content("field_complete"))}],
        tools=cspec.tool_definitions(),
    )
    assert result["tool_calls"][0]["function"]["arguments"] == cspec.EXPECTED_PROVIDED_WRITE


# --- real-model entry: offline stand-ins over the gated path ---


class StubRealProvider:
    """Offline stand-in for the real adapter: compliant actions, reported
    usage and a provider model version, like a real LiteLLM round."""

    name = "fake-real-model"

    def __init__(self, model: str) -> None:
        self.model = model
        self.inner = cspec.DeterministicClarificationProvider()

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict:
        reply = await self.inner.complete(messages=messages, tools=tools)
        return {**reply, "provider_model": "fake-real-model-2026-11", "usage": {"total_tokens": 42}}


class StubFailingRealProvider:
    """A real provider outage: every call raises."""

    name = "failing-real-model"

    def __init__(self, model: str) -> None:
        self.model = model

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict:
        raise ConnectionError("synthetic provider outage mid-case")


@pytest.fixture
def real_mode_env(monkeypatch):
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-local-test")
    return monkeypatch


async def test_real_mode_runs_are_labeled_and_keep_the_simulated_responder(
    tmp_path, real_mode_env, monkeypatch
) -> None:
    monkeypatch.setattr(baseline, "LiteLLMProvider", StubRealProvider)
    report = await baseline.collect(model="fake/test-model", root=tmp_path)
    meta = report["_meta"]
    assert meta["status"] == "recorded" and meta["source"] == "real_model"
    assert meta["model"] == "fake/test-model"
    assert meta["dataset_source"] == "synthetic_inventory"
    for run in report["runs"]:
        assert run["source"] == "real_model", run
        assert run["clarification"]["responder"] == "deterministic_injection"
        expected_tokens = 42 * (4 if run["case"] == "clarify_missing" else 3)
        assert run["usage"]["source"] == "reported" and run["usage"]["token_total"] == expected_tokens
        assert run["collection_error"] is None
        assert all(round_["provider_model"] == "fake-real-model-2026-11" for round_ in run["rounds"])
        assert run["model_calls"] == (4 if run["case"] == "clarify_missing" else 3)
    Draft202012Validator(baseline.TRIAL_REPORT_SCHEMA).validate(report)


async def test_real_mode_failure_is_recorded_without_deterministic_fallback(
    tmp_path, real_mode_env, monkeypatch
) -> None:
    monkeypatch.setattr(baseline, "LiteLLMProvider", StubFailingRealProvider)
    report = await baseline.collect(model="fake/test-model", root=tmp_path)
    assert report["_meta"]["source"] == "real_model"
    for run in report["runs"]:
        assert run["source"] == "real_model", run
        round_errors = [r.get("error_type") for r in run["rounds"]]
        if run["collection_error"] is not None:
            # Under load the controller's retry window can exceed the case
            # budget: the driver records the loss itself. Either window is
            # an honest failure record; neither falls back.
            assert run["usage"]["source"] == "unverified"
        else:
            assert run["stop"]["task_status"] in {"failed", "reconciliation_required"}
            summary = run["summary"]
            criterion = summary.get("clarify_deliver_compliance", summary.get("direct_deliver_compliance"))
            assert criterion is False
            assert round_errors and set(round_errors) == {"ConnectionError"}
            assert run["usage"]["source"] == "unverified"
    Draft202012Validator(baseline.TRIAL_REPORT_SCHEMA).validate(report)


async def test_real_mode_experiment_keeps_denominators_and_source_grouping(
    tmp_path, real_mode_env, monkeypatch
) -> None:
    monkeypatch.setattr(baseline, "LiteLLMProvider", StubRealProvider)
    definition = baseline.build_definition(1, source_mode="real_model", model="fake/test-model")
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        scenario=cspec.SCENARIO_ID,
    )
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["model"] == "fake/test-model"
    assert report["_meta"]["conclusion"]["real_model_quality"] == "recorded_below"
    for case in cspec.CLARIFICATION_CASES:
        block = _case_block(report, case)
        rates = block["criteria_pass"]["by_source"]
        assert rates["real_model"] == {"evaluated": 1, "passed": 1, "rate": 1.0}
        assert rates["deterministic_injection"] == {"evaluated": 0, "passed": 0, "rate": None}
    security = report["aggregate"]["security"]
    assert security["all_passed"] is True and security["checks_failed"] == 0
    Draft202012Validator(experiment.resolve_scenario(cspec.SCENARIO_ID).report_schema).validate(report)


async def test_real_mode_failed_trials_stay_in_the_denominator(tmp_path, real_mode_env, monkeypatch) -> None:
    monkeypatch.setattr(baseline, "LiteLLMProvider", StubFailingRealProvider)
    definition = baseline.build_definition(1, source_mode="real_model", model="fake/test-model")
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        scenario=cspec.SCENARIO_ID,
    )
    ledger_kinds = [entry["kind"] for entry in report["aggregate"]["failure_ledger"]]
    for case in cspec.CLARIFICATION_CASES:
        block = _case_block(report, case)
        rates = block["criteria_pass"]["by_source"]
        if block["collection_errors"]:
            # Load-driven driver loss: the sample stays missing from the
            # denominator, never silently dropped.
            assert block["missing"] + block["collection_errors"] == 1
            assert rates["real_model"]["evaluated"] == 0 and rates["real_model"]["rate"] is None
            assert "collection_error" in ledger_kinds
        else:
            # The runs completed collection, so the denominator is intact
            # and the pass rate honestly reads zero.
            assert rates["real_model"] == {"evaluated": 1, "passed": 0, "rate": 0.0}
            assert block["completion_rate"] == 1.0
            assert "case_criterion_failed" in ledger_kinds


def test_committed_clarification_history_stays_valid() -> None:
    records = sorted(
        Path(collector.BASELINE_DIR).glob("controller_host_experiment_controller-host-clarification-*.json")
    )
    assert records, "the committed clarification records must be present"
    for path in records:
        record = json.loads(path.read_text(encoding="utf-8"))
        # The recorded identity must remain self-consistent, and the old
        # deterministic definitions stay valid under the widened schema.
        assert experiment.comparison_group(record)
        Draft202012Validator(cspec.CLARIFICATION_DEFINITION_SCHEMA).validate(record["_meta"]["definition"])


def test_cli_real_mode_without_gate_writes_skipped_aggregate(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(collector.REAL_MODE_GATE_ENV, raising=False)
    out = baseline.main(
        [
            "--model",
            "zai/test-model",
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
    assert record["_meta"]["status"] == "skipped"
    assert record["aggregate"]["denominators"]["trials_skipped"] == 1
    # The skip reason lives on the trial record the aggregate pins.
    trial = record["trials"][0]
    trial_record = json.loads((tmp_path / "records" / trial["report"]).read_text(encoding="utf-8"))
    assert collector.REAL_MODE_GATE_ENV in trial_record["_meta"]["reason"]
    Draft202012Validator(experiment.resolve_scenario(cspec.SCENARIO_ID).report_schema).validate(record)


@pytest.mark.parametrize("missing", ["gate", "credentials"])
async def test_injected_real_provider_still_requires_entry_conditions(tmp_path, monkeypatch, missing) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-local-test")
    if missing == "gate":
        monkeypatch.delenv(collector.REAL_MODE_GATE_ENV)
    else:
        monkeypatch.delenv("OPENAI_API_KEY")
    calls = []

    async def unexpected_driver(*args):
        calls.append(args)
        raise AssertionError("the driver must not start")

    monkeypatch.setattr(baseline.driver, "run_clarification_case", unexpected_driver)
    report = await baseline.collect(
        provider=baseline.LiteLLMProvider("openai/test-model"),
        cases=("field_complete",),
        root=tmp_path,
    )
    assert report["_meta"]["status"] == "skipped"
    assert report["_meta"]["source"] == "real_model"
    assert report["_meta"]["model"] == "openai/test-model"
    assert report["runs"] == [] and calls == []


async def test_cancelled_gateway_request_remains_in_the_trace() -> None:
    from tests.scenarios.baseline.controller_host_platform import _RecordingGateway

    entered = asyncio.Event()

    class StubHangingProvider:
        async def complete(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    gateway = _RecordingGateway(StubHangingProvider())
    stream = gateway.chat_stream(messages=[{"role": "user", "content": "test"}], tools=[])
    task = asyncio.create_task(anext(stream))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(gateway.rounds) == 1
    assert gateway.rounds[0]["error_type"] == "CancelledError"
    assert gateway.rounds[0]["usage"] is None
    assert gateway.rounds[0]["input_messages"] == [{"role": "user", "content": "test"}]


@pytest.mark.parametrize("extra", [["--model", "openai/other-model"], ["--repetitions", "1"]])
def test_cli_rejects_overrides_of_a_pinned_definition(tmp_path, monkeypatch, extra) -> None:
    monkeypatch.delenv(collector.REAL_MODE_GATE_ENV, raising=False)
    path = tmp_path / "definition.json"
    experiment.dump_definition(baseline.build_definition(2, source_mode="real_model", model="openai/test-model"), path)
    with pytest.raises(SystemExit) as error:
        baseline.main(
            [
                "--definition",
                str(path),
                *extra,
                "--state-root",
                str(tmp_path / "state"),
                "--trial-report-dir",
                str(tmp_path / "records"),
                "--report-dir",
                str(tmp_path / "reports"),
            ]
        )
    assert error.value.code == 2


async def test_injected_real_provider_cannot_disagree_with_the_pinned_model(tmp_path) -> None:
    with pytest.raises(ValueError, match="match"):
        await baseline.collect(
            provider=baseline.LiteLLMProvider("openai/actual-model"),
            model="openai/pinned-model",
            root=tmp_path,
        )


@pytest.mark.parametrize("invalid_output", [False, True])
async def test_real_adapter_sdk_path_preserves_choices_and_invalid_output(
    tmp_path, real_mode_env, monkeypatch, invalid_output
) -> None:
    import litellm

    calls = []
    stand_in = cspec.DeterministicClarificationProvider()

    async def completion(**kwargs):
        calls.append(kwargs)
        reply = (
            {"content": "invalid structured answer", "tool_calls": []}
            if invalid_output
            else await stand_in.complete(messages=kwargs["messages"], tools=kwargs["tools"])
        )
        tool_calls = copy.deepcopy(reply["tool_calls"])
        for call in tool_calls:
            call["function"]["arguments"] = json.dumps(call["function"]["arguments"])
        return litellm.ModelResponse(
            model="sdk-reported-model-version",
            choices=[{"message": {"role": "assistant", "content": reply["content"], "tool_calls": tool_calls}}],
            usage={"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
        )

    monkeypatch.setattr(litellm, "acompletion", completion)
    provider = baseline.LiteLLMProvider("openai/test-model")
    report = await baseline.collect(provider=provider, root=tmp_path)
    assert report["_meta"]["source"] == "real_model"
    assert report["_meta"]["model"] == "openai/test-model"
    assert len(calls) == (2 if invalid_output else 7)
    for call in calls:
        assert call["model"] == "openai/test-model"
        assert call["temperature"] == cspec.SAMPLING["temperature"]
        assert call["max_tokens"] == cspec.SAMPLING["max_tokens"]
        assert call["timeout"] == cspec.SAMPLING["timeout_seconds"]
        assert call["num_retries"] == 0
        assert call["tools"] == cspec.tool_definitions()
    for run in report["runs"]:
        assert run["source"] == "real_model"
        assert run["collection_error"] is None
        assert run["summary"][cspec.CASE_CRITERIA[run["case"]]] is not invalid_output
        assert all(turn["provider_model"] == "sdk-reported-model-version" for turn in run["rounds"])
        assert run["usage"]["token_total"] == 42 * len(run["rounds"])
        assert run["usage"]["cost_amount"] is None
        if invalid_output:
            assert run["final_content"] == "invalid structured answer"
            assert run["stop"]["task_status"] == "failed"
            assert run["side_effect_count"] == 0


@pytest.mark.parametrize("model", ["", " "])
def test_cli_empty_real_model_never_selects_a_deterministic_experiment(tmp_path, model) -> None:
    with pytest.raises(ValueError, match="pin a concrete model"):
        baseline.main(
            [
                "--model",
                model,
                "--state-root",
                str(tmp_path / "state"),
                "--trial-report-dir",
                str(tmp_path / "records"),
                "--report-dir",
                str(tmp_path / "reports"),
            ]
        )
    assert not (tmp_path / "records").exists()
