"""Persistent timed waiting and expiry wake for platform controller tasks.

A real opt-in timer park (readonly polling tool declares the versioned
envelope, dispatcher parks with a receipt-anchored absolute deadline) is
driven through the waker the durable worker runs on its reconcile cycle:
the expired wait is consumed exactly once, the task requeues on the SAME
run, and the accepted-decision chain replays the parked position with zero
re-execution of the declaring action. Recovery re-derives the deadline
from the durable anchor — a crash between the receipt and the park commit
re-parks with the same deadline, and a crash between the wake and the
dispatch is healed by the worker's own reconcile. Races with cancels,
manual commands and repeated scans share the revision CAS and the
commit-time deadline re-verification; corrupt waits, unreadable contracts
and unverifiable anchors converge to explicit reconciliation instead of a
guessed wake. Model text, undeclared (non-readonly) tools and exhausted
wait bounds can never park a timer wait.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from hecate.execution.task_control import TaskControlService
from hecate.execution.task_dispatcher import PlatformTaskDispatcher
from tests.test_execution.test_controller_loop_dispatch import (  # noqa: F401 - fixture registration
    WS,
    ScriptedLoopPort,
    _dispatch_to_state,
    _dispatch_to_terminal,
    _issue_input_wake,
    _submit_without_dispatch,
    _worker_for,
    harness,
)

DECLARATION = {"version": 1, "wake_in_seconds": 30, "reason": "job pending"}


@pytest.fixture
def readonly_poll_tool():
    """Confirm the polling tool readonly for the host check, test-scoped."""

    from hecate_runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS, SideEffectClass

    _BUILTIN_CLASSIFICATIONS["query_inventory"] = SideEffectClass.READONLY
    yield "query_inventory"
    _BUILTIN_CLASSIFICATIONS.pop("query_inventory", None)


@pytest.fixture
def write_poll_tool():
    """Force the polling tool to a write class: the envelope must stay inert."""

    from hecate_runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS, SideEffectClass

    _BUILTIN_CLASSIFICATIONS["query_inventory"] = SideEffectClass.NON_IDEMPOTENT_WRITE
    yield "query_inventory"
    _BUILTIN_CLASSIFICATIONS.pop("query_inventory", None)


def _poll_call() -> dict:
    return {
        "id": "call-1",
        "type": "function",
        "function": {"name": "query_inventory", "arguments": json.dumps({"job": "J-1"})},
    }


class _TimerPollPort(ScriptedLoopPort):
    """Polling tool that declares the versioned timer wait until data flows.

    First poll of a run (no tool result yet) declares the wait; after the
    wake the model sees the declaration as history and polls again; that
    poll returns the real data and the loop finalizes. Counting is
    per-session (one run per task) so a shared harness serving several
    tasks never mixes their transcripts. ``declare_forever`` scripts a
    tool that never stops declaring (the bound test).
    """

    def __init__(self, *, declare_forever: bool = False) -> None:
        super().__init__(final_text="unused")
        self.declare_forever = declare_forever
        self.business_calls: list[dict] = []
        self._session_calls: dict[str, list[dict]] = {}

    async def tool_execute(self, name, args, context=None):
        self.business_calls.append(dict(args))
        calls = self._session_calls.setdefault(str((context or {}).get("session_id")), [])
        calls.append(dict(args))
        if self.declare_forever or len(calls) == 1:
            return {"status": "timer_wait_required", "wait": dict(DECLARATION)}
        return {"status": "succeeded", "result": "job-done"}

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        self.model_calls += 1
        self.messages = messages
        tool_messages = [m for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
        if any("job-done" in str(m.get("content")) for m in tool_messages):
            yield {"content": "job finished", "tool_calls": None}
        elif self.declare_forever and len(tool_messages) >= 2:
            # Two stale declarations observed: finalize without polling a
            # third time (the bound task must not park again).
            yield {"content": "still pending; giving up", "tool_calls": None}
        else:
            yield {"content": None, "tool_calls": [_poll_call()]}


async def _parked_timer_task(harness, port: Any, **overrides: Any) -> tuple[Any, Any, dict, Any]:  # noqa: F811
    """Submit an opted-in controller task and drive it to a real timer park."""

    controller = overrides.pop("controller", {"max_turns": 8, "timer_wait": {}})
    t_ref, task_uuid = await _submit_without_dispatch(harness, port, controller=controller, **overrides)
    worker = _worker_for(harness)
    record = await _dispatch_to_state(harness, t_ref, worker, "waiting_input")
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    wait = (record.extra or {})["wait"]
    assert wait["wake_kind"] == "timer" and wait["consumed"] is False
    return task_uuid, t_ref, wait, worker


def _timer_waker(harness, clock: Any = None, **overrides: Any) -> Any:  # noqa: F811
    from hecate.execution.timer_wait_wake import TimerWaitWaker

    return TimerWaitWaker(harness["store"], harness["session_factory"], clock=clock, **overrides)


def _record_of(harness, t_ref) -> Any:  # noqa: F811
    return asyncio.to_thread(harness["store"].get_task_state, t_ref)


async def test_opt_in_park_carries_receipt_anchored_deadline(harness, readonly_poll_tool) -> None:  # noqa: F811
    """The park deadline anchors on the declaring action's durable receipt."""

    _ = readonly_poll_tool
    port = _TimerPollPort()
    task_uuid, t_ref, wait, worker = await _parked_timer_task(harness, port)
    contract = wait["contract_ref"]
    assert contract["kind"] == "timer"
    assert contract["tool"] == "query_inventory"
    assert contract["execution_id"]
    assert contract["wake_seconds"] == 30.0
    assert contract["declaration"] == DECLARATION
    assert contract["run_id"] and contract["engine_session_id"]

    anchor_raw = await asyncio.to_thread(
        harness["store"].action_receipt_time,
        contract["execution_id"],
        task_ref=t_ref,
        action_name="query_inventory",
    )
    assert anchor_raw, "the declaring action's durable receipt anchors the deadline"
    expected = (datetime.fromisoformat(anchor_raw) + timedelta(seconds=30)).isoformat()
    assert wait["wait_expires_at"] == expected
    record = await _record_of(harness, t_ref)
    assert record.extra["timer_waits"] == 1

    # The worker is released: no further model usage before the wake.
    calls_after_park = (port.model_calls, len(port.business_calls))
    await _worker_for(harness).reconcile()
    assert (port.model_calls, len(port.business_calls)) == calls_after_park
    _ = task_uuid


