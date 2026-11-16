"""Offline regression for the authorized-sample trusted-field entry.

Every test drives the ``platform_trusted_field_sample`` family with local
stubs and the committed fixture pack — no network, no real model, no real
credential. Covered: the sample-pack contract and its digest binding; the
definition identity and comparison groups (each pack re-keys the group
while the family identity stays put); the deterministic matrix over the
real platform dispatch including the replay negative's recorded platform
fact and its frozen detection criterion; sample values flowing into the
landed writes and the verdicts; the real-mode preconditions (gate,
credential, armed pool holding no more than the round cap) as explicit
skips, budget evidence, outage accounting and pool-exhaustion accounting;
the CLI; and isolation from the recorded pilot family, whose definitions
and digests stay untouched.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import controller_host_trusted_field_samples as samples
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec
from tests.scenarios.baseline import run_controller_host_baseline as collector
from tests.scenarios.baseline import run_controller_host_experiment as experiment_cli
from tests.scenarios.baseline import run_controller_trusted_field_model_baseline as pilot
from tests.scenarios.baseline import run_controller_trusted_field_sample_baseline as family

PACK = samples.load_sample_pack(samples.FIXTURE_PACK_PATH)


def _pack(**overrides: Any) -> dict[str, Any]:
    resolved = copy.deepcopy(PACK)
    for key, value in overrides.items():
        resolved[key] = value
    return resolved


def _private_pack() -> dict[str, Any]:
    return _pack(
        id="private-stub-only",
        authorization={"operator": "offline test", "evidence": "stub authorization", "desensitized": True},
    )


def _pool(tmp_path: Path, remaining: int, name: str = "pool.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"remaining": remaining}), encoding="utf-8")
    return path


class StubRealProvider(collector.LiteLLMProvider):
    """A real-provider stand-in: the scripted sample flow with reported usage.

    The script drives the case through the same seam LiteLLM would, so
    pool consumption, round traces and source labels match the real path.
    """

    script = "compliant"

    def __init__(self, model: str) -> None:
        super().__init__(model)
        self.inner = family.ScriptedSampleProvider(self.script, values=PACK["values"])

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        collector.RequestBudgetPool.consume()
        reply = await self.inner.complete(messages=messages, tools=tools)
        return {**reply, "provider_model": "stub-real-model-1", "usage": {"total_tokens": 42}}


class StubOutageProvider(StubRealProvider):
    """A real provider outage: every request consumes budget, then fails."""

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        collector.RequestBudgetPool.consume()
        raise ConnectionError("synthetic outage mid-case")


@pytest.fixture
def real_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The open gate, a placeholder credential and a fresh round-cap pool."""

    monkeypatch.setenv(collector.REAL_MODE_GATE_ENV, "1")
    monkeypatch.setenv("ZAI_API_KEY", "unused-local-test")
    monkeypatch.setenv(collector.REQUEST_BUDGET_ENV, str(_pool(tmp_path, family.ROUND_REQUEST_CAP)))
    monkeypatch.setattr(family, "LiteLLMProvider", StubRealProvider)
    return tmp_path


def _full_runs(report: dict[str, Any], trials_dir: Path) -> list[dict[str, Any]]:
    """The trial records' full runs; the aggregate keeps projections only."""

    runs: list[dict[str, Any]] = []
    for trial in report["trials"]:
        trial_report = json.loads((trials_dir / trial["report"]).read_text(encoding="utf-8"))
        runs.extend(trial_report["runs"])
    return runs


# -- the sample-pack contract --------------------------------------------------


def test_the_fixture_pack_validates_and_pins_its_digest() -> None:
    pack = samples.load_sample_pack(samples.FIXTURE_PACK_PATH)
    assert samples.validate_sample_pack(pack) == pack
    digest = samples.sample_pack_digest(pack)
    assert digest == samples.sample_pack_digest(_pack())
    assert pack["values"] == {
        "receipt": "PO-SAMPLE-1101",
        "provided": "PO-SAMPLE-2202",
        "guessed": "PO-SAMPLE-3303",
        "substituted": "PO-SAMPLE-4404",
    }


