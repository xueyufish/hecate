"""Accepted-decision batch recovery against real SQL decisions, leases and ledger.

Covers the step6 batch slice over the durable stack: recovery replays the
completed prefix from the ledger and continues, unknown outcomes stop the
batch without redoing side effects, lease expiry/takeover rejects the next
action, and concurrent batches admit exactly one first executor per write
action. The cross-process case uses truly independent interpreter
processes. SQLite always; PostgreSQL when ``DURABLE_TEST_POSTGRES_URL`` is
set.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from ast import literal_eval
from dataclasses import asdict
from pathlib import Path
from time import monotonic

import pytest
from _decision_helpers import decision_database, prepare_decision_run
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore, SqlActionLedgerHook
from hecate_durable.storage.lease import LeaseHandle, task_dispatch_lease_key
from hecate_runtime.accepted_action_batch import AcceptedActionBatch
from hecate_runtime.accepted_action_step import AcceptedActionStep, execution_id_for_action
from hecate_runtime.accepted_decision import AcceptedAction, AcceptedDecisionRecord, DecisionOwnership, RunRef
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_call_executor import ToolCallExecutor

RUN = RunRef("domain", "run-1")
SQL_RUN = BackendRef(RefKind.RUN, RUN.issuer_domain, RUN.run_id)
SQL_TASK = BackendRef(RefKind.TASK, "domain", "task-run-1")
URLS = [""] + ([os.environ["DURABLE_TEST_POSTGRES_URL"]] if os.environ.get("DURABLE_TEST_POSTGRES_URL") else [])


class StubCountingPort(StubRuntimePort):
    """Record business dispatches per action; no external systems."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append(args["action_key"])
        return {"business": args["action_key"]}


@pytest.fixture(params=URLS, ids=["sqlite"] + (["postgres"] if len(URLS) > 1 else []))
def database(request, tmp_path):
    """Own a fresh SQLite file or isolated PostgreSQL schema per case."""
    with decision_database(tmp_path, request.param) as values:
        yield values


async def _commit(store, owner: DecisionOwnership) -> None:
    await store.commit(
        AcceptedDecisionRecord(
            RUN,
            "step-1",
            {"config": "v1"},
            {"text": "accepted"},
            (
                AcceptedAction("action-1", "memory_add", {"action_key": "action-1"}),
                AcceptedAction("action-2", "memory_add", {"action_key": "action-2"}),
                AcceptedAction("action-3", "web_search", {"action_key": "action-3"}),
            ),
        ),
        owner,
    )


def _executor(backend, owner, port):
    hook = SqlActionLedgerHook(
        backend,
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        holder=owner.holder,
        lease=LeaseHandle(owner.lease_key, owner.holder, owner.fence_token, ""),
    )
    return ToolCallExecutor(port=port, action_hook=hook)


async def test_recovery_replays_completed_prefix_and_continues(database) -> None:
    """Crash after A1: a fresh batch replays A1's result and executes A2/A3."""
    backend, _clock, _url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    await _commit(store, owner)
    port = StubCountingPort()
    step = AcceptedActionStep(store, _executor(backend, owner, port))

    # Simulated crash after A1: only the first action was dispatched.
    await step.dispatch(run_ref=RUN, decision_step_id="step-1", action_key="action-1", ownership=owner)
    assert port.calls == ["action-1"]
    before = backend.list_run_actions(SQL_RUN)

    fresh = StubCountingPort()
    report = await AcceptedActionBatch(AcceptedActionStep(store, _executor(backend, owner, fresh)), store).run(
        run_ref=RUN, decision_step_id="step-1", ownership=owner
    )
    assert [outcome.status for outcome in report.outcomes] == ["succeeded"] * 3
    assert report.unprocessed_action_keys == ()
    assert fresh.calls == ["action-2", "action-3"]  # A1 replayed from the ledger, zero new effect
    assert len(backend.list_run_actions(SQL_RUN)) == len(before) + 2


