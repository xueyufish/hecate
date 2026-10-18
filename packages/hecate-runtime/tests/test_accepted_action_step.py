"""Accepted-action dispatch: locate committed facts, execute through the chain.

Covers the step6 dispatch slice: read-only location with zero model calls,
stable cross-process execution identity, governance reuse (ownership gate,
policy, approval), and the three recovery paths over the shared ledger.
Deterministic doubles only — no model and no real business tools.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from typing import Any

import pytest
from hecate_runtime.accepted_action_step import (
    AcceptedActionLookupError,
    AcceptedActionStep,
    execution_id_for_action,
    session_key_for_run,
    tool_call_id_for_action,
)
from hecate_runtime.accepted_decision import (
    AcceptedAction,
    AcceptedDecisionRecord,
    AcceptedDecisionStore,
    DecisionConflictError,
    DecisionOwnership,
    DecisionStoreUnavailableError,
    DecisionValidationError,
    RunRef,
    canonical_decision_digest,
)
from hecate_runtime.action_ledger import (
    ActionClaimVerdict,
    ActionLedgerHook,
    ToolExecutionResolution,
    ToolExecutionState,
    tool_arguments_digest,
)
from hecate_runtime.eventstore import InMemoryEventStore
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_access import AccessDecision, ApprovalDecision
from hecate_runtime.tool_call_executor import ToolCallExecutor

STUB_OWNER = DecisionOwnership("stub-lease", 1, "stub-worker")


class StubToolPort(StubRuntimePort):
    """Record business dispatches; optionally fail the underlying tool."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict, dict]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args, dict(context or {})))
        if self.error is not None:
            raise self.error
        return {"ok": True, "by": name}


class StubPolicy:
    """Return one deterministic access decision and record evaluation."""

    def __init__(self, decision: AccessDecision) -> None:
        self.decision = decision
        self.calls = 0

    def evaluate(self, tool_meta, rules, eval_context, arguments=None):
        self.calls += 1
        return self.decision


class StubApproval:
    """Deterministic approval answer."""

    def __init__(self, approved: bool) -> None:
        self.approved = approved

    async def request_approval(self, tool_name, arguments, risk_level, context):
        return ApprovalDecision(approved=self.approved, reason="scripted", approval_ref="approval-1")


class StubLedger(ActionLedgerHook):
    """Atomic claims and persistent outcomes across step instances."""

    def __init__(self) -> None:
        self.states: dict[str, ToolExecutionResolution] = {}
        self.claims: list[dict] = []
        self.lock = asyncio.Lock()
        self.validator = None

    async def validate_accepted_dispatch(self, *, run_ref, ownership, execution_id):
        if ownership is None:
            raise DecisionValidationError("missing owner")
        if self.validator is not None:
            self.validator(run_ref, ownership)

    async def resolve(self, *, session_id, execution_id):
        return self.states.get(execution_id, ToolExecutionResolution(ToolExecutionState.NEVER_STARTED))

    async def record_claim(self, **kwargs):
        async with self.lock:
            self.claims.append(kwargs)
            key = kwargs["execution_id"]
            existing = self.states.get(key)
            if existing is not None:
                return ActionClaimVerdict(claimed=False, resolution=existing)
            self.states[key] = ToolExecutionResolution(
                ToolExecutionState.CLAIMED,
                arguments_digest=kwargs["arguments_digest"],
                tool_name=kwargs["tool_name"],
            )
            return ActionClaimVerdict(claimed=True)

    async def record_outcome(self, **kwargs):
        result = kwargs.get("result")
        self.states[kwargs["execution_id"]] = ToolExecutionResolution(
            ToolExecutionState.OUTCOME_UNKNOWN
            if kwargs["status"] == "unknown"
            else ToolExecutionState(kwargs["status"]),
            arguments_digest=kwargs["arguments_digest"],
            tool_name=kwargs["tool_name"],
            result_content=result if isinstance(result, str) else json.dumps(result),
        )


