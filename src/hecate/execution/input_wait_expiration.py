"""Persistent expiration of platform-local human input waits (step6).

An ordinary input wait parks the task in ``waiting_input`` after a tool
declared the explicit ``input_required`` contract. Without a declared
timeout the wait keeps its historical meaning — resumable until someone
resolves it. With ``controller.input_wait_timeout_seconds`` declared, the
wait carries a deadline anchored to the durable receipt time of the
input-requesting action's first transactional receipt event, so the crash
window between the outcome commit and the wait commit never restarts the
clock: a recovered park re-derives the same absolute ``wait_expires_at``.

The expirer is the durable worker's second named wait consumer, sharing
the approval expirer's shape: one bounded, keyset-paged pass; only
platform-locally owned executions (shared ``verify_platform_local``);
expiry judged under the row-lock commit re-verification the wake and
cancel paths share, so expiry, cancel and ``provide_input`` land exactly
one legal outcome. Convergence fails the task with
``input_wait_expired``, consumes the wait token, and preserves the
request, deadline and action facts — it never fabricates an input
revision, never claims the request was answered, and never re-invokes a
model or tool. Workflow child-result waits (``await_task_ref``), approval
waits, waits without a declared deadline and foreign executions are out
of scope by construction.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from hecate.contracts.execution.durable import InvalidTaskTransitionError, TaskLifecycleState
from hecate.contracts.execution.references import BackendRef
from hecate.execution.approval_wait_expiration import converge_workflow_parent, verify_platform_local
from hecate.execution.task_input import plain_human_input_wait

logger = logging.getLogger(__name__)

INPUT_WAIT_EXPIRED = "input_wait_expired"
RECONCILIATION_CATEGORY = "input_wait_unverifiable"


class InputWaitExpirer:
    """Bounded convergence pass for expired platform human input waits."""

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
            raise ValueError("expiry batch must be positive")
        self._batch = batch
        self._clock = clock
        self._cursor: tuple[str, str, str] | None = None

    async def expire_once(self) -> int:
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
                if await self._expire_one(record):
                    converged += 1
            except Exception:  # noqa: BLE001 — one bad record must not block the pass
                logger.warning("input wait expiry failed for task %s", record.task_ref.id, exc_info=True)
        return converged

    async def _expire_one(self, record: Any) -> bool:
        t_ref = record.task_ref
        if t_ref.issuer_domain != self._platform_issuer:
            # A foreign host owns the execution facts; the platform never
            # declares a terminal state on its behalf.
            return False
        wait = (record.extra or {}).get("wait") or {}
        if (
            wait.get("consumed")
            or wait.get("wake_kind") != "provide_input"
            or not isinstance(wait.get("wait_token"), str)
            or not wait["wait_token"]
        ):
            return False
        contract = wait.get("contract_ref") or {}
        if not plain_human_input_wait(contract):
            # Workflow child-result waits settle through their parent's own
            # mechanism; approval waits belong to the approval reconciler.
            return False
        if wait.get("wait_expires_at") is None:
            return False  # no declared deadline: the existing meaning stands

        async with self._session_factory() as db:
            if not await verify_platform_local(db, self._store, t_ref, record):
                return False
            deadline = _parse(wait.get("wait_expires_at"))
            if deadline is None:
                return await self._park_for_reconciliation(t_ref, record, "input wait deadline is malformed")
            run_ref = await asyncio.to_thread(self._store.run_for_task, t_ref)
            anchor = await asyncio.to_thread(
                self._store.action_receipt_time,
                contract["execution_id"],
                task_ref=t_ref,
                run_ref=run_ref,
                action_name=contract["tool"],
                arguments_digest=contract["arguments_digest"],
            )
            if _parse(anchor) is None:
                return await self._park_for_reconciliation(t_ref, record, "input request receipt is unverifiable")
            try:
                now = self._clock() if self._clock is not None else await asyncio.to_thread(self._store.wait_now)
            except (TypeError, ValueError):
                return await self._park_for_reconciliation(t_ref, record, "input wait clock cannot be trusted")
            if deadline > now:
                return False
            return await self._converge(db, t_ref, record, wait, self._clock)

    async def _converge(self, db: Any, t_ref: BackendRef, record: Any, wait: dict[str, Any], clock: Any | None) -> bool:
        """Fail the waiting task via the shared revision-CAS wait terminal.

        ``clock`` threads the reconciler's time source into the commit gate;
        ``None`` lets the store decide with its own clock at the lock.
        """

        outcome = {"status": "failed", "content": "", "error": INPUT_WAIT_EXPIRED, "error_category": INPUT_WAIT_EXPIRED}
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
                wait_clock=clock,
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
            # A legal wake, a cancel or another scan moved the task first;
            # their fact stands and this scan claims nothing.
            logger.info("task %s input wait expiry skipped (moved concurrently)", t_ref.id)
            return False
        logger.info("task %s converged: %s", t_ref.id, INPUT_WAIT_EXPIRED)
        await self._finalize_run_projection(db, t_ref, record, INPUT_WAIT_EXPIRED)
        await converge_workflow_parent(db, self._store, self._session_factory, t_ref, outcome)
        return True

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

    async def _finalize_run_projection(self, db: Any, t_ref: BackendRef, record: Any, category: str) -> None:
        """Mirror the terminal on the platform run projection.

        The outbox ``run_terminal`` event already carries the same fact;
        this direct write only avoids waiting for the relay. A lost write
        is recovered by the relay's projection, so failures are logged.
        """

        run_ref = await asyncio.to_thread(self._store.run_for_task, t_ref)
        workspace_raw = (record.extra or {}).get("workspace_id")
        if run_ref is None or not workspace_raw:
            return
        try:
            from hecate.execution.task_run_registry import TaskRunRegistry

            await TaskRunRegistry(db).finalize_run(
                uuid.UUID(run_ref.id), uuid.UUID(str(workspace_raw)), state="failed", error_category=category
            )
            await db.commit()
        except Exception:  # noqa: BLE001 — the relay re-projects the terminal fact
            logger.warning("task %s expired-input-wait run projection skipped", t_ref.id, exc_info=True)
            await db.rollback()


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        deadline = datetime.fromisoformat(value)
    except ValueError:
        return None
    return deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=UTC)