async def test_unknown_outcome_stops_without_redo(database, monkeypatch) -> None:
    """A2's outcome receipt cannot persist: stop; recovery never redoes A2."""
    backend, _clock, _url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    await _commit(store, owner)
    port = StubCountingPort()
    batch = AcceptedActionBatch(AcceptedActionStep(store, _executor(backend, owner, port)), store)

    # First pass: A1's receipt persists, A2's is lost (deterministic call
    # counter — the second record_outcome_ex in this pass is A2's).
    calls = {"n": 0}
    original = backend.record_outcome_ex

    def fail_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("receipt lost")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(backend, "record_outcome_ex", fail_second)
        report = await batch.run(run_ref=RUN, decision_step_id="step-1", ownership=owner)
    assert [outcome.status for outcome in report.outcomes] == ["succeeded", "stopped"]
    assert report.outcomes[-1].stop_reason == "reconciliation_required"
    assert report.unprocessed_action_keys == ("action-3",)
    # The side effect happened; only its receipt was lost — reconciliation,
    # not a redo.
    assert port.calls == ["action-1", "action-2"]

    # Recovery: A1 replays; A2 is claimed without a receipt → needs_review,
    # never redone; A3 stays undispatched behind the unresolved A2.
    recovery = StubCountingPort()
    report = await AcceptedActionBatch(AcceptedActionStep(store, _executor(backend, owner, recovery)), store).run(
        run_ref=RUN, decision_step_id="step-1", ownership=owner
    )
    assert [outcome.status for outcome in report.outcomes] == ["succeeded", "stopped"]
    assert report.outcomes[-1].stop_reason == "needs_review"
    assert recovery.calls == []  # A2 not redone; nothing else dispatched either


async def test_lease_expiry_after_first_action_rejects_next(database) -> None:
    """A1 succeeds; the lease then expires: A2 converts to stopped, A3 untouched."""
    backend, clock, _url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    await _commit(store, owner)
    a2_execution_id = execution_id_for_action(RUN, "step-1", "action-2")

    class StubExpiringHook(SqlActionLedgerHook):
        """Expire the lease exactly when A2's ownership is validated."""

        async def validate_accepted_dispatch(self, **kwargs):
            if kwargs["execution_id"] == a2_execution_id:
                clock.advance(120)
            await super().validate_accepted_dispatch(**kwargs)

    hook = StubExpiringHook(
        backend,
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        holder=owner.holder,
        lease=LeaseHandle(owner.lease_key, owner.holder, owner.fence_token, ""),
    )
    port = StubCountingPort()
    report = await AcceptedActionBatch(
        AcceptedActionStep(store, ToolCallExecutor(port=port, action_hook=hook)), store
    ).run(run_ref=RUN, decision_step_id="step-1", ownership=owner)
    assert [outcome.status for outcome in report.outcomes] == ["succeeded", "stopped"]
    assert report.outcomes[-1].stop_reason == "stale_ownership"
    assert report.unprocessed_action_keys == ("action-3",)
    assert port.calls == ["action-1"]


async def test_takeover_after_first_action_rejects_next(database) -> None:
    """A1 succeeds; another worker takes over: A2 is fencing-rejected."""
    backend, _clock, _url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    await _commit(store, owner)
    a2_execution_id = execution_id_for_action(RUN, "step-1", "action-2")

    class StubTakeoverHook(SqlActionLedgerHook):
        """Expire then take over the lease exactly when A2's ownership is validated."""

        async def validate_accepted_dispatch(self, **kwargs):
            if kwargs["execution_id"] == a2_execution_id:
                _clock.advance(120)  # the original lease must expire before takeover
                assert backend.leases.acquire(task_dispatch_lease_key("domain", "task-run-1"), "new-worker", 60)
            await super().validate_accepted_dispatch(**kwargs)

    hook = StubTakeoverHook(
        backend,
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        holder=owner.holder,
        lease=LeaseHandle(owner.lease_key, owner.holder, owner.fence_token, ""),
    )
    port = StubCountingPort()
    report = await AcceptedActionBatch(
        AcceptedActionStep(store, ToolCallExecutor(port=port, action_hook=hook)), store
    ).run(run_ref=RUN, decision_step_id="step-1", ownership=owner)
    assert [outcome.status for outcome in report.outcomes] == ["succeeded", "stopped"]
    assert report.unprocessed_action_keys == ("action-3",)
    assert port.calls == ["action-1"]


