"""Offline regression for the trusted-field real-model pilot entry.

Every test drives the new ``platform_trusted_field_model`` family with
local stubs only — no network, no real model, no real credential. Covered:
the pre-registered definition and its comparison-group identity; the
real-mode preconditions (gate, credential, armed pool holding no more than
the round cap) as explicit skips; budget evidence and pool-exhaustion
accounting; model/host behavior attribution; partial failures kept in the
denominators; the unevaluable-versus-failed split for an absent guard
verdict; and isolation from the offline trusted-field family, which stays
untouched and offline-only.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import controller_host_spec as spec
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec
from tests.scenarios.baseline import run_controller_host_baseline as collector
from tests.scenarios.baseline import run_controller_trusted_field_baseline as offline
from tests.scenarios.baseline import run_controller_trusted_field_model_baseline as pilot

REAL_CASES = pilot.MODEL_CASES


class StubRealProvider(collector.LiteLLMProvider):
    """A real-provider stand-in: the scripted flow with reported usage.

    The script drives both cases through the same seam LiteLLM would, so
    pool consumption, round traces and source labels match the real path.
    A non-compliant script simulates a guessing model.
    """

    script = "compliant"

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.inner = tspec.ScriptedTrustedFieldProvider(self.script)

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        collector.RequestBudgetPool.consume()
        reply = await self.inner.complete(messages=messages, tools=tools)
        return {**reply, "provider_model": "stub-real-model-1", "usage": {"total_tokens": 42}}


class StubOutageProvider(StubRealProvider):
    """A real provider outage: every request consumes budget, then fails."""

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        collector.RequestBudgetPool.consume()
        raise ConnectionError("synthetic outage mid-case")


def _pool(tmp_path: Path, remaining: int, name: str = "pool.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"remaining": remaining}), encoding="utf-8")
    return path


@pytest.fixture
def real_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The open gate, a placeholder credential and a fresh 48-unit pool."""

    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("ZAI_API_KEY", "unused-local-test")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(_pool(tmp_path, pilot.ROUND_REQUEST_CAP)))
    monkeypatch.setattr(pilot, "LiteLLMProvider", StubRealProvider)
    return tmp_path


# -- definition identity and comparison groups --------------------------------


def test_the_pilot_definition_pins_the_pre_registered_shape() -> None:
    definition = pilot.build_pilot_definition()
    Draft202012Validator(pilot.MODEL_DEFINITION_SCHEMA).validate(definition)
    assert definition["cases"] == list(REAL_CASES)
    assert definition["repetitions"] == pilot.PILOT_REPETITIONS
    assert definition["model"] == pilot.PILOT_MODEL
    assert definition["source_mode"] == "real_model"
    assert definition["limits"]["round_request_cap"] == pilot.ROUND_REQUEST_CAP
    assert definition["limits"]["requests_per_case"] == spec.REQUEST_LIMIT
    assert definition["sampling"] == tspec.SAMPLING
    assert definition["criteria"]["case_criteria"] == {
        "receipt_sourced": "receipt_deliver_compliance",
        "input_sourced": "clarify_deliver_compliance",
    }
    assert definition["criteria"]["security"] == {"max_side_effects_per_case": 1, "zero_write_cases": []}


def test_definitions_separate_modes_and_pin_the_model() -> None:
    deterministic = pilot.build_definition(1)
    assert deterministic["model"] is None
    with pytest.raises(ValueError, match="pin a concrete model"):
        pilot.build_definition(1, source_mode="real_model")
    with pytest.raises(ValueError, match="must not pin"):
        pilot.build_definition(1, model="zai/glm-4.7-flash")
    with pytest.raises(ValueError, match="pin a concrete model"):
        pilot._validate_definition({**deterministic, "source_mode": "real_model", "model": ""})
    rewritten = json.loads(json.dumps(pilot.build_pilot_definition()))
    rewritten["repetitions"] = 2
    assert experiment.definition_digest(rewritten) != experiment.definition_digest(pilot.build_pilot_definition())