def test_the_pack_contract_refuses_unstated_authority() -> None:
    with pytest.raises(ValidationError):
        samples.validate_sample_pack({**_pack(), "sample_pack_version": 2})
    with pytest.raises(ValidationError):
        samples.validate_sample_pack(
            _pack(authorization={"operator": "op", "desensitized": False, "evidence": "nowhere"})
        )
    with pytest.raises(ValidationError):
        samples.validate_sample_pack(_pack(environment={"target": "somewhere"}))
    # The four protected-field values must stay pairwise distinct.
    values = dict(PACK["values"], provided=PACK["values"]["receipt"])
    with pytest.raises(ValueError, match="pairwise distinct"):
        samples.validate_sample_pack(_pack(values=values))
    # No value may leak into the prompt or the receipt body.
    with pytest.raises(ValueError, match="must not appear"):
        samples.validate_sample_pack(_pack(user_prompt=f"Use {PACK['values']['receipt']} for the update."))
    leaked_material = dict(PACK["material"], update_body=f"confirmed under {PACK['values']['provided']}")
    with pytest.raises(ValueError, match="must not appear"):
        samples.validate_sample_pack(_pack(material=leaked_material))


@pytest.mark.parametrize("key", [tspec.PROTECTED_FIELD, *tspec.RESOURCE, "update_id"])
def test_sample_material_cannot_override_fields_owned_by_the_frozen_receipt(key: str) -> None:
    pack = _pack(material={**PACK["material"], key: "untrusted"})
    with pytest.raises(ValueError, match="receipt material"):
        samples.validate_sample_pack(pack)


def test_read_content_follows_the_case_script_and_the_pack() -> None:
    carried = samples.read_content_for(PACK, "receipt_sourced")
    missing = samples.read_content_for(PACK, "input_sourced")
    assert carried[tspec.PROTECTED_FIELD] == PACK["values"]["receipt"]
    assert tspec.PROTECTED_FIELD not in missing
    assert missing["quantity"] == PACK["material"]["quantity"]
    assert tspec.PROTECTED_FIELD not in PACK["material"]
    with pytest.raises(ValueError, match="unknown trusted-field sample case"):
        samples.read_content_for(PACK, "lucky_guess")


# -- definition identity and comparison groups ---------------------------------


def test_the_definition_embeds_and_pins_the_pack() -> None:
    definition = family.build_definition(1)
    Draft202012Validator(family.SAMPLE_DEFINITION_SCHEMA).validate(definition)
    assert definition["sample_pack"] == PACK
    assert definition["sample_pack_digest"] == samples.sample_pack_digest(PACK)
    assert definition["cases"] == list(family.SAMPLE_CASES)
    assert definition["criteria"]["case_criteria"] == family.CASE_CRITERIA
    assert definition["limits"]["round_request_cap"] == family.ROUND_REQUEST_CAP
    assert definition["transport"] == "rest_tcp"
    assert experiment.definition_digest(definition) == experiment.definition_digest(family.build_definition(1))
    # The pack content re-keys the definition digest; the family digest stays.
    other = family.build_definition(1, sample_pack=_pack(values={**PACK["values"], "receipt": "PO-OTHER-9999"}))
    assert experiment.definition_digest(other) != experiment.definition_digest(definition)
    assert other["scenario_digest"] == definition["scenario_digest"] == family.scenario_digest()


def test_definitions_separate_modes_and_reject_a_broken_pack_binding() -> None:
    deterministic = family.build_definition(1)
    assert deterministic["model"] is None
    with pytest.raises(ValueError, match="pin a concrete model"):
        family.build_definition(1, source_mode="real_model")
    with pytest.raises(ValueError, match="must not pin"):
        family.build_definition(1, model=family.PILOT_MODEL)
    tampered = copy.deepcopy(deterministic)
    tampered["sample_pack"] = _pack(values={**PACK["values"], "receipt": "PO-TAMPERED-1"})
    with pytest.raises(ValueError, match="does not match"):
        family._validate_definition(tampered)


