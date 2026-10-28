"""Persistent convergence of expired platform approval waits (step6).

A real approval park (binding mint, wait token, park TTL) is driven to
expiry through the expirer the durable worker runs on its reconcile
cycle: the task converges to ``failed`` with the wait's stop category,
the token is consumed while the approval reference and recorded facts
survive, the run projection and the outbox terminal event carry the
fact, and nothing re-executes. Races with legal resumes, cancels and
repeated scans share the revision CAS; unverifiable authorities park for
reconciliation instead of guessing; out-of-scope waits (no deadline,
waiting_input, foreign issuer, consumed) are left untouched.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from tests.test_execution.test_approval_wait_park import (  # noqa: F401 - fixture registration
    WS,
    _binding_of,
    _decide,
    _dispatcher,
    _issue_resume,
    _parked_task,
    _service,
    _submit,
    _task_detail,
    _task_result,
    _wait_for_state,
    harness,
)

_EXPIRED_CLOCK = lambda: datetime.now(UTC) + timedelta(hours=2)  # noqa: E731 - test clock


def _expirer(harness, **overrides: Any) -> Any:  # noqa: F811
    from hecate.execution.approval_wait_expiration import ApprovalWaitExpirer

    kwargs = {"clock": _EXPIRED_CLOCK}
    kwargs.update(overrides)
    return ApprovalWaitExpirer(harness["store"], harness["session_factory"], **kwargs)


async def _converged_record(harness, t_ref) -> Any:  # noqa: F811
    return await asyncio.to_thread(harness["store"].get_task_state, t_ref)


async def test_expired_wait_converges_without_reexecution(harness) -> None:  # noqa: F811
    """Expiry fails the task, consumes the token, preserves the facts."""
    task_uuid, t_ref, detail, _worker, stub = await _parked_task(harness)
    assert detail["lifecycle_state"] == "waiting_approval"
    wait = detail["wait"]
    approval_ref = wait["contract_ref"]["approval_ref"]
    calls_before = len(stub.calls)

    converged = await _expirer(harness).expire_once()
    assert converged == 1

    record = await _converged_record(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    result = (record.extra or {})["result"]
    assert result["error_category"] == "approval_wait_expired"
    # Token consumed; approval reference and deadline preserved verbatim.
    assert record.extra["wait"]["consumed"] is True
    assert record.extra["wait"]["contract_ref"]["approval_ref"] == approval_ref
    assert record.extra["wait"]["wait_expires_at"] == wait["wait_expires_at"]
    # The authority record survives untouched (still pending, not decided).
    binding = await _binding_of(harness, approval_ref)
    assert binding is not None and binding.judgment.value == "pending"
    # Zero model/tool re-execution.
    assert len(stub.calls) == calls_before
    # The platform run projection carries the terminal fact.
    run_detail = await _task_detail(harness, task_uuid)
    assert run_detail["runs"][0]["projection"]["state"] == "failed"
    assert run_detail["runs"][0]["projection"]["error_category"] == "approval_wait_expired"
    # The terminal fact rides the transactional outbox exactly once.
    events = harness["store"].read_events(harness["store"].run_for_task(t_ref), limit=100).events
    terminals = [e for e in events if e.payload.get("event_type") == "run_terminal"]
    assert len(terminals) == 1
    assert terminals[0].payload["error_category"] == "approval_wait_expired"


async def test_unexpired_wait_is_left_alone(harness) -> None:  # noqa: F811
    """Before the deadline the scan claims nothing."""
    _task_uuid, t_ref, _detail, _worker, _stub = await _parked_task(harness)
    converged = await _expirer(harness, clock=lambda: datetime.now(UTC)).expire_once()
    assert converged == 0
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_approved_but_unconsumed_wait_expires_without_executing(harness) -> None:  # noqa: F811
    """An approval that never woke still cannot execute after expiry."""
    task_uuid, t_ref, detail, _worker, stub = await _parked_task(harness)
    ref = detail["wait"]["contract_ref"]["approval_ref"]
    await _decide(harness, ref, approved=True, reason="late, never consumed")

    assert await _expirer(harness).expire_once() == 1
    record = await _converged_record(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert record.extra["result"]["error_category"] == "approval_wait_expired"
    assert len(stub.calls) == 1  # only the original park dispatch


async def test_expired_wait_rejects_late_wakes_and_commands(harness) -> None:  # noqa: F811
    """After convergence a late resume cannot reopen the task."""
    task_uuid, t_ref, detail, worker, _stub = await _parked_task(harness)
    token = detail["wait"]["wait_token"]
    await _expirer(harness).expire_once()

    issued = await _issue_resume(
        harness, task_uuid, token, worker, payload={"approval_ref": detail["wait"]["contract_ref"]["approval_ref"]}
    )
    assert issued.record.state is CommandState.REJECTED
    record = await _converged_record(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.FAILED  # never reopened


async def test_rejected_decision_keeps_its_real_verdict_at_expiry(harness) -> None:  # noqa: F811
    """Expiry never rewrites a human rejection into an expiry verdict."""
    task_uuid, t_ref, detail, _worker, _stub = await _parked_task(harness)
    ref = detail["wait"]["contract_ref"]["approval_ref"]
    # Decide rejected WITHOUT waking (the wake path that would normally
    # converge immediately is bypassed to exercise the expiry backstop).
    await _decide(harness, ref, approved=False, reason="no")

    assert await _expirer(harness).expire_once() == 1
    record = await _converged_record(harness, t_ref)
    assert record.extra["result"]["error_category"] == "approval_rejected"


async def test_legal_resume_wins_the_cas(harness) -> None:  # noqa: F811
    """A resume that moved the task first keeps its fact."""
    task_uuid, t_ref, detail, worker, stub = await _parked_task(harness)
    ref = detail["wait"]["contract_ref"]["approval_ref"]
    await _decide(harness, ref, approved=True, reason="approved")
    issued = await _issue_resume(
        harness, task_uuid, detail["wait"]["wait_token"], worker, payload={"approval_ref": ref}
    )
    assert issued.record.state is CommandState.APPLIED

    # The expired clock would expire the OLD wait; the task has moved on.
    assert await _expirer(harness).expire_once() == 0
    state = await _wait_for_state(harness, t_ref, "succeeded", "failed", worker=worker)
    assert state == "succeeded"  # resumed attempt ran the approved action


async def test_cancelled_wait_is_not_expired(harness) -> None:  # noqa: F811
    """A cancel that landed first stands; the scan claims nothing."""
    task_uuid, t_ref, _detail, worker, _stub = await _parked_task(harness)
    async with harness["session_factory"]() as db:
        service = _service(harness, db, worker=worker)
        await service.issue_command(
            workspace_id=WS,
            task_id=task_uuid,
            kind=ControlCommandKind.CANCEL,
            issuer="tester",
        )
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.CANCELLED
    assert await _expirer(harness).expire_once() == 0


async def test_repeated_and_concurrent_scans_converge_once(harness) -> None:  # noqa: F811
    """Idempotent passes and racing workers share the revision CAS."""
    _task_uuid, t_ref, _detail, _worker, _stub = await _parked_task(harness)
    expirer = _expirer(harness)
    assert await expirer.expire_once() == 1
    assert await expirer.expire_once() == 0

    _task_uuid2, t_ref2, _detail2, _worker2, _stub2 = await _parked_task(harness)
    results = await asyncio.gather(
        _expirer(harness).expire_once(), _expirer(harness).expire_once(), return_exceptions=True
    )
    total = sum(r for r in results if isinstance(r, int))
    assert total == 1
    record = await _converged_record(harness, t_ref2)
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    assert record.extra["wait"]["consumed"] is True


async def test_worker_reconcile_cycle_drives_the_expirer(harness) -> None:  # noqa: F811
    """The injected reconciler runs on the worker's own cycle."""
    from hecate_durable.worker import DurableWorker

    _task_uuid, t_ref, _detail, _worker, _stub = await _parked_task(harness)
    worker = DurableWorker(
        harness["store"],
        _dispatcher(harness),
        leases=harness["store"].leases,
        wait_reconciler=_expirer(harness).expire_once,
    )
    await worker.reconcile()
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.FAILED