async def test_timer_wake_requeues_and_the_same_run_completes(harness, readonly_poll_tool) -> None:  # noqa: F811
    """Expiry consumes the wait once and continues the SAME run's chain."""

    _ = readonly_poll_tool
    port = _TimerPollPort()
    task_uuid, t_ref, wait, _worker = await _parked_timer_task(harness, port)
    declaring_calls = list(port.business_calls)

    woken = await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once()
    assert woken == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.QUEUED
    assert record.extra["wait"]["consumed"] is True
    assert record.extra["wait"]["contract_ref"] == wait["contract_ref"]
    payload = await asyncio.to_thread(harness["store"].get_task_input, t_ref)
    assert wait["contract_ref"]["execution_id"] in payload["_input_wait_acknowledgements"]
    assert await asyncio.to_thread(harness["store"].list_input_revisions, t_ref) == []

    # A crash between the wake and the dispatch is healed by a fresh
    # worker's own reconcile; the declaring action never re-executes.
    record = await _dispatch_to_terminal(harness, t_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED, record.extra
    assert port.business_calls == declaring_calls + [{"job": "J-1"}]
    assert (record.extra["result"])["content"] == "job finished"

    # The continuation position is the durable chain: one run, no attempt mints.
    from sqlalchemy import select

    from hecate.models.run import RunModel

    async with harness["session_factory"]() as db:
        runs = (await db.execute(select(RunModel).where(RunModel.workspace_id == WS))).scalars().all()
    assert len(runs) == 1
    _ = task_uuid


async def test_repeated_and_concurrent_wakes_consume_once(harness, readonly_poll_tool) -> None:  # noqa: F811
    _ = readonly_poll_tool
    # One shared port for every task in this test: the assembly patch is
    # module-global, and a swapped port instance would reset the per-session
    # poll counters of an already-woken task mid-replay.
    port = _TimerPollPort()
    _task_uuid, t_ref, _wait, _worker = await _parked_timer_task(harness, port)
    waker = _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60))
    assert await waker.wake_once() == 1
    assert await waker.wake_once() == 0

    _second_uuid, second_tref, _wait2, _worker2 = await _parked_timer_task(harness, port)
    results = await asyncio.gather(
        _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once(),
        _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once(),
        return_exceptions=True,
    )
    assert sum(r for r in results if isinstance(r, int)) == 1
    record = await _record_of(harness, second_tref)
    assert record.lifecycle_state is TaskLifecycleState.QUEUED
    assert record.extra["wait"]["consumed"] is True