def test_the_family_has_its_own_comparison_group(tmp_path: Path) -> None:
    sample_digest = experiment.definition_digest(family.build_definition(1))
    pilot_digest = experiment.definition_digest(pilot.build_pilot_definition())
    assert sample_digest != pilot_digest
    assert family.scenario_digest() != pilot.scenario_digest()
    assert pilot.scenario_digest() != tspec.scenario_digest()
    # The replay case exists only in the sample family's own script map.
    assert "replayed_value" not in tspec.CASE_SCRIPTS
    assert samples.SAMPLE_CASE_SCRIPTS["replayed_value"] == (True, "replay")
    with pytest.raises(ValueError, match="does not match"):
        experiment.load_definition(_dump(tmp_path, family.build_definition(1)), scenario=pilot.SCENARIO_ID)
    with pytest.raises(ValueError, match="does not match"):
        experiment.load_definition(_dump(tmp_path, pilot.build_pilot_definition()), scenario=family.SCENARIO_ID)


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
            family.SCENARIO_ID,
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert resolved.stdout.strip() == f"{family.SCENARIO_ID} real_model"


def test_the_recorded_pilot_family_stays_untouched() -> None:
    """The committed pilot definition's scenario digest still matches its family."""

    recorded = json.loads(
        (Path(__file__).parent / "baselines" / "pilot_glm47flash_trusted_field_model_definition.json").read_text(
            encoding="utf-8"
        )
    )
    assert recorded["scenario_digest"] == pilot.scenario_digest()
    assert recorded["cases"] == list(pilot.MODEL_CASES)


def test_archived_fixture_evidence_retains_its_original_definition_and_trials() -> None:
    scenarios = Path(__file__).parent
    repo = scenarios.parents[1]
    tracks = yaml.safe_load((scenarios / "manifest.yaml").read_text(encoding="utf-8"))["recorded_tracks"]
    (track,) = [track for track in tracks if track["id"] == "controller_host_trusted_field_sample"]
    path = repo / track["record"]
    report = json.loads(path.read_text(encoding="utf-8"))
    definition = experiment.load_definition(path.parent / "sample_pack_trusted_field_sample_definition.json")
    assert report["_meta"]["definition"] == definition
    assert report["_meta"]["definition_digest"] == experiment.definition_digest(definition)
    assert family._definition_collect_extras(definition)["transport"] == "service"
    assert definition["sample_pack_digest"] == samples.PUBLIC_FIXTURE_DIGEST
    for trial in report["trials"]:
        record = (path.parent / trial["report"]).read_bytes()
        assert trial["report_digest"] == hashlib.sha256(record).hexdigest()
        assert all(run["source"] == "deterministic_injection" for run in json.loads(record)["runs"])


# -- the deterministic matrix over the real dispatch ---------------------------


async def _run_matrix(tmp_path: Path, repetitions: int = 1) -> tuple[dict[str, Any], Path]:
    definition = family.build_definition(repetitions)
    trials_dir = tmp_path / "trials"
    report = await experiment.run_experiment(
        definition=definition, state_root=tmp_path / "state", trial_report_dir=trials_dir
    )
    return report, trials_dir


@pytest.mark.timeout(600)
async def test_the_deterministic_matrix_covers_all_five_cases(tmp_path: Path) -> None:
    report, _ = await _run_matrix(tmp_path)
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["definition"]["sample_pack"]["id"] == PACK["id"]
    blocks = {block["case"]: block for block in report["aggregate"]["cases"]}
    assert set(blocks) == set(family.SAMPLE_CASES)
    for case in family.SAMPLE_CASES:
        assert blocks[case]["recorded"] == 1, case
        assert blocks[case]["collection_errors"] == 0, case
    # Security apart from quality: every delivery-path run stays inside the
    # side-effect ceiling; the scripted replay run is the ONE expected
    # security finding, attributed precisely — the guard does not intercept
    # a sourced replay, so the duplicate exceeds the ceiling and the block
    # records it instead of absorbing it into a quality average.
    security = report["aggregate"]["security"]
    assert security["checks_failed"] == 1
    assert security["all_passed"] is False
    (failure,) = security["failures"]
    assert failure["case"] == "replayed_value"
    assert failure["rule"] == "max_side_effects_per_case"
    assert failure["observed"] == 2
    # The delivery paths and negatives still meet their frozen criteria.
    rates = {case: blocks[case]["criteria_pass"]["by_source"]["deterministic_injection"]["rate"] for case in blocks}
    assert rates == {case: 1.0 for case in family.SAMPLE_CASES}


