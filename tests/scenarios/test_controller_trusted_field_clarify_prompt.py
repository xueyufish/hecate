"""Offline acceptance for the clarification-prompt experiment round.

Single-variable candidate: only the system prompt changes against the
recorded #398 pilot. These tests verify, without any provider access,
that the candidate instruction reaches the model's system message, that
the original pilot definition and its recorded comparison group stay
byte-identical, that the candidate forms its own comparison group, and
that the deterministic responder still drives the full host supplement
loop under the candidate persona.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tests.scenarios.baseline import controller_host_experiment as experiment
from tests.scenarios.baseline import controller_host_spec as spec
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec
from tests.scenarios.baseline import run_controller_trusted_field_model_baseline as pilot

PILOT_DEFINITION_DIGEST = "bc5baaf1aeee088fe8482d4a2f526d4046dec54c988c5515f390571ada0a7da7"
PILOT_SCENARIO_DIGEST = "395c0e5281c9d8502a620697e9fc6fbeb8f937bb169c11a5d398e4c98ad48439"
BASELINES = Path(__file__).parent / "baselines"


def test_the_original_pilot_definition_stays_compatible() -> None:
    """The recorded #398 round still loads with its original identity."""

    definition = pilot.build_pilot_definition()
    assert "persona" not in definition
    assert experiment.definition_digest(definition) == PILOT_DEFINITION_DIGEST
    assert definition["scenario_digest"] == pilot.scenario_digest() == PILOT_SCENARIO_DIGEST
    drafted = pilot.build_definition(3, source_mode="real_model", model=pilot.PILOT_MODEL)
    assert drafted == definition
    # The original definition still loads and validates end to end.
    loaded = experiment.load_definition(
        BASELINES / "pilot_glm47flash_trusted_field_model_definition.json", scenario=pilot.SCENARIO_ID
    )
    assert loaded == definition


def test_the_clarify_round_forms_its_own_comparison_group() -> None:
    """The candidate pins its prompt in the definition; the digest re-keys."""

    clarify = pilot.build_clarify_definition()
    Draft202012Validator(pilot.MODEL_DEFINITION_SCHEMA).validate(clarify)
    assert clarify["id"] == pilot.CLARIFY_EXPERIMENT_ID
    assert clarify["persona"] == pilot.CLARIFY_SYSTEM_PROMPT
    assert clarify["model"] == pilot.PILOT_MODEL and clarify["repetitions"] == 3
    assert clarify["cases"] == list(pilot.MODEL_CASES)
    # Same family identity, different comparison group.
    assert clarify["scenario_digest"] == pilot.scenario_digest() == PILOT_SCENARIO_DIGEST
    pilot_digest = experiment.definition_digest(pilot.build_pilot_definition())
    clarify_digest = experiment.definition_digest(clarify)
    assert clarify_digest != pilot_digest
    # Any persona rewrite re-keys the group; the scenario digest does not move.
    mutated = {**clarify, "persona": clarify["persona"] + " Always wait for the host value."}
    assert experiment.definition_digest(mutated) != clarify_digest
    assert mutated["scenario_digest"] == clarify["scenario_digest"]
    # The pinned candidate definition round-trips through the loader.
    loaded = experiment.load_definition(
        BASELINES / "pilot_glm47flash_trusted_field_model_clarify_definition.json", scenario=pilot.SCENARIO_ID
    )
    assert loaded == clarify


@pytest.mark.parametrize("persona", ["", " \t\n", " padded prompt", "padded prompt\n"])
async def test_invalid_pinned_persona_is_rejected_before_collection(tmp_path: Path, persona: str) -> None:
    definition = {**pilot.build_definition(1), "persona": persona}
    state_root = tmp_path / "state"
    reports = tmp_path / "records"
    with pytest.raises((ValueError, ValidationError), match="persona|match"):
        await experiment.run_experiment(definition=definition, state_root=state_root, trial_report_dir=reports)
    assert not state_root.exists() and not reports.exists()


def test_the_candidate_prompt_names_the_path_and_carries_no_answers() -> None:
    prompt = pilot.CLARIFY_SYSTEM_PROMPT
    assert prompt != tspec.SYSTEM_PROMPT and tspec.SYSTEM_PROMPT not in prompt
    # The instruction names the governed write, the omission and the host
    # supplement, without ever naming a value, an order number or a receipt.
    assert tspec.WRITE_TOOL in prompt and "omit order_ref" in prompt
    assert "provided input" in prompt
    for secret in (*tspec.DEFAULT_VALUES.values(), tspec.RECEIPT_ID, "PO-"):
        assert secret not in prompt