async def test_not_yet_due_wait_is_left_alone(harness, readonly_poll_tool) -> None:  # noqa: F811
    _ = readonly_poll_tool
    port = _TimerPollPort()
    _task_uuid, t_ref, _wait, _worker = await _parked_timer_task(harness, port)
    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC)).wake_once() == 0
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_timer_due_time_is_rechecked_at_effect_commit(harness, readonly_poll_tool) -> None:  # noqa: F811
    _task_uuid, t_ref, wait, _worker = await _parked_timer_task(harness, _TimerPollPort())
    due = datetime.fromisoformat(wait["wait_expires_at"])
    clocks = iter([due + timedelta(seconds=1), due - timedelta(seconds=1)])
    assert await _timer_waker(harness, clock=lambda: next(clocks)).wake_once() == 0
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    assert record.extra["wait"]["consumed"] is False


async def test_concurrent_cancel_and_timer_wake_preserve_the_winning_state(harness, readonly_poll_tool) -> None:  # noqa: F811
    port = _TimerPollPort()
    task_uuid, t_ref, _wait, _worker = await _parked_timer_task(harness, port)

    async def cancel():
        async with harness["session_factory"]() as db:
            service = TaskControlService(
                db,
                store=harness["store"],
                recorder=harness["store"],
                backend="postgres",
                ledger_source="core",
                session_factory=harness["session_factory"],
            )
            return await service.issue_command(
                workspace_id=WS, task_id=task_uuid, kind=ControlCommandKind.CANCEL, issuer="tester"
            )

    before = (port.model_calls, len(port.business_calls))
    _woken, command = await asyncio.gather(
        _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once(), cancel()
    )
    record = await _record_of(harness, t_ref)
    if command.record.state is CommandState.APPLIED:
        assert record.lifecycle_state is TaskLifecycleState.CANCELLED
    else:
        assert command.record.state is CommandState.REJECTED
        assert record.lifecycle_state is TaskLifecycleState.QUEUED
    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once() == 0
    assert (port.model_calls, len(port.business_calls)) == before


async def test_cancel_wins_the_race_and_terminals_never_reactivate(harness, readonly_poll_tool) -> None:  # noqa: F811
    _ = readonly_poll_tool
    port = _TimerPollPort()
    task_uuid, t_ref, _wait, _worker = await _parked_timer_task(harness, port)

    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["suite"].store,
            recorder=harness["suite"].recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
            dispatcher=None,
            worker=None,
        )
        await service.issue_command(workspace_id=WS, task_id=task_uuid, kind=ControlCommandKind.CANCEL, issuer="tester")
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.CANCELLED
    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once() == 0
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.CANCELLED


async def test_manual_provide_input_never_consumes_a_timer_wait(harness, readonly_poll_tool) -> None:  # noqa: F811
    _ = readonly_poll_tool
    port = _TimerPollPort()
    task_uuid, t_ref, wait, worker = await _parked_timer_task(harness, port)
    issue = await _issue_input_wake(harness, task_uuid, wait["wait_token"], {"region": "EU"}, worker=worker)
    assert issue.record.state is CommandState.REJECTED
    assert "timer waits reject manual wake" in (issue.detail or "")
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    assert record.extra["wait"]["consumed"] is False
    assert await asyncio.to_thread(harness["store"].list_input_revisions, t_ref) == []


