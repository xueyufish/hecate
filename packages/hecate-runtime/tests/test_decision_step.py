"""Single-step accepted-decision component behavior (scripted responder).

Covers the step6 reliable-commit slice at the component level: look-up-first
recovery with zero model calls, commit-before-accepted, explicit storage
failures that are never mistaken for "no decision", and proposal validation.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from hecate_runtime.accepted_decision import (
    AcceptedAction,
    AcceptedDecisionRecord,
    AcceptedDecisionStore,
    DecisionConflictError,
    DecisionOwnership,
    DecisionStoreUnavailableError,
    RunRef,
)
from hecate_runtime.decision_step import (
    DecisionProposal,
    DecisionResponder,
    DecisionStep,
    DecisionValidationError,
)


def _run_ref() -> RunRef:
    return RunRef(issuer_domain="test-domain", run_id=uuid.uuid4().hex)


def _record(run_ref: RunRef, step: str = "step-1") -> AcceptedDecisionRecord:
    return AcceptedDecisionRecord(
        run_ref=run_ref,
        decision_step_id=step,
        input_refs={"config": "v1"},
        accepted_output={"answer": "do-x"},
        actions=(
            AcceptedAction(
                action_key="a1", tool="biz_tool", arguments={"q": 1}, side_effect_class="non_idempotent_write"
            ),
        ),
    )


class InMemoryDecisionStore(AcceptedDecisionStore):
    """Local test double; the parameterized durable suite owns the real one."""

    def __init__(self, *, unavailable: bool = False, fail_commits: bool = False) -> None:
        self._records: dict[tuple[str, str, str], AcceptedDecisionRecord] = {}
        self._unavailable = unavailable
        self._fail_commits = fail_commits
        self.lookup_calls = 0

    async def lookup(self, run_ref: RunRef, decision_step_id: str) -> AcceptedDecisionRecord | None:
        self.lookup_calls += 1
        if self._unavailable:
            raise DecisionStoreUnavailableError("store down")
        return self._records.get((run_ref.issuer_domain, run_ref.run_id, decision_step_id))

    async def commit(
        self, record: AcceptedDecisionRecord, ownership: DecisionOwnership | None = None
    ) -> AcceptedDecisionRecord:
        if self._unavailable or self._fail_commits:
            raise DecisionStoreUnavailableError("store down")
        from hecate_runtime.accepted_decision import canonical_decision_digest

        digest = canonical_decision_digest(record)
        key = (record.run_ref.issuer_domain, record.run_ref.run_id, record.decision_step_id)
        existing = self._records.get(key)
        if existing is not None:
            if existing.content_digest != digest:
                raise DecisionConflictError(record.decision_step_id, existing.content_digest or "", digest)
            return existing
        stored = AcceptedDecisionRecord(
            run_ref=record.run_ref,
            decision_step_id=record.decision_step_id,
            input_refs=record.input_refs,
            accepted_output=record.accepted_output,
            actions=record.actions,
            accepted_at="2026-01-01T00:00:00+00:00",
            content_digest=digest,
        )
        self._records[key] = stored
        return stored


class ScriptedResponder(DecisionResponder):
    """Counts calls; returns one fixed proposal."""

    def __init__(self, proposal: DecisionProposal | None = None, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[RunRef, str, dict[str, Any]]] = []
        self._proposal = proposal or DecisionProposal(
            accepted_output={"answer": "do-x"},
            actions=(AcceptedAction(action_key="a1", tool="biz_tool", arguments={"q": 1}),),
        )
        self._error = error

    async def respond(self, run_ref: RunRef, decision_step_id: str, input_refs: dict[str, Any]) -> DecisionProposal:
        self.calls.append((run_ref, decision_step_id, dict(input_refs)))
        if self._error is not None:
            raise self._error
        return self._proposal


async def test_recovery_hit_returns_original_without_model_call() -> None:
    store = InMemoryDecisionStore()
    run_ref = _run_ref()
    first = await DecisionStep(store, ScriptedResponder()).execute(
        run_ref=run_ref, decision_step_id="step-1", input_refs={"config": "v1"}
    )
    assert first.resumed is False

    responder = ScriptedResponder()
    second = await DecisionStep(store, responder).execute(
        run_ref=run_ref, decision_step_id="step-1", input_refs={"config": "v1"}
    )
    assert second.resumed is True
    assert second.record == first.record
    assert responder.calls == []  # recovery never re-asks the model


async def test_miss_asks_responder_and_commits_before_accepted() -> None:
    store = InMemoryDecisionStore()
    responder = ScriptedResponder()
    outcome = await DecisionStep(store, responder).execute(run_ref=_run_ref(), decision_step_id="step-1")
    assert outcome.resumed is False
    assert outcome.record.accepted_at is not None  # stored fact, not in-memory passthrough
    assert outcome.record.actions[0].arguments == {"q": 1}


async def test_commit_failure_is_explicit_and_not_accepted() -> None:
    store = InMemoryDecisionStore(fail_commits=True)
    responder = ScriptedResponder()
    with pytest.raises(DecisionStoreUnavailableError):
        await DecisionStep(store, responder).execute(run_ref=_run_ref(), decision_step_id="step-1")
    assert len(responder.calls) == 1  # asked once; no retry loop, no accepted result


async def test_lookup_unavailable_is_not_treated_as_no_decision() -> None:
    store = InMemoryDecisionStore(unavailable=True)
    responder = ScriptedResponder()
    with pytest.raises(DecisionStoreUnavailableError):
        await DecisionStep(store, responder).execute(run_ref=_run_ref(), decision_step_id="step-1")
    assert responder.calls == []  # never re-asks the model on storage failure


async def test_store_double_rejects_conflicting_content() -> None:
    """Conflict is a store contract; the component never reaches it via lookup-first."""

    from hecate_runtime.accepted_decision import AcceptedDecisionRecord

    run_ref = _run_ref()
    store = InMemoryDecisionStore()
    await store.commit(_record(run_ref))
    conflicting = AcceptedDecisionRecord(
        run_ref=run_ref,
        decision_step_id="step-1",
        input_refs={"config": "v1"},
        accepted_output={"answer": "do-y"},
        actions=(AcceptedAction(action_key="a1", tool="biz_tool", arguments={"q": 1}),),
    )
    with pytest.raises(DecisionConflictError):
        await store.commit(conflicting)
    assert (await store.lookup(run_ref, "step-1")).accepted_output == {"answer": "do-x"}  # original intact


async def test_proposal_validation_rejects_before_commit() -> None:
    store = InMemoryDecisionStore()
    run_ref = _run_ref()

    empty = ScriptedResponder(DecisionProposal(accepted_output={}, actions=()))
    with pytest.raises(DecisionValidationError):
        await DecisionStep(store, empty).execute(run_ref=run_ref, decision_step_id="s")

    duplicate = ScriptedResponder(
        DecisionProposal(
            accepted_output={"a": 1},
            actions=(
                AcceptedAction(action_key="k", tool="t", arguments={}),
                AcceptedAction(action_key="k", tool="t", arguments={}),
            ),
        )
    )
    with pytest.raises(DecisionValidationError):
        await DecisionStep(store, duplicate).execute(run_ref=run_ref, decision_step_id="s")

    assert await store.lookup(run_ref, "s") is None  # nothing committed


async def test_recovery_with_changed_input_versions_conflicts_without_model_call() -> None:
    store = InMemoryDecisionStore()
    run = _run_ref()
    await DecisionStep(store, ScriptedResponder()).execute(
        run_ref=run, decision_step_id="step", input_refs={"config": "v1"}
    )
    responder = ScriptedResponder()
    with pytest.raises(DecisionConflictError):
        await DecisionStep(store, responder).execute(run_ref=run, decision_step_id="step", input_refs={"config": "v2"})
    assert responder.calls == []


async def test_responder_cannot_mutate_frozen_input_version_refs() -> None:
    class MutatingResponder(DecisionResponder):
        async def respond(self, run_ref, decision_step_id, input_refs):
            input_refs["config"]["version"] = "tampered"
            return DecisionProposal({"answer": "ok"}, (AcceptedAction("key", "tool", {"q": 1}),))

    refs = {"config": {"version": "v1"}}
    outcome = await DecisionStep(InMemoryDecisionStore(), MutatingResponder()).execute(
        run_ref=_run_ref(), decision_step_id="step", input_refs=refs
    )
    assert outcome.record.input_refs == refs == {"config": {"version": "v1"}}


async def test_corrupt_lookup_never_asks_model() -> None:
    store = InMemoryDecisionStore()
    run = _run_ref()
    stored = await store.commit(_record(run))
    stored.accepted_output["answer"] = "changed"
    responder = ScriptedResponder()
    with pytest.raises(DecisionStoreUnavailableError):
        await DecisionStep(store, responder).execute(
            run_ref=run, decision_step_id="step-1", input_refs={"config": "v1"}
        )
    assert responder.calls == []


async def test_invalid_record_version_and_json_fail_before_acceptance() -> None:
    from dataclasses import replace

    from hecate_runtime.accepted_decision import snapshot_decision

    record = _record(_run_ref())
    assert AcceptedDecisionRecord.from_dict(record.to_dict()) == record
    for invalid in (replace(record, schema_version=2), replace(record, accepted_output={"n": float("nan")})):
        with pytest.raises(DecisionValidationError):
            snapshot_decision(invalid)


def test_new_runtime_seams_require_implementations() -> None:
    import inspect

    assert inspect.isabstract(AcceptedDecisionStore)
    assert inspect.isabstract(DecisionResponder)