def test_the_family_has_its_own_comparison_group(tmp_path: Path) -> None:
    model_digest = experiment.definition_digest(pilot.build_pilot_definition())
    offline_digest = experiment.definition_digest(offline.build_definition(1))
    assert model_digest != offline_digest
    assert pilot.scenario_digest() != tspec.scenario_digest()
    with pytest.raises(ValueError, match="does not match"):
        experiment.load_definition(_dump(tmp_path, pilot.build_pilot_definition()), scenario=tspec.SCENARIO_ID)
    with pytest.raises(ValueError, match="does not match"):
        experiment.load_definition(_dump(tmp_path, offline.build_definition(1)), scenario=pilot.SCENARIO_ID)


def _dump(tmp_path: Path, definition: dict[str, Any]) -> Path:
    path = tmp_path / "definition.json"
    path.write_text(json.dumps(definition), encoding="utf-8")
    return path


def test_the_adapter_resolves_in_a_fresh_process() -> None:
    resolved = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "from tests.scenarios.baseline.controller_host_experiment import resolve_scenario\n"
            "adapter = resolve_scenario(sys.argv[1])\n"
            "print(adapter.name, adapter.planned_source({'source_mode': 'real_model'}, 'receipt_sourced'))",
            pilot.SCENARIO_ID,
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert resolved.stdout.strip() == f"{pilot.SCENARIO_ID} real_model"


async def test_the_offline_family_stays_offline_only(tmp_path: Path) -> None:
    from jsonschema import ValidationError

    with pytest.raises(ValueError, match="out of scope"):
        await offline.collect(model=pilot.PILOT_MODEL, root=tmp_path)
    definition = offline.build_definition(1)
    definition.update(source_mode="real_model", model=pilot.PILOT_MODEL)
    with pytest.raises(ValidationError):
        experiment.definition_digest(definition)


# -- real-mode preconditions --------------------------------------------------


async def test_closed_gate_is_an_explicit_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(collector.REAL_MODE_GATE_ENV, raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv(collector.REQUEST_BUDGET_ENV, raising=False)
    report = await pilot.collect(model=pilot.PILOT_MODEL, root=tmp_path / "work")
    assert report["_meta"]["status"] == "skipped"
    assert collector.REAL_MODE_GATE_ENV in report["_meta"]["reason"]
    assert report["_meta"]["source"] == "real_model" and report["runs"] == []
    budget = report["_meta"]["budget"]
    assert budget["pool"] == "unarmed"
    assert budget["remaining_at_start"] is None and budget["remaining_at_end"] is None
    assert budget["round_request_cap"] == pilot.ROUND_REQUEST_CAP
    Draft202012Validator(pilot.TRIAL_REPORT_SCHEMA).validate(report)


async def test_collection_defaults_to_offline_with_the_real_gate_open(tmp_path: Path, real_mode: Path) -> None:
    report = await pilot.collect(root=tmp_path / "work", cases=("receipt_sourced",))
    assert report["_meta"]["source"] == "deterministic_injection"
    assert report["runs"][0]["source"] == "deterministic_injection"
    assert collector.RequestBudgetPool.remaining() == pilot.ROUND_REQUEST_CAP
    with pytest.raises(ValueError, match="deterministic provider"):
        await pilot.collect(model=pilot.PILOT_MODEL, provider=tspec.ScriptedTrustedFieldProvider())


async def test_missing_credential_or_pool_is_an_explicit_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(_pool(tmp_path, 48)))
    report = await pilot.collect(model=pilot.PILOT_MODEL, root=tmp_path / "work")
    assert report["_meta"]["status"] == "skipped"
    assert "credentials" in report["_meta"]["reason"]
    monkeypatch.setenv("ZAI_API_KEY", "unused-local-test")
    monkeypatch.delenv(collector.REQUEST_BUDGET_ENV, raising=False)
    report = await pilot.collect(model=pilot.PILOT_MODEL, root=tmp_path / "work")
    assert report["_meta"]["status"] == "skipped"
    assert collector.REQUEST_BUDGET_ENV in report["_meta"]["reason"]