async def test_crash_before_park_rederives_the_same_deadline(harness, readonly_poll_tool, monkeypatch) -> None:  # noqa: F811
    """The receipt anchor survives a park-commit failure; the clock never restarts."""

    _ = readonly_poll_tool
    port = _TimerPollPort()
    t_ref, _task_uuid = await _submit_without_dispatch(harness, port, controller={"max_turns": 8, "timer_wait": {}})
    worker = _worker_for(harness)

    real_apply = harness["store"].apply_task_state
    parks: list[str] = []

    def flaky_apply(task_ref, target, **kwargs):
        if (
            target is TaskLifecycleState.WAITING_INPUT
            and (kwargs.get("extra_update") or {}).get("wait", {}).get("wake_kind") == "timer"
        ):
            parks.append(kwargs["extra_update"]["wait"]["wait_expires_at"])
            if len(parks) == 1:
                raise OSError("injected park-commit crash")
        return real_apply(task_ref, target, **kwargs)

    monkeypatch.setattr(harness["store"], "apply_task_state", flaky_apply)

    await worker.dispatch_once(t_ref)
    assert len(parks) == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.QUEUED  # failed back into retry

    # Skip the retry backoff; the second attempt re-parks from the ledger.
    await asyncio.to_thread(
        harness["store"].apply_task_state,
        t_ref,
        TaskLifecycleState.QUEUED,
        extra_update={"next_dispatch_at": None},
    )
    await worker.dispatch_once(t_ref)
    assert len(parks) == 2
    assert parks[0] == parks[1], "a recovered park must re-derive the identical deadline"
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    assert record.extra["wait"]["wait_expires_at"] == parks[1]
    assert len(port.business_calls) == 1  # the declaring action stayed settled


async def test_unverifiable_anchor_parks_for_reconciliation(harness, readonly_poll_tool, monkeypatch) -> None:  # noqa: F811
    _ = readonly_poll_tool
    port = _TimerPollPort()
    _task_uuid, t_ref, _wait, worker = await _parked_timer_task(harness, port)
    monkeypatch.setattr(harness["store"], "action_receipt_time", lambda *a, **k: None)
    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once() == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert "anchor is unverifiable" in record.extra["reconciliation_reason"]
    _ = worker


async def test_expired_owner_cannot_commit_timer_anchor_failure(harness, readonly_poll_tool, monkeypatch) -> None:  # noqa: F811
    from hecate_durable.storage.lease import task_dispatch_lease_key
    from hecate_durable.storage.models import LeaseRow

    port = _TimerPollPort()
    t_ref, _task_uuid = await _submit_without_dispatch(harness, port, controller={"max_turns": 8, "timer_wait": {}})

    def missing_anchor_after_lease_expiry(*_args, **_kwargs):
        with harness["store"].session_factory() as db, db.begin():
            lease = db.get(LeaseRow, task_dispatch_lease_key(t_ref.issuer_domain, t_ref.id))
            lease.expires_at_epoch = 0.0
        return None

    monkeypatch.setattr(harness["store"], "action_receipt_time", missing_anchor_after_lease_expiry)
    await _worker_for(harness).dispatch_once(t_ref)
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RUNNING
    assert "reconciliation_reason" not in record.extra
    assert len(port.business_calls) == 1


@pytest.mark.parametrize(
    ("tamper", "reason_fragment"),
    [
        (lambda wait: {**wait, "wait_expires_at": "not-a-timestamp"}, "malformed"),
        (
            lambda wait: {
                **wait,
                "wait_expires_at": (
                    datetime.fromisoformat(wait["wait_expires_at"]) + timedelta(seconds=999)
                ).isoformat(),
            },
            "does not match its receipt anchor",
        ),
        (
            lambda wait: {**wait, "contract_ref": {**wait["contract_ref"], "kind": "input"}},
            "contract is unreadable",
        ),
    ],
)
async def test_corrupt_wait_facts_converge_to_reconciliation(
    harness,  # noqa: F811
    readonly_poll_tool,
    tamper,
    reason_fragment,
) -> None:
    _ = readonly_poll_tool
    port = _TimerPollPort()
    _task_uuid, t_ref, wait, _worker = await _parked_timer_task(harness, port)
    from hecate_durable.storage.models import TaskStateRow

    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": tamper(wait)}
        session.commit()

    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once() == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert reason_fragment in record.extra["reconciliation_reason"]


