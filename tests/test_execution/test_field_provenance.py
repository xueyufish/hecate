"""Trusted field provenance: declaration, verdicts and durable source facts.

Unit coverage for :mod:`hecate.execution.field_provenance` and its two
production consumers — the governed execution boundary
(``PlatformGovernance.execute_tool``) and the dispatcher's refusal watch.
Verdicts are exercised with swapped legitimate values so every pass and
block follows the recorded source facts, never a fixed answer. The durable
loader runs over a store double whose rows have the real ledger, decision
and revision shapes; end-to-end coverage over the real platform dispatch
lives in ``tests/scenarios/test_controller_trusted_field.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from hecate_durable.contracts.durable import CommandState, ControlCommandKind, canonical_request_digest
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore
from hecate_runtime.accepted_action_step import execution_id_for_action, tool_call_id_for_action
from hecate_runtime.accepted_decision import (
    AcceptedAction,
    AcceptedDecisionRecord,
    RunRef,
    canonical_decision_digest,
    canonical_json,
)
from hecate_runtime.action_ledger import tool_arguments_digest
from hecate_runtime.agent_controller import CONTROLLER_LOOP_MARKER, decision_step_id_for_turn

from hecate.core.composition.governance import GovernanceDeniedError, GovernanceMode, build_platform_governance
from hecate.execution import field_provenance as fp
from hecate.execution.task_dispatcher import _ControllerFieldProvenanceStop

READ = "query_inventory"
WRITE = "submit_inventory_update"
RESOURCE = {"domain": "domain_a", "sku": "SKU-A1"}
DECLARATION: dict[str, Any] = {
    "version": 1,
    "profile": "synthetic-inventory-order-ref-1",
    "rules": [
        {
            "tool": WRITE,
            "field": "order_ref",
            "resource_keys": ["domain", "sku"],
            "sources": {"read_receipts": [{"tool": READ, "field": "order_ref"}], "provided_input": True},
            "prompt": "Provide the procurement order reference for this update.",
        }
    ],
}
PROFILE = fp.FieldProvenanceProfile.from_declaration(DECLARATION)
RULES = PROFILE.rules_for(WRITE)
RUN = RunRef("hecate", "run-1")
RUN_REF = BackendRef(RefKind.RUN, "hecate", "run-1")
TASK = BackendRef(RefKind.TASK, "hecate", "task-1")
VALUES = ("PO-RECEIPT-4410", "PO-ALT-7788", "po/ünïcode-1")


def _write(**extra: Any) -> dict[str, Any]:
    return {**RESOURCE, "quantity": 3, **extra}


def _facts(turn: int = 3, reads: tuple = (), supplements: tuple = ()) -> fp.ProvenanceFacts:
    return fp.ProvenanceFacts(turn=turn, reads=reads, supplements=supplements)


def _read(value: Any = None, *, turn: int = 1, tool: str = READ, args: dict | None = None, **payload: Any):
    result = {"status": "ok", **payload, **({"order_ref": value} if value is not None else {})}
    return fp.ReadFact(tool, dict(args or RESOURCE), result, turn)


def _answer(value: Any, *, turn: int = 3, resource: dict | None = None, request: bool = True):
    binding = {"tool": WRITE, "fields": ("order_ref",), "resource": dict(resource or RESOURCE)}
    return fp.SupplementFact("rev-1", {"order_ref": value}, turn, binding if request else None)


# -- declaration -------------------------------------------------------------


class TestDeclaration:
    def test_normalized_profile_round_trips_with_a_stable_digest(self) -> None:
        again = fp.FieldProvenanceProfile.from_declaration(PROFILE.to_dict())
        assert again == PROFILE and again.sha256 == PROFILE.sha256
        assert PROFILE.pin() == {"version": 1, "profile": "synthetic-inventory-order-ref-1", "sha256": PROFILE.sha256}
        assert PROFILE.rules_for(READ) == ()

    def test_a_rewrite_under_the_same_profile_name_changes_the_digest(self) -> None:
        rewritten = json.loads(json.dumps(DECLARATION))
        rewritten["rules"][0]["sources"]["provided_input"] = False
        assert fp.FieldProvenanceProfile.from_declaration(rewritten).sha256 != PROFILE.sha256

    @pytest.mark.parametrize(
        ("path", "value"),
        [
            ((), None),
            (("version",), True),
            (("version",), 2),
            (("profile",), "Upper Case"),
            (("rules",), []),
            (("extra",), 1),
            (("rules", 0, "extra"), 1),
            (("rules", 0, "field"), "approval_ref"),
            (("rules", 0, "field"), " order_ref"),
            (("rules", 0, "resource_keys"), []),
            (("rules", 0, "resource_keys"), ["sku", "sku"]),
            (("rules", 0, "resource_keys"), ["sku", "order_ref"]),
            (("rules", 0, "sources"), {}),
            (("rules", 0, "sources", "provided_input"), "yes"),
            (("rules", 0, "sources", "read_receipts"), [{"tool": WRITE, "field": "order_ref"}]),
            (("rules", 0, "sources", "read_receipts"), [{"tool": READ}]),
            (("rules", 0, "prompt"), ""),
        ],
    )
    def test_malformed_declarations_fail_fast(self, path: tuple, value: Any) -> None:
        declaration: Any = json.loads(json.dumps(DECLARATION))
        if not path:
            declaration = value
        else:
            target = declaration
            for key in path[:-1]:
                target = target[key]
            if path[-1] == "sources" and value == {}:
                target["sources"] = {"read_receipts": [], "provided_input": False}
            else:
                target[path[-1]] = value
        with pytest.raises(fp.FieldProvenanceDeclarationError):
            fp.FieldProvenanceProfile.from_declaration(declaration)

    def test_duplicate_rules_and_divergent_resources_per_tool_are_refused(self) -> None:
        duplicate = json.loads(json.dumps(DECLARATION))
        duplicate["rules"].append(json.loads(json.dumps(DECLARATION["rules"][0])))
        with pytest.raises(fp.FieldProvenanceDeclarationError, match="duplicates"):
            fp.FieldProvenanceProfile.from_declaration(duplicate)
        divergent = json.loads(json.dumps(DECLARATION))
        divergent["rules"].append({**DECLARATION["rules"][0], "field": "batch_ref", "resource_keys": ["sku"]})
        with pytest.raises(fp.FieldProvenanceDeclarationError, match="same resource_keys"):
            fp.FieldProvenanceProfile.from_declaration(divergent)

    def test_two_fields_from_the_same_read_tool_are_ambiguous(self) -> None:
        declaration = json.loads(json.dumps(DECLARATION))
        declaration["rules"][0]["sources"]["read_receipts"].append({"tool": READ, "field": "alternative_ref"})
        with pytest.raises(fp.FieldProvenanceDeclarationError, match="duplicates a declared read tool"):
            fp.FieldProvenanceProfile.from_declaration(declaration)


# -- pure verdicts -----------------------------------------------------------


class TestVerdicts:
    @pytest.mark.parametrize("value", VALUES)
    def test_an_earlier_receipt_for_the_same_resource_authorizes_its_own_value(self, value: str) -> None:
        verdict = fp.evaluate_field_provenance(RULES, _write(order_ref=value), _facts(reads=(_read(value),)))
        assert verdict.kind == "allow" and verdict.resource == RESOURCE
        other = next(item for item in VALUES if item != value)
        swapped = fp.evaluate_field_provenance(RULES, _write(order_ref=other), _facts(reads=(_read(value),)))
        assert (swapped.kind, swapped.reason) == ("refuse", "conflict")

    @pytest.mark.parametrize("value", VALUES)
    def test_an_answer_to_this_request_authorizes_its_own_value(self, value: str) -> None:
        facts = _facts(supplements=(_answer(value),))
        assert fp.evaluate_field_provenance(RULES, _write(order_ref=value), facts).kind == "allow"

    @pytest.mark.parametrize(
        "facts",
        [
            _facts(),
            # The same value from a receipt of the SAME decision: an exact guess.
            _facts(turn=1, reads=(_read("PO-X", turn=1),)),
            _facts(reads=(_read("PO-X", args={"domain": "domain_a", "sku": "SKU-B2"}),)),
            _facts(reads=(_read("PO-X", args={"domain": "domain_b", "sku": "SKU-A1"}),)),
            _facts(reads=(_read("PO-X", tool="lookup_order"),)),
            _facts(reads=(_read(None),)),
            _facts(supplements=(_answer("PO-X", resource={"domain": "domain_a", "sku": "SKU-B2"}),)),
            _facts(supplements=(_answer("PO-X", request=False),)),
            # A later revision never authorizes an earlier decision.
            _facts(turn=2, supplements=(_answer("PO-X", turn=3),)),
        ],
        ids=[
            "no-source",
            "same-decision-receipt",
            "other-sku",
            "other-domain",
            "undeclared-tool",
            "receipt-without-field",
            "answer-for-other-resource",
            "answer-to-another-request",
            "future-revision",
        ],
    )
    def test_unsourced_values_become_the_input_request(self, facts: fp.ProvenanceFacts) -> None:
        verdict = fp.evaluate_field_provenance(RULES, _write(order_ref="PO-X"), facts)
        assert (verdict.kind, verdict.fields, verdict.resource) == ("input_required", ("order_ref",), RESOURCE)

    def test_argument_provenance_claims_never_authorize(self) -> None:
        claimed = _write(order_ref="PO-X", order_ref_source={"tool": READ, "receipt": {"order_ref": "PO-X"}})
        assert fp.evaluate_field_provenance(RULES, claimed, _facts()).kind == "input_required"

    def test_an_absent_field_is_requested_even_when_a_source_exists(self) -> None:
        verdict = fp.evaluate_field_provenance(RULES, _write(), _facts(reads=(_read("PO-X"),)))
        assert verdict.kind == "input_required"

    @pytest.mark.parametrize(
        ("facts", "reason"),
        [
            (_facts(reads=(_read("PO-1"), _read("PO-2", turn=2))), "conflict"),
            (_facts(reads=(_read("PO-1"),), supplements=(_answer("PO-2"),)), "conflict"),
            (_facts(reads=(_read("PO-1", sku="SKU-B2"),)), "unbound"),
            (_facts(reads=(_read(7),)), "unbound"),
            (_facts(supplements=(_answer(""),)), "unbound"),
        ],
        ids=["receipts-disagree", "receipt-vs-answer", "receipt-names-other-resource", "bad-value", "blank-answer"],
    )
    def test_conflicting_or_malformed_sources_refuse(self, facts: fp.ProvenanceFacts, reason: str) -> None:
        verdict = fp.evaluate_field_provenance(RULES, _write(order_ref="PO-1"), facts)
        assert (verdict.kind, verdict.reason) == ("refuse", reason)

    def test_an_unbound_resource_refuses(self) -> None:
        verdict = fp.evaluate_field_provenance(RULES, {"domain": "domain_a", "order_ref": "PO-1"}, _facts())
        assert (verdict.kind, verdict.reason) == ("refuse", "unbound")

    def test_answers_count_only_when_the_rule_declares_them(self) -> None:
        receipts_only = json.loads(json.dumps(DECLARATION))
        receipts_only["rules"][0]["sources"]["provided_input"] = False
        rules = fp.FieldProvenanceProfile.from_declaration(receipts_only).rules_for(WRITE)
        facts = _facts(supplements=(_answer("PO-1"),))
        verdict = fp.evaluate_field_provenance(rules, _write(order_ref="PO-1"), facts)
        assert (verdict.kind, verdict.reason) == ("refuse", "unbound")

    def test_multiple_rules_aggregate_requests_and_any_refusal_wins(self) -> None:
        two = json.loads(json.dumps(DECLARATION))
        two["rules"].append({**DECLARATION["rules"][0], "field": "batch_ref"})
        rules = fp.FieldProvenanceProfile.from_declaration(two).rules_for(WRITE)
        requested = fp.evaluate_field_provenance(rules, _write(order_ref="PO-1", batch_ref="B-1"), _facts())
        assert (requested.kind, requested.fields) == ("input_required", ("order_ref", "batch_ref"))
        refused = fp.evaluate_field_provenance(
            rules, _write(order_ref="PO-9", batch_ref="B-1"), _facts(reads=(_read("PO-1"),))
        )
        assert (refused.kind, refused.reason, refused.fields) == ("refuse", "conflict", ("order_ref",))


# -- envelopes ---------------------------------------------------------------


class TestEnvelopes:
    def test_refusal_is_bound_to_action_tool_and_profile_and_names_no_value(self) -> None:
        text = fp.refusal_envelope(
            execution_id="exec-1", profile=PROFILE, tool=WRITE, fields=("order_ref",), reason="conflict"
        )
        parsed = json.loads(text)
        assert text == canonical_json(parsed) and "PO-" not in text
        bound = fp.field_provenance_refusal(parsed, execution_id="exec-1", tool=WRITE, profile=PROFILE)
        assert bound == {"execution_id": "exec-1", "tool": WRITE, "fields": ["order_ref"], "reason": "conflict"}
        for forged in (
            {**parsed, "execution_id": "exec-2"},
            {**parsed, "tool": READ},
            {**parsed, "profile_sha256": "0" * 64},
            {**parsed, "reason": "maybe"},
            {**parsed, "fields": []},
        ):
            assert fp.field_provenance_refusal(forged, execution_id="exec-1", tool=WRITE, profile=PROFILE) is None

    def test_only_this_profiles_request_binds_an_answer(self) -> None:
        request = {
            "status": "input_required",
            "contract": {
                "fields": ["order_ref"],
                "field_provenance": {
                    "profile_sha256": PROFILE.sha256,
                    "tool": WRITE,
                    "fields": ["order_ref"],
                    "resource": dict(RESOURCE),
                },
            },
        }
        assert fp.profile_request(request, PROFILE) == {
            "tool": WRITE,
            "fields": ("order_ref",),
            "resource": RESOURCE,
        }
        foreign = json.loads(json.dumps(request))
        foreign["contract"]["field_provenance"]["profile_sha256"] = "0" * 64
        plain = {"status": "input_required", "contract": {"fields": ["order_ref"]}}
        assert fp.profile_request(foreign, PROFILE) is None
        assert fp.profile_request(plain, PROFILE) is None


# -- durable loader ----------------------------------------------------------


PIN = PROFILE.pin()


def _decision(turn: int, *actions: tuple[str, dict], pin: Any = PIN, inputs: list | None = None):
    refs: dict[str, Any] = {"controller": CONTROLLER_LOOP_MARKER, "turn": turn, "field_provenance": pin}
    if inputs:
        refs["inputs"] = [row["revision_id"] for row in inputs]
        refs["input_digests"] = [_revision_digest(row) for row in inputs]
    record = AcceptedDecisionRecord(
        run_ref=RUN,
        decision_step_id=decision_step_id_for_turn("agent-turn", turn),
        input_refs=refs,
        accepted_output={"text": ""},
        actions=tuple(AcceptedAction(f"a{index}", tool, args) for index, (tool, args) in enumerate(actions)),
        accepted_at="2026-11-09T00:00:00+00:00",
    )
    return replace(record, content_digest=canonical_decision_digest(record))


def _revision(content: dict, effective_turn: int, *, source: str = "provide_input", rid: str = "rev-1") -> dict:
    return {
        "revision_id": rid,
        "content": content,
        "effective_turn": effective_turn,
        "source": source,
        "source_ref": f"command-{rid}",
        "wait_token": f"token-{rid}",
    }


def _revision_digest(row: dict) -> str:
    payload = {"id": row["revision_id"], "content": row["content"], "effective_turn": row["effective_turn"]}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _request_payload(resource: dict | None = None, *, sha: str | None = None) -> dict:
    return {
        "status": "input_required",
        "contract": {
            "fields": ["order_ref"],
            "field_provenance": {
                "profile_sha256": sha or PROFILE.sha256,
                "tool": WRITE,
                "fields": ["order_ref"],
                "resource": dict(resource or RESOURCE),
            },
        },
    }


class _Store:
    """Store double over the real decision, ledger and revision row shapes."""

    def __init__(self, decisions: list, revisions: list | None = None) -> None:
        self.decisions = decisions
        self.revisions = revisions or []
        self.rows: dict[str, dict] = {}
        self.commands: dict[str, Any] = {}
        self.fail: str | None = None

    def settle(self, decision, index: int, *, outcome: str = "succeeded", payload: Any = None, **row: Any) -> None:
        action = decision.actions[index]
        self.rows[execution_id_for_action(RUN, decision.decision_step_id, action.action_key)] = {
            "intent": {"action_name": action.tool, "arguments_digest": tool_arguments_digest(action.arguments)},
            "last_outcome": {
                "outcome": outcome,
                "result_digest": tool_arguments_digest(payload) if payload is not None else None,
            },
            "pending_reconciliation": False,
            "task_issuer": TASK.issuer_domain,
            "task_id": TASK.id,
            "result_payload": payload,
            **row,
        }

    def apply_answer(self, revision, decision, index: int = 0) -> None:
        execution_id = execution_id_for_action(RUN, decision.decision_step_id, decision.actions[index].action_key)
        self.commands[revision["source_ref"]] = SimpleNamespace(
            kind=ControlCommandKind.PROVIDE_INPUT,
            state=CommandState.APPLIED,
            task_ref=TASK,
            detail_ns={"wait_token": revision["wait_token"]},
            extra={
                "effect_run_ref": RUN_REF.to_dict(),
                "input_revision": revision["revision_id"],
                "input_request_binding": {
                    "execution_id": execution_id,
                    "contract_digest": canonical_request_digest(self.rows[execution_id]["result_payload"]["contract"]),
                },
            },
        )

    def get(self, command_id):
        return self.commands.get(command_id)

    def list_accepted_actions(self, run_ref: Any) -> list[dict]:
        if self.fail == "decisions":
            raise RuntimeError("decision table unavailable")
        return [{"decision_step_id": d.decision_step_id, "content_digest": d.content_digest} for d in self.decisions]

    def get_run_action(self, run_ref: Any, action_key: str) -> dict | None:
        return self.rows.get(action_key)

    def list_input_revisions(self, task_ref: Any, run_ref: Any) -> list[dict]:
        if self.fail == "revisions":
            raise ValueError("input revision history is incomplete")
        return list(self.revisions)

    def lookup(self, step: str) -> Any:
        return next((d for d in self.decisions if d.decision_step_id == step), None)


@pytest.fixture(autouse=True)
def _lookup_from_double(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SqlAcceptedDecisionStore, "lookup_sync", lambda self, run_ref, step: self._store.lookup(step))


def _load(store: _Store, decision, index: int = 0, *, arguments: dict | None = None) -> fp.ProvenanceFacts:
    action = decision.actions[index]
    return fp.load_provenance_facts(
        store,
        PROFILE,
        RULES,
        task_ref=TASK,
        run_ref=RUN_REF,
        controller_run=RUN,
        tool_call_id=tool_call_id_for_action(RUN, decision.decision_step_id, action.action_key),
        tool=action.tool,
        arguments=arguments if arguments is not None else action.arguments,
    )


def _clarified_chain(value: str = "PO-PROVIDED-5521"):
    answer = _revision({"order_ref": value}, 3)
    read = _decision(1, (READ, RESOURCE))
    asked = _decision(2, (WRITE, _write(order_ref="PO-GUESS")))
    answered = _decision(3, (WRITE, _write(order_ref=value)), inputs=[answer])
    store = _Store([read, asked, answered], [answer])
    store.settle(read, 0, payload={"status": "ok"})
    store.settle(asked, 0, payload=_request_payload())
    store.apply_answer(answer, asked)
    return store, asked, answered


class TestLoader:
    @pytest.mark.parametrize("value", VALUES)
    def test_a_pinned_answer_binds_to_the_request_it_answered(self, value: str) -> None:
        store, asked, answered = _clarified_chain(value)
        facts = _load(store, answered)
        assert facts.turn == 3 and len(facts.reads) == 1
        assert facts.supplements[0].request == {"tool": WRITE, "fields": ("order_ref",), "resource": RESOURCE}
        assert fp.evaluate_field_provenance(RULES, answered.actions[0].arguments, facts).kind == "allow"

    def test_a_later_answer_never_authorizes_the_decision_that_asked(self) -> None:
        store, asked, _answered = _clarified_chain()
        facts = _load(store, asked)
        assert facts.supplements == ()
        assert fp.evaluate_field_provenance(RULES, asked.actions[0].arguments, facts).kind == "input_required"

    def test_receipts_count_only_from_earlier_settled_decisions(self) -> None:
        earlier = _decision(1, (READ, RESOURCE))
        same = _decision(2, (READ, RESOURCE), (WRITE, _write(order_ref="PO-1")))
        store = _Store([earlier, same])
        store.settle(same, 0, payload={"order_ref": "PO-1"})
        assert _load(store, same, 1).reads == ()  # never dispatched + same decision
        for outcome in ("failed", "unknown"):
            store.settle(earlier, 0, outcome=outcome, payload={"order_ref": "PO-1"})
            assert _load(store, same, 1).reads == ()
        store.settle(earlier, 0, payload={"order_ref": "PO-1"}, pending_reconciliation=True)
        assert _load(store, same, 1).reads == ()
        store.settle(earlier, 0, payload={"order_ref": "PO-1"})
        assert [read.result for read in _load(store, same, 1).reads] == [{"order_ref": "PO-1"}]

    @pytest.mark.parametrize(
        "row",
        [{"task_id": "task-2"}, {"intent": {"action_name": READ, "arguments_digest": "0" * 64}}],
        ids=["foreign-task", "digest-mismatch"],
    )
    def test_a_ledger_that_disagrees_with_the_decision_is_unavailable(self, row: dict) -> None:
        earlier = _decision(1, (READ, RESOURCE))
        write = _decision(2, (WRITE, _write(order_ref="PO-1")))
        store = _Store([earlier, write])
        store.settle(earlier, 0, payload={"order_ref": "PO-1"}, **row)
        with pytest.raises(fp._UnavailableError):
            _load(store, write)

    def test_changed_receipt_content_cannot_authorize_its_new_value(self) -> None:
        read = _decision(1, (READ, RESOURCE))
        write = _decision(2, (WRITE, _write(order_ref="PO-2")))
        store = _Store([read, write])
        store.settle(read, 0, payload={"order_ref": "PO-1"})
        next(iter(store.rows.values()))["result_payload"] = {"order_ref": "PO-2"}
        with pytest.raises(fp._UnavailableError, match="result digest"):
            _load(store, write)

    def test_the_call_must_be_the_accepted_action_of_a_pinned_decision(self) -> None:
        write = _decision(1, (WRITE, _write(order_ref="PO-1")))
        store = _Store([write])
        with pytest.raises(fp._UnboundError, match="differs"):
            _load(store, write, arguments=_write(order_ref="PO-2"))
        with pytest.raises(fp._UnboundError, match="not an action"):
            fp.load_provenance_facts(
                store,
                PROFILE,
                RULES,
                task_ref=TASK,
                run_ref=RUN_REF,
                controller_run=RUN,
                tool_call_id=tool_call_id_for_action(RunRef("hecate", "run-2"), write.decision_step_id, "a0"),
                tool=WRITE,
                arguments=write.actions[0].arguments,
            )
        unpinned = _decision(1, (WRITE, _write(order_ref="PO-1")), pin={**PROFILE.pin(), "sha256": "0" * 64})
        with pytest.raises(fp._UnboundError, match="profile"):
            _load(_Store([unpinned]), unpinned)

    @pytest.mark.parametrize("damage", ["missing", "digest", "future"])
    def test_pinned_revisions_that_disagree_with_the_decision_are_unavailable(self, damage: str) -> None:
        store, _asked, answered = _clarified_chain()
        if damage == "missing":
            store.revisions = []
        elif damage == "digest":
            store.revisions = [{**store.revisions[0], "content": {"order_ref": "PO-REWRITTEN"}}]
        else:
            store.revisions = [{**store.revisions[0], "effective_turn": 4}]
        with pytest.raises(fp._UnavailableError):
            _load(store, answered)

    def test_live_supplements_and_answers_to_other_requests_are_not_sources(self) -> None:
        store, asked, answered = _clarified_chain()
        store.revisions = [{**store.revisions[0], "source": "live_input"}]
        assert _load(store, answered).supplements == ()
        store, asked, answered = _clarified_chain()
        store.commands["command-rev-1"].extra.pop("input_request_binding")
        store.settle(asked, 0, payload={"status": "input_required", "contract": {"fields": ["order_ref"]}})
        assert _load(store, answered).supplements[0].request is None
        store.settle(asked, 0, payload=_request_payload(sha="0" * 64))
        facts = _load(store, answered)
        assert facts.supplements[0].request is None
        assert fp.evaluate_field_provenance(RULES, answered.actions[0].arguments, facts).kind == "input_required"

    def test_one_wake_binds_only_the_consumed_request(self) -> None:
        answer = _revision({"order_ref": "PO-1"}, 2)
        asked = _decision(1, (WRITE, _write(order_ref="X")), (WRITE, _write(order_ref="Y")))
        answered = _decision(2, (WRITE, _write(order_ref="PO-1")), inputs=[answer])
        store = _Store([asked, answered], [answer])
        store.settle(asked, 0, payload=_request_payload())
        store.settle(asked, 1, payload=_request_payload())
        store.apply_answer(answer, asked, 1)
        assert _load(store, answered).supplements[0].request is not None
        binding = store.commands[answer["source_ref"]].extra["input_request_binding"]
        binding["contract_digest"] = "0" * 64
        with pytest.raises(fp._UnavailableError, match="does not match"):
            _load(store, answered)

    def test_an_unrelated_or_unapplied_command_cannot_vouch_for_an_answer(self) -> None:
        store, _, answered = _clarified_chain()
        command = store.commands["command-rev-1"]
        for field, forged in (("state", CommandState.REJECTED), ("detail_ns", {"wait_token": "foreign"})):
            original = getattr(command, field)
            setattr(command, field, forged)
            with pytest.raises(fp._UnavailableError, match="matching applied wake"):
                _load(store, answered)
            setattr(command, field, original)

    @pytest.mark.parametrize("fail", ["decisions", "revisions"])
    def test_store_failures_are_unavailable(self, fail: str) -> None:
        store, _asked, answered = _clarified_chain()
        store.fail = fail
        with pytest.raises(fp._UnavailableError):
            _load(store, answered)

    def test_two_decisions_claiming_one_turn_are_unavailable(self) -> None:
        first = _decision(1, (READ, RESOURCE))
        clash = replace(first, decision_step_id="agent-turn-dup")
        clash = replace(clash, content_digest=canonical_decision_digest(clash))
        with pytest.raises(fp._UnavailableError, match="same turn"):
            _load(_Store([first, clash]), first)


# -- guard -------------------------------------------------------------------


class _RaisingStore:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the guard read {name} for an unprotected or unbound call")


class TestGuard:
    def _guard(self, store: Any) -> fp.ControllerFieldProvenanceGuard:
        return fp.ControllerFieldProvenanceGuard(
            PROFILE, store=store, task_ref=TASK, run_ref=RUN_REF, controller_run=RUN
        )

    async def test_unprotected_tools_pass_without_reading_any_fact(self) -> None:
        assert await self._guard(_RaisingStore()).check(READ, RESOURCE, {"tool_call_id": "x"}) is None

    async def test_replay_restores_only_a_structured_ledger_result(self) -> None:
        store = _Store([])
        guard = self._guard(store)
        payload = {"order_ref": "PO-1"}
        content = json.dumps(payload)
        store.rows["source"] = {"result_payload": content}
        assert await guard.restore_source_result("source", READ, content) == content
        store.rows["source"] = {"result_payload": payload}
        assert await guard.restore_source_result("source", READ, content) == payload

    async def test_a_call_without_an_accepted_identity_is_refused_unbound(self) -> None:
        refusal = await self._guard(_RaisingStore()).check(WRITE, _write(order_ref="PO-1"), {"invocation_id": "e"})
        parsed = json.loads(refusal)
        assert (parsed["status"], parsed["reason"], parsed["execution_id"]) == (
            fp.FIELD_PROVENANCE_REFUSED,
            "unbound",
            "e",
        )

    async def test_unreadable_facts_are_refused_unavailable(self) -> None:
        store, _asked, answered = _clarified_chain()
        store.fail = "decisions"
        action = answered.actions[0]
        context = {
            "invocation_id": "e",
            "tool_call_id": tool_call_id_for_action(RUN, answered.decision_step_id, action.action_key),
        }
        assert json.loads(await self._guard(store).check(WRITE, action.arguments, context))["reason"] == "unavailable"

    async def test_an_unsourced_value_becomes_a_bound_input_request(self) -> None:
        store, asked, _answered = _clarified_chain()
        action = asked.actions[0]
        context = {
            "tool_call_id": tool_call_id_for_action(RUN, asked.decision_step_id, action.action_key),
            "invocation_id": execution_id_for_action(RUN, asked.decision_step_id, action.action_key),
        }
        request = await self._guard(store).check(WRITE, action.arguments, context)
        assert request["status"] == "input_required"
        assert request["contract"]["fields"] == ["order_ref"]
        assert request["contract"]["prompt"] == DECLARATION["rules"][0]["prompt"]
        assert fp.profile_request(request, PROFILE) == {"tool": WRITE, "fields": ("order_ref",), "resource": RESOURCE}

    async def test_a_valid_call_id_cannot_authorize_another_invocation(self) -> None:
        store, _, answered = _clarified_chain()
        action = answered.actions[0]
        context = {
            "tool_call_id": tool_call_id_for_action(RUN, answered.decision_step_id, action.action_key),
            "invocation_id": "another-execution",
        }
        result = await self._guard(store).check(WRITE, action.arguments, context)
        assert json.loads(result)["reason"] == "unbound"


# -- governance boundary -----------------------------------------------------


class _Registry:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        self.calls.append((name, dict(args)))
        return {"status": "ok"}


class _Guard:
    def __init__(self, verdict: Any) -> None:
        self.verdict = verdict
        self.checked: list[tuple[str, dict]] = []

    async def check(self, name: str, arguments: dict, context: dict) -> Any:
        self.checked.append((name, dict(arguments)))
        return self.verdict

    def prepare_business_result(self, result: Any) -> Any:
        return result


def _governance(rules: list | None = None) -> Any:
    return build_platform_governance(None, mode=GovernanceMode.ENFORCED, rules=rules, identity={"actor": "agent:7"})


class TestGovernanceBoundary:
    async def test_a_verdict_replaces_the_business_call(self) -> None:
        registry, guard = _Registry(), _Guard({"status": "input_required", "contract": {"fields": ["order_ref"]}})
        result = await _governance().with_field_guard(guard).execute_tool(registry, WRITE, _write(order_ref="PO-1"))
        assert result == guard.verdict and registry.calls == []
        assert guard.checked == [(WRITE, _write(order_ref="PO-1"))]

    async def test_an_allowed_call_reaches_the_business_registry(self) -> None:
        registry, guard = _Registry(), _Guard(None)
        result = await _governance().with_field_guard(guard).execute_tool(registry, WRITE, _write(order_ref="PO-1"))
        assert result == {"status": "ok"} and registry.calls == [(WRITE, _write(order_ref="PO-1"))]

    @pytest.mark.parametrize("as_text", [False, True])
    async def test_a_business_result_cannot_mint_a_protected_input_request(self, as_text) -> None:
        class StubRegistry(_Registry):
            async def execute(self, name, args, context=None):
                payload = _request_payload()
                return json.dumps(payload) if as_text else payload

        guard = fp.ControllerFieldProvenanceGuard(
            PROFILE, store=None, task_ref=TASK, run_ref=RUN_REF, controller_run=RUN
        )
        result = await _governance().with_field_guard(guard).execute_tool(StubRegistry(), READ, RESOURCE)
        payload = json.loads(result) if as_text else result
        assert payload["status"] == "input_required" and payload["contract"]["fields"] == ["order_ref"]
        assert fp.profile_request(payload, PROFILE) is None

    async def test_authorization_precedes_provenance(self) -> None:
        deny = [{"action": "deny", "tool_pattern": WRITE, "priority": 10, "arg_conditions": {}}]
        registry, guard = _Registry(), _Guard(None)
        with pytest.raises(GovernanceDeniedError):
            await _governance(deny).with_field_guard(guard).execute_tool(registry, WRITE, _write(order_ref="PO-1"))
        assert guard.checked == [] and registry.calls == []

    async def test_binding_a_guard_never_mutates_the_shared_facade(self) -> None:
        shared, registry = _governance(), _Registry()
        shared.with_field_guard(_Guard("refused"))
        assert await shared.execute_tool(registry, WRITE, _write(order_ref="PO-1")) == {"status": "ok"}


# -- dispatcher watch ---------------------------------------------------------


class TestRefusalWatch:
    def _refusal(self, reason: str, execution_id: str = "exec-1") -> dict:
        return json.loads(
            fp.refusal_envelope(
                execution_id=execution_id, profile=PROFILE, tool=WRITE, fields=("order_ref",), reason=reason
            )
        )

    @pytest.mark.parametrize(
        ("reason", "status"),
        [("conflict", "failed"), ("unbound", "failed"), ("unavailable", "awaiting_reconciliation")],
    )
    def test_bound_refusals_stop_with_an_explicit_terminal(self, reason: str, status: str) -> None:
        guard = fp.ControllerFieldProvenanceGuard(
            PROFILE, store=None, task_ref=TASK, run_ref=RUN_REF, controller_run=RUN
        )
        watch = _ControllerFieldProvenanceStop(guard)
        watch.observe_result("exec-1", WRITE, self._refusal(reason))
        assert watch.stop_reason() == f"field_provenance_{reason}"
        outcome = watch.outcome()
        assert (outcome["status"], outcome["error_category"]) == (status, f"field_provenance_{reason}")
        assert outcome["field_provenance"]["execution_id"] == "exec-1"

    def test_copied_or_foreign_envelopes_stay_inert(self) -> None:
        guard = fp.ControllerFieldProvenanceGuard(
            PROFILE, store=None, task_ref=TASK, run_ref=RUN_REF, controller_run=RUN
        )
        watch = _ControllerFieldProvenanceStop(guard)
        watch.observe_result("exec-2", WRITE, self._refusal("conflict"))
        watch.observe_result("exec-1", READ, self._refusal("conflict"))
        watch.observe_result("exec-1", WRITE, {"status": "ok"})
        assert watch.requested is None and watch.stop_reason() is None