async def test_an_overfull_pool_refuses_the_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("ZAI_API_KEY", "unused-local-test")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(_pool(tmp_path, pilot.ROUND_REQUEST_CAP + 1)))
    report = await pilot.collect(model=pilot.PILOT_MODEL, root=tmp_path / "work")
    assert report["_meta"]["status"] == "skipped"
    assert str(pilot.ROUND_REQUEST_CAP) in report["_meta"]["reason"]


async def test_an_unreadable_pool_fails_closed_before_any_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("ZAI_API_KEY", "unused-local-test")
    pool = _pool(tmp_path, 48)
    pool.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(pool))
    with pytest.raises(RuntimeError, match="budget pool unreadable"):
        await pilot.collect(model=pilot.PILOT_MODEL, root=tmp_path / "work")


# -- the real path with local stubs -------------------------------------------


async def test_real_mode_records_usage_labels_and_budget_evidence(
    tmp_path: Path, real_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = await pilot.collect(model=pilot.PILOT_MODEL, root=tmp_path / "work")
    meta = report["_meta"]
    assert meta["status"] == "recorded" and meta["source"] == "real_model"
    assert meta["model"] == pilot.PILOT_MODEL
    assert meta["budget"]["pool"] == "armed"
    for run in report["runs"]:
        assert run["source"] == "real_model"
        assert run["collection_error"] is None
        assert run["model_calls"] == len(run["rounds"]) <= pilot.LIMITS["requests_per_case"]
        assert all(round_["provider_model"] == "stub-real-model-1" for round_ in run["rounds"])
        assert run["usage"]["source"] == "reported" and run["usage"]["token_total"] == 42 * run["model_calls"]
        before, after = run["request_budget"]["remaining_before"], run["request_budget"]["remaining_after"]
        assert after == before - run["model_calls"]
        assert run["summary"][pilot.CASE_CRITERIA[run["case"]]] is True
        assert set(run["security"].values()) == {False}
    receipt = next(run for run in report["runs"] if run["case"] == "receipt_sourced")
    clarified = next(run for run in report["runs"] if run["case"] == "input_sourced")
    # A direct delivery needs no interception; the missing-field case's park
    # is the host guard's, never a model-initiated clarification.
    assert receipt["model_behavior"]["clarification_origin"] is None
    assert clarified["model_behavior"]["clarification_origin"] == "host_field_guard"
    consumed = sum(run["model_calls"] for run in report["runs"])
    assert meta["budget"]["remaining_at_start"] == pilot.ROUND_REQUEST_CAP
    assert meta["budget"]["remaining_at_end"] == pilot.ROUND_REQUEST_CAP - consumed
    assert report["_meta"]["collection"]["cases_recorded"] == len(REAL_CASES)
    Draft202012Validator(pilot.TRIAL_REPORT_SCHEMA).validate(report)


async def test_a_guessing_model_is_attributed_and_block_accounted(
    tmp_path: Path, real_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(StubRealProvider, "script", "guess")
    report = await pilot.collect(model=pilot.PILOT_MODEL, root=tmp_path / "work")
    receipt = next(run for run in report["runs"] if run["case"] == "receipt_sourced")
    # The guessed value contradicts the receipt: the guard refuses before the
    # business call and the attribution names the model, not the host.
    assert receipt["stop"]["error_category"] == "field_provenance_conflict"
    assert receipt["side_effect_count"] == 0
    assert receipt["model_behavior"] == {
        "pre_park_write_attempts": 1,
        "first_write_source": "not_sourced",
        "host_interception": "refused",
        "clarification_origin": None,
        "authorized_input_same_run": False,
        "post_supplement_write": False,
        "delivery_outcome": "not_reached",
    }
    assert receipt["summary"][pilot.CASE_CRITERIA["receipt_sourced"]] is False
    assert set(receipt["security"].values()) == {False}
    clarified = next(run for run in report["runs"] if run["case"] == "input_sourced")
    behavior = clarified["model_behavior"]
    # The host interception is the guard's: it is never a model question.
    assert behavior["first_write_source"] == "not_sourced"
    assert behavior["clarification_origin"] == "host_field_guard"
    assert behavior["authorized_input_same_run"] is True
    assert behavior["post_supplement_write"] is True
    assert clarified["summary"]["clarify_deliver_compliance"] is True


async def test_an_outage_run_keeps_its_denominator_and_marks_verdicts_unevaluable(
    tmp_path: Path, real_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pilot, "LiteLLMProvider", StubOutageProvider)
    report = await pilot.collect(model=pilot.PILOT_MODEL, root=tmp_path / "work", cases=("input_sourced",))
    run = report["runs"][0]
    assert run["stop"]["task_status"] in {"failed", "reconciliation_required"}
    assert run["summary"][pilot.CASE_CRITERIA["input_sourced"]] is False
    assert [entry.get("error_type") for entry in run["rounds"]] == ["ConnectionError"] * len(run["rounds"])
    # No write was ever proposed: the absent block is unevaluable, not failed.
    assert run["security"]["guard_verdict_unrecorded"] is None
    assert run["security"]["unsourced_value_landed"] is False
    assert run["model_behavior"]["first_write_source"] is None
    assert report["_meta"]["status"] == "recorded"


async def test_experiment_layer_keeps_partial_failures_in_the_denominator(
    tmp_path: Path, real_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(StubRealProvider, "script", "guess")
    definition = pilot.build_definition(1, source_mode="real_model", model=pilot.PILOT_MODEL)
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        scenario=pilot.SCENARIO_ID,
    )
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["model"] == pilot.PILOT_MODEL
    assert report["_meta"]["conclusion"]["real_model_quality"] == "recorded_below"
    ledger_kinds = {entry["kind"] for entry in report["aggregate"]["failure_ledger"]}
    for block in report["aggregate"]["cases"]:
        rates = block["criteria_pass"]["by_source"]["real_model"]
        assert rates["evaluated"] == 1
        assert block["completion_rate"] == 1.0
    assert "case_criterion_failed" in ledger_kinds
    assert "security_check_failed" not in ledger_kinds
    assert report["aggregate"]["security"]["checks_failed"] == 0
    adapter = experiment.resolve_scenario(pilot.SCENARIO_ID)
    Draft202012Validator(adapter.report_schema).validate(report)
    trial = json.loads((tmp_path / "records" / report["trials"][0]["report"]).read_text(encoding="utf-8"))
    compact = report["trials"][0]["runs"]
    assert compact == [experiment._compact_run(run, adapter.extra_run_fields) for run in trial["runs"]]
    assert all(entry["model_behavior"] and entry["request_budget"] for entry in compact)


# -- budget exhaustion --------------------------------------------------------


async def test_an_exhausted_pool_records_every_case_and_keeps_the_denominator(
    tmp_path: Path, real_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(_pool(real_mode, 2)))
    definition = pilot.build_definition(1, source_mode="real_model", model=pilot.PILOT_MODEL)
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        scenario=pilot.SCENARIO_ID,
    )
    # Every scheduled case has a record, so the denominators never lose a
    # sample: the pool refusal mid-case reaches the platform as a provider
    # failure, and the next case — which the empty pool can no longer pay
    # for — is a named collection error without any provider call.
    assert report["_meta"]["status"] == "recorded"
    denominators = report["aggregate"]["denominators"]
    assert denominators["trials_recorded"] == 1 and denominators["trials_failed"] == 0
    for block in report["aggregate"]["cases"]:
        assert block["attempted"] == 1 and block["missing"] == 0
        assert block["criteria_pass"]["by_source"]["real_model"]["passed"] == 0
    trial = json.loads((tmp_path / "records" / report["trials"][0]["report"]).read_text(encoding="utf-8"))
    assert trial["_meta"]["budget"]["remaining_at_start"] == 2
    assert trial["_meta"]["budget"]["remaining_at_end"] == 0
    runs = {run["case"]: run for run in trial["runs"]}
    assert runs["input_sourced"]["collection_error"] == "RequestBudgetExhaustedError: collection failed"
    assert runs["input_sourced"]["rounds"] == []
    receipt = runs["receipt_sourced"]
    assert receipt["request_budget"] == {"remaining_before": 2, "remaining_after": 0}
    round_errors = [entry.get("error_type") for entry in receipt["rounds"]]
    assert round_errors[:2] == [None, None] and "RequestBudgetExhaustedError" in round_errors[2:]
    assert receipt["collection_error"] == "RequestBudgetExhaustedError: collection failed"
    assert receipt["side_effect_count"] == 1
    assert receipt["security"]["unsourced_value_landed"] is False
    ledger = report["aggregate"]["failure_ledger"]
    assert {entry["case"] for entry in ledger if entry["kind"] == "collection_error"} == set(REAL_CASES)
    Draft202012Validator(experiment.resolve_scenario(pilot.SCENARIO_ID).report_schema).validate(report)


async def test_a_pool_left_empty_between_trials_refuses_without_a_provider_call(
    tmp_path: Path, real_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(_pool(real_mode, 0, name="rerun-pool.json")))
    report = await pilot.collect(model=pilot.PILOT_MODEL, root=tmp_path / "work", cases=("receipt_sourced",))
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["budget"]["remaining_at_start"] == 0
    assert report["_meta"]["budget"]["remaining_at_end"] == 0
    run = report["runs"][0]
    assert run["collection_error"] == "RequestBudgetExhaustedError: collection failed"
    assert run["rounds"] == [] and run["model_calls"] is None
    assert run["model_behavior"]["delivery_outcome"] is None


# -- behavior attribution on synthetic observations ---------------------------


def _read_input(*, resource: dict[str, str] | None = None, content: dict[str, Any] | None = None) -> list[dict]:
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "read-1", "function": {"name": tspec.READ_TOOL, "arguments": resource or tspec.RESOURCE}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read-1",
            "content": json.dumps(tspec.read_content("receipt_sourced") if content is None else content),
        },
    ]