class StubDecisionStore(AcceptedDecisionStore):
    """Seeded accepted decisions with optional unavailability."""

    def __init__(self, *, unavailable: bool = False) -> None:
        self._records: dict[tuple[str, str, str], AcceptedDecisionRecord] = {}
        self._unavailable = unavailable

    def seed(self, record: AcceptedDecisionRecord) -> None:
        self._records[(record.run_ref.issuer_domain, record.run_ref.run_id, record.decision_step_id)] = record

    async def lookup(self, run_ref: RunRef, decision_step_id: str) -> AcceptedDecisionRecord | None:
        if self._unavailable:
            raise DecisionStoreUnavailableError("store down")
        return self._records.get((run_ref.issuer_domain, run_ref.run_id, decision_step_id))

    async def commit(
        self, record: AcceptedDecisionRecord, ownership: DecisionOwnership | None = None
    ) -> AcceptedDecisionRecord:
        if self._unavailable:
            raise DecisionStoreUnavailableError("store down")
        key = (record.run_ref.issuer_domain, record.run_ref.run_id, record.decision_step_id)
        existing = self._records.get(key)
        if existing is not None:
            if existing.content_digest != canonical_decision_digest(record):
                raise DecisionConflictError(record.decision_step_id, existing.content_digest or "", "")
            return existing
        stored = replace(
            record,
            accepted_at="2026-01-01T00:00:00+00:00",
            content_digest=canonical_decision_digest(record),
        )
        self._records[key] = stored
        return stored


def _run_ref() -> RunRef:
    return RunRef(issuer_domain="test-domain", run_id=uuid.uuid4().hex)


def _committed(store: StubDecisionStore, run_ref: RunRef, *, second_action: bool = False) -> tuple[str, str]:
    """Commit a two-action-capable decision; return (step_id, first_action_key)."""
    actions = [
        AcceptedAction("action-primary", "memory_add", {"q": 7}),
        AcceptedAction("action-secondary", "web_search", {"query": "stock"}),
    ][: 2 if second_action else 1]
    record = AcceptedDecisionRecord(
        run_ref=run_ref,
        decision_step_id="step-1",
        input_refs={"config": "v1"},
        accepted_output={"text": "do it"},
        actions=tuple(actions),
    )
    store.seed(
        replace(record, accepted_at="2026-01-01T00:00:00+00:00", content_digest=canonical_decision_digest(record))
    )
    return "step-1", "action-primary"


def _step(
    store: StubDecisionStore,
    port: StubToolPort,
    ledger: StubLedger,
    *,
    policy: StubPolicy | None = None,
    approval: StubApproval | None = None,
    validator: Any = None,
) -> AcceptedActionStep:
    executor = ToolCallExecutor(
        port=port,
        event_store=InMemoryEventStore(),
        action_hook=ledger,
        access_policy=policy,
        approval_callback=approval,
    )
    ledger.validator = validator
    return AcceptedActionStep(store, executor)


async def test_missing_decision_fails_without_business_call() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    step = _step(store, port, ledger)
    with pytest.raises(AcceptedActionLookupError) as excinfo:
        await step.dispatch(
            ownership=STUB_OWNER, run_ref=_run_ref(), decision_step_id="absent", action_key="action-primary"
        )
    assert excinfo.value.reason == "no_accepted_decision"
    assert port.calls == [] and ledger.claims == []


async def test_unknown_action_key_fails_without_business_call() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    _committed(store, run_ref)
    step = _step(store, port, ledger)
    with pytest.raises(AcceptedActionLookupError) as excinfo:
        await step.dispatch(ownership=STUB_OWNER, run_ref=run_ref, decision_step_id="step-1", action_key="ghost")
    assert excinfo.value.reason == "no_such_action"
    assert port.calls == [] and ledger.claims == []


async def test_corrupt_record_fails_as_store_error() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)
    seeded = store._records[(run_ref.issuer_domain, run_ref.run_id, step_id)]
    store.seed(replace(seeded, content_digest="0" * 64))  # tampered after commit
    step = _step(store, port, ledger)
    with pytest.raises(DecisionStoreUnavailableError):
        await step.dispatch(ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key)
    assert port.calls == [] and ledger.claims == []


async def test_uncommitted_record_fails_as_store_error() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    store.seed(
        AcceptedDecisionRecord(
            run_ref=run_ref,
            decision_step_id="step-1",
            actions=(AcceptedAction("action-primary", "memory_add", {"q": 7}),),
        )
    )  # no accepted_at / digest: never committed
    step = _step(store, port, ledger)
    with pytest.raises(DecisionStoreUnavailableError):
        await step.dispatch(
            ownership=STUB_OWNER, run_ref=run_ref, decision_step_id="step-1", action_key="action-primary"
        )
    assert port.calls == [] and ledger.claims == []


