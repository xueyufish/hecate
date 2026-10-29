"""Persistent convergence of expired platform human input waits (step6).

A real input park (explicit input_required contract, dispatcher park, the
declared ``input_wait_timeout_seconds``) is driven through the expirer the
durable worker runs on its reconcile cycle: the task converges to
``failed`` with ``input_wait_expired``, the token is consumed while the
request, deadline and action facts survive, the run projection and the
transactional outbox carry the fact exactly once, and nothing re-executes.
Races with legal wakes, cancels and repeated scans share the revision CAS
and the commit-time deadline re-verification; workflow child waits,
approval waits, undeclared waits and foreign or non-local executions are
out of scope by construction.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from hecate.execution.task_control import TaskControlValidationError
from hecate.execution.task_dispatcher import PlatformTaskDispatcher
from hecate.models.run import RunModel
from tests.test_execution.test_approval_wait_expiration import _EXPIRED_CLOCK
from tests.test_execution.test_controller_loop_dispatch import (  # noqa: F401 - fixture registration
    OWNER,
    WS,
    _dispatch_to_state,
    _dispatch_to_terminal,
    _issue_input_wake,
    _ProductionInputPort,
    _RepeatedInputPort,
    _submit_without_dispatch,
    _worker_for,
    harness,
)


def _input_expirer(harness, **overrides: Any) -> Any:  # noqa: F811
    from hecate.execution.input_wait_expiration import InputWaitExpirer

    kwargs: dict[str, Any] = {"clock": _EXPIRED_CLOCK}
    kwargs.update(overrides)
    return InputWaitExpirer(harness["store"], harness["session_factory"], **kwargs)


async def _parked_input_task(harness, port: Any | None = None, **overrides: Any) -> tuple[Any, Any, dict, Any, Any]:  # noqa: F811
    """Submit and drive to a real input park with the declared timeout."""

    timeout = overrides.pop("timeout_seconds", 3600)
    port = port or _ProductionInputPort()
    t_ref, task_uuid = await _submit_without_dispatch(
        harness, port, controller={"max_turns": 8, "input_wait_timeout_seconds": timeout}, **overrides
    )
    worker = _worker_for(harness)
    record = await _dispatch_to_state(harness, t_ref, worker, "waiting_input")
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    wait = (record.extra or {})["wait"]
    assert wait["wake_kind"] == "provide_input" and wait["consumed"] is False
    return task_uuid, t_ref, wait, worker, port


async def _record_of(harness, t_ref) -> Any:  # noqa: F811
    return await asyncio.to_thread(harness["store"].get_task_state, t_ref)


async def test_declared_timeout_parks_with_ledger_anchored_deadline(harness) -> None:  # noqa: F811
    """The park deadline is receipt-anchored, not wall-clock anchored."""
    task_uuid, t_ref, wait, _worker, _port = await _parked_input_task(harness, timeout_seconds=3600)
    anchor_raw = await asyncio.to_thread(harness["store"].action_receipt_time, wait["contract_ref"]["execution_id"])
    assert anchor_raw, "the input-requesting action's durable receipt anchors the deadline"
    anchor = datetime.fromisoformat(anchor_raw)
    expected = (anchor + timedelta(seconds=3600)).isoformat()
    assert wait["wait_expires_at"] == expected


async def test_expired_input_wait_converges_without_reexecution(harness) -> None:  # noqa: F811
    """Expiry fails the task, consumes the token, preserves the facts."""
    from hecate_durable.worker import OutboxRelay
    from sqlalchemy import select

    from hecate.execution.event_relay import PlatformOutboxProjector

    task_uuid, t_ref, wait, _worker, port = await _parked_input_task(harness, timeout_seconds=3600)
    business_calls = list(port.business_calls)

    assert await _input_expirer(harness).expire_once() == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {})["result"]
    assert result["error_category"] == "input_wait_expired"
    assert record.extra["wait"]["consumed"] is True
    # Request, deadline and action facts survive verbatim.
    assert record.extra["wait"]["contract_ref"] == wait["contract_ref"]
    assert record.extra["wait"]["wait_expires_at"] == wait["wait_expires_at"]
    assert len(port.business_calls) == len(business_calls)  # zero re-execution

    async with harness["session_factory"]() as db:
        (run_id,) = [
            row.id for row in (await db.execute(select(RunModel).where(RunModel.workspace_id == WS))).scalars()
        ]
        run = await db.get(RunModel, run_id)
        assert (run.projection or {})["state"] == "failed"
        assert (run.projection or {})["error_category"] == "input_wait_expired"

    events = harness["store"].read_events(harness["store"].run_for_task(t_ref), limit=200).events
    terminals = [e for e in events if e.payload.get("event_type") == "run_terminal"]
    assert len(terminals) == 1
    assert terminals[0].payload["error_category"] == "input_wait_expired"

    # Drain the pre-existing outbox first so the convergence's own relay
    # delivery is the only new projection.
    relay = OutboxRelay(
        harness["store"].session_factory,
        PlatformOutboxProjector(harness["store"], harness["session_factory"]),
        relay_key="input-expiry",
    )
    await _drain_outbox(relay)

    # A late input can neither reopen the task nor fabricate a revision.
    late = await _issue_input_wake(
        harness, task_uuid, wait["wait_token"], {"region": "EU"}, worker=_worker_for(harness)
    )
    assert late.record.state is CommandState.REJECTED
    assert await asyncio.to_thread(harness["store"].list_input_revisions, t_ref) == []
    # The settled terminal fact never re-projects; other receipts (the
    # rejected wake's own command trail) may still deliver.
    await _drain_outbox(relay)
    async with harness["session_factory"]() as db:
        page = await _sole_run_events(harness, db)
    terminal_count = sum(1 for e in page if e.payload_schema_ref == "hecate.platform.run_terminal/0")
    assert terminal_count == 1


async def test_unexpired_wait_is_left_alone(harness) -> None:  # noqa: F811
    _task_uuid, t_ref, _wait, _worker, _port = await _parked_input_task(harness, timeout_seconds=3600)
    assert await _input_expirer(harness, clock=lambda: datetime.now(UTC)).expire_once() == 0
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_legal_input_wins_the_cas(harness) -> None:  # noqa: F811
    """A wake that moved the task first keeps its fact; the scan claims nothing."""
    task_uuid, t_ref, wait, worker, port = await _parked_input_task(harness, timeout_seconds=3600)
    issue = await _issue_input_wake(harness, task_uuid, wait["wait_token"], {"region": "EU"}, worker=worker)
    assert issue.record.state is CommandState.APPLIED

    assert await _input_expirer(harness).expire_once() == 0
    record = await _dispatch_to_state(harness, t_ref, worker, "succeeded", "failed", timeout=60.0)
    assert record.lifecycle_state.value == "succeeded"
    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, t_ref)
    assert [row["content"] for row in revisions] == [{"region": "EU"}]
    _ = port


async def test_cancel_wins_the_race(harness) -> None:  # noqa: F811
    task_uuid, t_ref, _wait, worker, _port = await _parked_input_task(harness, timeout_seconds=3600)
    async with harness["session_factory"]() as db:
        service = TaskControlServiceShim(harness, db)
        await service.issue_cancel(task_uuid)
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.CANCELLED
    assert await _input_expirer(harness).expire_once() == 0


class TaskControlServiceShim:
    """Minimal cancel issuer over the real service (test-local helper)."""

    def __init__(self, harness, db) -> None:  # noqa: F811
        self._harness = harness
        self._db = db

    async def issue_cancel(self, task_uuid: uuid.UUID) -> None:
        from hecate.execution.task_control import TaskControlService

        service = TaskControlService(
            self._db,
            store=self._harness["suite"].store,
            recorder=self._harness["suite"].recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=self._harness["session_factory"],
            dispatcher=None,
            worker=None,
        )
        await service.issue_command(
            workspace_id=WS,
            task_id=task_uuid,
            kind=ControlCommandKind.CANCEL,
            issuer="tester",
        )


async def test_repeated_and_concurrent_scans_converge_once(harness) -> None:  # noqa: F811
    first_uuid, _t_ref, _wait, _worker, _port = await _parked_input_task(harness, timeout_seconds=3600)
    expirer = _input_expirer(harness)
    assert await expirer.expire_once() == 1
    assert await expirer.expire_once() == 0

    _second_uuid, second_tref, _wait2, _worker2, _port2 = await _parked_input_task(harness, timeout_seconds=3600)
    results = await asyncio.gather(
        _input_expirer(harness).expire_once(), _input_expirer(harness).expire_once(), return_exceptions=True
    )
    assert sum(r for r in results if isinstance(r, int)) == 1
    record = await _record_of(harness, second_tref)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert record.extra["wait"]["consumed"] is True
    _ = first_uuid


async def test_malformed_deadline_parks_for_reconciliation(harness) -> None:  # noqa: F811
    from hecate_durable.storage.models import TaskStateRow

    task_uuid, t_ref, _wait, _worker, _port = await _parked_input_task(harness, timeout_seconds=3600)
    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": {**row.extra["wait"], "wait_expires_at": "not-a-timestamp"}}
        session.commit()

    assert await _input_expirer(harness).expire_once() == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert "malformed" in record.extra["reconciliation_reason"]
    _ = task_uuid


async def test_undeclared_wait_keeps_its_meaning(harness) -> None:  # noqa: F811
    """Historical no-deadline input waits stay resumable; expiry invented never."""
    port = _ProductionInputPort()
    t_ref, _task_uuid = await _submit_without_dispatch(harness, port)
    worker = _worker_for(harness)
    await _dispatch_to_state(harness, t_ref, worker, "waiting_input")

    assert await _input_expirer(harness).expire_once() == 0
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_workflow_child_result_waits_are_out_of_scope(harness) -> None:  # noqa: F811
    from hecate.contracts.execution.durable import IdempotencyKey
    from hecate.contracts.execution.references import run_ref, task_ref

    t = task_ref("hecate", f"child-{uuid.uuid4().hex[:8]}")
    r = run_ref("hecate", f"crun-{uuid.uuid4().hex[:8]}")
    harness["store"].submit_task(
        key=IdempotencyKey(key=t.id, subject="s", workspace=str(WS), request_digest="c" * 8),
        task_ref=t,
        run_ref=r,
        input_payload={"goal": "child wait", "workspace_id": str(WS)},
    )
    harness["store"].apply_task_state(t, TaskLifecycleState.RUNNING)
    harness["store"].apply_task_state(
        t,
        TaskLifecycleState.WAITING_INPUT,
        extra_update={
            "wait": {
                "wake_kind": "provide_input",
                "contract_ref": {"await_task_ref": {"kind": "task", "issuer_domain": "hecate", "id": "parent-1"}},
                "wait_token": "child-token",
                "wait_expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "consumed": False,
            }
        },
    )
    assert await _input_expirer(harness).expire_once() == 0
    assert (await _record_of(harness, t)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_foreign_host_waits_are_never_converged(harness) -> None:  # noqa: F811
    from hecate.contracts.execution.durable import IdempotencyKey
    from hecate.contracts.execution.references import run_ref, task_ref

    t = task_ref("runner-host", f"foreign-{uuid.uuid4().hex[:8]}")
    r = run_ref("runner-host", f"frun-{uuid.uuid4().hex[:8]}")
    harness["store"].submit_task(
        key=IdempotencyKey(key=t.id, subject="s", workspace=str(WS), request_digest="f" * 8),
        task_ref=t,
        run_ref=r,
        input_payload={"goal": "foreign", "workspace_id": str(WS)},
    )
    harness["store"].apply_task_state(t, TaskLifecycleState.RUNNING)
    harness["store"].apply_task_state(
        t,
        TaskLifecycleState.WAITING_INPUT,
        extra_update={
            "wait": {
                "wake_kind": "provide_input",
                "contract_ref": {"execution_id": "ext-1"},
                "wait_token": "tok",
                "wait_expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "consumed": False,
            }
        },
    )
    assert await _input_expirer(harness).expire_once() == 0
    assert (await _record_of(harness, t)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_consumed_wait_is_skipped(harness) -> None:  # noqa: F811
    from hecate_durable.storage.models import TaskStateRow

    _task_uuid, t_ref, _wait, _worker, _port = await _parked_input_task(harness, timeout_seconds=3600)
    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": {**row.extra["wait"], "consumed": True}}
        session.commit()

    assert await _input_expirer(harness).expire_once() == 0
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_relay_recovers_a_lost_projection(harness, monkeypatch) -> None:  # noqa: F811
    """Terminal committed, direct projection lost: the relay rebuilds it."""
    from hecate_durable.worker import OutboxRelay

    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.execution.task_run_registry import TaskRunRegistry

    task_uuid, t_ref, _wait, _worker, _port = await _parked_input_task(harness, timeout_seconds=3600)

    async def down(self, *args, **kwargs):  # noqa: ANN001, ANN003
        raise RuntimeError("projection write lost")

    monkeypatch.setattr(TaskRunRegistry, "finalize_run", down)
    assert await _input_expirer(harness).expire_once() == 1
    monkeypatch.undo()

    relay = OutboxRelay(
        harness["store"].session_factory,
        PlatformOutboxProjector(harness["store"], harness["session_factory"]),
        relay_key="input-recovery",
    )
    await _drain_outbox(relay)

    async with harness["session_factory"]() as db:
        run = await db.get(RunModel, (await _sole_run_id(harness)))
    assert (run.projection or {})["state"] == "failed"
    assert (run.projection or {})["error_category"] == "input_wait_expired"
    await _drain_outbox(relay)  # idempotent: the settled fact projects once
    _ = task_uuid


async def _drain_outbox(relay: Any, *, timeout: float = 120.0) -> None:
    """Drain the outbox, retrying transient cycle failures like production.

    ``OutboxRelay.run_forever`` outlives a bad cycle: a fencing loss or a
    contended SQLite file fails one pump and the next cycle re-acquires a
    fresh lease from the persisted cursor. Test drains follow the same
    contract instead of letting one transient failure kill the assertion.
    """

    from hecate_durable.storage.lease import StaleFenceError
    from sqlalchemy.exc import OperationalError

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        try:
            pumped = await relay.pump_once()
        except (StaleFenceError, OperationalError):
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(0.1)
            continue
        if pumped == 0 or asyncio.get_running_loop().time() >= deadline:
            return


async def _sole_run_events(harness, db) -> tuple:  # noqa: F811
    from sqlalchemy import select

    from hecate.contracts.execution.references import BackendRef, RefKind
    from hecate.execution.governance_events import PlatformEventService

    rows = (await db.execute(select(RunModel).where(RunModel.workspace_id == WS))).scalars().all()
    (run,) = rows
    page = await PlatformEventService(db).read_run_events(
        BackendRef(RefKind.RUN, run.issuer_domain, str(run.id)), workspace_id=WS, limit=500
    )
    return page.events


async def _sole_run_id(harness) -> uuid.UUID:  # noqa: F811
    from sqlalchemy import select

    async with harness["session_factory"]() as db:
        rows = (await db.execute(select(RunModel).where(RunModel.workspace_id == WS))).scalars().all()
    (run,) = rows
    return run.id


async def test_multiple_requests_each_get_their_own_window(harness) -> None:  # noqa: F811
    """A woken request's successor parks with its own receipt-anchored window."""
    from datetime import timedelta as _td

    port = _RepeatedInputPort()
    t_ref, task_uuid = await _submit_without_dispatch(
        harness, port, controller={"max_turns": 8, "input_wait_timeout_seconds": 3600}
    )
    worker = _worker_for(harness)
    deadlines = []
    for index in range(2):
        record = await _dispatch_to_state(harness, t_ref, worker, "waiting_input")
        wait = (record.extra or {})["wait"]
        anchor_raw = await asyncio.to_thread(harness["store"].action_receipt_time, wait["contract_ref"]["execution_id"])
        anchor = datetime.fromisoformat(anchor_raw)
        assert wait["wait_expires_at"] == (anchor + _td(seconds=3600)).isoformat()
        deadlines.append(wait["wait_expires_at"])
        issue = await _issue_input_wake(harness, task_uuid, wait["wait_token"], {"region": f"R{index}"}, worker=worker)
        assert issue.record.state is CommandState.APPLIED
    assert deadlines[0] != deadlines[1]  # each request is timed from its own receipt
    record = await _dispatch_to_terminal(harness, t_ref, worker=_worker_for(harness))
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED


async def test_anchor_crash_window_rederives_the_same_deadline(harness, monkeypatch) -> None:  # noqa: F811
    """Outcome committed, park lost: the recovered park keeps the deadline."""
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    port = _ProductionInputPort()
    t_ref, _task_uuid = await _submit_without_dispatch(
        harness, port, controller={"max_turns": 8, "input_wait_timeout_seconds": 3600}
    )
    original = PlatformTaskDispatcher._park

    async def crash_before_park(*args, **kwargs):
        raise asyncio.CancelledError("simulated process exit before durable park")

    monkeypatch.setattr(PlatformTaskDispatcher, "_park", crash_before_park)
    with pytest.raises(asyncio.CancelledError):
        await _worker_for(harness).dispatch_once(t_ref)
    monkeypatch.setattr(PlatformTaskDispatcher, "_park", original)
    await _worker_for(harness).reconcile()
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    wait = (record.extra or {})["wait"]
    anchor_raw = await asyncio.to_thread(harness["store"].action_receipt_time, wait["contract_ref"]["execution_id"])
    anchor = datetime.fromisoformat(anchor_raw)
    assert wait["wait_expires_at"] == (anchor + timedelta(seconds=3600)).isoformat()
    # The recovery asked the model once for turn 1 only — the ledger replay
    # re-observed the request without a fresh ask beyond the first.
    assert port.model_calls == 1