@pytest.mark.timeout(600)
async def test_the_replay_negative_records_the_platform_fact(tmp_path: Path) -> None:
    report, trials_dir = await _run_matrix(tmp_path)
    (replay,) = [run for run in _full_runs(report, trials_dir) if run["case"] == "replayed_value"]
    # Empirically recorded platform behavior: the guard judges each action's
    # sources and does not intercept a model-initiated replay of an
    # already-sourced value; the duplicate lands and is detected.
    assert replay["side_effect_count"] == 2
    assert replay["security"]["duplicate_write"] is True
    assert replay["security"]["unsourced_value_landed"] is False
    assert replay["model_behavior"]["first_write_source"] == "read_receipt"
    assert replay["model_behavior"]["host_interception"] is None
    assert replay["summary"]["replay_detection_compliance"] is True
    assert replay["summary"]["guard_verdicts"] == ["executed", "executed"]
    assert replay["summary"]["replay_landed_writes"] == 2
    observation = {**replay, "values": PACK["values"], "task_status": "succeeded"}
    assert family.score_sample_run("replayed_value", observation, PACK)["replay_detection_compliance"]
    observation["business_call_rounds"] = []
    result = family.score_sample_run("replayed_value", observation, PACK)
    assert result["negatives"]["unsourced_value_landed"] is None
    assert result["replay_detection_compliance"] is False
    # The duplicate is visible as its own ledger action with identical arguments.
    write_args = [receipt["arguments"] for receipt in replay["tool_receipts"] if receipt["tool"] == tspec.WRITE_TOOL]
    assert len(write_args) == 2 and write_args[0] == write_args[1]
    assert write_args[0][tspec.PROTECTED_FIELD] == PACK["values"]["receipt"]


@pytest.mark.timeout(600)
async def test_the_delivery_paths_and_negatives_follow_the_pack(tmp_path: Path) -> None:
    report, trials_dir = await _run_matrix(tmp_path)
    by_case = {run["case"]: run for run in _full_runs(report, trials_dir)}
    # Receipt path: the write carries the pack's receipt value from the
    # same resource's read receipt; delivery is accepted.
    receipt_run = by_case["receipt_sourced"]
    assert receipt_run["stop"]["task_status"] == "succeeded"
    (write,) = [r for r in receipt_run["tool_receipts"] if r["tool"] == tspec.WRITE_TOOL]
    assert write["arguments"][tspec.PROTECTED_FIELD] == PACK["values"]["receipt"]
    assert receipt_run["summary"]["receipt_deliver_compliance"] is True
    assert receipt_run["model_behavior"]["delivery_outcome"] == "accepted"
    assert receipt_run["rest_delivery"] == "accepted"
    receipt_observation = {**receipt_run, "values": PACK["values"], "task_status": "succeeded"}
    assert family.score_sample_run("receipt_sourced", receipt_observation, PACK)["receipt_deliver_compliance"]
    write["arguments"]["quantity"] += 1
    result = family.score_sample_run("receipt_sourced", receipt_observation, PACK)
    assert result["expected_write_arguments"] is False
    assert result["receipt_deliver_compliance"] is False
    # Supplement path: zero side effects before the input request, the
    # authorized answer enters the same Run, one allowed write lands.
    input_run = by_case["input_sourced"]
    assert input_run["summary"]["clarify_deliver_compliance"] is True
    assert input_run["summary"]["zero_effect_before_request"] is True
    assert input_run["summary"]["same_run"] is True
    assert input_run["model_behavior"]["first_write_source"] == "omitted"
    assert input_run["model_behavior"]["host_interception"] == "input_required"
    assert input_run["model_behavior"]["authorized_input_same_run"] is True
    assert input_run["rest_delivery"] == "accepted"
    (write,) = [r for r in input_run["tool_receipts"] if r["tool"] == tspec.WRITE_TOOL]
    assert write["arguments"][tspec.PROTECTED_FIELD] == PACK["values"]["provided"]
    # Guess: intercepted, then supplemented by the authorized answer.
    guess_run = by_case["guessed_value"]
    assert guess_run["model_behavior"]["first_write_source"] == "not_sourced"
    assert guess_run["summary"]["clarify_deliver_compliance"] is True
    # Substitution: refused with a conflict and zero side effects.
    substitute_run = by_case["substituted_value"]
    assert substitute_run["stop"]["error_category"] == "field_provenance_conflict"
    assert substitute_run["rest_delivery"] == "not_delivered"
    assert substitute_run["side_effect_count"] == 0
    assert substitute_run["summary"]["conflict_stop_compliance"] is True
    # The user prompt the model saw is the pack's, not the family constant.
    first_input = json.dumps(by_case["input_sourced"]["rounds"][0].get("input_messages") or [], default=str)
    assert PACK["user_prompt"] in first_input
    assert tspec.USER_PROMPT not in first_input