async def test_worker_without_reconciler_keeps_waiting_tasks(harness) -> None:  # noqa: F811
    """Unset reconciler = the exact prior behavior: waits are not touched."""
    from hecate_durable.worker import DurableWorker

    _task_uuid, t_ref, _detail, _worker, _stub = await _parked_task(harness)
    worker = DurableWorker(harness["store"], _dispatcher(harness), leases=harness["store"].leases)
    await worker.reconcile()
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_missing_authority_parks_for_reconciliation(harness) -> None:  # noqa: F811
    """A wait whose authority record is gone is reconciled, not guessed."""
    _task_uuid, t_ref, detail, _worker, _stub = await _parked_task(harness)
    ref = detail["wait"]["contract_ref"]["approval_ref"]
    async with harness["session_factory"]() as db:
        from sqlalchemy import delete

        from hecate.models.approval import ApprovalAuthorityModel

        await db.execute(delete(ApprovalAuthorityModel).where(ApprovalAuthorityModel.approval_ref == ref))
        await db.commit()

    assert await _expirer(harness).expire_once() == 1
    record = await _converged_record(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert "missing authority" in record.extra["reconciliation_reason"]


async def test_untrusted_authority_time_parks_for_reconciliation(harness, monkeypatch) -> None:  # noqa: F811
    """An authority whose time cannot be verified is never guessed expired."""
    from hecate.enterprise.approval_wait_ledger import TaskApprovalLedger

    async def untrusted(self, ref):
        raise ValueError("local clock disagrees with approval authority time")

    monkeypatch.setattr(TaskApprovalLedger, "get", untrusted)
    _task_uuid, t_ref, _detail, _worker, _stub = await _parked_task(harness)
    assert await _expirer(harness).expire_once() == 1
    record = await _converged_record(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert "could not be verified" in record.extra["reconciliation_reason"]


async def _foreign_waiting_task(harness) -> None:  # noqa: F811
    """A waiting_approval row issued by a foreign host, in the same store."""

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
        TaskLifecycleState.WAITING_APPROVAL,
        extra_update={
            "wait": {
                "wake_kind": "resume",
                "contract_ref": {"approval_ref": "task:foreign:ref"},
                "wait_token": "tok",
                "wait_expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "consumed": False,
            }
        },
    )
    return t


async def test_foreign_host_waits_are_never_converged(harness) -> None:  # noqa: F811
    """The platform does not declare terminals for another host's tasks."""
    t = await _foreign_waiting_task(harness)
    assert await _expirer(harness).expire_once() == 0
    assert (await _converged_record(harness, t)).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_wait_without_deadline_keeps_its_meaning(harness) -> None:  # noqa: F811
    """No declared deadline: the wait stays resumable, expiry invented never."""
    from hecate_durable.storage.models import TaskStateRow

    task_uuid, t_ref, _detail, _worker, _stub = await _parked_task(harness)
    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        extra = dict(row.extra or {})
        extra["wait"] = {**extra["wait"], "wait_expires_at": None}
        row.extra = extra
        session.commit()

    assert await _expirer(harness).expire_once() == 0
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_consumed_wait_is_skipped(harness) -> None:  # noqa: F811
    """A consumed token belongs to a moved-on task; the scan claims nothing."""
    from hecate_durable.storage.models import TaskStateRow

    task_uuid, t_ref, _detail, _worker, _stub = await _parked_task(harness)
    with harness["store"].session_factory() as session:
        row = session.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        extra = dict(row.extra or {})
        extra["wait"] = {**extra["wait"], "consumed": True}
        row.extra = extra
        session.commit()

    assert await _expirer(harness).expire_once() == 0
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_waiting_input_waits_are_out_of_scope(harness) -> None:  # noqa: F811
    """waiting_input (and any provide_input wait) is not this slice's scope."""
    from hecate.contracts.execution.durable import IdempotencyKey
    from hecate.contracts.execution.references import run_ref, task_ref

    t = task_ref("hecate", f"input-{uuid.uuid4().hex[:8]}")
    r = run_ref("hecate", f"irun-{uuid.uuid4().hex[:8]}")
    harness["store"].submit_task(
        key=IdempotencyKey(key=t.id, subject="s", workspace=str(WS), request_digest="i" * 8),
        task_ref=t,
        run_ref=r,
        input_payload={"goal": "input wait", "workspace_id": str(WS)},
    )
    harness["store"].apply_task_state(t, TaskLifecycleState.RUNNING)
    harness["store"].apply_task_state(
        t,
        TaskLifecycleState.WAITING_INPUT,
        extra_update={
            "wait": {
                "wake_kind": "provide_input",
                "contract_ref": {},
                "wait_token": "tok",
                "wait_expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
                "consumed": False,
            }
        },
    )
    assert await _expirer(harness).expire_once() == 0
    assert (await _converged_record(harness, t)).lifecycle_state is TaskLifecycleState.WAITING_INPUT


async def test_projection_loss_recovers_through_the_relay(harness, monkeypatch) -> None:  # noqa: F811
    """Terminal committed, direct projection lost: the relay rebuilds it."""
    from hecate_durable.worker import OutboxRelay

    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.execution.task_run_registry import TaskRunRegistry

    task_uuid, t_ref, _detail, _worker, _stub = await _parked_task(harness)

    async def down(self, *args, **kwargs):  # noqa: ANN001, ANN003
        raise RuntimeError("projection write lost")

    monkeypatch.setattr(TaskRunRegistry, "finalize_run", down)
    assert await _expirer(harness).expire_once() == 1  # terminal still committed
    monkeypatch.undo()

    projector = PlatformOutboxProjector(harness["store"], harness["session_factory"])
    relay = OutboxRelay(harness["store"].session_factory, projector, relay_key="expiry-test")
    await relay.pump_once()

    detail = await _task_detail(harness, task_uuid)
    assert detail["lifecycle_state"] == "failed"
    assert detail["runs"][0]["projection"]["state"] == "failed"
    assert detail["runs"][0]["projection"]["error_category"] == "approval_wait_expired"
    # Repeated relays never duplicate the terminal fact.
    assert await relay.pump_once() == 0


@pytest.mark.parametrize("ownership", ["remote", "imported", "host_owner"])
async def test_platform_issuer_does_not_imply_local_execution_ownership(harness, ownership):  # noqa: F811
    """An issued platform ID is also used for host-owned tasks."""
    from sqlalchemy import select

    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel
    from hecate.models.run import RunModel, RunOrigin

    _, t_ref, _, _, _ = await _parked_task(harness)
    async with harness["session_factory"]() as db:
        run = (await db.execute(select(RunModel))).scalars().one()
        if ownership == "remote":
            deployment = await db.get(AgentDeploymentModel, run.deployment_id)
            deployment.access_mode = AccessMode.REMOTE_SERVICE
        elif ownership == "imported":
            run.origin = RunOrigin.IMPORTED_OBSERVATION
        else:
            run.control_owner = "host"
        await db.commit()
    assert await _expirer(harness).expire_once() == 0
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_binding_from_another_action_cannot_expire_the_wait(harness):  # noqa: F811
    from sqlalchemy import update

    from hecate.models.approval import ApprovalAuthorityModel

    _, t_ref, detail, _, _ = await _parked_task(harness)
    async with harness["session_factory"]() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(ApprovalAuthorityModel.approval_ref == detail["wait"]["contract_ref"]["approval_ref"])
            .values(tool_name="another-action")
        )
        await db.commit()
    assert await _expirer(harness).expire_once() == 1
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED


async def test_bounded_scan_does_not_starve_later_expired_waits(harness):  # noqa: F811
    from hecate_durable.storage.models import TaskStateRow
    from sqlalchemy import update

    from hecate.models.approval import ApprovalAuthorityModel

    _, first, detail, _, _ = await _parked_task(harness)
    future = datetime.now(UTC) + timedelta(days=1)
    with harness["store"].session_factory.begin() as db:
        row = db.get(TaskStateRow, (first.issuer_domain, first.id))
        row.extra = {**row.extra, "wait": {**row.extra["wait"], "wait_expires_at": future.isoformat()}}
    async with harness["session_factory"]() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(ApprovalAuthorityModel.approval_ref == detail["wait"]["contract_ref"]["approval_ref"])
            .values(expires_at=future)
        )
        await db.commit()
    _, later, _, _, _ = await _parked_task(harness)
    expirer = _expirer(harness, batch=1)
    assert await expirer.expire_once() == 0
    assert await expirer.expire_once() == 1
    assert (await _converged_record(harness, later)).lifecycle_state is TaskLifecycleState.FAILED


@pytest.mark.parametrize("clock_problem", [None, "wall clock diverged"])
async def test_wait_expiring_after_wake_gate_is_rejected_at_commit(harness, monkeypatch, clock_problem):  # noqa: F811
    from hecate_governance.clock import TrustedClock

    task_id, t_ref, detail, worker, stub = await _parked_task(harness)
    ref = detail["wait"]["contract_ref"]["approval_ref"]
    await _decide(harness, ref, approved=True, reason="valid at the gate")
    store = harness["store"]
    apply = store.apply_task_state

    def delayed(task_ref, target, **kwargs):
        if target is TaskLifecycleState.QUEUED:
            monkeypatch.setattr(TrustedClock, "trusted_now", lambda *args: (_EXPIRED_CLOCK(), clock_problem))
        return apply(task_ref, target, **kwargs)

    monkeypatch.setattr(store, "apply_task_state", delayed)
    issued = await _issue_resume(harness, task_id, detail["wait"]["wait_token"], worker)
    assert issued.record.state is CommandState.REJECTED
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
    assert len(stub.calls) == 1


