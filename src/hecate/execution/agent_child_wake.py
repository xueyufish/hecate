"""Persistent convergence of platform Agent child waits.

An Agent child call parks the parent task in ``waiting_input`` with
``wake_kind: "agent_child"``, bound to the submitted child task and the
accepting action's execution id. The normal wake is the child's own
verified terminal callback. This module is the durable worker's named
wait consumer that converges the cases no callback will ever deliver:

- the child reached a terminal state but the callback was lost (a crash
  between the child's terminal commit and the wake): the reconciliation
  pass re-issues the SAME verified callback from the platform's records —
  idempotent by its deterministic command id, so repeated and concurrent
  scans consume exactly once;
- the parent's own task deadline expired while the child is still
  running: the child receives a durable cancel request (a request, not a
  stop fact) and the parent converges to ``failed`` with
  ``agent_child_deadline`` under the shared wait commit gate — exactly
  one of {verified callback, deadline convergence, cancel, manual
  command} lands.

Corrupt contracts, missing tokens and unverifiable ownership converge to
explicit reconciliation. Manual ``provide_input`` never consumes an agent
child wait, and foreign or non-local executions are out of scope by
construction.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from hecate.contracts.execution.durable import InvalidTaskTransitionError, TaskLifecycleState
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.execution.agent_child import _QueuedChildWorker, agent_child_contract, verify_child_call
from hecate.execution.approval_wait_expiration import verify_platform_local

logger = logging.getLogger(__name__)

RECONCILIATION_CATEGORY = "agent_child_unverifiable"
AGENT_CHILD_DEADLINE = "agent_child_deadline"


class AgentChildRunReconciler:
    """Bounded convergence pass for platform Agent child waits."""

    def __init__(
        self,
        store: Any,
        session_factory: Any,
        *,
        platform_issuer: str = "hecate",
        batch: int = 32,
        clock: Any | None = None,
    ) -> None:
        self._store = store
        self._session_factory = session_factory
        self._platform_issuer = platform_issuer
        if batch < 1:
            raise ValueError("agent child batch must be positive")
        self._batch = batch
        self._clock = clock
        self._cursor: tuple[str, str, str] | None = None

    async def converge_once(self) -> int:
        """One bounded keyset pass; returns the number of converged waits."""

        records, self._cursor = await asyncio.to_thread(
            self._store.list_task_page,
            {TaskLifecycleState.WAITING_INPUT},
            after=self._cursor,
            limit=self._batch,
        )
        converged = 0
        for record in records:
            try:
                if await self._converge_one(record):
                    converged += 1
            except Exception:  # noqa: BLE001 — one bad record must not block the pass
                logger.warning("agent child convergence failed for task %s", record.task_ref.id, exc_info=True)
        return converged

    async def _converge_one(self, record: Any) -> bool:
        t_ref = record.task_ref
        if t_ref.issuer_domain != self._platform_issuer:
            # A foreign host owns the execution facts; the platform never
            # moves its tasks.
            return False
        wait = (record.extra or {}).get("wait")
        if not isinstance(wait, dict) or wait.get("wake_kind") != "agent_child":
            return False
        contract = agent_child_contract(wait.get("contract_ref"))
        async with self._session_factory() as db:
            # Ownership gates every write, including corruption convergence.
            if not await verify_platform_local(db, self._store, t_ref, record):
                return False
            if contract is None:
                return await self._park_for_reconciliation(t_ref, record, "agent child contract is unreadable")
            if (
                wait.get("consumed") is not False
                or not isinstance(wait.get("wait_token"), str)
                or not wait["wait_token"]
            ):
                return await self._park_for_reconciliation(t_ref, record, "agent child wait token is invalid")
            try:
                await verify_child_call(self._store, t_ref, contract, wait=wait)
            except (KeyError, TypeError, ValueError) as exc:
                return await self._park_for_reconciliation(t_ref, record, str(exc))
            child_raw = contract.get("child_task_ref") or {}
            if not child_raw.get("id"):
                return await self._park_for_reconciliation(t_ref, record, "agent child reference is missing")
            child_ref = BackendRef(RefKind.TASK, str(child_raw.get("issuer_domain")), str(child_raw.get("id")))
            child_record = await asyncio.to_thread(self._store.get_task_state, child_ref)
            child_state = child_record.lifecycle_state.value if child_record else None
            if child_state is None:
                # A missing child is corruption, not a pending call: the
                # acceptance recorded a task that no longer resolves.
                return await self._park_for_reconciliation(t_ref, record, "agent child task is missing")
            deadline = _parse(wait.get("wait_expires_at"))
            if wait.get("wait_expires_at") is not None and deadline is None:
                return await self._park_for_reconciliation(t_ref, record, "agent child deadline is malformed")
            try:
                now = self._clock() if self._clock is not None else await asyncio.to_thread(self._store.wait_now)
                if not isinstance(now, datetime) or now.tzinfo is None:
                    raise ValueError("agent child clock must be timezone-aware")
            except (TypeError, ValueError):
                return await self._park_for_reconciliation(t_ref, record, "agent child clock cannot be trusted")
            if deadline is not None and deadline <= now:
                return await self._converge_deadline(db, t_ref, record, wait, child_ref)
            if child_state == "reconciliation_required":
                return await self._park_for_reconciliation(t_ref, record, "agent child requires reconciliation")
            if child_state in ("succeeded", "failed", "cancelled"):
                return await self._reissue_callback(db, t_ref, record, wait, contract, child_state)
            return False

    async def _reissue_callback(
        self, db: Any, t_ref: BackendRef, record: Any, wait: dict[str, Any], contract: dict[str, Any], state: str
    ) -> bool:
        from hecate.execution.task_control import TaskControlService

        service = TaskControlService(
            db,
            store=self._store,
            recorder=self._store,
            backend="postgres",
            ledger_source="core",
            session_factory=self._session_factory,
            dispatcher=None,
            worker=_QueuedChildWorker(),
        )
        token = str(wait.get("wait_token"))
        receipt = await service.submit_agent_child_callback(
            workspace_id=await self._workspace_of(t_ref, record),
            task_id=await self._task_id_of(db, t_ref, record),
            command_id=f"agent-child-callback-{contract.get('call_id')}-{token}",
            wait_token=token,
            child_task_id=self._uuid_of(str(contract["child_task_ref"]["id"])),
            declared_state=state,
        )
        if receipt.record.state.value == "applied":
            logger.info("task %s agent child callback recovered; requeued", t_ref.id)
            return True
        if receipt.record.state.value == "rejected":
            # The verification refused (corrupt facts): park instead of
            # retrying the same refusal forever.
            return await self._park_for_reconciliation(
                t_ref, record, f"agent child callback verification refused: {receipt.detail}"
            )
        return False

    async def _converge_deadline(
        self, db: Any, t_ref: BackendRef, record: Any, wait: dict[str, Any], child_ref: BackendRef
    ) -> bool:
        """Fail the expired parent under the shared gate; request the child cancel."""

        outcome = {
            "status": "failed",
            "content": "",
            "error": AGENT_CHILD_DEADLINE,
            "error_category": AGENT_CHILD_DEADLINE,
        }
        run_ref = await asyncio.to_thread(self._store.run_for_task, t_ref)
        try:
            await asyncio.to_thread(
                self._store.apply_task_state,
                t_ref,
                TaskLifecycleState.FAILED,
                expected_revision=record.revision,
                expected_wait=wait,
                expected_wait_state=TaskLifecycleState.WAITING_INPUT,
                wait_expired=True,
                wait_clock=self._clock,
                extra_update={"wait": {**wait, "consumed": True}, "result": outcome},
                **(
                    {
                        "event_run_ref": run_ref,
                        "terminal_payload": outcome,
                    }
                    if hasattr(self._store, "engine") and run_ref is not None
                    else {}
                ),
            )
        except (InvalidTaskTransitionError, ValueError):
            # A verified callback, a cancel or another scan moved the task
            # first; their fact stands and this scan claims nothing.
            logger.info("task %s agent child deadline convergence skipped (moved concurrently)", t_ref.id)
            return False
        logger.info("task %s converged: %s", t_ref.id, AGENT_CHILD_DEADLINE)
        await self._request_child_cancel(db, t_ref, wait, child_ref)
        await self._finalize_run_projection(db, t_ref, record, AGENT_CHILD_DEADLINE)
        return True

    async def _request_child_cancel(
        self, db: Any, t_ref: BackendRef, wait: dict[str, Any], child_ref: BackendRef
    ) -> None:
        """Persist a cancel REQUEST for the still-running child; never fake a stop."""

        from hecate.contracts.execution.durable import ControlCommandKind
        from hecate.execution.task_control import TaskControlService

        try:
            service = TaskControlService(
                db,
                store=self._store,
                recorder=self._store,
                backend="postgres",
                ledger_source="core",
                session_factory=self._session_factory,
                dispatcher=None,
                worker=None,
            )
            await service.issue_command(
                workspace_id=await self._workspace_of(t_ref, None),
                task_id=self._uuid_of(str(child_ref.id)),
                kind=ControlCommandKind.CANCEL,
                issuer="agent-child-deadline",
                command_id=f"agent-child-cancel:{t_ref.id}:{(wait.get('contract_ref') or {}).get('call_id')}",
            )
        except Exception:  # noqa: BLE001 — the parent's convergence never depends on propagation
            logger.warning("task %s agent child cancel request failed", t_ref.id, exc_info=True)

    async def _finalize_run_projection(self, db: Any, t_ref: BackendRef, record: Any, category: str) -> None:
        run_ref = await asyncio.to_thread(self._store.run_for_task, t_ref)
        workspace_raw = (record.extra or {}).get("workspace_id")
        if run_ref is None or not workspace_raw:
            return
        try:
            from hecate.execution.task_run_registry import TaskRunRegistry

            await TaskRunRegistry(db).finalize_run(
                self._uuid_of(str(run_ref.id)),
                self._uuid_of(str(workspace_raw)),
                state="failed",
                error_category=category,
            )
            await db.commit()
        except Exception:  # noqa: BLE001 — the relay re-projects the terminal fact
            logger.warning("task %s agent child deadline run projection skipped", t_ref.id, exc_info=True)
            await db.rollback()

    async def _park_for_reconciliation(self, t_ref: BackendRef, record: Any, reason: str) -> bool:
        outcome = {
            "status": "awaiting_reconciliation",
            "content": "",
            "error": reason,
            "error_category": RECONCILIATION_CATEGORY,
        }
        try:
            await asyncio.to_thread(
                self._store.apply_task_state,
                t_ref,
                TaskLifecycleState.RECONCILIATION_REQUIRED,
                expected_revision=record.revision,
                extra_update={"reconciliation_reason": reason, "result": outcome},
            )
        except (InvalidTaskTransitionError, ValueError):
            return False
        logger.warning("task %s parked for reconciliation: %s", t_ref.id, reason)
        return True

    async def _workspace_of(self, t_ref: BackendRef, record: Any) -> Any:
        if record is not None and (record.extra or {}).get("workspace_id"):
            return self._uuid_of(str(record.extra["workspace_id"]))
        payload = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
        return self._uuid_of(str(payload.get("workspace_id")))

    async def _task_id_of(self, db: Any, t_ref: BackendRef, record: Any) -> Any:
        return self._uuid_of(str(t_ref.id))

    @staticmethod
    def _uuid_of(raw: str) -> Any:
        import uuid

        return uuid.UUID(raw)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
