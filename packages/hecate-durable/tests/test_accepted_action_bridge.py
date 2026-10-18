"""Accepted-action dispatch against real SQL decisions, leases and action ledger."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from _decision_helpers import decision_database, prepare_decision_run
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore, SqlActionLedgerHook
from hecate_durable.storage.lease import LeaseHandle, task_dispatch_lease_key
from hecate_runtime.accepted_action_step import AcceptedActionStep, execution_id_for_action
from hecate_runtime.accepted_decision import (
    AcceptedAction,
    AcceptedDecisionRecord,
    DecisionValidationError,
    RunRef,
    StaleDecisionFenceError,
)
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_access import AccessDecision, ApprovalDecision
from hecate_runtime.tool_call_executor import ToolCallExecutor

RUN = RunRef("domain", "run-1")
SQL_RUN = BackendRef(RefKind.RUN, RUN.issuer_domain, RUN.run_id)
SQL_TASK = BackendRef(RefKind.TASK, "domain", "task-run-1")
URLS = [""] + ([os.environ["DURABLE_TEST_POSTGRES_URL"]] if os.environ.get("DURABLE_TEST_POSTGRES_URL") else [])


class StubBusinessPort(StubRuntimePort):
    """Count business calls without contacting external systems."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args))
        return {"business": args["q"]}


@pytest.fixture(params=URLS, ids=["sqlite"] + (["postgres"] if len(URLS) > 1 else []))
def database(request, tmp_path):
    """Own a fresh SQLite file or isolated PostgreSQL schema per case."""
    with decision_database(tmp_path, request.param) as values:
        yield values


@pytest.fixture
async def accepted(database):
    """Persist two original actions under an authoritative Task dispatch lease."""
    backend, clock, url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    await store.commit(
        AcceptedDecisionRecord(
            RUN,
            "step-1",
            {"config": "v1"},
            {"text": "accepted"},
            (AcceptedAction("action-1", "memory_add", {"q": 7}), AcceptedAction("read-1", "web_search", {"q": 8})),
        ),
        owner,
    )
    return backend, clock, url, store, owner


def _executor(backend, owner, port, *, run=SQL_RUN, task=SQL_TASK, **kwargs):
    hook = SqlActionLedgerHook(
        backend,
        task_ref=task,
        run_ref=run,
        holder=owner.holder,
        lease=LeaseHandle(owner.lease_key, owner.holder, owner.fence_token, ""),
    )
    return ToolCallExecutor(port=port, action_hook=hook, **kwargs)


async def test_real_ledger_reuses_completed_result_without_reexecuting(accepted) -> None:
    backend, _, _, store, owner = accepted
    port = StubBusinessPort()
    first = await AcceptedActionStep(store, _executor(backend, owner, port)).dispatch(
        run_ref=RUN, decision_step_id="step-1", action_key="action-1", ownership=owner
    )
    before = backend.list_run_actions(SQL_RUN)
    fresh = StubBusinessPort()
    second = await AcceptedActionStep(SqlAcceptedDecisionStore(backend), _executor(backend, owner, fresh)).dispatch(
        run_ref=RUN, decision_step_id="step-1", action_key="action-1", ownership=owner
    )
    assert first.status == second.status == "succeeded"
    assert first.execution_id == second.execution_id
    assert second.result is not None
    assert json.loads(second.result["content"]) == {"business": 7}
    assert port.calls == [("memory_add", {"q": 7})]
    assert fresh.calls == []
    assert backend.list_run_actions(SQL_RUN) == before
    assert len(before) == 1  # Other accepted actions are still undispatched.


@pytest.mark.parametrize(
    "kind", ["missing", "holder", "token", "expired", "takeover", "other_run", "wrong_hook_run", "wrong_hook_task"]
)
async def test_bad_owner_or_hook_binding_cannot_create_an_intent(accepted, kind) -> None:
    backend, clock, _, store, owner = accepted
    presented = owner
    run, task = SQL_RUN, SQL_TASK
    if kind == "missing":
        presented = None
    elif kind == "holder":
        presented = replace(owner, holder="forged")
    elif kind == "token":
        presented = replace(owner, fence_token=owner.fence_token + 100)
    elif kind in ("expired", "takeover"):
        clock.advance(120)
        if kind == "takeover":
            assert backend.leases.acquire(task_dispatch_lease_key("domain", "task-run-1"), "new-worker", 60)
    elif kind == "other_run":
        presented = prepare_decision_run(backend, RunRef("other", "run-2"))
    elif kind == "wrong_hook_run":
        run = BackendRef(RefKind.RUN, "domain", "wrong")
    elif kind == "wrong_hook_task":
        task = BackendRef(RefKind.TASK, "domain", "wrong")
    port = StubBusinessPort()
    with pytest.raises((DecisionValidationError, StaleDecisionFenceError)):
        await AcceptedActionStep(store, _executor(backend, owner, port, run=run, task=task)).dispatch(
            run_ref=RUN, decision_step_id="step-1", action_key="action-1", ownership=presented
        )
    assert port.calls == []
    assert backend.list_run_actions(SQL_RUN) == []