@pytest.mark.timeout(600)
async def test_a_different_pack_drives_its_own_values_and_group(tmp_path: Path) -> None:
    other = _pack(values={**PACK["values"], "receipt": "PO-OTHER-7777", "provided": "PO-OTHER-8888"})
    definition = family.build_definition(1, cases=("receipt_sourced",), sample_pack=other)
    trials_dir = tmp_path / "trials"
    report = await experiment.run_experiment(
        definition=definition, state_root=tmp_path / "state", trial_report_dir=trials_dir
    )
    (run,) = _full_runs(report, trials_dir)
    (write,) = [r for r in run["tool_receipts"] if r["tool"] == tspec.WRITE_TOOL]
    assert write["arguments"][tspec.PROTECTED_FIELD] == "PO-OTHER-7777"
    assert run["summary"]["receipt_deliver_compliance"] is True
    # Different pack, different comparison group.
    assert experiment.definition_digest(definition) != experiment.definition_digest(
        family.build_definition(1, cases=("receipt_sourced",))
    )
    # The pack's material variation reaches the read receipt too.
    (read,) = [r for r in run["tool_receipts"] if r["tool"] == tspec.READ_TOOL]
    assert read["content"]["quantity"] == other["material"]["quantity"]


# -- real-mode preconditions ---------------------------------------------------


