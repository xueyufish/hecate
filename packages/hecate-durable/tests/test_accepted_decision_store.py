"""Shared acceptance contract: SQL and memory use real LeaseManager authority."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest
from _decision_helpers import decision_database, prepare_decision_run
from hecate_durable.runtime_hook import InMemoryAcceptedDecisionStore, SqlAcceptedDecisionStore
from hecate_durable.storage.models import AcceptedDecisionRow, EventRow
from hecate_runtime.accepted_decision import (
    AcceptedAction,
    AcceptedDecisionRecord,
    DecisionConflictError,
    DecisionStoreUnavailableError,
    DecisionValidationError,
    RunRef,
    StaleDecisionFenceError,
    canonical_decision_digest,
    canonical_json,
)
from sqlalchemy import select, update

POSTGRES_URL = os.environ.get("DURABLE_TEST_POSTGRES_URL", "")


@pytest.fixture(params=["sql", "inmemory"] + (["postgres"] if POSTGRES_URL else []))
def case(request, tmp_path):
    with decision_database(tmp_path, POSTGRES_URL if request.param == "postgres" else "") as (backend, clock, url):
        sql = SqlAcceptedDecisionStore(backend)
        store = (
            InMemoryAcceptedDecisionStore(validate_ownership=sql.validate_ownership)
            if request.param == "inmemory"
            else sql
        )
        run_ref = RunRef("domain", "run")
        owner = prepare_decision_run(backend, run_ref)
        yield store, backend, clock, run_ref, owner, url


def _record(run_ref: RunRef, step: str = "step", answer: str = "do-x") -> AcceptedDecisionRecord:
    return AcceptedDecisionRecord(
        run_ref,
        step,
        {"config": "v1"},
        {"answer": answer},
        (AcceptedAction("action-1", "biz_tool", {"nested": {"q": 1}}, "non_idempotent_write"),),
    )


def _accepted_events(backend) -> list:
    with backend.session_factory() as session:
        return [
            row.envelope
            for row in session.scalars(select(EventRow))
            if row.envelope["payload"]["event_type"] == "agent_decision_accepted"
        ]


async def test_roundtrip_and_idempotency_emit_one_event(case):
    store, backend, _, run, owner, _ = case
    first = await store.commit(_record(run), owner)
    again = await store.commit(_record(run), owner)
    assert first == again == await store.lookup(run, "step")
    assert first.content_digest == canonical_decision_digest(first)
    assert first.accepted_at and first.schema_version == 1
    assert await store.lookup(run, "absent") is None
    if isinstance(store, SqlAcceptedDecisionStore):
        events = _accepted_events(backend)
        assert len(events) == 1
        assert events[0]["run_ref"]["id"] == run.run_id
        assert events[0]["task_ref"]["id"] == "task-" + run.run_id
        assert events[0]["payload"]["action_keys"] == ["action-1"]
        assert "arguments" not in events[0]["payload"]


@pytest.mark.parametrize("field", ["output", "arguments", "refs"])
async def test_conflicting_content_does_not_replace_original(case, field):
    store, _, _, run, owner, _ = case
    original = await store.commit(_record(run), owner)
    candidate = _record(run)
    if field == "output":
        candidate = replace(candidate, accepted_output={"answer": "other"})
    elif field == "arguments":
        candidate = replace(candidate, actions=(replace(candidate.actions[0], arguments={"q": 2}),))
    else:
        candidate = replace(candidate, input_refs={"config": "v2"})
    with pytest.raises(DecisionConflictError):
        await store.commit(candidate, owner)
    assert await store.lookup(run, "step") == original


@pytest.mark.parametrize("fault", ["missing", "unknown", "holder", "higher", "lower", "scope"])
async def test_invalid_real_ownership_rejected_before_first_write(case, fault):
    store, backend, _, run, owner, _ = case
    forged = owner
    if fault == "missing":
        forged = None
    elif fault == "unknown":
        forged = replace(owner, lease_key="invented")
    elif fault == "holder":
        forged = replace(owner, holder="someone-else")
    elif fault == "higher":
        forged = replace(owner, fence_token=owner.fence_token + 50)
    elif fault == "lower":
        forged = replace(owner, fence_token=0)
    else:
        forged = prepare_decision_run(backend, RunRef("other", run.run_id), "other-worker")
    with pytest.raises(StaleDecisionFenceError):
        await store.commit(_record(run), forged)
    assert await store.lookup(run, "step") is None


async def test_expiry_and_takeover_fence_new_steps_and_idempotent_commits(case):
    store, backend, clock, run, owner, _ = case
    first = await store.commit(_record(run), owner)
    clock.advance()
    for record in (_record(run), _record(run, step="new")):
        with pytest.raises(StaleDecisionFenceError):
            await store.commit(record, owner)
    new_owner = prepare_decision_run(backend, run, "new-worker")
    for record in (_record(run), _record(run, step="new")):
        with pytest.raises(StaleDecisionFenceError):
            await store.commit(record, owner)
    assert await store.commit(_record(run), new_owner) == first
    assert (await store.commit(_record(run, step="new"), new_owner)).decision_step_id == "new"


async def test_nested_mutation_cannot_rewrite_accepted_fact(case):
    store, _, _, run, owner, _ = case
    request = _record(run)
    accepted = await store.commit(request, owner)
    request.actions[0].arguments["nested"]["q"] = 999
    accepted.actions[0].arguments["nested"]["q"] = 888
    accepted.accepted_output["answer"] = "tampered"
    accepted.input_refs["config"] = "tampered"
    found = await store.lookup(run, "step")
    assert found.actions[0].arguments == {"nested": {"q": 1}}
    assert found.accepted_output == {"answer": "do-x"}
    assert found.input_refs == {"config": "v1"}
    assert canonical_decision_digest(found) == found.content_digest
    exported = found.to_dict()
    exported["actions"][0]["arguments"]["nested"]["q"] = 777
    assert found.actions[0].arguments["nested"]["q"] == 1


@pytest.mark.parametrize("fault", ["nan", "key", "arguments", "classification", "blank", "duplicate", "version"])
async def test_invalid_payload_never_commits(case, fault):
    store, _, _, run, owner, _ = case
    record = _record(run)
    if fault == "nan":
        record = replace(record, accepted_output={"n": float("nan")})
    elif fault == "key":
        with pytest.raises(DecisionValidationError):
            canonical_json({1: "lossy"})
        assert await store.lookup(run, "step") is None
        return
    elif fault == "arguments":
        record = replace(record, actions=(replace(record.actions[0], arguments=[]),))
    elif fault == "classification":
        record = replace(record, actions=(replace(record.actions[0], side_effect_class="write"),))
    elif fault == "blank":
        record = replace(record, actions=(replace(record.actions[0], tool=""),))
    elif fault == "duplicate":
        record = replace(record, actions=record.actions * 2)
    else:
        record = replace(record, schema_version=99)
    with pytest.raises(DecisionValidationError):
        await store.commit(record, owner)
    assert await store.lookup(run, "step") is None


async def test_same_run_id_in_another_issuer_is_isolated(case):
    store, backend, _, run, owner, _ = case
    other = RunRef("other-domain", run.run_id)
    other_owner = prepare_decision_run(backend, other)
    first = await store.commit(_record(run), owner)
    second = await store.commit(_record(other, answer="other"), other_owner)
    assert first.run_ref != second.run_ref
    assert (await store.lookup(run, "step")).accepted_output == {"answer": "do-x"}
    assert (await store.lookup(other, "step")).accepted_output == {"answer": "other"}


async def test_outbox_failure_rolls_back_acceptance(case, monkeypatch):
    store, backend, _, run, owner, _ = case
    if not isinstance(store, SqlAcceptedDecisionStore):
        store._fail_commits = True
    else:

        def fail_after_event(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("outbox failure after append")

        original = backend.events.emit
        monkeypatch.setattr(backend.events, "emit", fail_after_event)
    with pytest.raises(DecisionStoreUnavailableError):
        await store.commit(_record(run), owner)
    assert await store.lookup(run, "step") is None
    assert _accepted_events(backend) == []


async def test_corrupt_storage_fails_closed(case):
    store, backend, _, run, owner, _ = case
    await store.commit(_record(run), owner)
    if not isinstance(store, SqlAcceptedDecisionStore):
        store._unavailable = True
    else:
        with backend.session_factory() as session, session.begin():
            session.execute(update(AcceptedDecisionRow).values(accepted_output={"corrupt": True}))
    with pytest.raises(DecisionStoreUnavailableError):
        await store.lookup(run, "step")


async def test_storage_failure_never_becomes_a_miss(case, monkeypatch):
    store, backend, _, run, owner, _ = case
    if not isinstance(store, SqlAcceptedDecisionStore):
        store._unavailable = True
    else:

        def unavailable():
            raise OSError("database unavailable")

        monkeypatch.setattr(backend, "_session_factory", unavailable)
    with pytest.raises(DecisionStoreUnavailableError):
        await store.lookup(run, "step")
    with pytest.raises(DecisionStoreUnavailableError):
        await store.commit(_record(run), owner)
