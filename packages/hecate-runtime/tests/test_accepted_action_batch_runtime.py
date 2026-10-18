"""Serial batch execution over one committed accepted decision.

Covers the batch orchestration slice: persisted-order dispatch, terminal
outcomes stop the pass with later actions undispatched, recovery replays
recorded results without new side effects, and the entry point cannot
accept an injected action list. Deterministic doubles only — the durable
ledger, real leases and cross-process behavior live in the
hecate-durable integration tests.

Action mix note: action-1/action-2 use ``memory_add`` (side-effecting) so
the unknown-outcome stop scenario exercises a real redo hazard;
``web_search`` is READONLY and legitimately re-executes under a ledger
claim, which the durable integration tests cover separately.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import replace

import pytest
from hecate_runtime.accepted_action_batch import AcceptedActionBatch
from hecate_runtime.accepted_action_step import AcceptedActionLookupError, execution_id_for_action
from hecate_runtime.accepted_decision import AcceptedAction, AcceptedDecisionRecord, RunRef, canonical_decision_digest
from hecate_runtime.action_ledger import ToolExecutionResolution, ToolExecutionState, tool_arguments_digest
from hecate_runtime.tool_access import AccessDecision
from test_accepted_action_step import (
    STUB_OWNER,
    StubDecisionStore,
    StubLedger,
    StubToolPort,
    _run_ref,
    _step,
)


def _commit_three(store: StubDecisionStore, run_ref: RunRef) -> list[str]:
    """Commit a three-action decision; return the persisted action keys."""
    record = AcceptedDecisionRecord(
        run_ref=run_ref,
        decision_step_id="step-1",
        input_refs={"config": "v1"},
        accepted_output={"text": "do all"},
        actions=(
            AcceptedAction("action-1", "memory_add", {"q": 1}),
            AcceptedAction("action-2", "memory_add", {"q": 2}),
            AcceptedAction("action-3", "web_search", {"q": 3}),
        ),
    )
    store.seed(
        replace(record, accepted_at="2026-01-01T00:00:00+00:00", content_digest=canonical_decision_digest(record))
    )
    return ["action-1", "action-2", "action-3"]


def _batch(store: StubDecisionStore, port: StubToolPort, ledger: StubLedger, **kwargs) -> AcceptedActionBatch:
    return AcceptedActionBatch(_step(store, port, ledger, **kwargs), store)


def _tool_names(port: StubToolPort) -> list[str]:
    return [name for name, _, _ in port.calls]


async def test_batch_runs_all_actions_in_persisted_order() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    _commit_three(store, run_ref)
    report = await _batch(store, port, ledger).run(run_ref=run_ref, decision_step_id="step-1", ownership=STUB_OWNER)
    assert report.unprocessed_action_keys == ()
    assert [outcome.status for outcome in report.outcomes] == ["succeeded"] * 3
    assert _tool_names(port) == ["memory_add", "memory_add", "web_search"]


async def test_terminal_failure_stops_and_reports_unprocessed() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    _commit_three(store, run_ref)

    class StubFailingPort(StubToolPort):
        async def tool_execute(self, name, args, context=None):
            self.calls.append((name, args, dict(context or {})))
            if name == "memory_add" and args.get("q") == 2:
                raise RuntimeError("tool down")
            return {"ok": True, "by": name}

    port = StubFailingPort()
    report = await _batch(store, port, ledger).run(run_ref=run_ref, decision_step_id="step-1", ownership=STUB_OWNER)
    assert [outcome.status for outcome in report.outcomes] == ["succeeded", "failed"]
    assert report.outcomes[-1].result is not None and report.outcomes[-1].result["is_error"] is True
    assert report.unprocessed_action_keys == ("action-3",)
    assert _tool_names(port) == ["memory_add", "memory_add"]  # action-3 never dispatched


class _DenyMemoryAddOnSecondPolicy:
    """Allow the first memory_add (q=1); deny the second (q=2)."""

    def evaluate(self, tool_meta, rules, eval_context, arguments=None):
        name = tool_meta.get("name") if isinstance(tool_meta, dict) else getattr(tool_meta, "name", None)
        if name == "memory_add" and arguments is not None and arguments.get("q") == 2:
            return AccessDecision.DENY
        return AccessDecision.EXECUTE


async def test_withheld_stops_the_pass() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    _commit_three(store, run_ref)
    report = await _batch(store, port, ledger, policy=_DenyMemoryAddOnSecondPolicy()).run(
        run_ref=run_ref, decision_step_id="step-1", ownership=STUB_OWNER
    )
    assert [outcome.status for outcome in report.outcomes] == ["succeeded", "withheld"]
    assert report.unprocessed_action_keys == ("action-3",)
    assert _tool_names(port) == ["memory_add"]  # action-3 never dispatched


async def test_unknown_outcome_stops_and_recovery_does_not_redo() -> None:
    """A2 claimed-without-outcome (previous crash): stop, never redo; A3 stays queued."""
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    _commit_three(store, run_ref)
    ledger.states[execution_id_for_action(run_ref, "step-1", "action-2")] = ToolExecutionResolution(
        ToolExecutionState.CLAIMED,
        arguments_digest=tool_arguments_digest({"q": 2}),
        tool_name="memory_add",
    )

    report = await _batch(store, port, ledger).run(run_ref=run_ref, decision_step_id="step-1", ownership=STUB_OWNER)
    assert [outcome.status for outcome in report.outcomes] == ["succeeded", "stopped"]
    assert report.outcomes[-1].stop_reason == "needs_review"
    assert report.unprocessed_action_keys == ("action-3",)
    assert _tool_names(port) == ["memory_add"]  # A2 never blindly redone

    # A later recovery attempt hits the same unknown state: still no redo.
    report = await _batch(store, port, ledger).run(run_ref=run_ref, decision_step_id="step-1", ownership=STUB_OWNER)
    assert [outcome.status for outcome in report.outcomes] == ["succeeded", "stopped"]
    assert _tool_names(port) == ["memory_add"]


async def test_recovery_replays_completed_prefix_and_continues() -> None:
    """A crash after A1: the rerun replays A1's real result and executes A2/A3."""
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run_ref = _run_ref()
    _commit_three(store, run_ref)
    component = _step(store, port, ledger)

    # Simulated crash after A1: only the first action was dispatched.
    await component.dispatch(ownership=STUB_OWNER, run_ref=run_ref, decision_step_id="step-1", action_key="action-1")
    assert _tool_names(port) == ["memory_add"]

    # Fresh port: anything A1 would re-execute would show up here and fail.
    fresh_port = StubToolPort()
    report = await _batch(store, fresh_port, ledger).run(
        run_ref=run_ref, decision_step_id="step-1", ownership=STUB_OWNER
    )
    assert [outcome.status for outcome in report.outcomes] == ["succeeded"] * 3
    assert report.unprocessed_action_keys == ()
    assert _tool_names(fresh_port) == ["memory_add", "web_search"]  # A1 replayed from the ledger, zero calls
    assert report.outcomes[0].result is not None
    assert json.loads(report.outcomes[0].result["content"]) == {"ok": True, "by": "memory_add"}