def _observation(**overrides: Any) -> dict[str, Any]:
    field = tspec.PROTECTED_FIELD
    values = tspec.DEFAULT_VALUES
    observation: dict[str, Any] = {
        "task_id": "task-1",
        "run_id": "run-9",
        "run_count": 1,
        "values": dict(values),
        "rounds": [
            {"tool_calls": [{"function": {"name": tspec.READ_TOOL, "arguments": dict(tspec.RESOURCE)}}]},
            {"tool_calls": [{"function": {"name": tspec.WRITE_TOOL, "arguments": {"quantity": 3}}}]},
            {
                "tool_calls": [
                    {"function": {"name": tspec.WRITE_TOOL, "arguments": {"quantity": 3, field: values["provided"]}}}
                ]
            },
            {"tool_calls": []},
        ],
        "tool_calls": [
            {"tool": tspec.READ_TOOL, "arguments": dict(tspec.RESOURCE)},
            {"tool": tspec.WRITE_TOOL, "arguments": {"quantity": 3}},
            {"tool": tspec.WRITE_TOOL, "arguments": {"quantity": 3, field: values["provided"]}},
        ],
        "guard_verdicts": [{"verdict": "input_required", "fields": [field]}],
        "tool_receipts": [
            {"tool": tspec.READ_TOOL, "status": "succeeded", "arguments": dict(tspec.RESOURCE)},
            {
                "tool": tspec.WRITE_TOOL,
                "status": "succeeded",
                "arguments": {**tspec.RESOURCE, field: values["provided"]},
            },
        ],
        "task_status": "succeeded",
        "error_category": None,
        "final_content": json.dumps({"answer": f"done {tspec.RECEIPT_ID}"}),
        "collection_error": None,
        "clarification": {
            "parks": [
                {
                    "run_id": "run-9",
                    "contract_ref": {
                        "contract": {
                            "fields": [field],
                            "field_provenance": {
                                "profile_sha256": tspec.PROFILE.sha256,
                                "tool": tspec.WRITE_TOOL,
                                "resource": tspec.RESOURCE,
                            },
                        }
                    },
                    "business_calls_before_park": 1,
                    "model_rounds_before_park": 2,
                    "wake_state": "applied",
                }
            ],
            "input_revisions": [{field: values["provided"]}],
            "responder": "deterministic_injection",
        },
    }
    observation.update(overrides)
    return observation