async def test_expiry_rechecks_current_wait_under_the_commit_lock(harness, monkeypatch):  # noqa: F811
    from hecate_durable.storage.models import TaskStateRow

    _, t_ref, _, _, _ = await _parked_task(harness)
    store = harness["store"]
    apply = store.apply_task_state

    def changed_wait(task_ref, target, **kwargs):
        if target is TaskLifecycleState.FAILED:
            with store.session_factory.begin() as db:
                row = db.get(TaskStateRow, (task_ref.issuer_domain, task_ref.id))
                row.extra = {**row.extra, "wait": {**row.extra["wait"], "wait_token": "replacement-wait"}}
        return apply(task_ref, target, **kwargs)

    monkeypatch.setattr(store, "apply_task_state", changed_wait)
    assert await _expirer(harness).expire_once() == 0
    record = await _converged_record(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
    assert record.extra["wait"]["wait_token"] == "replacement-wait"


async def test_default_clock_divergence_never_fabricates_expiry(harness, monkeypatch):  # noqa: F811
    from hecate_governance.clock import TrustedClock

    _, t_ref, _, _, _ = await _parked_task(harness)
    monkeypatch.setattr(TrustedClock, "trusted_now", lambda *args: (_EXPIRED_CLOCK(), "wall clock diverged"))
    assert await _expirer(harness, clock=None).expire_once() == 1
    record = await _converged_record(harness, t_ref)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert record.extra["result"]["error_category"] == "approval_wait_unverifiable"


async def test_real_process_restart_expires_a_preexisting_wait(harness):  # noqa: F811
    """A new process reconstructs the binding clock and retained wait."""
    import sys

    from hecate_durable.storage.models import TaskStateRow
    from sqlalchemy import update

    from hecate.models.approval import ApprovalAuthorityModel

    _, t_ref, detail, _, stub = await _parked_task(harness)
    expired = datetime.now(UTC) - timedelta(minutes=1)
    with harness["store"].session_factory.begin() as db:
        row = db.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": {**row.extra["wait"], "wait_expires_at": expired.isoformat()}}
    async with harness["session_factory"]() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(ApprovalAuthorityModel.approval_ref == detail["wait"]["contract_ref"]["approval_ref"])
            .values(expires_at=expired)
        )
        await db.commit()
    script = """
import asyncio, sys
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from hecate_durable.storage import SqlDurableStore
from hecate.execution.approval_wait_expiration import ApprovalWaitExpirer
async def main():
    store = SqlDurableStore(sys.argv[1])
    engine = create_async_engine(sys.argv[1].replace("sqlite:", "sqlite+aiosqlite:", 1))
    try:
        print(await ApprovalWaitExpirer(store, async_sessionmaker(engine, expire_on_commit=False)).expire_once())
    finally:
        await engine.dispose()
        store.dispose()
asyncio.run(main())
"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        harness["sync_url"],
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), 45)
    assert proc.returncode == 0, stderr.decode()
    assert stdout.decode().strip() == "1"
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.FAILED
    assert len(stub.calls) == 1


async def test_standalone_factory_uses_worker_database_and_rejects_mismatch(harness, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from sqlalchemy.engine import make_url

    from hecate.execution.approval_wait_expiration import standalone_expirer

    monkeypatch.setattr("hecate.core.database.async_session_factory", harness["session_factory"])
    reconciler = standalone_expirer(store=harness["store"])
    assert reconciler.__self__._store is harness["store"]
    with pytest.raises(ValueError, match="same SQL database"):
        standalone_expirer(store=SimpleNamespace(engine=SimpleNamespace(url=make_url("sqlite:///other.db"))))


async def test_managed_delivery_with_platform_issuer_is_untouched(harness):  # noqa: F811
    from sqlalchemy import select

    from hecate.execution.task_dispatcher import run_row_ref
    from hecate.models.managed_delivery import ManagedDeliveryModel
    from hecate.models.run import RunModel
    from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

    _, t_ref, _, _, _ = await _parked_task(harness)
    async with harness["session_factory"]() as db:
        run = (await db.execute(select(RunModel))).scalars().one()
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
    assert await _expirer(harness).expire_once() == 0
    assert (await _converged_record(harness, t_ref)).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_legacy_wait_without_token_deadline_still_accepts_valid_approval(harness):  # noqa: F811
    """No wait deadline retains the original wake contract, without inventing one."""
    from hecate_durable.storage.models import TaskStateRow

    task_id, t_ref, detail, worker, _ = await _parked_task(harness)
    ref = detail["wait"]["contract_ref"]["approval_ref"]
    with harness["store"].session_factory.begin() as db:
        row = db.get(TaskStateRow, (t_ref.issuer_domain, t_ref.id))
        row.extra = {**row.extra, "wait": {**row.extra["wait"], "wait_expires_at": None}}
    await _decide(harness, ref, approved=True, reason="legacy approval")
    issued = await _issue_resume(harness, task_id, detail["wait"]["wait_token"], worker)
    assert issued.record.state is CommandState.APPLIED