async def test_entry_point_accepts_no_injected_action_list() -> None:
    """The construction-level guarantee: there is no action-list parameter."""
    parameters = inspect.signature(AcceptedActionBatch.run).parameters
    assert not any("action" in name for name in parameters)


async def test_missing_decision_propagates_lookup_error() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    with pytest.raises(AcceptedActionLookupError) as excinfo:
        await _batch(store, port, ledger).run(run_ref=_run_ref(), decision_step_id="absent", ownership=STUB_OWNER)
    assert excinfo.value.reason == "no_accepted_decision"
    assert port.calls == []


async def test_different_step_store_cannot_substitute_accepted_arguments() -> None:
    outer, inner = StubDecisionStore(), StubDecisionStore()
    run = _run_ref()
    _commit_three(outer, run)
    record = await outer.lookup(run, "step-1")
    assert record is not None
    changed = replace(
        record, actions=(AcceptedAction("action-1", "memory_add", {"q": "substituted"}), *record.actions[1:])
    )
    inner.seed(replace(changed, content_digest=canonical_decision_digest(changed)))
    port = StubToolPort()
    report = await AcceptedActionBatch(_step(inner, port, StubLedger()), outer).run(
        run_ref=run, decision_step_id="step-1", ownership=STUB_OWNER
    )
    assert port.calls == []
    assert [o.status for o in report.outcomes] == ["stopped"]
    assert report.outcomes[0].stop_reason == "decision_conflict"
    assert report.unprocessed_action_keys == ("action-2", "action-3")