async def test_concurrent_batches_single_first_executor(database) -> None:
    """Two concurrent batches over the same write action: one first executor."""
    backend, _clock, _url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    await _commit(store, owner)
    started, release = asyncio.Event(), asyncio.Event()

    class StubBlockingPort(StubCountingPort):
        async def tool_execute(self, name, args, context=None):
            result = await super().tool_execute(name, args, context)
            if args["action_key"] == "action-1" and not started.is_set():
                started.set()
                await release.wait()
            return result

    port = StubBlockingPort()
    batch = AcceptedActionBatch(AcceptedActionStep(store, _executor(backend, owner, port)), store)
    first = asyncio.create_task(batch.run(run_ref=RUN, decision_step_id="step-1", ownership=owner))
    try:
        await asyncio.wait_for(started.wait(), 5)
        loser = await batch.run(run_ref=RUN, decision_step_id="step-1", ownership=owner)
        assert loser.outcomes[0].status in {"stopped", "succeeded"}  # replay or stop, never a second effect
        assert port.calls.count("action-1") == 1
    finally:
        release.set()
        winner = await first
    assert winner.outcomes[0].status == "succeeded"
    assert port.calls.count("action-1") == 1
    assert port.calls.count("action-2") == 1
    assert port.calls.count("action-3") == 1


