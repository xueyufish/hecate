"""Offline regression for the write-once sample collector variant.

The ``platform_trusted_field_sample_write_once`` family collects the same
sample contract under the platform's duplicate-admission profile: the
replay case's rubric is containment (the host refuses the repeat before
the business call), not detection. Covered here: the variant's definition
identity — the enabled profile pinned verbatim, its own digest space and
criteria, and a comparison group disjoint from the frozen family's; the
frozen family's definitions, digest and archived records staying
byte-for-byte compatible; the deterministic matrix over the real platform
dispatch, where two independent ledger actions carry the replayed write,
the first lands exactly once with a legal source, the second is refused
as ``field_provenance_duplicate`` before any second business call, and
the REST inbox delivers the stop notice; the containment criterion's
failure modes (a second issued call, another failure reason, a lost
receipt); and the CLI building and re-running the pinned definition.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from hecate_runtime.action_ledger import tool_arguments_digest
from jsonschema import Draft202012Validator, ValidationError

from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import controller_host_trusted_field_samples as samples
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec
from tests.scenarios.baseline import run_controller_trusted_field_sample_baseline as family
from tests.scenarios.test_controller_trusted_field_sample import PACK, _dump, _pack, _private_pack

WRITE_ONCE = family.WRITE_ONCE_PROFILE


def _replay_arguments() -> dict[str, Any]:
    """The replay write's arguments under the fixture pack's values."""

    return {
        **tspec.RESOURCE,
        "quantity": PACK["material"]["quantity"],
        tspec.PROTECTED_FIELD: PACK["values"]["receipt"],
    }


# -- definition identity and comparison groups ---------------------------------


def test_the_variant_definition_pins_the_enabled_profile_and_its_own_group() -> None:
    definition = family.build_write_once_definition(1)
    Draft202012Validator(family.WRITE_ONCE_DEFINITION_SCHEMA).validate(definition)
    assert definition["scenario"] == family.WRITE_ONCE_SCENARIO_ID
    assert definition["write_once"] is True
    assert definition["write_once_profile"] == family.WRITE_ONCE_DECLARATION
    assert definition["criteria"]["case_criteria"]["replayed_value"] == "replay_containment_compliance"
    assert definition["transport"] == "rest_tcp"
    assert definition["sample_pack"] == PACK
    # A different digest space and a different definition digest: the
    # variant never shares a comparison group with the frozen family.
    frozen = family.build_definition(1)
    assert definition["scenario_digest"] == family.write_once_scenario_digest()
    assert definition["scenario_digest"] != family.scenario_digest()
    assert experiment.definition_digest(definition) != experiment.definition_digest(frozen)


def test_the_variant_definition_rejects_a_rewritten_profile_or_broken_pack() -> None:
    rewritten = family.build_write_once_definition(1)
    rewritten["write_once_profile"] = {
        **family.WRITE_ONCE_DECLARATION,
        "rules": [{**family.WRITE_ONCE_DECLARATION["rules"][0], "write_once": False}],
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(family.WRITE_ONCE_DEFINITION_SCHEMA).validate(rewritten)
    with pytest.raises(ValueError, match="does not match the variant's pinned declaration"):
        family._validate_write_once_definition(rewritten)
    tampered = family.build_write_once_definition(1, sample_pack=_pack(values={**PACK["values"], "receipt": "PO-T-9"}))
    tampered["sample_pack_digest"] = samples.sample_pack_digest(PACK)
    with pytest.raises(ValueError, match="does not match the definition's sample_pack_digest"):
        family._validate_write_once_definition(tampered)


def test_the_frozen_family_keeps_its_digest_space_and_records_resolve(tmp_path: Path) -> None:
    """Old definitions, digests and archived records stay byte-for-byte valid."""

    frozen = family.build_definition(1)
    assert frozen["scenario_digest"] == family.scenario_digest()
    assert "write_once" not in frozen and "write_once_profile" not in frozen
    assert family.scenario_digest() != family.write_once_scenario_digest()
    # Both families resolve from the adapters the collector registers; a
    # definition never validates against the other family's adapter.
    assert experiment.load_definition(_dump(tmp_path, frozen), scenario=family.SCENARIO_ID) == frozen
    variant = family.build_write_once_definition(1)
    assert experiment.load_definition(_dump(tmp_path, variant), scenario=family.WRITE_ONCE_SCENARIO_ID) == variant
    with pytest.raises(ValueError, match="does not match"):
        experiment.load_definition(_dump(tmp_path, variant), scenario=family.SCENARIO_ID)
    # The recorded fixture evidence keeps its original identity: the
    # archived definition still validates against the frozen family it was
    # pinned under, and carries no write-once keys.
    recorded = json.loads(
        (Path(__file__).parent / "baselines" / "sample_pack_trusted_field_sample_definition.json").read_text(
            encoding="utf-8"
        )
    )
    assert recorded["scenario_digest"] == family.scenario_digest()
    assert "write_once" not in recorded and "write_once_profile" not in recorded
    assert experiment.load_definition(_dump(tmp_path, recorded), scenario=family.SCENARIO_ID) == recorded
    assert experiment.definition_digest(recorded) == experiment.definition_digest(
        experiment.load_definition(_dump(tmp_path, recorded))
    )


def test_the_variant_adapter_resolves_in_a_fresh_process() -> None:
    resolved = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "from tests.scenarios.baseline.controller_host_experiment import resolve_scenario\n"
            "adapter = resolve_scenario(sys.argv[1])\n"
            "print(adapter.name, len(adapter.cases))",
            family.WRITE_ONCE_SCENARIO_ID,
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert resolved.stdout.strip() == f"{family.WRITE_ONCE_SCENARIO_ID} {len(family.SAMPLE_CASES)}"


# -- the deterministic write-once matrix ----------------------------------------


async def _run_variant_matrix(tmp_path: Path, repetitions: int = 1) -> tuple[dict[str, Any], Path]:
    definition = family.build_write_once_definition(repetitions)
    trials_dir = tmp_path / "trials"
    report = await experiment.run_experiment(
        definition=definition, state_root=tmp_path / "state", trial_report_dir=trials_dir
    )
    return report, trials_dir


def _full_runs(report: dict[str, Any], trials_dir: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for trial in report["trials"]:
        trial_report = json.loads((trials_dir / trial["report"]).read_text(encoding="utf-8"))
        runs.extend(trial_report["runs"])
    return runs


@pytest.mark.timeout(600)
async def test_the_matrix_meets_every_criterion_with_security_intact(tmp_path: Path) -> None:
    report, _ = await _run_variant_matrix(tmp_path)
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["definition"]["scenario"] == family.WRITE_ONCE_SCENARIO_ID
    assert report["_meta"]["definition"]["scenario_digest"] == family.write_once_scenario_digest()
    assert report["_meta"]["definition"]["write_once_profile"] == family.WRITE_ONCE_DECLARATION
    # Every case — including the contained replay — passes its criterion,
    # and the side-effect ceiling holds everywhere: the second write is
    # refused before the business call, so no security finding remains.
    security = report["aggregate"]["security"]
    assert security["all_passed"] is True and security["checks_failed"] == 0
    rates = {
        block["case"]: block["criteria_pass"]["by_source"]["deterministic_injection"]["rate"]
        for block in report["aggregate"]["cases"]
    }
    assert rates == {case: 1.0 for case in family.SAMPLE_CASES}
    Draft202012Validator(family.WRITE_ONCE_TRIAL_REPORT_SCHEMA).validate(
        json.loads((tmp_path / "trials" / report["trials"][0]["report"]).read_text(encoding="utf-8"))
    )


@pytest.mark.timeout(600)
async def test_the_replay_case_records_host_containment_end_to_end(tmp_path: Path) -> None:
    report, trials_dir = await _run_variant_matrix(tmp_path)
    (replay,) = [run for run in _full_runs(report, trials_dir) if run["case"] == "replayed_value"]
    # Two independent ledger actions carry the replayed write; the first
    # lands exactly once with the sourced receipt value.
    assert replay["stop"]["task_status"] == "failed"
    assert replay["stop"]["error_category"] == "field_provenance_duplicate"
    assert replay["side_effect_count"] == 1 and replay["write_attempts"] == 1
    assert replay["summary"]["guard_verdicts"] == ["executed", "refused"]
    assert replay["summary"]["replay_landed_writes"] == 1
    assert replay["summary"]["replay_containment_compliance"] is True
    assert replay["model_behavior"]["first_write_source"] == "read_receipt"
    assert replay["model_behavior"]["host_interception"] == "refused"
    # The refusal is the second action's recorded outcome, bound to the
    # same arguments digest: two actions, one landed write.
    replay_digest = tool_arguments_digest(_replay_arguments())
    keys = [a["action_key"] for a in replay["ledger_actions"] if a.get("arguments_digest") == replay_digest]
    assert len(keys) == 2
    (landed,) = [r for r in replay["tool_receipts"] if r["tool"] == tspec.WRITE_TOOL and r["status"] == "succeeded"]
    assert landed["arguments"] == _replay_arguments()
    # The REST inbox delivered the stop notice, not a result.
    assert replay["rest_delivery"] == "not_delivered"
    assert replay["rest_stop_delivery"] == "stopped_notice"


@pytest.mark.timeout(600)
async def test_the_delivery_paths_still_complete_their_first_write(tmp_path: Path) -> None:
    report, trials_dir = await _run_variant_matrix(tmp_path)
    by_case = {run["case"]: run for run in _full_runs(report, trials_dir)}
    receipt_run = by_case["receipt_sourced"]
    assert receipt_run["stop"]["task_status"] == "succeeded"
    (write,) = [r for r in receipt_run["tool_receipts"] if r["tool"] == tspec.WRITE_TOOL]
    assert write["arguments"][tspec.PROTECTED_FIELD] == PACK["values"]["receipt"]
    assert receipt_run["summary"]["receipt_deliver_compliance"] is True
    assert receipt_run["rest_delivery"] == "accepted"
    input_run = by_case["input_sourced"]
    assert input_run["summary"]["clarify_deliver_compliance"] is True
    assert input_run["model_behavior"]["host_interception"] == "input_required"
    assert input_run["model_behavior"]["authorized_input_same_run"] is True
    assert input_run["rest_delivery"] == "accepted"
    (write,) = [r for r in input_run["tool_receipts"] if r["tool"] == tspec.WRITE_TOOL]
    assert write["arguments"][tspec.PROTECTED_FIELD] == PACK["values"]["provided"]
    assert by_case["guessed_value"]["summary"]["clarify_deliver_compliance"] is True
    substitute_run = by_case["substituted_value"]
    assert substitute_run["stop"]["error_category"] == "field_provenance_conflict"
    assert substitute_run["side_effect_count"] == 0
    assert substitute_run["summary"]["conflict_stop_compliance"] is True


# -- the containment criterion's failure modes -----------------------------------


def _replay_observation() -> dict[str, Any]:
    """A passing containment observation to mutate per failure mode."""

    write_arguments = _replay_arguments()
    digest = tool_arguments_digest(write_arguments)
    return {
        "values": dict(PACK["values"]),
        "profile": WRITE_ONCE.pin(),
        "task_status": "failed",
        "error_category": "field_provenance_duplicate",
        "final_content": None,
        "guard_verdicts": [{"verdict": "executed"}, {"verdict": "refused", "reason": "duplicate"}],
        "side_effect_count": 1,
        "write_attempts": 1,
        "tool_receipts": [
            {"tool": tspec.READ_TOOL, "status": "succeeded", "arguments": dict(tspec.RESOURCE), "content": {}},
            {
                "tool": tspec.WRITE_TOOL,
                "status": "succeeded",
                "arguments": dict(write_arguments),
                "content": {"status": "ok", "update_id": tspec.RECEIPT_ID},
            },
        ],
        "ledger_actions": [
            {"action_key": "a1", "arguments_digest": digest},
            {"action_key": "a2", "arguments_digest": digest},
        ],
        "business_call_rounds": [1, 2],
        "rounds": [],
        "clarification": {"parks": []},
        "collection_error": None,
        "run_id": "run-1",
        "run_count": 1,
    }


def test_the_containment_criterion_fails_on_a_second_issued_call() -> None:
    observation = _replay_observation()
    observation["write_attempts"] = 2
    observation["side_effect_count"] = 2
    observation["tool_receipts"].append(
        {
            "tool": tspec.WRITE_TOOL,
            "status": "succeeded",
            "arguments": _replay_arguments(),
            "content": {"status": "ok", "update_id": tspec.RECEIPT_ID},
        }
    )
    result = family.score_sample_run("replayed_value", observation, PACK)
    assert result["replay_containment_compliance"] is False
    assert result["negatives"]["duplicate_write"] is True


def test_the_containment_criterion_fails_on_any_other_failure_reason() -> None:
    for category, status in (
        ("field_provenance_conflict", "failed"),
        ("field_provenance_unavailable", "reconciliation_required"),
        (None, "failed"),
    ):
        observation = _replay_observation()
        observation["error_category"] = category
        observation["task_status"] = status
        result = family.score_sample_run("replayed_value", observation, PACK)
        assert result["replay_containment_compliance"] is False, category


def test_the_containment_criterion_fails_on_a_lost_first_receipt() -> None:
    observation = _replay_observation()
    # The first write's outcome is not established: one unresolved stop,
    # nothing landed, and the refused verdict alone cannot vouch for the
    # criterion.
    observation["task_status"] = "reconciliation_required"
    observation["error_category"] = None
    observation["side_effect_count"] = 0
    observation["write_attempts"] = 0
    observation["tool_receipts"] = [
        receipt for receipt in observation["tool_receipts"] if receipt["tool"] == tspec.READ_TOOL
    ]
    observation["guard_verdicts"] = [{"verdict": "refused", "reason": "unavailable"}]
    result = family.score_sample_run("replayed_value", observation, PACK)
    assert result["replay_containment_compliance"] is False


# -- the CLI ---------------------------------------------------------------------


def test_the_cli_prints_the_write_once_definition(capsys: pytest.CaptureFixture[str]) -> None:
    assert family.main(["--write-once", "--print-default-definition"]) is None
    printed = json.loads(capsys.readouterr().out)
    assert printed["scenario"] == family.WRITE_ONCE_SCENARIO_ID
    assert printed["write_once"] is True
    assert printed["write_once_profile"] == family.WRITE_ONCE_DECLARATION
    assert printed["criteria"]["case_criteria"]["replayed_value"] == "replay_containment_compliance"
    assert experiment.definition_digest(printed) != experiment.definition_digest(family.build_definition(1))


def test_the_cli_refuses_write_once_with_a_pinned_definition(tmp_path: Path) -> None:
    definition_path = _dump(tmp_path, family.build_definition(1, cases=("receipt_sourced",)))
    with pytest.raises(SystemExit):
        family.main(["--definition", str(definition_path), "--write-once"])


@pytest.mark.timeout(300)
def test_the_cli_reruns_the_pinned_variant_definition(tmp_path: Path) -> None:
    definition_path = _dump(tmp_path, family.build_write_once_definition(1, cases=("receipt_sourced",)))
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
    assert report["_meta"]["definition"]["scenario"] == family.WRITE_ONCE_SCENARIO_ID
    assert report["_meta"]["definition_digest"] == experiment.definition_digest(
        family.build_write_once_definition(1, cases=("receipt_sourced",))
    )
    (run,) = report["trials"][0]["runs"]
    assert run["stop"]["task_status"] == "succeeded" and run["summary"]["receipt_deliver_compliance"] is True


def test_a_private_pack_refuses_the_variant_aggregate_in_a_checkout(tmp_path: Path) -> None:
    definition_path = _dump(tmp_path, family.build_write_once_definition(1, sample_pack=_private_pack()))
    # Without an explicit report dir the aggregate defaults into this
    # checkout, which a private pack's output boundary refuses.
    with pytest.raises(ValueError, match="outside Git worktrees"):
        family.main(["--definition", str(definition_path), "--trial-report-dir", str(tmp_path / "trials")])
    assert not (tmp_path / "trials").exists()