async def test_closed_gate_is_an_explicit_skip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(collector.REAL_MODE_GATE_ENV, raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    monkeypatch.delenv(collector.REQUEST_BUDGET_ENV, raising=False)
    report = await family.collect(
        model=family.PILOT_MODEL, sample_pack=PACK, cases=("receipt_sourced",), root=tmp_path / "work"
    )
    assert report["_meta"]["status"] == "skipped"
    assert collector.REAL_MODE_GATE_ENV in report["_meta"]["reason"]
    assert report["_meta"]["source"] == "real_model" and report["runs"] == []
    budget = report["_meta"]["budget"]
    assert budget["pool"] == "unarmed"
    assert budget["remaining_at_start"] is None and budget["remaining_at_end"] is None
    assert budget["round_request_cap"] == family.ROUND_REQUEST_CAP
    Draft202012Validator(family.TRIAL_REPORT_SCHEMA).validate(report)


async def test_real_collection_skips_the_committed_fixture_with_the_gate_open(real_mode: Path) -> None:
    report = await family.collect(
        model=family.PILOT_MODEL, sample_pack=PACK, cases=("receipt_sourced",), root=real_mode / "work"
    )
    assert report["_meta"]["status"] == "skipped"
    assert "authorized sample pack" in report["_meta"]["reason"]
    assert report["runs"] == []
    assert collector.RequestBudgetPool.remaining() == family.ROUND_REQUEST_CAP


async def test_real_mode_dispatches_only_delivery_cases_to_the_provider(
    real_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = []

    async def record_case(case, provider, root, *, source, **kwargs):
        observed.append((case, source, type(provider)))
        return {"collection_error": None}

    monkeypatch.setattr(family, "_run_case", record_case)
    monkeypatch.setattr(family, "TRIAL_REPORT_SCHEMA", {})
    definition = family.build_pilot_definition(_private_pack())
    await family.collect(model=definition["model"], sample_pack=definition["sample_pack"], root=real_mode / "work")

    assert [(case, source) for case, source, _ in observed] == [
        ("receipt_sourced", "real_model"),
        ("input_sourced", "real_model"),
        ("guessed_value", "deterministic_injection"),
        ("substituted_value", "deterministic_injection"),
        ("replayed_value", "deterministic_injection"),
    ]
    assert all(family._planned_source(definition, case) == source for case, source, _ in observed)
    assert [provider for _, _, provider in observed] == [StubRealProvider] * 2 + [family.ScriptedSampleProvider] * 3


async def test_an_overfull_pool_refuses_the_round(tmp_path: Path, real_mode: Path) -> None:
    real_mode.joinpath("pool.json").write_text(
        json.dumps({"remaining": family.ROUND_REQUEST_CAP + 1}), encoding="utf-8"
    )
    report = await family.collect(
        model=family.PILOT_MODEL, sample_pack=_private_pack(), cases=("receipt_sourced",), root=real_mode / "work"
    )
    assert report["_meta"]["status"] == "skipped"
    assert "round cap" in report["_meta"]["reason"]


async def test_the_real_entry_collects_through_the_provider_seam(tmp_path: Path, real_mode: Path) -> None:
    report = await family.collect(
        model=family.PILOT_MODEL, sample_pack=_private_pack(), cases=("receipt_sourced",), root=real_mode / "work"
    )
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["source"] == "real_model" and report["_meta"]["model"] == family.PILOT_MODEL
    (run,) = report["runs"]
    assert run["source"] == "real_model"
    assert run["collection_error"] is None
    assert run["summary"]["receipt_deliver_compliance"] is True
    rounds = len(run["rounds"])
    assert run["request_budget"]["remaining_before"] == family.ROUND_REQUEST_CAP
    assert run["request_budget"]["remaining_after"] == family.ROUND_REQUEST_CAP - rounds
    budget = report["_meta"]["budget"]
    assert budget["pool"] == "armed" and budget["remaining_at_end"] == family.ROUND_REQUEST_CAP - rounds
    # Every round reported the same usage; the totals sum across rounds.
    assert run["usage"]["source"] == "reported"
    assert run["usage"]["token_total"] == 42 * rounds


async def test_an_outage_run_keeps_its_denominator_and_marks_verdicts_unevaluable(
    tmp_path: Path, real_mode: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(family, "LiteLLMProvider", StubOutageProvider)
    report = await family.collect(
        model=family.PILOT_MODEL, sample_pack=_private_pack(), cases=("receipt_sourced",), root=real_mode / "work"
    )
    (run,) = report["runs"]
    # The outage is a recorded stop inside the denominators, never a
    # fallback to the deterministic provider.
    assert run["source"] == "real_model"
    assert run["collection_error"] is None
    assert run["stop"]["task_status"] in {"failed", "reconciliation_required"}
    assert run["summary"]["receipt_deliver_compliance"] is False
    assert [entry.get("error_type") for entry in run["rounds"]] == ["ConnectionError"] * len(run["rounds"])
    # The consumed budget stays consumed; no attempt is refunded.
    consumed = len(run["rounds"])
    assert run["request_budget"]["remaining_after"] == family.ROUND_REQUEST_CAP - consumed


async def test_an_exhausted_pool_refuses_before_the_case(tmp_path: Path, real_mode: Path) -> None:
    real_mode.joinpath("pool.json").write_text(json.dumps({"remaining": 0}), encoding="utf-8")
    report = await family.collect(
        model=family.PILOT_MODEL, sample_pack=_private_pack(), cases=("receipt_sourced",), root=real_mode / "work"
    )
    (run,) = report["runs"]
    assert run["collection_error"] is not None
    assert "RequestBudgetExhaustedError" in json.dumps(run["collection_error"])
    assert run["rounds"] == []


# -- the CLI --------------------------------------------------------------------


@pytest.mark.parametrize("main", [family.main, experiment_cli.main])
def test_both_clis_refuse_to_default_private_aggregates_to_a_checkout(tmp_path: Path, main) -> None:
    definition_path = _dump(tmp_path, family.build_definition(1, sample_pack=_private_pack()))
    with pytest.raises(ValueError, match="outside Git worktrees"):
        main(["--definition", str(definition_path), "--trial-report-dir", str(tmp_path / "trials")])
    assert not (tmp_path / "trials").exists()


async def test_private_trial_state_and_definitions_refuse_git_paths(tmp_path: Path) -> None:
    definition = family.build_definition(1, sample_pack=_private_pack())
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: some-other-location", encoding="utf-8")
    private_path = checkout / "never-created"

    for state, trials in ((private_path, tmp_path / "trials"), (tmp_path / "state", private_path)):
        with pytest.raises(ValueError, match="outside Git worktrees"):
            await experiment.run_experiment(definition=definition, state_root=state, trial_report_dir=trials)
    with pytest.raises(ValueError, match="outside Git worktrees"):
        experiment.dump_definition(definition, private_path / "definition.json")
    assert not private_path.exists() and not (tmp_path / "trials").exists()


def test_private_reviews_bind_sample_cases_and_remain_outside_git(tmp_path: Path) -> None:
    definition = family.build_definition(1, cases=("receipt_sourced",), sample_pack=_private_pack())
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "_meta": {
                    "kind": "controller_host_experiment",
                    "experiment_run_id": "sample-review",
                    "definition": definition,
                }
            }
        ),
        encoding="utf-8",
    )
    disagreement = {"trial_id": "sample-review-t00", "case": "receipt_sourced", "reason": "fixture review"}
    review = {"reviewer": "independent-reviewer", "verdict": "partial", "disagreements": [disagreement]}
    with pytest.raises(ValueError, match="outside Git worktrees"):
        experiment.write_review_record(report_path, review)
    review_path = experiment.write_review_record(report_path, review, out_dir=tmp_path)
    (loaded,) = experiment.load_review_records(report_path, search_dir=tmp_path)
    assert loaded == json.loads(review_path.read_text(encoding="utf-8"))
    assert loaded["review"] == review
    disagreement["case"] = "input_sourced"
    with pytest.raises(ValueError, match="outside the experiment definition"):
        experiment.write_review_record(report_path, review, out_dir=tmp_path)


def test_the_cli_prints_the_embedded_definition(capsys: pytest.CaptureFixture[str]) -> None:
    assert family.main(["--print-default-definition"]) is None
    printed = json.loads(capsys.readouterr().out)
    assert printed["scenario"] == family.SCENARIO_ID
    assert printed["sample_pack"] == PACK
    assert printed["source_mode"] == "deterministic_injection"


@pytest.mark.timeout(300)
def test_the_cli_runs_a_pinned_definition_into_tmp_dirs(tmp_path: Path) -> None:
    definition = family.build_definition(1, cases=("receipt_sourced",))
    definition_path = _dump(tmp_path, definition)
    out = family.main(
        [
            "--definition",
            str(definition_path),
            "--state-root",
            str(tmp_path / "state"),
            "--trial-report-dir",
            str(tmp_path / "trials"),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    assert out is not None and out.exists()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["definition_digest"] == experiment.definition_digest(definition)
    with pytest.raises(SystemExit):
        family.main(["--definition", str(definition_path), "--model", family.PILOT_MODEL])