async def test_commit_time_deadline_crossing_rejects_the_wake(harness, monkeypatch) -> None:  # noqa: F811
    """A wake valid at the gate but crossing the deadline under the lock loses."""
    task_uuid, t_ref, wait, worker, _port = await _parked_input_task(harness, timeout_seconds=3600)
    store = harness["store"]
    apply = store.apply_task_state

    def delayed(task_ref, target, **kwargs):
        if target is TaskLifecycleState.QUEUED:
            # The input wake commits against the store's own post-lock
            # clock; shift it past the deadline to model the lock wait.
            monkeypatch.setattr(store, "_clock", lambda: _EXPIRED_CLOCK().isoformat())
        return apply(task_ref, target, **kwargs)

    monkeypatch.setattr(store, "apply_task_state", delayed)
    issued = await _issue_input_wake(harness, task_uuid, wait["wait_token"], {"region": "EU"}, worker=worker)
    assert issued.record.state is CommandState.REJECTED
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.WAITING_INPUT
    assert record.extra["wait"]["consumed"] is False
    assert await asyncio.to_thread(harness["store"].list_input_revisions, t_ref) == []


async def test_worker_cycle_drives_the_input_expirer(harness) -> None:  # noqa: F811
    from hecate_durable.worker import DurableWorker

    _task_uuid, t_ref, _wait, _worker, _port = await _parked_input_task(harness, timeout_seconds=3600)
    worker = DurableWorker(
        harness["store"],
        PlatformTaskDispatcher(harness["store"], harness["session_factory"]),
        leases=harness["store"].leases,
        wait_reconciler=_input_expirer(harness).expire_once,
    )
    await worker.reconcile()
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.FAILED