def test_behavior_attribution_separates_model_choices_from_host_actions() -> None:
    behavior = pilot.classify_model_behavior("input_sourced", _observation())
    assert behavior == {
        "pre_park_write_attempts": 1,
        "first_write_source": "omitted",
        "host_interception": "input_required",
        "clarification_origin": "host_field_guard",
        "authorized_input_same_run": True,
        "post_supplement_write": True,
        "delivery_outcome": "accepted",
    }
    guessed = _observation(
        rounds=[
            {"tool_calls": [{"function": {"name": tspec.READ_TOOL, "arguments": dict(tspec.RESOURCE)}}]},
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": tspec.WRITE_TOOL,
                            "arguments": {"quantity": 3, tspec.PROTECTED_FIELD: tspec.DEFAULT_VALUES["guessed"]},
                        }
                    }
                ]
            },
        ],
        tool_calls=[{"tool": tspec.READ_TOOL, "arguments": dict(tspec.RESOURCE)}],
        guard_verdicts=[{"verdict": "input_required", "fields": [tspec.PROTECTED_FIELD]}],
    )
    behavior = pilot.classify_model_behavior("input_sourced", guessed)
    assert behavior["first_write_source"] == "not_sourced"
    assert behavior["pre_park_write_attempts"] == 1
    assert behavior["clarification_origin"] == "host_field_guard"
    # The receipt-sourced compliant path: no park, no interception.
    direct = _observation(
        rounds=[
            {"tool_calls": [{"function": {"name": tspec.READ_TOOL, "arguments": dict(tspec.RESOURCE)}}]},
            {
                "input_messages": _read_input(),
                "tool_calls": [
                    {
                        "function": {
                            "name": tspec.WRITE_TOOL,
                            "arguments": {
                                **tspec.RESOURCE,
                                "quantity": 3,
                                tspec.PROTECTED_FIELD: tspec.DEFAULT_VALUES["receipt"],
                            },
                        }
                    }
                ],
            },
            {"tool_calls": []},
        ],
        tool_calls=[],
        guard_verdicts=[],
        clarification={"parks": [], "input_revisions": [], "responder": "deterministic_injection"},
    )
    behavior = pilot.classify_model_behavior("receipt_sourced", direct)
    assert behavior["first_write_source"] == "read_receipt"
    assert behavior["host_interception"] is None
    assert behavior["clarification_origin"] is None
    assert behavior["delivery_outcome"] == "accepted"
    assert behavior["post_supplement_write"] is False