async def test_cross_process_crash_then_recover(database, tmp_path) -> None:
    """Independent interpreter processes: crash after A1, then recover A2/A3."""
    backend, _clock, url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    await _commit(store, owner)
    probe = Path(__file__).with_name("_accepted_action_batch_process.py")
    effect = tmp_path / "batch-effects.txt"
    config = json.dumps({"url": url, "ownership": asdict(owner)})

    crashed = subprocess.run(  # noqa: S603 — fixed interpreter and local test helper; no shell
        [sys.executable, "-I", str(probe), config, "write-crash", str(effect)],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert crashed.returncode == 9, crashed.stderr  # hard crash after action-1
    assert effect.read_text(encoding="utf-8").splitlines() == ["effect-action-1"]
    assert len(backend.list_run_actions(SQL_RUN)) == 1

    recovered = subprocess.run(  # noqa: S603 — fixed interpreter and local test helper; no shell
        [sys.executable, "-I", str(probe), config, "continue", str(effect)],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout)["statuses"] == ["succeeded"] * 3
    assert effect.read_text(encoding="utf-8").splitlines() == [
        "effect-action-1",
        "effect-action-2",
        "effect-action-3",
    ]
    assert len(backend.list_run_actions(SQL_RUN)) == 3


async def test_two_processes_compete_before_first_receipt_commits(database, tmp_path) -> None:
    """Second process observes a claimed write; only the first produces effects."""
    backend, _clock, url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    await _commit(store, owner)
    probe = Path(__file__).with_name("_accepted_action_batch_process.py")
    effect = tmp_path / "race-effects.txt"
    ready = Path(str(effect) + ".ready")
    release = Path(str(effect) + ".release")
    config = json.dumps({"url": url, "ownership": asdict(owner)})
    args = [sys.executable, "-I", str(probe), config]

    with subprocess.Popen(  # noqa: S603 — fixed interpreter and local helper, no shell
        [*args, "race-first", str(effect)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as first:
        try:
            deadline = monotonic() + 20
            while not ready.exists():
                assert first.poll() is None, first.communicate()
                assert monotonic() < deadline, "first process did not claim A1"
                await asyncio.sleep(0.02)
            second = await asyncio.to_thread(
                subprocess.run, [*args, "race-second", str(effect)], capture_output=True, text=True, timeout=25
            )
            assert second.returncode == 0, second.stderr
            losing = json.loads(second.stdout)
            assert losing["statuses"] == ["stopped"]
            assert losing["outcomes"][0]["stop_reason"] == "needs_review"
            assert losing["unprocessed_action_keys"] == ["action-2", "action-3"]
            assert effect.read_text(encoding="utf-8").splitlines() == ["effect-action-1"]
            assert len(backend.list_run_actions(SQL_RUN)) == 1
            release.write_text("continue", encoding="utf-8")
            stdout, stderr = await asyncio.to_thread(first.communicate, timeout=25)
            assert first.returncode == 0, stderr
            winning = json.loads(stdout)
            assert winning["statuses"] == ["succeeded"] * 3
        finally:
            release.write_text("continue", encoding="utf-8")
            if first.poll() is None:
                first.kill()
                first.communicate(timeout=5)

    # A fresh interpreter must recover all three exact results without any tool call.
    recovered = await asyncio.to_thread(
        subprocess.run, [*args, "recover-all", str(effect)], capture_output=True, text=True, timeout=25
    )
    assert recovered.returncode == 0, recovered.stderr
    replay = json.loads(recovered.stdout)
    for original, restored in zip(winning["outcomes"], replay["outcomes"], strict=True):
        assert restored["status"] == original["status"] == "succeeded"
        assert restored["execution_id"] == original["execution_id"]
        assert restored["stop_reason"] == original["stop_reason"] is None
        # The existing executor stringifies dict results on first dispatch;
        # the SQL ledger recovers their JSON serialization. Compare payloads.
        assert literal_eval(original["result"]["content"]) == json.loads(restored["result"]["content"])
        assert restored["result"]["tool_call_id"] == original["result"]["tool_call_id"]
    assert replay["outcomes"][0]["execution_id"] == losing["outcomes"][0]["execution_id"]
    assert effect.read_text(encoding="utf-8").splitlines() == [
        "effect-action-1",
        "effect-action-2",
        "effect-action-3",
    ]
    assert len(backend.list_run_actions(SQL_RUN)) == 3


async def test_new_owner_recovers_completed_prefix_after_takeover(database) -> None:
    """A genuinely new holder/token resumes recorded A1 without redoing it."""
    from hecate_runtime.accepted_decision import StaleDecisionFenceError

    backend, clock, _url = database
    store = SqlAcceptedDecisionStore(backend)
    old = prepare_decision_run(backend, RUN)
    await _commit(store, old)
    port = StubCountingPort()
    await AcceptedActionStep(store, _executor(backend, old, port)).dispatch(
        run_ref=RUN, decision_step_id="step-1", action_key="action-1", ownership=old
    )
    clock.advance(120)
    lease = backend.leases.acquire(task_dispatch_lease_key("domain", "task-run-1"), "new-worker", 60)
    assert lease is not None
    owner = DecisionOwnership(lease.lease_key, lease.fencing_token, lease.holder)
    with pytest.raises(StaleDecisionFenceError):
        await AcceptedActionStep(store, _executor(backend, old, port)).dispatch(
            run_ref=RUN, decision_step_id="step-1", action_key="action-2", ownership=old
        )
    fresh = StubCountingPort()
    report = await AcceptedActionBatch(AcceptedActionStep(store, _executor(backend, owner, fresh)), store).run(
        run_ref=RUN, decision_step_id="step-1", ownership=owner
    )
    assert [o.status for o in report.outcomes] == ["succeeded"] * 3
    first_result = report.outcomes[0].result
    assert first_result is not None
    assert json.loads(first_result["content"]) == {"business": "action-1"}
    assert fresh.calls == ["action-2", "action-3"]
    assert len(backend.list_run_actions(SQL_RUN)) == 3