async def test_store_unavailable_is_not_treated_as_miss() -> None:
    store, port, ledger = StubDecisionStore(unavailable=True), StubToolPort(), StubLedger()
    step = _step(store, port, ledger)
    with pytest.raises(DecisionStoreUnavailableError):
        await step.dispatch(
            ownership=STUB_OWNER, run_ref=_run_ref(), decision_step_id="step-1", action_key="action-primary"
        )
    assert port.calls == [] and ledger.claims == []


async def test_dispatch_uses_accepted_params_and_derived_identity() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)
    step = _step(store, port, ledger)
    outcome = await step.dispatch(
        ownership=STUB_OWNER,
        run_ref=run_ref,
        decision_step_id=step_id,
        action_key=action_key,
        execution_context={"agent_id": "agent-1"},  # provenance only, never identity
    )
    assert outcome.status == "succeeded"
    assert outcome.result is not None and outcome.result.get("is_error") is not True
    assert [(name, args) for name, args, _ in port.calls] == [("memory_add", {"q": 7})]
    context = port.calls[0][2]
    assert context["session_id"] == session_key_for_run(run_ref)
    assert context["agent_id"] == "agent-1"  # provenance passed through
    expected_id = execution_id_for_action(run_ref, step_id, action_key)
    assert list(ledger.states) == [expected_id]  # deterministic, never random fallback


async def test_execution_identity_survives_component_reconstruction() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)
    first = await _step(store, port, ledger).dispatch(
        ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key
    )
    assert first.status == "succeeded"

    # Simulated restart: fresh executor and step over the SAME durable ledger
    # and decision store; the session event log does not survive.
    fresh_port = StubToolPort()
    second = await _step(store, fresh_port, ledger).dispatch(
        ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key
    )
    assert second.status == "succeeded"
    assert second.result is not None and first.result is not None
    # The replay carries the real recorded business result (JSON-serialized in
    # the ledger), not a placeholder or an error marker.
    assert second.result.get("is_error") is not True
    assert json.loads(second.result["content"]) == {"ok": True, "by": "memory_add"}
    assert fresh_port.calls == []  # recovered from the ledger, zero new side effects


async def test_stale_ownership_rejected_before_any_business_call() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)

    def failing_validator(run: RunRef, ownership: DecisionOwnership | None) -> None:
        raise DecisionStoreUnavailableError("lease expired")

    step = _step(store, port, ledger, validator=failing_validator)
    with pytest.raises(DecisionStoreUnavailableError):
        await step.dispatch(ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key)
    assert port.calls == [] and ledger.claims == []  # rejected before claim, no new intents


async def test_valid_ownership_proceeds_and_validator_receives_context() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)
    seen: list[tuple[RunRef, DecisionOwnership | None]] = []

    def validator(run: RunRef, ownership: DecisionOwnership | None) -> None:
        seen.append((run, ownership))

    ownership = DecisionOwnership("lease-key", 3, "worker-1")
    outcome = await _step(store, port, ledger, validator=validator).dispatch(
        run_ref=run_ref, decision_step_id=step_id, action_key=action_key, ownership=ownership
    )
    assert outcome.status == "succeeded"
    assert seen and all(item == (run_ref, ownership) for item in seen)
    assert len(port.calls) == 1


async def test_policy_deny_and_approval_rejection_stay_withheld() -> None:
    store, ledger = StubDecisionStore(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)

    deny_port = StubToolPort()
    outcome = await _step(store, deny_port, ledger, policy=StubPolicy(AccessDecision.DENY)).dispatch(
        ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key
    )
    assert outcome.status == "withheld"
    assert outcome.result is not None and outcome.result["is_error"] is True
    assert deny_port.calls == []  # policy ran inside the shared chain, business call never happened

    approval_port = StubToolPort()
    outcome = await _step(
        store,
        approval_port,
        ledger,
        policy=StubPolicy(AccessDecision.REQUIRE_APPROVAL),
        approval=StubApproval(approved=False),
    ).dispatch(ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key)
    assert outcome.status == "withheld"
    assert approval_port.calls == []