async def test_worker_cycle_wakes_and_redispatches(harness, readonly_poll_tool) -> None:  # noqa: F811
    """The waker rides the worker's reconcile cycle end to end."""

    _ = readonly_poll_tool
    from hecate_durable.worker import DurableWorker

    port = _TimerPollPort()
    _task_uuid, t_ref, _wait, _worker = await _parked_timer_task(harness, port)
    waker = _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60))
    worker = DurableWorker(
        harness["store"],
        PlatformTaskDispatcher(harness["store"], harness["session_factory"]),
        leases=harness["store"].leases,
        wait_reconciler=waker.wake_once,
    )
    # The parked state is exactly what the wait reconciler converges, so
    # drive the cycle unconditionally (the shared helper only reconciles
    # queued/running tasks).
    record = await _record_of(harness, t_ref)
    for _ in range(200):
        if record.lifecycle_state.value in {"succeeded", "failed", "cancelled", "reconciliation_required"}:
            break
        await worker.reconcile()
        await asyncio.sleep(0.05)
        record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED, record.extra
    assert len(port.business_calls) == 2


async def test_wait_bound_exhaustion_keeps_the_loop_going(harness, readonly_poll_tool) -> None:  # noqa: F811
    """An exhausted bound stops honoring declarations; the envelope is inert."""

    _ = readonly_poll_tool
    port = _TimerPollPort(declare_forever=True)
    t_ref, _task_uuid = await _submit_without_dispatch(
        harness, port, controller={"max_turns": 8, "timer_wait": {"max_waits": 1}}
    )
    worker = _worker_for(harness)
    record = await _dispatch_to_state(harness, t_ref, worker, "waiting_input")
    assert record.extra["wait"]["wake_kind"] == "timer"

    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once() == 1
    record = await _dispatch_to_terminal(harness, t_ref, worker=worker)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED, record.extra
    assert record.extra["timer_waits"] == 1  # the second declaration was never honored