async def test_the_candidate_prompt_reaches_the_model_system_message(tmp_path: Path) -> None:
    """The seeded persona is the system message the model actually sees."""

    report = await pilot.collect(root=tmp_path / "candidate", system_prompt=pilot.CLARIFY_SYSTEM_PROMPT)
    assert report["_meta"]["status"] == "recorded"
    assert report["_meta"]["persona"] == pilot.CLARIFY_SYSTEM_PROMPT
    assert report["_meta"]["prompt_digest"] == spec.digest([pilot.CLARIFY_SYSTEM_PROMPT, tspec.USER_PROMPT])
    runs = report["runs"]
    assert len(runs) == 2
    for run in runs:
        first_input = run["rounds"][0]["input_messages"]
        assert first_input[0] == {"role": "system", "content": pilot.CLARIFY_SYSTEM_PROMPT}
        assert tspec.SYSTEM_PROMPT not in json.dumps(first_input)
    # The default path is unchanged: the family persona stays the system
    # message when no candidate is given.
    default_report = await pilot.collect(root=tmp_path / "default")
    for run in default_report["runs"]:
        assert run["rounds"][0]["input_messages"][0] == {"role": "system", "content": tspec.SYSTEM_PROMPT}


async def test_the_clarify_variant_drives_the_full_supplement_loop_offline(tmp_path: Path) -> None:
    """Deterministic responder: the omitted write parks, the authorized
    answer completes one write in the same Run, delivery is accepted."""

    definition = pilot.build_definition(
        1,
        source_mode="deterministic_injection",
        persona=pilot.CLARIFY_SYSTEM_PROMPT,
        experiment_id=pilot.CLARIFY_EXPERIMENT_ID,
    )
    report = await experiment.run_experiment(
        definition=definition,
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
    )
    Draft202012Validator(experiment.resolve_scenario(pilot.SCENARIO_ID).report_schema).validate(report)
    assert report["_meta"]["definition"] == definition
    assert report["aggregate"]["denominators"]["trials_recorded"] == 1
    # The full trial record carries the model rounds; the aggregate keeps
    # the whitelisted behavior, security and stop fields.
    trial_record = json.loads((tmp_path / "records" / report["trials"][0]["report"]).read_bytes())
    full_runs = {run["case"]: run for run in trial_record["runs"]}
    runs = report["trials"][0]["runs"]
    assert [run["case"] for run in runs] == list(pilot.MODEL_CASES)
    for run in runs:
        full = full_runs[run["case"]]
        assert full["rounds"][0]["input_messages"][0] == {"role": "system", "content": pilot.CLARIFY_SYSTEM_PROMPT}
        assert run["security"] == {
            "unsourced_value_landed": False,
            "guard_verdict_unrecorded": False,
            "duplicate_write": False,
        }
        assert run["side_effect_count"] == 1
        assert run["stop"]["task_status"] == "succeeded"
        assert run["model_behavior"]["delivery_outcome"] == "accepted"
    receipt = next(run for run in runs if run["case"] == "receipt_sourced")
    assert receipt["model_behavior"]["first_write_source"] == "read_receipt"
    assert receipt["model_behavior"]["host_interception"] is None
    assert receipt["summary"]["receipt_deliver_compliance"] is True
    clarify = next(run for run in runs if run["case"] == "input_sourced")
    behavior = clarify["model_behavior"]
    assert behavior["first_write_source"] == "omitted"
    assert behavior["host_interception"] == "input_required"
    assert behavior["clarification_origin"] == "host_field_guard"
    assert behavior["authorized_input_same_run"] is True
    assert behavior["post_supplement_write"] is True
    park = clarify["clarification"]["parks"][0]
    assert park["landed_writes_before_park"] == 0
    assert park["contract_ref"]["contract"]["field_provenance"]["resource"] == tspec.RESOURCE
    assert clarify["summary"]["clarify_deliver_compliance"] is True
    security = report["aggregate"]["security"]
    assert security["checks_failed"] == 0 and security["checks_missing"] == 0


def test_the_cli_builds_and_prints_the_clarify_definition(capsys: pytest.CaptureFixture[str]) -> None:
    pilot.main(["--clarify-prompt", "--print-default-definition"])
    printed = json.loads(capsys.readouterr().out)
    assert printed["id"] == pilot.CLARIFY_EXPERIMENT_ID
    assert printed["persona"] == pilot.CLARIFY_SYSTEM_PROMPT
    assert printed["source_mode"] == "deterministic_injection" and printed["model"] is None
    with pytest.raises(SystemExit):
        pilot.main(["--clarify-prompt", "--definition", "unused.json", "--print-default-definition"])