async def test_concurrent_dispatch_has_one_first_executor() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)
    step = _step(store, port, ledger)
    outcomes = await asyncio.gather(
        step.dispatch(ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key),
        step.dispatch(ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key),
    )
    statuses = {outcome.status for outcome in outcomes}
    assert statuses <= {"succeeded", "stopped"}  # both terminal, no partial states
    assert "succeeded" in statuses
    assert len(port.calls) == 1  # business side effect executed exactly once
    # Whichever dispatch loses the claim either replays the recorded result or
    # stops for review (deterministic coverage of the stop branch lives in
    # test_claimed_unknown_outcome_stops_without_redo).


async def test_claimed_unknown_outcome_stops_without_redo() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)
    execution_id = execution_id_for_action(run_ref, step_id, action_key)
    ledger.states[execution_id] = ToolExecutionResolution(
        ToolExecutionState.CLAIMED,
        arguments_digest=tool_arguments_digest({"q": 7}),
        tool_name="memory_add",
    )  # a previous process claimed, then crashed before any outcome
    outcome = await _step(store, port, ledger).dispatch(
        ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key
    )
    assert outcome.status == "stopped"
    assert outcome.stop_reason == "needs_review"
    assert port.calls == []  # unknown outcome: never blindly redo


async def test_tool_failure_returns_failed_receipt() -> None:
    """A tool-reported failure is a terminal is_error result, not a redo."""
    store, ledger = StubDecisionStore(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)
    outcome = await _step(store, StubToolPort(error=RuntimeError("tool down")), ledger).dispatch(
        ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key
    )
    assert outcome.status == "failed"
    assert outcome.result is not None and outcome.result["is_error"] is True


async def test_only_requested_action_is_dispatched() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    step_id, _ = _committed(store, run_ref, second_action=True)
    outcome = await _step(store, port, ledger).dispatch(
        ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key="action-secondary"
    )
    assert outcome.status == "succeeded"
    assert [name for name, _, _ in port.calls] == ["web_search"]  # the other action stays undispatched
    # Single-action success carries no task-level semantics: the outcome type
    # exposes only status/result/stop_reason — no task status to flip.


async def test_metadata_cannot_override_execution_identity() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)
    step = _step(store, port, ledger)
    await step.dispatch(
        ownership=STUB_OWNER,
        run_ref=run_ref,
        decision_step_id=step_id,
        action_key=action_key,
        execution_context={"session_id": "spoofed-1"},
    )
    await step.dispatch(
        ownership=STUB_OWNER,
        run_ref=run_ref,
        decision_step_id=step_id,
        action_key=action_key,
        execution_context={"session_id": "spoofed-2"},
    )
    assert len(port.calls) == 1
    assert port.calls[0][2]["session_id"] == session_key_for_run(run_ref)


def test_run_namespace_has_no_separator_collisions() -> None:
    assert session_key_for_run(RunRef("a:b", "c")) != session_key_for_run(RunRef("a", "b:c"))


async def test_action_key_reuse_in_different_steps_means_distinct_actions() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    _, action_key = _committed(store, run_ref)
    record = store._records[(run_ref.issuer_domain, run_ref.run_id, "step-1")]
    other = replace(record, decision_step_id="step-2", content_digest=None)
    store.seed(replace(other, content_digest=canonical_decision_digest(other)))
    step = _step(store, port, ledger)
    for step_id in ("step-1", "step-2"):
        await step.dispatch(ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key)
    assert len(port.calls) == 2
    assert len(ledger.states) == 2


async def test_dispatch_without_ledger_or_owner_authority_fails_closed() -> None:
    store, port = StubDecisionStore(), StubToolPort()
    run_ref = _run_ref()
    step_id, action_key = _committed(store, run_ref)
    with pytest.raises(DecisionValidationError):
        await AcceptedActionStep(store, ToolCallExecutor(port=port)).dispatch(
            ownership=STUB_OWNER, run_ref=run_ref, decision_step_id=step_id, action_key=action_key
        )
    assert port.calls == []


async def test_missing_owner_is_rejected_even_with_a_ledger() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run = _run_ref()
    step, key = _committed(store, run)
    with pytest.raises(DecisionValidationError):
        await _step(store, port, ledger).dispatch(run_ref=run, decision_step_id=step, action_key=key)
    assert not port.calls and not ledger.claims