async def test_nonreadonly_tool_declaration_is_inert(harness, write_poll_tool) -> None:  # noqa: F811
    _ = write_poll_tool
    port = _TimerPollPort(declare_forever=True)
    t_ref, _task_uuid = await _submit_without_dispatch(harness, port, controller={"max_turns": 4, "timer_wait": {}})
    record = await _dispatch_to_terminal(harness, t_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED, record.extra
    assert "wait" not in (record.extra or {})
    assert record.extra["result"]["content"] == "still pending; giving up"


async def test_model_text_can_never_declare_a_timer_wait(harness, readonly_poll_tool) -> None:  # noqa: F811
    """The envelope as model output is prose, not a wait declaration."""

    _ = readonly_poll_tool
    from tests.test_execution.test_controller_loop_dispatch import ScriptedLoopPort

    port = ScriptedLoopPort(final_text=json.dumps({"status": "timer_wait_required", "wait": dict(DECLARATION)}))
    t_ref, _task_uuid = await _submit_without_dispatch(harness, port, controller={"max_turns": 4, "timer_wait": {}})
    record = await _dispatch_to_terminal(harness, t_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED, record.extra
    assert "wait" not in (record.extra or {})


async def test_malformed_opt_in_is_rejected_before_submission(harness, readonly_poll_tool) -> None:  # noqa: F811
    _ = readonly_poll_tool
    from hecate.execution.task_control import TaskControlValidationError

    port = _TimerPollPort()
    with pytest.raises(TaskControlValidationError, match="max_waits"):
        await _submit_without_dispatch(harness, port, controller={"max_turns": 4, "timer_wait": {"max_waits": 0}})
    assert harness["store"].list_tasks() == []
    assert port.model_calls == 0


@pytest.mark.parametrize("field", ["wait_token", "wait_expires_at"])
async def test_missing_timer_fact_requires_reconciliation(harness, readonly_poll_tool, field) -> None:  # noqa: F811
    _task_uuid, t_ref, wait, _worker = await _parked_timer_task(harness, _TimerPollPort())
    from hecate_durable.storage.models import TaskStateRow

    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": {key: value for key, value in wait.items() if key != field}}
        session.commit()
    assert await _timer_waker(harness).wake_once() == 1
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED


async def test_corrupt_nonlocal_timer_is_never_modified(harness, readonly_poll_tool) -> None:  # noqa: F811
    _task_uuid, t_ref, wait, _worker = await _parked_timer_task(harness, _TimerPollPort())
    from hecate_durable.storage.models import TaskStateRow

    from hecate.models.run import RunModel

    async with harness["session_factory"]() as db:
        run = await db.get(RunModel, uuid.UUID(wait["contract_ref"]["run_id"]))
        run.control_owner = "runner"
        await db.commit()
    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": {**wait, "wait_expires_at": None}}
        session.commit()
    assert await _timer_waker(harness).wake_once() == 0
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_timer_declaration_must_match_the_actual_action_receipt(harness, readonly_poll_tool) -> None:  # noqa: F811
    _task_uuid, t_ref, wait, _worker = await _parked_timer_task(harness, _TimerPollPort())
    from hecate_durable.storage.models import TaskStateRow

    # Moving both fields together still cannot rewrite the recorded tool declaration.
    contract = wait["contract_ref"]
    moved = {
        **wait,
        "contract_ref": {
            **contract,
            "wake_seconds": 1.0,
            "declaration": {**contract["declaration"], "wake_in_seconds": 1},
        },
        "wait_expires_at": (datetime.fromisoformat(wait["wait_expires_at"]) - timedelta(seconds=29)).isoformat(),
    }
    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": moved}
        session.commit()
    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once() == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert "receipt" in record.extra["reconciliation_reason"]


@pytest.mark.parametrize("overrides", [{"controller_loop": False}, {"workflow": {"steps": []}}])
async def test_timer_wait_rejects_unsupported_execution_modes(harness, overrides) -> None:  # noqa: F811
    from hecate.execution.task_control import TaskControlValidationError

    with pytest.raises(TaskControlValidationError, match="timer_wait"):
        await _submit_without_dispatch(harness, _TimerPollPort(), controller={"timer_wait": {}}, **overrides)
    assert harness["store"].list_tasks() == []


async def test_timer_wait_rejects_a_store_without_ledger_reads(harness, monkeypatch) -> None:  # noqa: F811
    from hecate.execution.task_control import TaskControlValidationError

    monkeypatch.setattr(harness["store"], "get_run_action", None)
    with pytest.raises(TaskControlValidationError, match="SQL action ledger"):
        await _submit_without_dispatch(harness, _TimerPollPort(), controller={"timer_wait": {}})
    assert harness["store"].list_tasks() == []


@pytest.mark.parametrize("version", [True, 1.0, "1"])
def test_timer_contract_version_requires_an_integer(version) -> None:
    from hecate.execution.timer_wait import timer_wait_declaration

    assert (
        timer_wait_declaration({"status": "timer_wait_required", "wait": {**DECLARATION, "version": version}}) is None
    )


@pytest.mark.parametrize(
    "controller",
    [
        {"max_turns": 1, "timer_wait": {}},
        {"max_turns": 8, "timer_wait": {}, "no_progress": {"limit": 1, "observations": ["query_inventory"]}},
    ],
)
async def test_timer_wake_preserves_controller_limits(harness, readonly_poll_tool, controller) -> None:  # noqa: F811
    port = _TimerPollPort(declare_forever=True)
    _task_uuid, t_ref, _wait, _worker = await _parked_timer_task(harness, port, controller=controller)
    calls = (port.model_calls, len(port.business_calls))
    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once() == 1
    record = await _dispatch_to_terminal(harness, t_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert (port.model_calls, len(port.business_calls)) == calls
    assert record.extra["timer_waits"] == 1


async def test_changed_timer_policy_conflicts_before_new_model_or_tool_calls(harness, readonly_poll_tool) -> None:  # noqa: F811
    port = _TimerPollPort()
    _task_uuid, t_ref, _wait, _worker = await _parked_timer_task(harness, port)
    from hecate_durable.storage.models import TaskStateRow

    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.input_payload = {**row.input_payload, "controller": {"max_turns": 8, "timer_wait": {"max_waits": 99}}}
        session.commit()
    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once() == 1
    calls = (port.model_calls, len(port.business_calls))
    record = await _dispatch_to_terminal(harness, t_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert (port.model_calls, len(port.business_calls)) == calls


async def test_timer_wake_preserves_the_original_task_deadline(harness, readonly_poll_tool, monkeypatch) -> None:  # noqa: F811
    from dataclasses import replace

    from hecate_runtime.agent_controller import AgentController

    port = _TimerPollPort()
    deadline = datetime.now(UTC) + timedelta(seconds=20)
    _task_uuid, t_ref, wait, _worker = await _parked_timer_task(
        harness, port, controller={"max_turns": 8, "timer_wait": {}, "deadline": deadline.isoformat()}
    )
    assert datetime.fromisoformat(wait["wait_expires_at"]) == deadline
    after = deadline + timedelta(seconds=1)
    assert await _timer_waker(harness, clock=lambda: after).wake_once() == 1
    original_stream = AgentController.stream

    async def stream_after_deadline(self, spec, **kwargs):
        async for event in original_stream(self, replace(spec, clock=lambda: after), **kwargs):
            yield event

    monkeypatch.setattr(AgentController, "stream", stream_after_deadline)
    calls = (port.model_calls, len(port.business_calls))
    record = await _dispatch_to_terminal(harness, t_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert "deadline" in record.extra["result"]["error_category"]
    assert (port.model_calls, len(port.business_calls)) == calls


async def test_restart_in_an_independent_process_resumes_the_same_chain(harness, readonly_poll_tool) -> None:  # noqa: F811
    import subprocess
    import sys
    from pathlib import Path

    port = _TimerPollPort()
    _task_uuid, t_ref, wait, _worker = await _parked_timer_task(harness, port)
    command = [
        sys.executable,
        "-m",
        "tests.test_execution._timer_wait_process",
        str(harness["store"].engine.url),
        t_ref.id,
        (datetime.fromisoformat(wait["wait_expires_at"]) + timedelta(seconds=1)).isoformat(),
    ]
    completed = await asyncio.to_thread(
        subprocess.run,
        command,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report == {"woken": 1, "state": "succeeded", "model_calls": 2, "tool_calls": 1}
    record = await _record_of(harness, t_ref)
    assert record.extra["wait"]["wait_expires_at"] == wait["wait_expires_at"]
    assert record.extra["wait"]["contract_ref"] == wait["contract_ref"]
    assert len(harness["store"].list_tasks()) == 1


async def test_standalone_composite_includes_the_timer_waker(harness, readonly_poll_tool, monkeypatch) -> None:  # noqa: F811
    _ = readonly_poll_tool
    from hecate.execution.approval_wait_expiration import standalone_wait_reconcilers

    port = _TimerPollPort()
    _task_uuid, t_ref, _wait, _worker = await _parked_timer_task(harness, port)
    # The composite resolves the platform engine through the process-wide
    # binding; tests bind it to the harness session factory.
    monkeypatch.setattr("hecate.core.database.async_session_factory", harness["session_factory"])
    reconcile = standalone_wait_reconcilers(store=harness["store"])
    # Not due yet: the composite runs every pass (approval, resume, input,
    # timer) and none may raise on the shared store.
    processed = await reconcile()
    assert isinstance(processed, int)
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT

    waker = _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60))
    assert await waker.wake_once() == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.QUEUED


async def test_foreign_issuer_timer_wait_is_untouched(harness) -> None:  # noqa: F811
    from hecate.contracts.execution.durable import IdempotencyKey
    from hecate.contracts.execution.references import run_ref, task_ref

    t = task_ref("foreign-host", f"timer-{uuid.uuid4().hex[:8]}")
    r = run_ref("foreign-host", f"frun-{uuid.uuid4().hex[:8]}")
    harness["store"].submit_task(
        key=IdempotencyKey(key=t.id, subject="s", workspace=str(WS), request_digest="c" * 8),
        task_ref=t,
        run_ref=r,
        input_payload={"goal": "foreign timer", "workspace_id": str(WS)},
    )
    harness["store"].apply_task_state(
        t,
        TaskLifecycleState.WAITING_INPUT,
        extra_update={
            "wait": {
                "wake_kind": "timer",
                "contract_ref": {
                    "kind": "timer",
                    "tool": "poll",
                    "execution_id": "exec-1",
                    "arguments_digest": "d" * 64,
                    "wake_seconds": 30.0,
                    "declaration": dict(DECLARATION),
                },
                "wait_token": "token-foreign",
                "wait_expires_at": "2020-01-01T00:00:00+00:00",
                "consumed": False,
            }
        },
    )
    assert await _timer_waker(harness, clock=lambda: datetime.now(UTC) + timedelta(seconds=60)).wake_once() == 0
    record = await _record_of(harness, t)
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    assert record.extra["wait"]["consumed"] is False
