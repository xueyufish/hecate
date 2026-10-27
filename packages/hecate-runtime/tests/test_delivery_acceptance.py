"""Shared versioned delivery acceptance (deterministic, host-independent).

Covers the shared module both hosts consume: declaration validation and
digest freezing (a standard modified under the same version number is a
different standard), evaluation verdicts for every check kind including
the ones a checker version cannot interpret, the ``not_declared`` record
for paths without an acceptance declaration, and the controller's
decision-level binding of the declared standard (pinned refs, final
decision identity, legacy-chain compatibility).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from hecate_runtime.accepted_decision import DecisionConflictError
from hecate_runtime.delivery_acceptance import (
    ACCEPTANCE_CHECKER_VERSION,
    declared_acceptance,
    evaluate_delivery,
    evaluation_declaration,
    validate_acceptance_declaration,
)
from test_accepted_action_step import _run_ref
from test_agent_controller import LoopScriptedSource, _final
from test_decision_step import InMemoryDecisionStore


def _schema_check() -> dict[str, Any]:
    return {"kind": "output_schema", "schema": {"type": "object", "required": ["answer"]}}


def _contains_check(value: str = "verified") -> dict[str, Any]:
    return {"kind": "content_contains", "value": value}


# -- declaration validation --------------------------------------------------


def test_validate_accepts_none_and_well_formed_blocks() -> None:
    validate_acceptance_declaration(None)
    validate_acceptance_declaration({"version": 2, "required_checks": [_schema_check(), _contains_check()]})
    validate_acceptance_declaration({"required_checks": []})  # version defaults to 1


@pytest.mark.parametrize(
    ("block", "message"),
    [
        ("not-an-object", "acceptance must be an object"),
        ({"version": 0}, "acceptance.version must be a positive integer"),
        ({"version": "2"}, "acceptance.version must be a positive integer"),
        ({"required_checks": "no"}, "acceptance.required_checks must be an array"),
        ({"required_checks": ["no"]}, "each acceptance required check must be an object"),
        ({"required_checks": [{"kind": "output_schema"}]}, "output_schema check requires a schema object"),
        ({"required_checks": [{"kind": "content_contains"}]}, "content_contains check requires a nonempty value"),
        ({"required_checks": [{"kind": "future"}]}, "unsupported acceptance check kind 'future'"),
        (
            {"required_checks": [{"kind": "output_schema", "schema": {"$ref": "https://example.com/x.json"}}]},
            "output_schema references must be local fragments",
        ),
    ],
)
def test_validate_rejects_malformed_blocks(block: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_acceptance_declaration(block)


# -- declaration digest freezing ----------------------------------------------


def test_same_standard_shares_one_digest() -> None:
    first = declared_acceptance({"version": 4, "required_checks": [_schema_check(), _contains_check("ok")]})
    second = declared_acceptance({"version": 4, "required_checks": [_schema_check(), _contains_check("ok")]})
    assert first.sha256 == second.sha256
    assert first.to_dict()["sha256"] == first.sha256


def test_modified_standard_under_the_same_version_is_a_different_digest() -> None:
    original = declared_acceptance({"version": 4, "required_checks": [_contains_check("verified")]})
    tampered_value = declared_acceptance({"version": 4, "required_checks": [_contains_check("tampered")]})
    dropped_check = declared_acceptance({"version": 4, "required_checks": []})
    bumped_version = declared_acceptance({"version": 5, "required_checks": [_contains_check("verified")]})
    changed_schema = declared_acceptance(
        {"version": 4, "required_checks": [_schema_check(), _contains_check("verified")]}
    )
    for other in (tampered_value, dropped_check, bumped_version, changed_schema):
        assert other.sha256 != original.sha256


def test_digest_freezes_check_parameters_not_key_order() -> None:
    reordered = declared_acceptance({"required_checks": [dict(reversed(list(_contains_check().items())))]})
    assert reordered.sha256 == declared_acceptance({"required_checks": [_contains_check()]}).sha256


def test_evaluation_declaration_keeps_bare_output_schema_history() -> None:
    declaration = evaluation_declaration(None, {"type": "object"})
    assert declaration is not None
    assert declaration.version == 1
    assert declaration.required_checks == ({"kind": "output_schema", "schema": {"type": "object"}},)
    # An explicit acceptance block wins over the bare schema.
    explicit = evaluation_declaration({"version": 2, "required_checks": [_contains_check()]}, {"type": "object"})
    assert explicit is not None
    assert explicit.required_checks == (_contains_check(),)
    assert evaluation_declaration(None, None) is None


# -- evaluation -----------------------------------------------------------------


def _evaluate(acceptance: Any, text: str) -> dict[str, Any]:
    return evaluate_delivery(text, evaluation_declaration(acceptance, None))


def test_passing_all_declared_checks() -> None:
    record = _evaluate(
        {"version": 3, "required_checks": [_schema_check(), _contains_check()]}, '{"answer": "verified"}'
    )
    assert record["status"] == "passed"
    assert record["version"] == 3
    assert (
        record["declaration_sha256"]
        == declared_acceptance({"version": 3, "required_checks": [_schema_check(), _contains_check()]}).sha256
    )
    assert record["checker_version"] == ACCEPTANCE_CHECKER_VERSION
    assert record["checks"] == [
        {"kind": "output_schema", "status": "passed"},
        {"kind": "content_contains", "status": "passed"},
    ]


def test_schema_passes_but_required_content_fails() -> None:
    record = _evaluate({"version": 3, "required_checks": [_schema_check(), _contains_check()]}, '{"answer": "not it"}')
    assert record["status"] == "acceptance_failed"
    statuses = {check["kind"]: check["status"] for check in record["checks"]}
    assert statuses == {"output_schema": "passed", "content_contains": "failed"}
    detail = next(check["detail"] for check in record["checks"] if check["status"] != "passed")
    assert "verified" in detail


def test_non_json_delivery_fails_the_schema_check() -> None:
    record = _evaluate({"required_checks": [_schema_check()]}, "not JSON")
    assert record["status"] == "acceptance_failed"
    assert record["checks"][0]["detail"].startswith("final answer is not valid JSON")


def test_uninterpretable_required_check_is_unverified_and_withholds_success() -> None:
    # A persisted declaration may carry a kind this checker version cannot
    # interpret (admission was validated by an older admission); the
    # lenient evaluation view keeps it and records it unverified.
    record = _evaluate(
        {"version": 2, "required_checks": [_schema_check(), {"kind": "future_check", "x": 1}]}, '{"answer": "ok"}'
    )
    assert record["status"] == "acceptance_unverified"
    statuses = {check["kind"]: check["status"] for check in record["checks"]}
    assert statuses == {"output_schema": "passed", "future_check": "unverified"}


def test_unresolvable_local_reference_fails_the_schema_check() -> None:
    record = _evaluate(
        {"required_checks": [_schema_check() | {"schema": {"$ref": "#/$defs/missing"}}]}, '{"answer": 1}'
    )
    assert record["status"] == "acceptance_failed"
    assert "cannot be evaluated" in record["checks"][0]["detail"]


def test_no_declaration_records_not_declared_not_acceptance() -> None:
    record = evaluate_delivery("free-form delivery", None)
    assert record["status"] == "not_declared"
    assert record["version"] is None
    assert record["declaration_sha256"] is None
    assert record["checks"] == []
    # Content digest still preserves the delivery.
    import hashlib

    assert record["content_sha256"] == hashlib.sha256(b"free-form delivery").hexdigest()


def test_record_binds_final_decision_and_namespace() -> None:
    record = evaluate_delivery(
        '{"answer": "verified"}',
        evaluation_declaration({"version": 2, "required_checks": [_contains_check()]}, None),
        final_decision={"decision_step_id": "agent-turn-000003", "content_digest": "digest-1"},
        evaluator={"host": "platform", "workspace_id": "ws"},
        task_ref={"issuer": "hecate.platform", "id": "task-1"},
        run_ref={"issuer": "hecate.platform", "id": "run-1"},
    )
    assert record["final_decision"] == {"decision_step_id": "agent-turn-000003", "content_digest": "digest-1"}
    assert record["task_ref"] == {"issuer": "hecate.platform", "id": "task-1"}
    assert record["run_ref"] == {"issuer": "hecate.platform", "id": "run-1"}
    assert record["evaluated_by"] == {"host": "platform", "workspace_id": "ws"}
    # Absent bindings stay absent, not null-filled.
    bare = evaluate_delivery("t", None)
    assert "final_decision" not in bare and "evaluated_by" not in bare


def test_evaluation_is_deterministic_in_declaration_and_delivery() -> None:
    declaration = evaluation_declaration({"version": 9, "required_checks": [_schema_check()]}, None)
    first = evaluate_delivery('{"answer": "same"}', declaration)
    second = evaluate_delivery('{"answer": "same"}', declaration)
    third = evaluate_delivery('{"answer": "different"}', declaration)
    assert first == second
    assert first != third


# -- controller binding -----------------------------------------------------------


def test_controller_pins_declared_standard_on_every_decision() -> None:
    """A declared standard rides the frozen refs of every committed decision."""

    import asyncio

    from test_accepted_action_step import StubToolPort
    from test_agent_controller import _controller, _spec

    store, port = InMemoryDecisionStore(), StubToolPort()
    source = LoopScriptedSource({(0, ""): _final("delivered answer")})
    declaration = declared_acceptance({"version": 6, "required_checks": [_contains_check("delivered")]})
    spec = _spec(acceptance=declaration)
    outcome = asyncio.run(_controller(store, source, port).run(spec))
    assert outcome.is_final_answer
    assert outcome.final_decision_step_id == "agent-turn-000001"
    assert outcome.final_decision_digest

    async def pinned() -> Any:
        return await store.lookup(spec.run_ref, outcome.final_decision_step_id)

    record = asyncio.run(pinned())
    assert record is not None
    assert record.input_refs["acceptance_version"] == 6
    assert record.input_refs["acceptance_digest"] == declaration.sha256


def test_controller_without_a_declared_standard_pins_no_acceptance_refs() -> None:
    import asyncio

    from test_accepted_action_step import StubToolPort
    from test_agent_controller import _controller, _spec

    store, port = InMemoryDecisionStore(), StubToolPort()
    source = LoopScriptedSource({(0, ""): _final("bare delivery")})
    spec = _spec()
    outcome = asyncio.run(_controller(store, source, port).run(spec))
    assert outcome.final_decision_digest

    async def pinned() -> Any:
        return await store.lookup(spec.run_ref, outcome.final_decision_step_id)

    record = asyncio.run(pinned())
    assert record is not None
    assert "acceptance_version" not in record.input_refs
    assert "acceptance_digest" not in record.input_refs


def test_replay_under_a_modified_same_version_standard_conflicts() -> None:
    """The compatibility stop: a committed decision replays only under the
    exact standard it pinned. A same-version rewrite of the standard (or a
    pre-binding legacy chain replayed with a newly declared standard) is a
    conflict, never a silent re-evaluation."""

    import asyncio

    from test_accepted_action_step import StubToolPort
    from test_agent_controller import _controller, _spec

    store, port = InMemoryDecisionStore(), StubToolPort()
    source = LoopScriptedSource({(0, ""): _final("tamper-proof")})
    run_ref = _run_ref()
    original = declared_acceptance({"version": 2, "required_checks": [_contains_check("tamper-proof")]})
    spec = _spec(run_ref=run_ref, acceptance=original)
    controller = _controller(store, source, port)
    assert asyncio.run(controller.run(spec)).is_final_answer

    # Same version, different check parameter: the standard changed.
    tampered = declared_acceptance({"version": 2, "required_checks": [_contains_check("tampered")]})
    with pytest.raises(DecisionConflictError):
        asyncio.run(controller.run(_spec(run_ref=run_ref, acceptance=tampered)))

    # The original standard still replays byte-for-byte.
    asyncio.run(controller.run(spec))


def test_outcome_roundtrip_carries_final_decision_identity() -> None:
    from hecate_runtime.agent_controller import AgentControllerOutcome

    outcome = AgentControllerOutcome(
        state="final_answer",
        reason=None,
        final_text="text",
        turns=2,
        decision_step_ids=("agent-turn-000001", "agent-turn-000002"),
        final_decision_step_id="agent-turn-000002",
        final_decision_digest="digest-9",
    )
    restored = AgentControllerOutcome.from_dict(json.loads(json.dumps(outcome.to_dict())))
    assert restored == outcome


def test_declaration_detaches_nested_caller_data_and_exported_views() -> None:
    """Changing caller-owned dictionaries cannot change a frozen standard."""
    checks = [_contains_check("verified"), _schema_check()]
    declaration = declared_acceptance({"required_checks": checks})
    original_digest = declaration.sha256
    checks[0]["value"] = "rewritten"
    checks[1]["schema"]["required"].append("missing")
    exported = declaration.to_dict()
    exported["required_checks"][0]["value"] = "export changed"
    assert evaluate_delivery('{"answer":"verified"}', declaration)["status"] == "passed"
    assert declaration.sha256 == original_digest


def test_mutated_declaration_never_signs_a_stale_digest() -> None:
    """Nested mutation of an exposed declaration is detected before evaluation."""
    declaration = declared_acceptance({"required_checks": [_contains_check("verified")]})
    declaration.required_checks[0]["value"] = "rewritten"
    with pytest.raises(ValueError, match="digest mismatch"):
        evaluate_delivery("rewritten", declaration)


@pytest.mark.parametrize("checks", [None, {}, "corrupt"])
def test_malformed_persisted_check_list_cannot_become_an_empty_pass(checks) -> None:
    """A malformed required-check array must never erase the required checks."""
    with pytest.raises(ValueError, match="must be an array"):
        evaluation_declaration({"required_checks": checks}, None)


def test_malformed_persisted_check_value_is_unverified_instead_of_coerced() -> None:
    """Wrongly typed parameters cannot pass through string coercion."""
    declaration = evaluation_declaration({"required_checks": [{"kind": "content_contains", "value": 42}]}, None)
    record = evaluate_delivery("42", declaration)
    assert record["status"] == "acceptance_unverified"


def test_checker_unavailability_is_unverified(monkeypatch) -> None:
    """An unavailable required checker cannot become success or lose the delivery."""
    import hecate_runtime.delivery_acceptance as acceptance_module

    def unavailable(*args):
        raise RuntimeError("checker dependency unavailable")

    monkeypatch.setattr(acceptance_module, "_validate_check_strict", unavailable)
    declaration = declared_acceptance({"required_checks": [_contains_check()]})
    record = evaluate_delivery("verified", declaration)
    assert record["status"] == "acceptance_unverified"
    assert record["checks"][0]["status"] == "unverified"
