"""Worker/decision/recovery process body for the approval-decision PG drill.

Runs as ``python -m tests.test_execution._approval_decision_process``: one
REAL process wired against the shared SQL database (platform ORM + durable
store over the same paths the test parent uses). The decision service, the
approval ledger CAS, the durable resume command and the recovery consumer
are the production chain — in separate OS processes the test parent races,
kills and replaces. Only the model and business-tool responses are
scripted. The Controller, action ledger and approval consumption run
through the production assembly. This
module is a process body, never imported by the test module.

Modes (``HECATE_APPROVAL_MODE``):

- ``worker``  — one durable worker claiming from the shared database: it
  parks tasks on the approval signal and drives authorized resumes to
  their terminal, including the wait reconciler cycle.
- ``decide``  — one ``submit_decision`` call from environment facts,
  behind a ready/go file barrier so concurrent processes decide AT the
  same boundary; the last stdout line is a JSON report.
- ``recover`` — one ``ApprovalResumeRecovery.resume_once()`` pass; the
  last stdout line is a JSON report.

Fault point: ``HECATE_APPROVAL_DEATH_MARKER`` turns the resume phase into
a hard ``os._exit(9)`` AFTER the decision committed — the exact crash
window the recovery consumer heals. Connection material travels through
the child environment (``_agent_child_db``), never through argv.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from hecate_runtime.ports import StubRuntimePort


def _append_trace(path: str, event: dict) -> None:
    with open(path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"pid": os.getpid(), **event}, default=str) + "\n")


class _ApprovalPort(StubRuntimePort):
    """Script only the model and business tool behind the governed Controller."""

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        """Propose one governed call, then report its returned result."""
        _append_trace(os.environ["HECATE_APPROVAL_ACTION_TRACE"], {"event": "model_call"})
        if any(message.get("role") == "tool" for message in messages):
            yield {"content": json.dumps({"answer": "stock checked"}), "tool_calls": None}
        else:
            yield {
                "content": None,
                "tool_calls": [
                    {
                        "id": "approval-stock-call",
                        "type": "function",
                        "function": {"name": "query_inventory", "arguments": json.dumps({"sku": "S-9"})},
                    }
                ],
            }

    async def tool_execute(self, name: str, args: dict, context=None):
        """Record the business boundary after production authorization."""
        _append_trace(
            os.environ["HECATE_APPROVAL_ACTION_TRACE"],
            {"event": "business_call", "tool": name, "arguments": args},
        )
        return {"quantity": 3}


def _open_database() -> tuple[Any, Any, Any]:
    """``(store, async_engine, session_factory)`` on the shared database.

    The async engine KEEPS the scoped URL (libpq ``options`` included) so
    the wait reconciler's same-database check sees the URL the durable
    store carries; the asyncpg driver boundary translates the options out
    at connect time — the same wiring the managed wait-resource drill uses.
    """
    from hecate_durable.storage import SqlDurableStore
    from sqlalchemy import event
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.test_execution._agent_child_db import SCHEMA_ENV, durable_dsn

    store = SqlDurableStore(durable_dsn(), source="platform")
    url = make_url(durable_dsn())
    schema = os.environ.get(SCHEMA_ENV)
    async_url = url.set(drivername="postgresql+asyncpg" if schema else "sqlite+aiosqlite")
    engine = create_async_engine(async_url, connect_args={"server_settings": {"search_path": schema}} if schema else {})
    if schema:

        @event.listens_for(engine.sync_engine, "do_connect")
        def _asyncpg_options(_dialect, _connection_record, _args, parameters):
            parameters.pop("options", None)

    return store, engine, async_sessionmaker(engine, expire_on_commit=False)


def _wait_for_go(go_file: str, timeout: float = 300.0) -> None:
    deadline = time.monotonic() + timeout
    while not Path(go_file).exists():
        if time.monotonic() > deadline:
            raise TimeoutError("the decision race barrier was never opened")
        time.sleep(0.05)


def _announce(ready_file: str) -> None:
    Path(ready_file).write_text(str(os.getpid()), encoding="utf-8")


async def _worker() -> None:
    from hecate_durable.worker import DurableWorker, OutboxRelay

    import hecate.execution.task_control as task_control_module
    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    # Fleet-worker mode: the child owns every claim; inline dispatch from
    # the control plane would bypass the process split the drill measures.
    task_control_module._spawn_background = lambda coroutine: coroutine.close()

    store, engine, factory = _open_database()

    def _reconciler_without_resume_recovery() -> Any:
        """The production composite minus the approval-resume consumer.

        The crash-window drills need the decision→resume gap to stay
        pending until the SUBSTITUTE recovery consumer runs; the platform
        composition heals that gap inside the worker cycle instead.
        """
        import hecate.core.database as database_module
        from hecate.execution.agent_child_wake import AgentChildRunReconciler
        from hecate.execution.approval_wait_expiration import standalone_expirer
        from hecate.execution.input_wait_expiration import InputWaitExpirer
        from hecate.execution.timer_wait_wake import TimerWaitWaker

        database_module.async_session_factory = factory

        async def reconcile() -> int:
            processed = 0
            for name, callback in (
                ("approval expiry", standalone_expirer(store=store)),
                ("input expiry", InputWaitExpirer(store, factory).expire_once),
                ("timer wake", TimerWaitWaker(store, factory).wake_once),
                ("agent child", AgentChildRunReconciler(store, factory).converge_once),
            ):
                try:
                    processed += await callback()
                except Exception:  # noqa: BLE001 — one broken pass never blocks the others
                    logging.getLogger(__name__).exception("%s reconciliation pass failed", name)
            return processed

        return reconcile

    def _wait_reconciler() -> Any:
        import hecate.core.database as database_module
        from hecate.execution.approval_wait_expiration import standalone_wait_reconcilers

        database_module.async_session_factory = factory
        if os.environ.get("HECATE_APPROVAL_NO_RESUME_RECOVERY"):
            return _reconciler_without_resume_recovery()
        return standalone_wait_reconcilers(store=store)

    from hecate_runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS, SideEffectClass

    from tests.test_execution._wait_resource_process import _wire

    _BUILTIN_CLASSIFICATIONS["query_inventory"] = SideEffectClass.READONLY
    _wire("approval", _ApprovalPort())
    relay = OutboxRelay(
        store.session_factory,
        PlatformOutboxProjector(store, factory),
        relay_key="platform",
    )
    dispatcher = PlatformTaskDispatcher(store, factory)

    class _ObservedWorker(DurableWorker):
        async def reconcile(self) -> int:
            dispatched = await super().reconcile()
            await relay.pump_once()
            return dispatched

    worker = _ObservedWorker(
        store,
        dispatcher,
        leases=store.leases,
        # The child's first dispatch cold-loads the assembly; a short TTL
        # would expire mid-dispatch and fence the executor before the park.
        lease_ttl=float(os.environ.get("HECATE_APPROVAL_LEASE_TTL", "30")),
        poll_interval=0.2,
        max_attempts=3,
        wait_reconciler=_wait_reconciler(),
    )
    if os.environ.get("HECATE_APPROVAL_PID_FILE"):
        Path(os.environ["HECATE_APPROVAL_PID_FILE"]).write_text(str(os.getpid()), encoding="utf-8")
    if os.environ.get("HECATE_APPROVAL_READY_FILE"):
        _announce(os.environ["HECATE_APPROVAL_READY_FILE"])
    print(f"approval decision worker ready pid={os.getpid()}", flush=True)
    try:
        await worker.run_forever()
    finally:
        await worker.drain()
        await engine.dispose()
        store.dispose()


async def _decide() -> None:
    from hecate.execution.approval_decision import ApprovalDecisionError, ApprovalDecisionService

    store, engine, factory = _open_database()
    if os.environ.get("HECATE_APPROVAL_DEATH_MARKER"):
        marker = os.environ["HECATE_APPROVAL_DEATH_MARKER"]

        async def _die(self, row, task_id, workspace_id) -> dict[str, Any]:
            # The decision row is committed by the time the resume phase
            # runs; dying here leaves the exact recoverable window.
            Path(marker).write_text(str(os.getpid()), encoding="utf-8")
            os._exit(9)

        ApprovalDecisionService._issue_resume = _die
    if os.environ.get("HECATE_APPROVAL_READY_FILE") and os.environ.get("HECATE_APPROVAL_GO_FILE"):
        original_validate = ApprovalDecisionService._validate_wait

        async def _validated_barrier(self, *args, **kwargs):
            await original_validate(self, *args, **kwargs)
            _announce(os.environ["HECATE_APPROVAL_READY_FILE"])
            await asyncio.to_thread(_wait_for_go, os.environ["HECATE_APPROVAL_GO_FILE"])

        ApprovalDecisionService._validate_wait = _validated_barrier
    report: dict[str, Any]
    try:
        async with factory() as db:
            service = ApprovalDecisionService(
                db,
                store=store,
                recorder=store,
                backend="postgres",
                ledger_source="core",
                session_factory=factory,
            )
            result = await service.submit_decision(
                uuid.UUID(os.environ["HECATE_APPROVAL_WORKSPACE_ID"]),
                uuid.UUID(os.environ["HECATE_APPROVAL_TASK_ID"]),
                operation_id=os.environ["HECATE_APPROVAL_OPERATION_ID"],
                approval_ref=os.environ["HECATE_APPROVAL_REF"],
                decision=os.environ["HECATE_APPROVAL_DECISION"],
                reason=os.environ["HECATE_APPROVAL_REASON"],
                wait_token=os.environ["HECATE_APPROVAL_TOKEN"],
                expected_revision=int(os.environ["HECATE_APPROVAL_REVISION"]),
                decided_by=os.environ["HECATE_APPROVAL_DECIDED_BY"],
            )
            await db.commit()
        report = {"ok": True, "result": result}
    except ApprovalDecisionError as exc:
        report = {"ok": False, "code": exc.code, "message": exc.message}
    finally:
        await engine.dispose()
        store.dispose()
    print(json.dumps(report, default=str), flush=True)


async def _recover() -> None:
    from hecate.execution.approval_decision import ApprovalDecisionService, ApprovalResumeRecovery

    store, engine, factory = _open_database()
    if os.environ.get("HECATE_APPROVAL_READY_FILE") and os.environ.get("HECATE_APPROVAL_GO_FILE"):
        original_issue = ApprovalDecisionService._issue_resume

        async def _pending_barrier(self, row, task_id, workspace_id):
            _announce(os.environ["HECATE_APPROVAL_READY_FILE"])
            await asyncio.to_thread(_wait_for_go, os.environ["HECATE_APPROVAL_GO_FILE"])
            return await original_issue(self, row, task_id, workspace_id)

        ApprovalDecisionService._issue_resume = _pending_barrier
    try:
        processed = await ApprovalResumeRecovery(store, factory).resume_once()
    finally:
        await engine.dispose()
        store.dispose()
    print(json.dumps({"ok": True, "processed": processed}, default=str), flush=True)


async def _serve() -> None:
    mode = os.environ["HECATE_APPROVAL_MODE"]
    if mode == "worker":
        await _worker()
    elif mode == "decide":
        await _decide()
    elif mode == "recover":
        await _recover()
    else:
        raise SystemExit(f"unknown HECATE_APPROVAL_MODE {mode!r}")


def main() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    print(
        f"approval-decision process start pid={os.getpid()} mode={os.environ.get('HECATE_APPROVAL_MODE')}",
        flush=True,
    )
    asyncio.run(_serve())


if __name__ == "__main__":
    main()