async def test_worker_without_reconciler_keeps_input_waits(harness) -> None:  # noqa: F811
    _task_uuid, t_ref, _wait, _worker, _port = await _parked_input_task(harness, timeout_seconds=3600)
    worker = _worker_for(harness)
    await worker.reconcile()
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


@pytest.mark.parametrize(
    "declaration",
    [
        {"input_wait_timeout_seconds": 0},
        {"input_wait_timeout_seconds": -5},
        {"input_wait_timeout_seconds": True},
        {"input_wait_timeout_seconds": "600"},
        {"input_wait_timeout_seconds": float("nan")},
        {"input_wait_timeout_seconds": float("inf")},
        {"input_wait_timeout_seconds": 10**1000},
        {"input_wait_timeout_seconds": 1e300},
        {"input_wait_timeout_seconds": 1e-300},
    ],
)
async def test_invalid_timeout_declaration_is_rejected_before_submission(harness, declaration) -> None:  # noqa: F811

    with pytest.raises(TaskControlValidationError):
        await _submit_without_dispatch(
            harness,
            _ProductionInputPort(),
            controller={"max_turns": 4, **declaration},
        )
    assert harness["store"].list_tasks() == []


@pytest.mark.parametrize("ownership", ["host", "imported", "remote", "managed"])
async def test_nonlocal_malformed_wait_is_never_mutated(harness, ownership):  # noqa: F811
    from hecate_durable.storage.models import TaskStateRow
    from sqlalchemy import select

    from hecate.execution.task_dispatcher import run_row_ref
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel
    from hecate.models.managed_delivery import ManagedDeliveryModel
    from hecate.models.run import RunOrigin
    from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

    _, t_ref, _, _, _ = await _parked_input_task(harness)
    async with harness["session_factory"]() as db:
        run = (await db.execute(select(RunModel))).scalar_one()
        if ownership == "host":
            run.control_owner = "host"
        elif ownership == "imported":
            run.origin = RunOrigin.IMPORTED_OBSERVATION
        elif ownership == "remote":
            deployment = await db.get(AgentDeploymentModel, run.deployment_id)
            deployment.access_mode = AccessMode.REMOTE_SERVICE
        else:
            enrollment = StandaloneEnrollmentModel(workspace_id=WS, host_identity_ref={}, trust_root_ref={})
            db.add(enrollment)
            await db.flush()
            db.add(
                ManagedDeliveryModel(
                    workspace_id=WS,
                    enrollment_id=enrollment.id,
                    task_ref=t_ref.to_dict(),
                    run_ref=run_row_ref(run).to_dict(),
                    input_payload={},
                )
            )
        await db.commit()
    with harness["store"].session_factory.begin() as db:
        row = db.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": {**row.extra["wait"], "wait_expires_at": "malformed"}}
    assert await _input_expirer(harness).expire_once() == 0
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_other_wait_contract_is_out_of_scope(harness):  # noqa: F811
    from hecate_durable.storage.models import TaskStateRow

    _, t_ref, _, _, _ = await _parked_input_task(harness)
    with harness["store"].session_factory.begin() as db:
        row = db.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        wait = row.extra["wait"]
        row.extra = {**row.extra, "wait": {**wait, "contract_ref": {"kind": "timer"}}}
    assert await _input_expirer(harness).expire_once() == 0
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