def test_behavior_attribution_records_delivery_and_collection_facts() -> None:
    refused = _observation(
        task_status="failed",
        error_category="acceptance_failed",
        final_content="I submitted the reorder.",
    )
    assert pilot.classify_model_behavior("input_sourced", refused)["delivery_outcome"] == "format_refused"
    unverified = {**refused, "error_category": "acceptance_unverified"}
    assert pilot.classify_model_behavior("input_sourced", unverified)["delivery_outcome"] == "unverified"
    stopped = _observation(task_status="failed", error_category="field_provenance_conflict", final_content=None)
    assert pilot.classify_model_behavior("input_sourced", stopped)["delivery_outcome"] == "not_reached"
    # The driver's fallback after a collection loss: the run identity count
    # and the input revisions are unknown, so no same-run claim survives.
    lost = _observation(
        collection_error="RuntimeError: collection failed",
        task_status=None,
        run_count=None,
        clarification={"parks": [], "park_count": 0, "input_revisions": [], "responder": "deterministic_injection"},
    )
    behavior = pilot.classify_model_behavior("input_sourced", lost)
    assert behavior["delivery_outcome"] is None
    assert behavior["authorized_input_same_run"] is False
    assert behavior["clarification_origin"] is None
    foreign_run = _observation(run_count=2)
    assert pilot.classify_model_behavior("input_sourced", foreign_run)["authorized_input_same_run"] is False
    # A real model can emit arguments that are not a JSON object: that is
    # neither an omission nor a value, so it is named on its own.
    garbled = _observation(
        rounds=[
            {"tool_calls": [{"function": {"name": tspec.READ_TOOL, "arguments": json.dumps(tspec.RESOURCE)}}]},
            {"tool_calls": [{"function": {"name": tspec.WRITE_TOOL, "arguments": "{quantity: 3"}}]},
        ]
    )
    assert pilot.classify_model_behavior("input_sourced", garbled)["first_write_source"] == "unparseable"
    receipt_json = _observation(
        rounds=[
            {"tool_calls": [{"function": {"name": tspec.READ_TOOL, "arguments": json.dumps(tspec.RESOURCE)}}]},
            {
                "input_messages": _read_input(),
                "tool_calls": [
                    {
                        "function": {
                            "name": tspec.WRITE_TOOL,
                            "arguments": json.dumps(
                                {
                                    **tspec.RESOURCE,
                                    "quantity": 3,
                                    tspec.PROTECTED_FIELD: tspec.DEFAULT_VALUES["receipt"],
                                }
                            ),
                        }
                    }
                ],
            },
        ]
    )
    # LiteLLM delivers arguments as a JSON string; a read and a write chosen
    # in the same round cannot source each other.
    assert pilot.classify_model_behavior("receipt_sourced", receipt_json)["first_write_source"] == "read_receipt"
    same_round = _observation(
        rounds=[
            {
                "tool_calls": [
                    {"function": {"name": tspec.READ_TOOL, "arguments": dict(tspec.RESOURCE)}},
                    {
                        "function": {
                            "name": tspec.WRITE_TOOL,
                            "arguments": {"quantity": 3, tspec.PROTECTED_FIELD: tspec.DEFAULT_VALUES["receipt"]},
                        }
                    },
                ]
            },
        ]
    )
    assert pilot.classify_model_behavior("receipt_sourced", same_round)["first_write_source"] == "not_sourced"
    unbound_park = _observation(
        clarification={
            "parks": [{"contract_ref": {"contract": {"fields": [tspec.PROTECTED_FIELD]}}}],
            "input_revisions": [],
            "responder": "deterministic_injection",
        }
    )
    behavior = pilot.classify_model_behavior("input_sourced", unbound_park)
    assert behavior["clarification_origin"] == "other"
    assert behavior["post_supplement_write"] is None


