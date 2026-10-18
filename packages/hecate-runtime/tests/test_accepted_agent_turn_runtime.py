"""One-turn composition: accept a decision, then dispatch its actions.

Covers the agent-turn slice: the fixed respond→commit→dispatch order,
the decision-phase digest pinning the action phase (a record swapped in
between is a conflict raised before any dispatch), ownership threading
through both phases, same-entry recovery with zero model calls, and the
outcome carrying facts without completion semantics. Deterministic
doubles only — the durable ledger, real leases and cross-process
restarts live in the hecate-durable integration tests.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import replace
from typing import Any

import pytest
from hecate_runtime.accepted_decision import (
    FINAL_ANSWER_SCHEMA_VERSION,
    AcceptedAction,
    DecisionConflictError,
    DecisionOwnership,
    DecisionStoreUnavailableError,
    canonical_decision_digest,
)
from hecate_runtime.agent_turn import AgentTurn, AgentTurnOutcome
from hecate_runtime.decision_step import KIND_FINAL_ANSWER, DecisionProposal, DecisionStep
from hecate_runtime.eventstore import InMemoryEventStore
from hecate_runtime.tool_call_executor import ToolCallExecutor
from test_accepted_action_step import (
    STUB_OWNER,
    StubLedger,
    StubToolPort,
    _run_ref,
)
from test_decision_step import InMemoryDecisionStore, ScriptedResponder


def _proposal(*tools: tuple[str, str, dict[str, Any]]) -> DecisionProposal:
    return DecisionProposal(
        accepted_output={"answer": "do-x"},
        actions=tuple(AcceptedAction(action_key=key, tool=tool, arguments=args) for key, tool, args in tools),
    )


def _final_answer_proposal(text: str = "no tools are needed") -> DecisionProposal:
    return DecisionProposal(accepted_output={"text": text}, actions=(), kind=KIND_FINAL_ANSWER)


def _turn(
    store: InMemoryDecisionStore,
    responder: ScriptedResponder,
    port: StubToolPort,
    ledger: StubLedger,
) -> AgentTurn:
    executor = ToolCallExecutor(port=port, event_store=InMemoryEventStore(), action_hook=ledger)
    return AgentTurn(store, responder, executor)


async def _commit_via_step(store: InMemoryDecisionStore, run_ref, proposal: DecisionProposal) -> None:
    """Establish a committed decision without going through AgentTurn."""
    await DecisionStep(store, ScriptedResponder(proposal)).execute(
        run_ref=run_ref, decision_step_id="step-1", input_refs={"config": "v1"}
    )


def _tool_names(port: StubToolPort) -> list[str]:
    return [name for name, _, _ in port.calls]


async def test_new_turn_follows_respond_commit_dispatch_order() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    responder = ScriptedResponder(_proposal(("a1", "memory_add", {"q": 1}), ("a2", "web_search", {"q": 2})))
    outcome = await _turn(store, responder, port, ledger).run(
        run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
    )
    assert outcome.resumed is False
    assert len(responder.calls) == 1  # exactly one model call
    assert outcome.record.accepted_at is not None  # committed before dispatch
    assert _tool_names(port) == ["memory_add", "web_search"]  # business calls only after commit
    assert [o.status for o in outcome.report.outcomes] == ["succeeded", "succeeded"]
    assert outcome.report.unprocessed_action_keys == ()


@pytest.mark.parametrize("failure", ["commit_fails", "store_down", "responder_error"])
async def test_decision_phase_failure_keeps_business_calls_at_zero(failure) -> None:
    store = InMemoryDecisionStore(
        unavailable=failure == "store_down",
        fail_commits=failure == "commit_fails",
    )
    port, ledger = StubToolPort(), StubLedger()
    responder = ScriptedResponder(error=RuntimeError("model down") if failure == "responder_error" else None)
    with pytest.raises((DecisionStoreUnavailableError, RuntimeError)):
        await _turn(store, responder, port, ledger).run(run_ref=_run_ref(), decision_step_id="step-1")
    assert port.calls == []
    assert ledger.claims == []


async def test_record_swapped_between_phases_conflicts_before_any_dispatch() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    await _commit_via_step(store, run_ref, _proposal(("a1", "memory_add", {"q": 1})))
    original = await store.lookup(run_ref, "step-1")
    assert original is not None
    # A different but internally valid committed record: swapped in after
    # the decision phase accepted the original one.
    forged = replace(original, accepted_output={"answer": "do-something-else"})
    forged = replace(forged, content_digest=canonical_decision_digest(forged))
    base_lookup = store.lookup
    calls = 0

    async def swapping_lookup(r, step_id):
        nonlocal calls
        calls += 1
        return forged if calls >= 2 else await base_lookup(r, step_id)

    store.lookup = swapping_lookup
    responder = ScriptedResponder()
    with pytest.raises(DecisionConflictError):
        await _turn(store, responder, port, ledger).run(
            run_ref=run_ref, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
        )
    assert responder.calls == []  # recovery hit: no model call before the conflict
    assert port.calls == []  # conflict raised before any dispatch
    assert ledger.claims == []


async def test_turn_pins_the_decision_phase_digest_into_the_batch() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    turn = _turn(store, ScriptedResponder(_proposal(("a1", "memory_add", {"q": 1}))), port, ledger)
    received: dict[str, object] = {}
    batch_run = turn._action_batch.run

    async def spy_run(**kwargs):
        received["expected_decision_digest"] = kwargs.get("expected_decision_digest")
        return await batch_run(**kwargs)

    turn._action_batch.run = spy_run
    outcome = await turn.run(run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"})
    assert received["expected_decision_digest"] == outcome.record.content_digest


async def test_batch_expected_digest_matching_succeeds_and_mismatching_raises() -> None:
    from hecate_runtime.accepted_action_batch import AcceptedActionBatch
    from hecate_runtime.accepted_action_step import AcceptedActionStep

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    await _commit_via_step(store, run_ref, _proposal(("a1", "memory_add", {"q": 1})))
    record = await store.lookup(run_ref, "step-1")
    assert record is not None
    batch = AcceptedActionBatch(
        AcceptedActionStep(store, ToolCallExecutor(port=port, event_store=InMemoryEventStore(), action_hook=ledger)),
        store,
    )

    ok = await batch.run(
        run_ref=run_ref,
        decision_step_id="step-1",
        ownership=STUB_OWNER,
        expected_decision_digest=record.content_digest,
    )
    assert [o.status for o in ok.outcomes] == ["succeeded"]

    with pytest.raises(DecisionConflictError):
        await batch.run(
            run_ref=run_ref,
            decision_step_id="step-1",
            ownership=STUB_OWNER,
            expected_decision_digest="0" * 64,
        )
    assert _tool_names(port) == ["memory_add"]  # the mismatching pass dispatched nothing


async def test_turn_outcome_threads_one_ownership_instance_through_both_phases() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    owner = DecisionOwnership("lease-turn", 7, "worker-turn")
    committed: list[DecisionOwnership | None] = []
    base_commit = store.commit

    async def commit_spy(record, ownership=None):
        committed.append(ownership)
        return await base_commit(record, ownership)

    store.commit = commit_spy
    dispatched: list[DecisionOwnership] = []
    ledger.validator = lambda run_ref, ownership: dispatched.append(ownership)

    outcome = await _turn(store, ScriptedResponder(_proposal(("a1", "memory_add", {"q": 1}))), port, ledger).run(
        run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"}, ownership=owner
    )
    assert outcome.report.outcomes[0].status == "succeeded"
    assert committed == [owner]
    assert dispatched and all(seen is owner for seen in dispatched)  # same instance, not a copy


async def test_missing_ownership_is_never_synthesized() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    outcome = await _turn(store, ScriptedResponder(_proposal(("a1", "memory_add", {"q": 1}))), port, ledger).run(
        run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"}
    )
    # The stub ledger requires an owner: the turn reports the stop instead
    # of inventing a lease, and no business call escapes.
    assert outcome.report.outcomes[0].status == "stopped"
    assert port.calls == []


async def test_committed_but_undispatched_recovery_asks_no_model() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    await _commit_via_step(store, run_ref, _proposal(("a1", "memory_add", {"q": 1})))

    responder = ScriptedResponder()
    outcome = await _turn(store, responder, port, ledger).run(
        run_ref=run_ref, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
    )
    assert outcome.resumed is True
    assert responder.calls == []  # zero model calls on recovery
    assert _tool_names(port) == ["memory_add"]  # actions dispatch from the beginning


async def test_recovery_replays_completed_prefix_and_continues() -> None:
    from hecate_runtime.accepted_action_step import AcceptedActionStep

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    await _commit_via_step(store, run_ref, _proposal(("a1", "memory_add", {"q": 1}), ("a2", "web_search", {"q": 2})))
    # Simulated crash after A1: only the first action was dispatched.
    executor = ToolCallExecutor(port=port, event_store=InMemoryEventStore(), action_hook=ledger)
    await AcceptedActionStep(store, executor).dispatch(
        ownership=STUB_OWNER, run_ref=run_ref, decision_step_id="step-1", action_key="a1"
    )
    assert _tool_names(port) == ["memory_add"]

    fresh_port = StubToolPort()
    outcome = await _turn(store, ScriptedResponder(), fresh_port, ledger).run(
        run_ref=run_ref, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
    )
    assert outcome.resumed is True
    assert [o.status for o in outcome.report.outcomes] == ["succeeded", "succeeded"]
    assert _tool_names(fresh_port) == ["web_search"]  # A1 replayed from the ledger, zero calls
    first = outcome.report.outcomes[0]
    assert first.result is not None and json.loads(first.result["content"]) == {"ok": True, "by": "memory_add"}


async def test_input_refs_conflict_rejects_recovery_without_model_call() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    await _commit_via_step(store, run_ref, _proposal(("a1", "memory_add", {"q": 1})))

    responder = ScriptedResponder()
    with pytest.raises(DecisionConflictError):
        await _turn(store, responder, port, ledger).run(
            run_ref=run_ref, decision_step_id="step-1", input_refs={"config": "v2"}
        )
    assert responder.calls == []
    assert port.calls == []


async def test_outcome_carries_facts_without_completion_semantics() -> None:
    store, ledger = InMemoryDecisionStore(), StubLedger()

    class StubFailingSecondPort(StubToolPort):
        """Fail the second business action with a confirmed error."""

        async def tool_execute(self, name, args, context=None):
            self.calls.append((name, args, dict(context or {})))
            if name == "memory_add" and args.get("q") == 2:
                raise RuntimeError("tool down")
            return {"ok": True, "by": name}

    turn = _turn(
        store,
        ScriptedResponder(
            _proposal(("a1", "memory_add", {"q": 1}), ("a2", "memory_add", {"q": 2}), ("a3", "web_search", {"q": 3}))
        ),
        StubFailingSecondPort(),
        ledger,
    )
    outcome = await turn.run(
        run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
    )
    assert [o.status for o in outcome.report.outcomes] == ["succeeded", "failed"]
    assert outcome.report.unprocessed_action_keys == ("a3",)
    assert {f.name for f in dataclasses.fields(AgentTurnOutcome)} == {"record", "resumed", "report"}


async def test_final_answer_turn_returns_text_with_zero_dispatch() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    outcome = await _turn(store, ScriptedResponder(_final_answer_proposal()), port, ledger).run(
        run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
    )
    assert outcome.resumed is False
    assert outcome.is_final_answer is True
    assert outcome.record.schema_version == FINAL_ANSWER_SCHEMA_VERSION
    assert outcome.record.accepted_output == {"text": "no tools are needed"}
    assert port.calls == []  # zero business tool calls
    assert ledger.claims == []  # zero ledger claims
    assert outcome.report.outcomes == ()
    assert outcome.report.unprocessed_action_keys == ()
    # The discriminator is derived from the record, not a completion-carrying field.
    assert {f.name for f in dataclasses.fields(AgentTurnOutcome)} == {"record", "resumed", "report"}


async def test_final_answer_recovery_hits_lookup_with_zero_model_and_tool_calls() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    await _commit_via_step(store, run_ref, _final_answer_proposal("done"))

    responder = ScriptedResponder()
    outcome = await _turn(store, responder, port, ledger).run(
        run_ref=run_ref, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
    )
    assert outcome.resumed is True
    assert outcome.is_final_answer is True
    assert responder.calls == []  # zero model calls
    assert port.calls == []  # zero tool dispatch
    assert ledger.claims == []
    assert outcome.record.accepted_output == {"text": "done"}


async def test_final_answer_is_not_presented_as_task_success() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    outcome = await _turn(store, ScriptedResponder(_final_answer_proposal("answered")), port, ledger).run(
        run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"}
    )
    # Only the accepted-output facts exist; nothing in the outcome asserts
    # task success, completion or goal attainment — hosts decide next steps.
    assert not any(field in dataclasses.asdict(outcome) for field in ("success", "completed", "task_status"))
    assert outcome.is_final_answer is True


async def test_invalid_run_ref_rejected_before_any_work() -> None:
    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()
    from hecate_runtime.decision_step import DecisionValidationError

    responder = ScriptedResponder()
    with pytest.raises(DecisionValidationError):
        await _turn(store, responder, port, ledger).run(
            run_ref=None,
            decision_step_id="step-1",
        )
    assert responder.calls == []
    assert port.calls == []
    assert ledger.claims == []


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("context", []),
        ("context", ["not-a-mapping"]),
        ("execution_context", False),
        ("execution_context", ["not-a-mapping"]),
        ("ownership", "not-an-owner"),
    ],
)
async def test_invalid_turn_arguments_fail_before_any_decision_work(parameter, value) -> None:
    from hecate_runtime.accepted_decision import DecisionValidationError

    store, responder, port, ledger = InMemoryDecisionStore(), ScriptedResponder(), StubToolPort(), StubLedger()
    arguments = {"ownership": STUB_OWNER, parameter: value}
    with pytest.raises(DecisionValidationError):
        await _turn(store, responder, port, ledger).run(
            run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"}, **arguments
        )
    assert store.lookup_calls == 0
    assert responder.calls == []
    assert port.calls == []
    assert ledger.claims == []


async def test_business_dispatch_observes_completed_decision_commit() -> None:
    phases = []
    run = _run_ref()

    class StubOrderedStore(InMemoryDecisionStore):
        """Record commit completion before business dispatch."""

        async def commit(self, record, ownership=None):
            phases.append("commit-start")
            result = await super().commit(record, ownership)
            phases.append("commit-finished")
            return result

    class StubOrderedResponder(ScriptedResponder):
        """Record the model phase in the observed turn sequence."""

        async def respond(self, *args):
            phases.append("respond")
            return await super().respond(*args)

    store = StubOrderedStore()

    class StubOrderedPort(StubToolPort):
        """Assert the accepted record exists at the business boundary."""

        async def tool_execute(self, name, args, context=None):
            record = await store.lookup(run, "step-1")
            assert record is not None and record.accepted_at
            assert record.content_digest == canonical_decision_digest(record)
            assert phases == ["respond", "commit-start", "commit-finished"]
            phases.append("business")
            return await super().tool_execute(name, args, context)

    outcome = await _turn(store, StubOrderedResponder(), StubOrderedPort(), StubLedger()).run(
        run_ref=run, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
    )
    assert phases == ["respond", "commit-start", "commit-finished", "business"]
    assert outcome.report.outcomes[0].status == "succeeded"


@pytest.mark.parametrize("kind", ["cancel", "approval_wait"])
async def test_turn_does_not_swallow_control_signals_or_dispatch_later_actions(kind) -> None:
    import asyncio

    from hecate_runtime.errors import ApprovalWaitSignal
    from hecate_runtime.tool_access import AccessDecision

    store, port, ledger = InMemoryDecisionStore(), StubToolPort(), StubLedger()

    class StubSecondActionPolicy:
        """Require approval only for the second action."""

        def evaluate(self, tool_meta, rules, eval_context, arguments=None):
            return AccessDecision.REQUIRE_APPROVAL if arguments.get("q") == 2 else AccessDecision.EXECUTE

    class StubControlApproval:
        """Emit the original cancel or durable approval-wait signal."""

        async def request_approval(self, tool_name, arguments, risk_level, context):
            if kind == "cancel":
                raise asyncio.CancelledError
            raise ApprovalWaitSignal(tool_name, arguments, risk_level, context)

    executor = ToolCallExecutor(
        port=port, action_hook=ledger, access_policy=StubSecondActionPolicy(), approval_callback=StubControlApproval()
    )
    proposal = _proposal(("a1", "memory_add", {"q": 1}), ("a2", "memory_add", {"q": 2}), ("a3", "web_search", {"q": 3}))
    with pytest.raises(asyncio.CancelledError if kind == "cancel" else ApprovalWaitSignal):
        await AgentTurn(store, ScriptedResponder(proposal), executor).run(
            run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
        )
    assert len(port.calls) == 1
    assert len(ledger.claims) == 1


async def test_fresh_commit_cannot_be_substituted_before_first_batch_load() -> None:
    class StubSwapAfterCommitStore(InMemoryDecisionStore):
        """Return a different valid record only after fresh acceptance."""

        substituted = None

        async def commit(self, record, ownership=None):
            original = await super().commit(record, ownership)
            changed = replace(original, accepted_output={"answer": "substituted"})
            self.substituted = replace(changed, content_digest=canonical_decision_digest(changed))
            return original

        async def lookup(self, run_ref, decision_step_id):
            if self.substituted is not None:
                return self.substituted
            return await super().lookup(run_ref, decision_step_id)

    store, responder, port, ledger = StubSwapAfterCommitStore(), ScriptedResponder(), StubToolPort(), StubLedger()
    with pytest.raises(DecisionConflictError):
        await _turn(store, responder, port, ledger).run(
            run_ref=_run_ref(), decision_step_id="step-1", input_refs={"config": "v1"}, ownership=STUB_OWNER
        )
    assert len(responder.calls) == 1
    assert port.calls == []
    assert ledger.claims == []