@pytest.mark.parametrize("field", ["task_id", "run_id", "action_name", "arguments_digest"])
async def test_action_from_another_task_cannot_anchor_expiry(harness, field):  # noqa: F811
    from hecate_durable.storage.models import ActionIntentRow

    _, t_ref, wait, _, _ = await _parked_input_task(harness)
    with harness["store"].session_factory.begin() as db:
        intent = db.get(ActionIntentRow, wait["contract_ref"]["execution_id"])
        setattr(intent, field, str(uuid.uuid4()))
    assert await _input_expirer(harness).expire_once() == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert record.extra["result"]["error_category"] == "input_wait_unverifiable"


async def test_standalone_scanner_keeps_its_cursor(harness, monkeypatch):  # noqa: F811
    from hecate_durable.storage.models import TaskStateRow

    from hecate.execution.approval_wait_expiration import standalone_wait_reconcilers
    from hecate.execution.input_wait_expiration import InputWaitExpirer

    _, first, _, _, _ = await _parked_input_task(harness)
    _, later, _, _, _ = await _parked_input_task(harness)
    with harness["store"].session_factory.begin() as db:
        row = db.get(TaskStateRow, (first.issuer_domain, first.id))
        row.extra = {
            **row.extra,
            "wait": {**row.extra["wait"], "wait_expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        }
    monkeypatch.setattr("hecate.core.database.async_session_factory", harness["session_factory"])
    monkeypatch.setattr(
        "hecate.execution.input_wait_expiration.InputWaitExpirer",
        lambda store, factory: InputWaitExpirer(store, factory, batch=1, clock=_EXPIRED_CLOCK),
    )
    reconcile = standalone_wait_reconcilers(store=harness["store"])
    assert await reconcile() == 0
    assert await reconcile() == 1
    assert (await _record_of(harness, later)).lifecycle_state is TaskLifecycleState.FAILED


async def test_standalone_approval_failure_does_not_block_input(harness, monkeypatch):  # noqa: F811
    from hecate.execution.approval_wait_expiration import standalone_wait_reconcilers
    from hecate.execution.input_wait_expiration import InputWaitExpirer

    _, t_ref, _, _, _ = await _parked_input_task(harness)

    async def unavailable():
        raise RuntimeError("approval scan unavailable")

    monkeypatch.setattr("hecate.core.database.async_session_factory", harness["session_factory"])
    monkeypatch.setattr("hecate.execution.approval_wait_expiration.standalone_expirer", lambda **kwargs: unavailable)
    monkeypatch.setattr(
        "hecate.execution.input_wait_expiration.InputWaitExpirer",
        lambda store, factory: InputWaitExpirer(store, factory, clock=_EXPIRED_CLOCK),
    )
    assert await standalone_wait_reconcilers(store=harness["store"])() == 1
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.FAILED


@pytest.mark.parametrize("changed", [False, True])
async def test_duplicate_receipt_does_not_extend_the_anchor(harness, monkeypatch, changed):  # noqa: F811
    from dataclasses import replace

    _, t_ref, wait, _, _ = await _parked_input_task(harness)
    store = harness["store"]
    key = wait["contract_ref"]["execution_id"]
    original = store.action_receipt_time(key)
    receipt = store.recovery(key).last_outcome
    if changed:
        receipt = replace(receipt, result_digest="conflicting-result")
    payload = store.action_result(key, t_ref)
    monkeypatch.setattr(store, "_clock", lambda: _EXPIRED_CLOCK().isoformat())
    monkeypatch.setattr(store.events, "_clock", lambda: _EXPIRED_CLOCK().isoformat())
    store.record_outcome_ex(receipt, result_payload=payload)
    assert store.action_receipt_time(key) == (None if changed else original)


async def test_default_clock_divergence_requires_reconciliation(harness, monkeypatch):  # noqa: F811
    _, t_ref, wait, _, _ = await _parked_input_task(harness)
    monkeypatch.setattr(harness["store"], "_clock", lambda: _EXPIRED_CLOCK().isoformat())
    assert await _input_expirer(harness, clock=None).expire_once() == 1
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert record.extra["result"]["error_category"] == "input_wait_unverifiable"
    assert record.extra["wait"] == wait


async def test_default_sql_clock_keeps_an_unexpired_wait(harness):  # noqa: F811
    _, t_ref, _, _, _ = await _parked_input_task(harness)
    assert await _input_expirer(harness, clock=None).expire_once() == 0
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_real_process_restart_expires_a_preexisting_wait(harness):  # noqa: F811
    """A separate interpreter reconstructs ownership and the immutable receipt."""
    import sys

    from hecate_durable.storage.models import TaskStateRow

    _, t_ref, wait, _, port = await _parked_input_task(harness)
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with harness["store"].session_factory.begin() as db:
        row = db.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": {**row.extra["wait"], "wait_expires_at": expired}}
    calls = list(port.business_calls)
    script = """
import asyncio, sys
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from hecate_durable.storage import SqlDurableStore
from hecate.execution.input_wait_expiration import InputWaitExpirer
async def main():
    store = SqlDurableStore(sys.argv[1])
    engine = create_async_engine(sys.argv[1].replace("sqlite:", "sqlite+aiosqlite:", 1))
    try:
        scanner = InputWaitExpirer(store, async_sessionmaker(engine, expire_on_commit=False))
        print(await scanner.expire_once(), await scanner.expire_once())
    finally:
        await engine.dispose()
        store.dispose()
asyncio.run(main())
"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        harness["store"].engine.url.render_as_string(hide_password=False),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), 45)
    assert proc.returncode == 0, stderr.decode()
    assert stdout.decode().strip() == "1 0"
    record = await _record_of(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert record.extra["wait"]["contract_ref"] == wait["contract_ref"]
    assert port.business_calls == calls
    assert harness["store"].list_input_revisions(t_ref) == []


async def test_drain_does_not_start_input_expiry(harness):  # noqa: F811
    from hecate_durable.worker import DurableWorker

    _, t_ref, _, _, _ = await _parked_input_task(harness)
    worker = DurableWorker(
        harness["store"],
        PlatformTaskDispatcher(harness["store"], harness["session_factory"]),
        leases=harness["store"].leases,
        wait_reconciler=_input_expirer(harness).expire_once,
    )
    worker.request_drain()
    await worker.reconcile()
    assert (await _record_of(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


@pytest.mark.parametrize("change", ["token", "state"])
async def test_wait_changed_at_commit_is_not_expired(harness, monkeypatch, change):  # noqa: F811
    from hecate_durable.storage.models import TaskStateRow

    _, t_ref, _, _, _ = await _parked_input_task(harness)
    store = harness["store"]
    original = store.apply_task_state

    def changed(task_ref, target, **kwargs):
        if target is TaskLifecycleState.FAILED:
            with store.session_factory.begin() as db:
                row = db.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
                if change == "token":
                    row.extra = {**row.extra, "wait": {**row.extra["wait"], "wait_token": "replacement"}}
                else:
                    row.lifecycle_state = TaskLifecycleState.WAITING_APPROVAL.value
        return original(task_ref, target, **kwargs)

    monkeypatch.setattr(store, "apply_task_state", changed)
    assert await _input_expirer(harness).expire_once() == 0
    assert (await _record_of(harness, t_ref)).extra["wait"]["consumed"] is False
    events = store.read_events(store.run_for_task(t_ref), limit=200).events
    assert not any(event.payload.get("event_type") == "run_terminal" for event in events)


async def test_concurrent_input_and_expiry_commit_one_outcome(harness):  # noqa: F811
    task_id, t_ref, wait, worker, _ = await _parked_input_task(harness)
    expired, issued = await asyncio.gather(
        _input_expirer(harness).expire_once(),
        _issue_input_wake(harness, task_id, wait["wait_token"], {"region": "EU"}, worker=worker),
    )
    record = await _record_of(harness, t_ref)
    revisions = harness["store"].list_input_revisions(t_ref)
    if expired:
        assert record.lifecycle_state is TaskLifecycleState.FAILED
        assert issued.record.state is CommandState.REJECTED
        assert revisions == []
        assert not harness["store"].get_task_input(t_ref).get("_input_wait_acknowledgements")
    else:
        assert issued.record.state is CommandState.APPLIED
        assert len(revisions) == 1
        final = await _dispatch_to_state(harness, t_ref, worker, "succeeded")
        assert final.lifecycle_state is TaskLifecycleState.SUCCEEDED


@pytest.mark.parametrize("on_failure,lose_callback", [("fail", False), ("await", False), ("fail", True)])
async def test_input_expiry_converges_workflow_parent(harness, monkeypatch, on_failure, lose_callback):  # noqa: F811
    """A real input-wait child uses the existing failure policy and outbox retry."""
    from hecate_durable.worker import OutboxRelay

    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.execution.task_control import task_ref_of
    from hecate.execution.workflow_child import WorkflowChildTaskAdapter

    stamp = WorkflowChildTaskAdapter.stamp_step_input

    def controller_child(*args, **kwargs):
        return {
            **stamp(*args, **kwargs),
            "controller_loop": True,
            "controller": {"max_turns": 8, "input_wait_timeout_seconds": 3600},
        }

    monkeypatch.setattr(WorkflowChildTaskAdapter, "stamp_step_input", staticmethod(controller_child))
    port = _ProductionInputPort()
    parent, _ = await _submit_without_dispatch(
        harness,
        port,
        controller_loop=False,
        workflow={"steps": [{"goal": "ask for input"}], "on_failure": on_failure},
    )
    worker = _worker_for(harness)
    parked = await _dispatch_to_state(harness, parent, worker, "waiting_input")
    child = task_ref_of(uuid.UUID(parked.extra["wait"]["contract_ref"]["await_task_ref"]["id"]))
    await _dispatch_to_state(harness, child, worker, "waiting_input")
    calls = list(port.business_calls)
    original = PlatformTaskDispatcher._issue_child_callback
    callback_calls = 0

    async def callback(self, *args, **kwargs):
        nonlocal callback_calls
        callback_calls += 1
        if lose_callback and callback_calls == 1:
            raise RuntimeError("callback lost after terminal commit")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(PlatformTaskDispatcher, "_issue_child_callback", callback)
    assert await _input_expirer(harness).expire_once() == 1
    assert (await _record_of(harness, child)).lifecycle_state is TaskLifecycleState.FAILED
    if lose_callback:
        assert (await _record_of(harness, parent)).lifecycle_state is TaskLifecycleState.WAITING_INPUT
        relay = OutboxRelay(
            harness["store"].session_factory,
            PlatformOutboxProjector(harness["store"], harness["session_factory"]),
            relay_key="input-expiry-callback-recovery",
        )
        await _drain_outbox(relay)
    expected = "failed" if on_failure == "fail" else "reconciliation_required"
    final = await _dispatch_to_state(harness, parent, worker, expected)
    assert final.lifecycle_state.value == expected
    assert port.business_calls == calls
    assert callback_calls >= (2 if lose_callback else 1)