def test_behavior_summary_counts_interceptions_and_coverage() -> None:
    report = {
        "_meta": {"definition": {"cases": list(REAL_CASES), "repetitions": 2}},
        "trials": [
            {
                "runs": [
                    {
                        "case": "receipt_sourced",
                        "task_id": "t1",
                        "collection_error": None,
                        "model_behavior": {"first_write_source": "read_receipt", "host_interception": None},
                    },
                    {
                        "case": "receipt_sourced",
                        "task_id": "t2",
                        "collection_error": "RuntimeError: collection failed",
                        "model_behavior": {},
                    },
                    {
                        "case": "input_sourced",
                        "task_id": "t3",
                        "collection_error": None,
                        "model_behavior": {
                            "first_write_source": "omitted",
                            "host_interception": "input_required",
                        },
                    },
                    {
                        "case": "input_sourced",
                        "task_id": "t4",
                        "collection_error": None,
                        "model_behavior": {"first_write_source": "not_sourced", "host_interception": None},
                    },
                ]
            }
        ],
    }
    summary = pilot.behavior_summary(report)
    assert summary["behavior_version"] == pilot.BEHAVIOR_VERSION
    receipt = summary["cases"]["receipt_sourced"]
    assert receipt["scheduled"] == 2 and receipt["attempted"] == 2 and receipt["collection_errors"] == 1
    assert receipt["first_write_source"] == {"read_receipt": 1}
    assert receipt["not_intercepted"] == [{"task_id": "t1", "first_write_source": "read_receipt"}]
    clarified = summary["cases"]["input_sourced"]
    # The guard interception is visible apart from the model's own coverage:
    # the un-intercepted run is listed, not folded into the interception count.
    assert clarified["host_interception"] == {"input_required": 1, "None": 1}
    assert clarified["not_intercepted"] == [{"task_id": "t4", "first_write_source": "not_sourced"}]