async def test_later_valid_but_different_record_cannot_change_original_order() -> None:
    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run = _run_ref()
    _commit_three(store, run)
    original = await store.lookup(run, "step-1")
    assert original is not None
    changed = replace(original, actions=(original.actions[0], original.actions[2], original.actions[1]))
    changed = replace(changed, content_digest=canonical_decision_digest(changed))
    lookup = store.lookup
    calls = 0

    async def changing_lookup(run_ref, decision_step_id):
        nonlocal calls
        calls += 1
        return changed if calls >= 3 else await lookup(run_ref, decision_step_id)

    store.lookup = changing_lookup
    report = await _batch(store, port, ledger).run(run_ref=run, decision_step_id="step-1", ownership=STUB_OWNER)
    assert len(port.calls) == 1
    assert [o.status for o in report.outcomes] == ["succeeded", "stopped"]
    assert report.outcomes[-1].stop_reason == "decision_conflict"
    assert report.unprocessed_action_keys == ("action-3",)


@pytest.mark.parametrize("kind", ["unavailable", "corrupt", "missing"])
async def test_mid_batch_store_failure_keeps_prefix_and_explicit_stop_reason(kind) -> None:
    from hecate_runtime.accepted_decision import DecisionStoreUnavailableError

    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run = _run_ref()
    _commit_three(store, run)
    lookup = store.lookup
    calls = 0

    async def broken_lookup(run_ref, decision_step_id):
        nonlocal calls
        calls += 1
        if calls >= 3:
            if kind == "missing":
                return None
            if kind == "corrupt":
                record = await lookup(run_ref, decision_step_id)
                assert record is not None
                return replace(record, content_digest="invalid")
            raise DecisionStoreUnavailableError("storage unavailable")
        return await lookup(run_ref, decision_step_id)

    store.lookup = broken_lookup
    report = await _batch(store, port, ledger).run(run_ref=run, decision_step_id="step-1", ownership=STUB_OWNER)
    assert len(port.calls) == 1
    assert [o.status for o in report.outcomes] == ["succeeded", "stopped"]
    assert report.outcomes[-1].stop_reason == "decision_store_unavailable"
    assert report.unprocessed_action_keys == ("action-3",)


@pytest.mark.parametrize("step_id", ["", "  ", "x" * 257, None, []])
async def test_invalid_step_identity_rejected_before_store_read(step_id) -> None:
    from hecate_runtime.accepted_decision import DecisionValidationError

    class StubUnreadableStore(StubDecisionStore):
        async def lookup(self, *args):
            raise AssertionError("invalid identity reached storage")

    store, port = StubUnreadableStore(), StubToolPort()
    with pytest.raises(DecisionValidationError):
        await _batch(store, port, StubLedger()).run(run_ref=_run_ref(), decision_step_id=step_id, ownership=STUB_OWNER)
    assert port.calls == []


@pytest.mark.parametrize("kind", ["cancelled", "approval_wait"])
async def test_control_signals_escape_without_dispatching_later_actions(kind) -> None:
    import asyncio

    from hecate_runtime.errors import ApprovalWaitSignal

    store, port, ledger = StubDecisionStore(), StubToolPort(), StubLedger()
    run = _run_ref()
    _commit_three(store, run)
    step = _step(store, port, ledger)
    dispatch = step.dispatch

    async def interrupted_dispatch(**kwargs):
        if kwargs["action_key"] == "action-2":
            if kind == "cancelled":
                raise asyncio.CancelledError
            raise ApprovalWaitSignal("memory_add")
        return await dispatch(**kwargs)

    step.dispatch = interrupted_dispatch
    with pytest.raises(asyncio.CancelledError if kind == "cancelled" else ApprovalWaitSignal):
        await AcceptedActionBatch(step, store).run(run_ref=run, decision_step_id="step-1", ownership=STUB_OWNER)
    assert len(port.calls) == 1
