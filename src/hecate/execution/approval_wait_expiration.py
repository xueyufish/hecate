"""Persistent expiration of platform-local approval waits (step6).

A task parked in ``waiting_approval`` carries its own deadline (the wait
token's ``wait_expires_at`` and the pending binding's authority expiry,
minted together at park time). Wakes already refuse an expired token, but
until this module nothing converged the task itself: the worker's
reconcile cycle only scans queued/running, so an abandoned approval
wait sat forever unless someone kept poking the command API.

The expirer is the durable worker's named wait-reconciliation consumer.
One bounded pass lists ``waiting_approval`` tasks and, for each, either
converges or deliberately leaves the record alone:

- Only platform-issued approval waits are candidates: foreign-issuer
  tasks, ``waiting_input`` waits, consumed tokens and waits without a
  declared deadline are skipped — an undeclared deadline keeps its
  existing meaning (no expiry, no invention).
- Expiry is judged against BOTH the wait deadline and the bound
  authority's own expiry (the earlier one wins; neither is ever
  extended). A binding whose authority record is missing, or whose
  trusted time cannot be re-anchored, is parked for reconciliation
  instead of guessed.
- Convergence is a revision-CAS terminal commit on the same durable
  state machine the wake and cancel paths use: ``failed`` with the
  ``approval_wait_expired`` stop category (a rejected decision keeps its
  real ``approval_rejected`` verdict — expiry is never rewritten into a
  human decision). The wait token is consumed, the approval reference,
  deadlines and recorded action facts are preserved, and no model call,
  tool dispatch or action redo happens — expiry terminates the wait, it
  does not execute anything.
- The terminal payload rides the same transactional outbox as every
  other terminal fact (the relay recovers the run projection and the
  workflow-parent callback if the direct writes are lost), and a
  workflow child's expiry converges its parent through the verified
  callback path under the parent's declared ``on_failure`` policy.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from hecate.contracts.execution.durable import InvalidTaskTransitionError, TaskLifecycleState
from hecate.contracts.execution.references import BackendRef

logger = logging.getLogger(__name__)

APPROVAL_WAIT_EXPIRED = "approval_wait_expired"
APPROVAL_REJECTED = "approval_rejected"

_EXPIRY_UNVERIFIABLE = "approval wait expiry could not be verified"


async def converge_workflow_parent(
    db: Any,
    store: Any,
    session_factory: Any,
    t_ref: BackendRef,
    outcome: dict[str, Any],
) -> None:
    """Converge a waiting workflow parent after its child reached a terminal.

    The verified callback path is inherited unchanged: the parent trusts
    only its own records, and idempotency (wait-token consumption, command
    receipts) makes the relay's retry of this same callback safe. The
    child's terminal fact is already committed, so failures here are
    logged, never surfaced as an un-commit.
    """

    try:
        payload = await asyncio.to_thread(store.get_task_input, t_ref) or {}
        if not isinstance(payload.get("workflow_parent"), dict):
            return
        from hecate.execution.task_dispatcher import PlatformTaskDispatcher

        dispatcher = PlatformTaskDispatcher(store, session_factory)
        context = PlatformTaskDispatcher._context_of(t_ref, payload)
        await dispatcher._issue_child_callback(db, context, {**outcome, "status": "failed"})
    except Exception:  # noqa: BLE001 — convergence must not un-commit the terminal
        logger.warning("task %s terminal callback to workflow parent failed", t_ref.id, exc_info=True)


class ApprovalWaitExpirer:
    """Bounded convergence pass for expired platform approval waits."""

    def __init__(
        self,
        store: Any,
        session_factory: Any,
        *,
        platform_issuer: str = "hecate",
        batch: int = 32,
        clock: Callable[[], datetime] | None = None,
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
        """One bounded pass; returns the number of converged waits."""

        records, self._cursor = await asyncio.to_thread(
            self._store.list_task_page,
            {TaskLifecycleState.WAITING_APPROVAL},
            after=self._cursor,
            limit=self._batch,
        )
        converged = 0
        for record in records:
            try:
                if await self._expire_one(record):
                    converged += 1
            except Exception:  # noqa: BLE001 — one bad record must not block the pass
                logger.warning("approval wait expiry failed for task %s", record.task_ref.id, exc_info=True)
        return converged

    async def _expire_one(self, record: Any) -> bool:
        t_ref = record.task_ref
        if t_ref.issuer_domain != self._platform_issuer:
            # A foreign host owns the execution facts; the platform never
            # declares a terminal state on its behalf.
            return False
        wait = (record.extra or {}).get("wait") or {}
        if wait.get("consumed") or wait.get("wake_kind") != "resume":
            return False
        if not str((wait.get("contract_ref") or {}).get("approval_ref") or ""):
            return False
        if wait.get("wait_expires_at") is None:
            return False
        async with self._session_factory() as db:
            from hecate.enterprise.approval_wait_ledger import task_approval_ledger

            if not await self._is_local(db, t_ref, record):
                return False
            wait_deadline = _parse(wait.get("wait_expires_at"))
            if wait_deadline is None:
                return await self._park_for_reconciliation(t_ref, record, "approval wait deadline is malformed")
            if not isinstance(wait.get("wait_token"), str) or not wait["wait_token"]:
                return await self._park_for_reconciliation(t_ref, record, "approval wait token is malformed")
            ledger = task_approval_ledger(db)
            if ledger is None:
                logger.warning("approval authority unavailable; task %s wait left pending", t_ref.id)
                return False
            approval_ref = str(wait["contract_ref"]["approval_ref"])
            try:
                binding = await ledger.get(approval_ref)
            except Exception:  # noqa: BLE001 — untrusted time must not guess expiry
                logger.warning("task %s approval authority unreadable", t_ref.id, exc_info=True)
                return await self._park_for_reconciliation(t_ref, record, _EXPIRY_UNVERIFIABLE)
            if binding is None:
                return await self._park_for_reconciliation(
                    t_ref, record, "approval wait references a missing authority record"
                )
            from hecate_governance.approval import ActionRequest, ApprovalJudgment, BindingCheck, check_binding
            from hecate_governance.clock import TimeAnchor, TrustedClock

            from hecate.execution.approval_wait import task_principal_fingerprint

            payload = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
            contract = wait["contract_ref"]
            request = ActionRequest(
                invocation_id=str(contract.get("invocation_id") or wait.get("wait_token") or ""),
                tool_name=str(contract.get("tool_name") or ""),
                arguments_digest=str(contract.get("arguments_digest") or ""),
                target_resources=tuple(contract.get("target_resources") or ()),
                principal_fingerprint=task_principal_fingerprint(
                    actor=str(payload.get("user_id") or f"agent:{payload.get('agent_id')}"),
                    agent_principal=payload.get("agent_id"),
                ),
                task_scope=t_ref.id,
            )
            # Verify binding identity without granting or consuming it.
            # Expiry and clock trust are assessed separately below.
            verdict = check_binding(
                binding.model_copy(
                    update={"judgment": ApprovalJudgment.APPROVED, "expires_at": None, "revoked": False}
                ),
                request,
                now=datetime.now(UTC),
            )
            if verdict.check is not BindingCheck.OK or binding.consumed_by_invocation_id is not None:
                return await self._park_for_reconciliation(
                    t_ref, record, "approval wait binding does not match its action"
                )

            def trusted_now() -> datetime:
                if self._clock is not None:
                    return self._clock()  # Explicit authoritative test clock.
                now, problem = TrustedClock().trusted_now(
                    TimeAnchor(binding.anchor_wall, binding.anchor_monotonic, binding.clock_epoch)
                )
                if problem:
                    raise ValueError(_EXPIRY_UNVERIFIABLE)
                return now

            try:
                now = trusted_now()
            except ValueError:
                return await self._park_for_reconciliation(t_ref, record, _EXPIRY_UNVERIFIABLE)

            authority_deadline = getattr(binding, "expires_at", None)
            deadline = wait_deadline if authority_deadline is None else min(wait_deadline, authority_deadline)
            if deadline > now:
                return False
            # A rejected decision keeps its real human verdict; every other
            # judgment (pending, approved-but-unconsumed, revoked) converges
            # as an expired wait — expiry is neither an approval nor a
            # fabricated human decision.
            category = APPROVAL_REJECTED if binding.judgment is ApprovalJudgment.REJECTED else APPROVAL_WAIT_EXPIRED
            return await self._converge(db, t_ref, record, wait, category, authority_deadline, trusted_now)

    async def _is_local(self, db: Any, t_ref: BackendRef, record: Any) -> bool:
        return await verify_platform_local(db, self._store, t_ref, record)

    async def _converge(
        self,
        db: Any,
        t_ref: BackendRef,
        record: Any,
        wait: dict[str, Any],
        category: str,
        authority_deadline: datetime | None,
        clock: Callable[[], datetime],
    ) -> bool:
        """Fail the waiting task via the same revision-CAS terminal as wakes."""

        outcome = {"status": "failed", "content": "", "error": category, "error_category": category}
        run_ref = await asyncio.to_thread(self._store.run_for_task, t_ref)
        try:
            await asyncio.to_thread(
                self._store.apply_task_state,
                t_ref,
                TaskLifecycleState.FAILED,
                expected_revision=record.revision,
                expected_wait=wait,
                wait_expired=True,
                approval_deadline=authority_deadline.isoformat() if authority_deadline is not None else None,
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
            # A legal resume, a cancel or another scan moved the task
            # first; their fact stands and this scan claims nothing.
            logger.info("task %s wait expiry skipped (moved concurrently)", t_ref.id)
            return False
        logger.info("task %s converged: %s", t_ref.id, category)
        await self._finalize_run_projection(db, t_ref, record, category)
        await converge_workflow_parent(db, self._store, self._session_factory, t_ref, outcome)
        return True

    async def _park_for_reconciliation(self, t_ref: BackendRef, record: Any, reason: str) -> bool:
        outcome = {
            "status": "awaiting_reconciliation",
            "content": "",
            "error": reason,
            "error_category": "approval_wait_unverifiable",
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
            logger.warning("task %s expired-wait run projection skipped", t_ref.id, exc_info=True)
            await db.rollback()


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        deadline = datetime.fromisoformat(value)
    except ValueError:
        return None
    return deadline if deadline.tzinfo is not None else deadline.replace(tzinfo=UTC)


async def verify_platform_local(db: Any, store: Any, t_ref: BackendRef, record: Any) -> bool:
    """Verify the task is a platform-local builtin execution this worker owns.

    Shared by the approval and input wait reconcilers: issuer matching alone
    proves nothing — the Task/Run registry must resolve the run to a
    platform-origin, platform-controlled builtin in-process deployment with
    no managed-delivery claim. Unverifiable facts leave the wait untouched.
    """
    from sqlalchemy import select

    from hecate.execution.task_run_registry import TaskRunRegistry
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.managed_delivery import ManagedDeliveryModel
    from hecate.models.run import RunOrigin

    run_ref = await asyncio.to_thread(store.run_for_task, t_ref)
    try:
        workspace = uuid.UUID(str(record.extra["workspace_id"]))
        registry = TaskRunRegistry(db)
        task = await registry.get_task(uuid.UUID(t_ref.id), workspace)
        if run_ref is None or run_ref.issuer_domain != t_ref.issuer_domain:
            return False
        run = await registry.get_run(uuid.UUID(run_ref.id), workspace)
        if run.task_id != task.id or run.issuer_domain != t_ref.issuer_domain:
            return False
        if run.origin != RunOrigin.PLATFORM or run.control_owner != "platform":
            return False
        deployment = await db.get(AgentDeploymentModel, run.deployment_id)
        if (
            deployment is None
            or deployment.deleted
            or deployment.workspace_id != workspace
            or deployment.backend_type != BackendType.BUILTIN
            or deployment.access_mode != AccessMode.IN_PROCESS
        ):
            return False
        managed = await db.scalar(
            select(ManagedDeliveryModel.id)
            .where(
                ManagedDeliveryModel.workspace_id == workspace,
                ManagedDeliveryModel.deleted.is_(False),
                ManagedDeliveryModel.task_ref["id"].as_string() == t_ref.id,
                ManagedDeliveryModel.task_ref["issuer_domain"].as_string() == t_ref.issuer_domain,
            )
            .limit(1)
        )
        return managed is None
    except (KeyError, ValueError):
        logger.warning("task %s has unverifiable local ownership; wait unchanged", t_ref.id)
        return False


WaitReconciler = Callable[[], Awaitable[int]]


def standalone_expirer(*, store: Any | None = None) -> WaitReconciler:
    """``--wait-reconciler`` factory for the standalone worker process.

    The dedicated worker process runs against the same platform database
    and configuration as the app process, so the process-wide durable
    suite binding resolves the same store the dispatcher reads.
    """

    from sqlalchemy.engine import make_url

    from hecate.core.composition.durable_platform import get_durable_suite, to_sync_database_url
    from hecate.core.database import async_session_factory

    store = store if store is not None else get_durable_suite().store
    platform_engine = async_session_factory.kw["bind"]
    platform_url = make_url(to_sync_database_url(platform_engine.url.render_as_string(hide_password=False)))
    if not hasattr(store, "engine") or store.engine.url != platform_url:
        raise ValueError("approval wait reconciler and platform must use the same SQL database")
    return ApprovalWaitExpirer(store, async_session_factory).expire_once


def standalone_wait_reconcilers(*, store: Any | None = None) -> WaitReconciler:
    """Composite ``--wait-reconciler`` factory: approval, input AND timer waits.

    The dedicated worker process must run against the same platform
    database as the app process (enforced against the bound engine URL);
    each reconciler stays fault-isolated inside the composite.
    """

    from hecate.core.composition.durable_platform import get_durable_suite
    from hecate.core.database import async_session_factory
    from hecate.execution.approval_decision import ApprovalResumeRecovery
    from hecate.execution.input_wait_expiration import InputWaitExpirer
    from hecate.execution.timer_wait_wake import TimerWaitWaker

    store = store if store is not None else get_durable_suite().store
    approval = standalone_expirer(store=store)
    input_expiry = InputWaitExpirer(store, async_session_factory).expire_once
    timer_wake = TimerWaitWaker(store, async_session_factory).wake_once
    # A non-None worker suppresses inline dispatch in the control service.
    # Resumes only requeue here; the dedicated worker acquires the normal
    # dispatch lease in its next cycle, after this callback returns.
    resume = ApprovalResumeRecovery(store, async_session_factory, worker=_QueuedResumeWorker()).resume_once
    reconcilers = (
        ("approval expiry", approval),
        ("approval resume", resume),
        ("input expiry", input_expiry),
        ("timer wake", timer_wake),
    )

    async def reconcile() -> int:
        processed = 0
        for name, callback in reconcilers:
            try:
                processed += await callback()
            except Exception:  # noqa: BLE001 — one broken pass never blocks the others
                logger.exception("%s reconciliation pass failed", name)
        return processed

    return reconcile


class _QueuedResumeWorker:
    """Leave resumed commands queued for the dedicated worker's leased cycle."""

    async def dispatch_once(self, task_ref: BackendRef) -> bool:
        return False