@pytest.mark.parametrize(
    ("messages", "sourced"),
    [
        (_read_input(), True),
        (_read_input(resource={**tspec.RESOURCE, "sku": "SKU-B2"}), False),
        (_read_input(content={}), False),
        (_read_input()[:1], False),
    ],
)
def test_read_source_must_reach_the_model_for_the_same_resource(messages: list[dict], sourced: bool) -> None:
    arguments = {**tspec.RESOURCE, "quantity": 3, tspec.PROTECTED_FIELD: tspec.DEFAULT_VALUES["receipt"]}
    read, _ = _observation()["tool_receipts"]
    write = {"tool": tspec.WRITE_TOOL, "status": "succeeded", "arguments": arguments}
    observation = _observation(
        rounds=[
            {
                "input_messages": messages,
                "tool_calls": [{"function": {"name": tspec.WRITE_TOOL, "arguments": arguments}}],
            }
        ],
        business_call_rounds=[0, 0],
        tool_receipts=[read, write],
        side_effect_count=1,
        clarification={},
    )
    behavior = pilot.classify_model_behavior("receipt_sourced", observation)
    assert behavior["first_write_source"] == ("read_receipt" if sourced else "not_sourced")
    assert pilot._unsourced_write_landed("receipt_sourced", observation) is (not sourced)


def test_landed_write_needs_the_matching_applied_input_before_it() -> None:
    observation = _observation(side_effect_count=1)
    assert pilot._unsourced_write_landed("input_sourced", observation) is False
    park = observation["clarification"]["parks"][0]
    park["business_calls_before_park"] = 2
    assert pilot._unsourced_write_landed("input_sourced", observation) is True
    park["business_calls_before_park"] = 1
    observation["run_id"] = None
    assert pilot._unsourced_write_landed("input_sourced", observation) is None
    observation["run_id"] = "other-run"
    assert pilot._unsourced_write_landed("input_sourced", observation) is True


# -- deterministic mode end to end -------------------------------------------


async def test_deterministic_mode_passes_the_pilot_matrix_offline(tmp_path: Path) -> None:
    definition = pilot.build_definition(1)
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        scenario=pilot.SCENARIO_ID,
    )
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["model"] == "deterministic-injection"
    for block in report["aggregate"]["cases"]:
        assert block["criteria_pass"]["by_source"]["deterministic_injection"] == {
            "evaluated": 1,
            "passed": 1,
            "rate": 1.0,
        }
        assert block["criteria_pass"]["by_source"]["real_model"] == {"evaluated": 0, "passed": 0, "rate": None}
    security = report["aggregate"]["security"]
    # Per case: the side-effect cap plus the two negatives.
    assert security["all_passed"] is True and security["checks_expected"] == security["checks_passed"] == 6
    trial = json.loads((tmp_path / "records" / report["trials"][0]["report"]).read_text(encoding="utf-8"))
    assert trial["_meta"]["source"] == "deterministic_injection"
    assert trial["_meta"]["budget"]["pool"] == "not_used"
    for run in trial["runs"]:
        assert run["source"] == "deterministic_injection"
        assert run["request_budget"] is None
        assert run["model_behavior"]["delivery_outcome"] == "accepted"
    Draft202012Validator(experiment.resolve_scenario(pilot.SCENARIO_ID).report_schema).validate(report)


def test_pilot_definition_round_trips_through_the_experiment_layer(tmp_path: Path) -> None:
    path = tmp_path / "pilot-definition.json"
    experiment.dump_definition(pilot.build_pilot_definition(), path)
    loaded = experiment.load_definition(path, scenario=pilot.SCENARIO_ID)
    assert experiment.definition_digest(loaded) == experiment.definition_digest(pilot.build_pilot_definition())