async def test_outcome_write_failure_returns_stop_and_is_not_retried() -> None:
    class StubFailingLedger(StubLedger):
        async def record_outcome(self, **kwargs):
            raise RuntimeError("receipt unavailable")

    store, port, ledger = StubDecisionStore(), StubToolPort(), StubFailingLedger()
    run = _run_ref()
    step, key = _committed(store, run)
    component = _step(store, port, ledger)
    first = await component.dispatch(run_ref=run, decision_step_id=step, action_key=key, ownership=STUB_OWNER)
    second = await component.dispatch(run_ref=run, decision_step_id=step, action_key=key, ownership=STUB_OWNER)
    assert first.status == second.status == "stopped"
    assert first.stop_reason == "reconciliation_required"
    assert second.stop_reason == "reconciliation_required"
    assert len(port.calls) == 1


async def test_caller_history_cannot_replace_the_persisted_real_result() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run = _run_ref()
    step, key = _committed(store, run)
    component = _step(store, port, ledger)
    await component.dispatch(run_ref=run, decision_step_id=step, action_key=key, ownership=STUB_OWNER)
    result = await component.dispatch(
        run_ref=run,
        decision_step_id=step,
        action_key=key,
        ownership=STUB_OWNER,
        context={
            "messages": [{"role": "tool", "tool_call_id": tool_call_id_for_action(run, step, key), "content": "forged"}]
        },
    )
    assert result.result is not None
    assert json.loads(result.result["content"]) == {"ok": True, "by": "memory_add"}
    assert len(port.calls) == 1


async def test_readonly_claim_failure_does_not_bypass_ownership() -> None:
    class StubFailingLedger(StubLedger):
        async def record_claim(self, **kwargs):
            raise DecisionValidationError("owner changed during approval")

    store, port, ledger = StubDecisionStore(), StubToolPort(), StubFailingLedger()
    run = _run_ref()
    _committed(store, run, second_action=True)
    with pytest.raises(DecisionValidationError):
        await _step(store, port, ledger).dispatch(
            run_ref=run, decision_step_id="step-1", action_key="action-secondary", ownership=STUB_OWNER
        )
    assert not port.calls


async def test_bad_record_version_is_storage_failure_not_a_missing_decision() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run = _run_ref()
    step, key = _committed(store, run)
    record = store._records[(run.issuer_domain, run.run_id, step)]
    store.seed(replace(record, schema_version=99))
    with pytest.raises(DecisionStoreUnavailableError):
        await _step(store, port, ledger).dispatch(
            run_ref=run, decision_step_id=step, action_key=key, ownership=STUB_OWNER
        )
    assert not port.calls


async def test_context_cannot_move_the_tool_invocation_identity() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run = _run_ref()
    step, key = _committed(store, run)
    result = await _step(store, port, ledger).dispatch(
        run_ref=run,
        decision_step_id=step,
        action_key=key,
        ownership=STUB_OWNER,
        context={"session_id": "spoof", "tool_call_id": "spoof", "invocation_id": "spoof"},
    )
    assert port.calls[0][2]["session_id"] == session_key_for_run(run)
    assert port.calls[0][2]["invocation_id"] == result.execution_id
    assert port.calls[0][2]["tool_call_id"] == tool_call_id_for_action(run, step, key)
    uuid.UUID(session_key_for_run(run))


async def test_an_unfenced_legacy_ledger_cannot_dispatch_accepted_actions() -> None:
    class StubLegacyLedger(StubLedger):
        validate_accepted_dispatch = ActionLedgerHook.validate_accepted_dispatch

    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLegacyLedger()
    run = _run_ref()
    step, key = _committed(store, run)
    with pytest.raises(DecisionValidationError):
        await _step(store, port, ledger).dispatch(
            run_ref=run, decision_step_id=step, action_key=key, ownership=STUB_OWNER
        )
    assert not port.calls and not ledger.claims


async def test_hooks_cannot_silently_change_accepted_arguments() -> None:
    from hecate_runtime.guardrail import GuardrailAction, GuardrailResult, PreToolHook

    class StubMutatingHook(PreToolHook):
        async def on_pre_tool_call(self, name: str, arguments: dict, context: dict | None) -> GuardrailResult:
            arguments["q"] = 999
            return GuardrailResult(action=GuardrailAction.ALLOW)

    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run = _run_ref()
    step, key = _committed(store, run)
    executor = ToolCallExecutor(port=port, action_hook=ledger, pre_tool_hook=StubMutatingHook())
    with pytest.raises(DecisionValidationError):
        await AcceptedActionStep(store, executor).dispatch(
            run_ref=run, decision_step_id=step, action_key=key, ownership=STUB_OWNER
        )
    assert not port.calls and not ledger.claims