async def test_readonly_cannot_execute_after_lease_expires_while_awaiting_approval(accepted) -> None:
    backend, clock, _, store, owner = accepted

    class StubPolicy:
        def evaluate(self, *args, **kwargs):
            return AccessDecision.REQUIRE_APPROVAL

    class StubApproval:
        async def request_approval(self, **kwargs):
            clock.advance(120)
            return ApprovalDecision(approved=True, reason="approved", approval_ref="a1")

    port = StubBusinessPort()
    executor = _executor(backend, owner, port, access_policy=StubPolicy(), approval_callback=StubApproval())
    with pytest.raises(StaleDecisionFenceError):
        await AcceptedActionStep(store, executor).dispatch(
            run_ref=RUN, decision_step_id="step-1", action_key="read-1", ownership=owner
        )
    assert port.calls == []
    assert backend.list_run_actions(SQL_RUN) == []


async def test_outcome_storage_failure_stops_and_recovery_does_not_repeat_effect(accepted, monkeypatch) -> None:
    backend, _, _, store, owner = accepted
    port = StubBusinessPort()

    def fail(*args, **kwargs):
        raise RuntimeError("receipt write failed")

    with monkeypatch.context() as patch:
        patch.setattr(backend, "record_outcome_ex", fail)
        first = await AcceptedActionStep(store, _executor(backend, owner, port)).dispatch(
            run_ref=RUN, decision_step_id="step-1", action_key="action-1", ownership=owner
        )
    second = await AcceptedActionStep(store, _executor(backend, owner, port)).dispatch(
        run_ref=RUN, decision_step_id="step-1", action_key="action-1", ownership=owner
    )
    assert first.status == second.status == "stopped"
    assert first.stop_reason == "reconciliation_required"
    assert second.stop_reason == "needs_review"
    assert len(port.calls) == 1


async def test_concurrent_sql_hooks_grant_only_one_first_execution(accepted) -> None:
    backend, _, _, store, owner = accepted
    started, release = asyncio.Event(), asyncio.Event()

    class StubBlockingPort(StubBusinessPort):
        async def tool_execute(self, name, args, context=None):
            result = await super().tool_execute(name, args, context)
            started.set()
            await release.wait()
            return result

    port = StubBlockingPort()
    first = asyncio.create_task(
        AcceptedActionStep(store, _executor(backend, owner, port)).dispatch(
            run_ref=RUN, decision_step_id="step-1", action_key="action-1", ownership=owner
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 5)
        loser = await AcceptedActionStep(store, _executor(backend, owner, port)).dispatch(
            run_ref=RUN, decision_step_id="step-1", action_key="action-1", ownership=owner
        )
        assert loser.status == "stopped"
        assert len(port.calls) == 1
    finally:
        release.set()
        winner = await first
    assert winner.status == "succeeded"


def test_two_independent_processes_recover_original_action(accepted, tmp_path) -> None:
    backend, _, url, _, owner = accepted
    config = json.dumps({"url": url, "ownership": asdict(owner)})
    probe = Path(__file__).with_name("_accepted_action_process.py")
    effect = tmp_path / "business-effects.txt"
    values = []
    for mode in ("write", "read"):
        process = subprocess.run(  # noqa: S603 — fixed interpreter and local test helper; no shell
            [sys.executable, "-I", str(probe), config, mode, str(effect)], capture_output=True, text=True, timeout=25
        )
        assert process.returncode == 0, process.stderr
        values.append(json.loads(process.stdout))
    assert values[0]["execution_id"] == values[1]["execution_id"] == execution_id_for_action(RUN, "step-1", "action-1")
    assert json.loads(values[1]["content"]) == {"business": 7}
    assert effect.read_text(encoding="utf-8").splitlines() == ["effect"]
    assert len(backend.list_run_actions(SQL_RUN)) == 1
