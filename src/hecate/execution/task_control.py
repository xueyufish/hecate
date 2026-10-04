"""Platform task control service (step6 platform track).

The application service behind ``/api/tasks``: idempotent submission,
Task lifecycle projection over the durable seam, control-command
receipts, run projections, governance-event emission, and the
pending-reconciliation view. State vocabulary and transition rules come
exclusively from ``contracts/execution/durable`` — this module never
invents states.

Dispatch today is **in-process** (the dev binding): ``wait`` executes
inside the request; non-wait executions run as process-local background
tasks that survive the HTTP disconnect but not a process restart. The
durable worker core (worktree A) replaces dispatch behind this surface
without API changes.

Cancellation is honest about what the platform can enforce. A queued
task that was never dispatched is genuinely cancelled — the dispatch
observes the request before starting, skips execution, applies the
lifecycle transition, and marks the command ``applied``. A running
inline execution has no cooperative abort channel, so its command stays
``requested`` with an explicit note; commands against terminal tasks
are ``rejected``. ``applied`` never appears without a real effect, and
an HTTP success response never claims it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import false, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.contracts.execution.durable import (
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyConflictError,
    IdempotencyKey,
    InvalidTaskTransitionError,
    SubmissionAssociation,
    TaskLifecycleState,
    TaskStateRecord,
    canonical_request_digest,
)
from hecate.contracts.execution.events import ActorKind, ActorRef
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import (
    BackendRef,
    RefKind,
    deployment_ref,
    run_ref,
    task_ref,
)
from hecate.execution.backend import EventPage
from hecate.execution.durable import ControlCommandRecorder, DurableTaskStore
from hecate.execution.entry_events import RunEventMapper
from hecate.execution.governance_events import (
    COMMAND_RECORDED,
    COMMAND_TRANSITIONED,
    RUN_TERMINAL,
    TASK_STATE_CHANGED,
    TASK_SUBMITTED,
    PlatformEventService,
)
from hecate.execution.task_run_registry import (
    TaskNotFoundError,
    TaskRunRegistry,
    TaskRunRegistryError,
)
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AgentDeploymentModel
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.control_command import ControlCommandModel
from hecate.models.run import RunModel
from hecate.models.task import TaskModel
from hecate.models.task_lifecycle import TaskLifecycleStateModel

logger = logging.getLogger(__name__)

PLATFORM_ISSUER = "hecate"
UNRECORDED_LIFECYCLE = "unrecorded"
POSTGRES_BACKEND = "postgres"
_CANCEL_RUNNING_NOTE = (
    "running inline execution has no cooperative abort channel; "
    "applied cancellation arrives with the durable worker core (step6 track A)"
)


class TaskControlError(Exception):
    """Base error for the task control service."""


class TaskControlNotFoundError(TaskControlError):
    """Missing/foreign-workspace resource (404 semantics)."""


class TaskControlValidationError(TaskControlError):
    """Invalid request data or rejected transition (422 semantics)."""


class SubmissionConflictError(TaskControlError):
    """Same idempotency key replayed with a different request digest (409)."""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline <= datetime.now(UTC)


class _DispatchGuard:
    """Process-local dispatch state for cancel arbitration.

    Every mutation happens in a no-await critical section, so event-loop
    atomicity holds: cancellation is either observed before the dispatch
    starts (the dispatch applies it) or the execution has already begun
    (the command honestly stays ``requested``).
    """

    __slots__ = ("cancel_requested", "pending_cancel_command_id", "started")

    def __init__(self) -> None:
        self.cancel_requested = False
        self.pending_cancel_command_id: str | None = None
        self.started = False


_guards: dict[uuid.UUID, _DispatchGuard] = {}
# Workspace attribution for commands under the stub binding (the stub
# recorder has no table; postgres-mode attribution lives on the row).
_command_workspaces: dict[str, uuid.UUID] = {}
# Strong references for fire-and-forget background dispatches (an
# unreferenced task can be garbage-collected mid-execution).
_background_tasks: set[asyncio.Task[None]] = set()


def _spawn_background(coro: Coroutine[Any, Any, None]) -> None:
    """Schedule one fire-and-forget dispatch, keeping a strong reference.

    Named (and therefore patchable) so tests can hold the dispatch before
    it starts - the queued-cancel arbitration window is otherwise too
    narrow to exercise deterministically through the async API.
    """

    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _guard_for(task_id: uuid.UUID) -> _DispatchGuard:
    return _guards.setdefault(task_id, _DispatchGuard())


def task_ref_of(task_id: uuid.UUID | str) -> BackendRef:
    """Platform-issued task reference for one task row."""
    return task_ref(PLATFORM_ISSUER, str(task_id))


def run_row_ref(run: RunModel) -> BackendRef:
    """Platform run-row reference for one execution attempt."""
    return BackendRef(RefKind.RUN, run.issuer_domain, str(run.id))


@dataclass(frozen=True)
class SubmitResult:
    """What the platform returned for one submission (or its replay)."""

    task_ref: BackendRef
    run_ref: BackendRef
    lifecycle_state: str
    replayed: bool
    result: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CommandIssueResult:
    """Issued command plus the receipt state the platform can attest."""

    record: ControlCommandRecord
    detail: str | None = None


class TaskControlService:
    """Platform task control surface over the durable seams and registry."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        store: DurableTaskStore,
        recorder: ControlCommandRecorder,
        backend: str = "stub",
        ledger_source: str = "stub",
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._db = db
        self._store = store
        self._recorder = recorder
        self._backend = backend
        self._ledger_source = ledger_source
        self._session_factory = session_factory
        self._registry = TaskRunRegistry(db)

    # ------------------------------------------------------------------
    # submission
    # ------------------------------------------------------------------

    async def submit(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID | None,
        goal: str,
        agent_id: uuid.UUID,
        input: dict[str, Any],
        idempotency_key: str | None = None,
        wait: bool = False,
        stream: bool = False,
    ) -> SubmitResult:
        """Idempotently submit one task; dev dispatch executes it in-process."""
        if not goal or not goal.strip():
            raise TaskControlValidationError("goal must be a non-empty string")
        messages = input.get("messages")
        if not isinstance(messages, list) or not messages:
            raise TaskControlValidationError("input.messages must be a non-empty list")
        agent = await self._db.get(AgentModel, agent_id)
        if agent is None or agent.deleted or agent.workspace_id != workspace_id:
            raise TaskControlNotFoundError(f"agent {agent_id} not found in workspace")
        deployment = await self._default_deployment(agent_id, workspace_id)
        if deployment is None:
            raise TaskControlValidationError(f"agent {agent_id} has no default deployment")

        digest = canonical_request_digest({"goal": goal, "agent_id": str(agent_id), "input": input, "stream": stream})
        minted_task, minted_run, engine_session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        t_ref = task_ref_of(minted_task)
        run_backend_ref = run_ref(deployment.issuer_domain, str(engine_session))

        if idempotency_key:
            key = IdempotencyKey(
                key=idempotency_key,
                subject=str(user_id) if user_id is not None else "anonymous",
                workspace=str(workspace_id),
                request_digest=digest,
            )
            try:
                association = await asyncio.to_thread(
                    self._store.record_submission,
                    key,
                    t_ref,
                    BackendRef(RefKind.RUN, PLATFORM_ISSUER, str(minted_run)),
                )
            except IdempotencyConflictError as exc:
                raise SubmissionConflictError(
                    f"idempotency key {idempotency_key!r} is already registered with a different request digest"
                ) from exc
            if association.task_ref != t_ref:
                return await self._replay_result(workspace_id, association)

        chain = await self._identity_chain(workspace_id, agent_id, user_id, deployment)
        try:
            task = await self._registry.create_task(
                goal=goal,
                initiator_ref=chain.to_dict(),
                workspace_id=workspace_id,
                task_id=minted_task,
            )
            run = await self._registry.create_run(
                task_id=task.id,
                workspace_id=workspace_id,
                deployment_id=deployment.id,
                identity_chain=chain,
                backend_run_ref=run_backend_ref,
                run_id=minted_run,
            )
        except TaskRunRegistryError as exc:
            raise TaskControlValidationError(str(exc)) from exc
        # Commit the rows before the seam write: the durable store rides its
        # own engine/transaction, and holding this transaction open across
        # that write locks the database (single-writer sqlite today; one
        # independent session per transaction regardless).
        await self._db.commit()

        await self._apply_state(t_ref, TaskLifecycleState.QUEUED, workspace_id=workspace_id)
        r_ref = run_row_ref(run)
        await PlatformEventService(self._db).emit(
            task_ref=t_ref,
            run_ref=r_ref,
            payload_schema_ref=TASK_SUBMITTED,
            payload={"goal": goal[:512], "agent_id": str(agent_id), "deployment_id": str(deployment.id)},
            actor=self._actor(user_id),
            workspace_id=workspace_id,
        )
        await self._db.commit()

        if wait:
            outcome = await self._dispatch(
                db=self._db,
                task_ref=t_ref,
                run_ref=r_ref,
                workspace_id=workspace_id,
                agent_id=agent_id,
                engine_session=engine_session,
                messages=messages,
                model=input.get("model"),
                stream=stream,
                goal=goal,
                user_id=user_id,
            )
            return SubmitResult(
                task_ref=t_ref,
                run_ref=r_ref,
                lifecycle_state=await self._lifecycle_state(t_ref),
                replayed=False,
                result=outcome,
            )
        _spawn_background(
            self._dispatch_background(
                task_ref=t_ref,
                run_ref=r_ref,
                workspace_id=workspace_id,
                agent_id=agent_id,
                engine_session=engine_session,
                messages=messages,
                model=input.get("model"),
                stream=stream,
                goal=goal,
                user_id=user_id,
            )
        )
        return SubmitResult(
            task_ref=t_ref,
            run_ref=r_ref,
            lifecycle_state=TaskLifecycleState.QUEUED.value,
            replayed=False,
        )

    async def _replay_result(self, workspace_id: uuid.UUID, association: SubmissionAssociation) -> SubmitResult:
        """Return the original submission's outcome for a key replay."""
        try:
            original_id = uuid.UUID(association.task_ref.id)
        except ValueError as exc:
            raise TaskControlValidationError("registered task reference is not a platform task id") from exc
        task = await self._db.get(TaskModel, original_id)
        if task is None or task.deleted or task.workspace_id != workspace_id:
            raise TaskControlNotFoundError("registered task does not resolve in workspace")
        return SubmitResult(
            task_ref=association.task_ref,
            run_ref=association.run_ref,
            lifecycle_state=await self._lifecycle_state(association.task_ref),
            replayed=True,
            reason="idempotent replay of the registered submission",
        )

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    async def get_task_detail(self, workspace_id: uuid.UUID, task_id: uuid.UUID) -> dict[str, Any]:
        """Task responsibility record + lifecycle projection + runs."""
        task = await self._get_task(workspace_id, task_id)
        runs = await self._registry.list_runs_for_task(task.id, workspace_id)
        return {
            "task_id": str(task.id),
            "goal": task.goal,
            "issuer_domain": task.issuer_domain,
            "workspace_id": str(task.workspace_id),
            "lifecycle_state": await self._lifecycle_state(task_ref_of(task.id)),
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "runs": [self._run_summary(run) for run in runs],
        }

    async def list_tasks(
        self,
        workspace_id: uuid.UUID,
        *,
        state: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """Workspace-scoped task list joined with lifecycle projection."""
        base = select(TaskModel).where(TaskModel.workspace_id == workspace_id, TaskModel.deleted.is_(False))
        if state is not None and state != UNRECORDED_LIFECYCLE:
            lifecycle_rows = await self._db.execute(
                select(TaskLifecycleStateModel.task_ref_id).where(
                    TaskLifecycleStateModel.workspace_id == workspace_id,
                    TaskLifecycleStateModel.lifecycle_state == state,
                    TaskLifecycleStateModel.deleted.is_(False),
                )
            )
            task_ids = [uuid.UUID(row) for row in lifecycle_rows.scalars()]
            base = base.where(TaskModel.id.in_(task_ids) if task_ids else false())
        count = (await self._db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        tasks = (
            (
                await self._db.execute(
                    base.order_by(TaskModel.created_at.desc()).limit(page_size).offset((page - 1) * page_size)
                )
            )
            .scalars()
            .all()
        )
        states = await self._lifecycle_map(workspace_id, [str(t.id) for t in tasks])
        items = [
            {
                "task_id": str(task.id),
                "goal": task.goal[:200],
                "lifecycle_state": states.get(str(task.id), UNRECORDED_LIFECYCLE),
                "created_at": task.created_at.isoformat() if task.created_at else None,
            }
            for task in tasks
        ]
        return items, count

    async def get_run_detail(self, workspace_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
        try:
            run = await self._registry.get_run(run_id, workspace_id)
        except TaskNotFoundError as exc:
            raise TaskControlNotFoundError(str(exc)) from exc
        return self._run_summary(run)

    async def read_run_events(
        self,
        workspace_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> EventPage:
        try:
            run = await self._registry.get_run(run_id, workspace_id)
        except TaskNotFoundError as exc:
            raise TaskControlNotFoundError(str(exc)) from exc
        return await PlatformEventService(self._db).read_run_events(
            run_row_ref(run), workspace_id=workspace_id, cursor=cursor, limit=limit
        )

    async def stream_run_events(
        self,
        workspace_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        cursor: str | None = None,
        poll_interval: float = 0.2,
    ) -> Any:
        """Subscribe view: yield stored envelopes until the terminal event."""
        try:
            run = await self._registry.get_run(run_id, workspace_id)
        except TaskNotFoundError as exc:
            raise TaskControlNotFoundError(str(exc)) from exc
        run_ref = run_row_ref(run)
        events = PlatformEventService(self._db)
        current = cursor
        while True:
            page = await events.read_run_events(run_ref, workspace_id=workspace_id, cursor=current)
            for envelope in page.events:
                yield envelope
                current = str(envelope.source_sequence)
                if envelope.payload_schema_ref == RUN_TERMINAL:
                    return
            # Everything stored has been consumed: stop only when the newest
            # envelope is the terminal marker, otherwise keep polling.
            latest = await events.latest_run_envelope(run_ref, workspace_id=workspace_id)
            if (
                latest is not None
                and latest.payload_schema_ref == RUN_TERMINAL
                and (current is None or latest.source_sequence <= int(current))
            ):
                return
            await asyncio.sleep(poll_interval)

    # ------------------------------------------------------------------
    # control commands
    # ------------------------------------------------------------------

    async def issue_command(
        self,
        *,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID,
        kind: ControlCommandKind,
        issuer: str,
        payload: dict[str, Any] | None = None,
        payload_schema_ref: str | None = None,
        expected_revision: int | None = None,
        expires_at: str | None = None,
    ) -> CommandIssueResult:
        """Record a command and act on what the platform can enforce."""
        task = await self._get_task(workspace_id, task_id)
        t_ref = task_ref_of(task.id)
        runs = await self._registry.list_runs_for_task(task.id, workspace_id)
        latest_run = runs[-1] if runs else None
        record = ControlCommandRecord(
            command_id=str(uuid.uuid4()),
            kind=kind,
            issuer=issuer,
            task_ref=t_ref,
            issued_at=_utc_now_iso(),
            state=CommandState.REQUESTED,
            run_ref=run_row_ref(latest_run) if latest_run is not None else None,
            expires_at=expires_at,
            expected_revision=expected_revision,
            payload=dict(payload) if payload else {},
            payload_schema_ref=payload_schema_ref if payload else None,
        )
        record = await asyncio.to_thread(self._recorder.record, record)
        await self._attribute_command(record.command_id, workspace_id)
        await self._emit_command_event(t_ref, record, workspace_id, CommandState.REQUESTED)
        # Close this session's write transaction before the recorder's own
        # transaction runs — the seam store never shares a transaction with
        # the application session (one independent session per transaction).
        await self._db.commit()

        if kind is ControlCommandKind.CANCEL:
            detail = await self._request_cancel(t_ref, task_id, record, workspace_id)
        else:
            # pause/resume/provide_input: builtin deployments declare none of
            # these capabilities — reject explicitly, never fake success.
            await self._db.commit()
            record = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            await self._emit_command_event(t_ref, record, workspace_id, record.state)
            detail = "builtin deployments declare no pause/resume/provide_input capability"
        refreshed = await asyncio.to_thread(self._recorder.get, record.command_id)
        return CommandIssueResult(record=refreshed or record, detail=detail)

    async def _request_cancel(
        self, t_ref: BackendRef, task_id: uuid.UUID, record: ControlCommandRecord, workspace_id: uuid.UUID
    ) -> str:
        state = await self._lifecycle_state(t_ref)
        if state in (
            TaskLifecycleState.SUCCEEDED.value,
            TaskLifecycleState.FAILED.value,
            TaskLifecycleState.CANCELLED.value,
        ):
            rejected = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            await self._emit_command_event(t_ref, rejected, workspace_id, rejected.state)
            return f"task already terminal ({state}); cancel rejected"
        guard = _guard_for(task_id)
        if state == TaskLifecycleState.QUEUED.value and not guard.started and not guard.cancel_requested:
            # Critical section (no awaits): the dispatch either already saw
            # started=True or will observe cancel_requested and apply it.
            guard.cancel_requested = True
            guard.pending_cancel_command_id = record.command_id
            return "cancel requested before dispatch; the dispatch applies it and closes the receipt"
        guard.cancel_requested = True
        return _CANCEL_RUNNING_NOTE

    async def get_command(self, workspace_id: uuid.UUID, command_id: str) -> ControlCommandRecord:
        """Read one command receipt with workspace scoping and lazy expiry."""
        if self._backend == POSTGRES_BACKEND:
            row = (
                await self._db.execute(
                    select(ControlCommandModel).where(
                        ControlCommandModel.command_id == command_id,
                        ControlCommandModel.deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.workspace_id != workspace_id:
                raise TaskControlNotFoundError(f"command {command_id} not found in workspace")
        elif _command_workspaces.get(command_id) != workspace_id:
            raise TaskControlNotFoundError(f"command {command_id} not found in workspace")
        record = await asyncio.to_thread(self._recorder.get, command_id)
        if record is None:
            raise TaskControlNotFoundError(f"command {command_id} not found in workspace")
        if record.state is CommandState.REQUESTED and _expired(record.expires_at):
            record = await asyncio.to_thread(self._recorder.transition, command_id, CommandState.EXPIRED)
        return record

    # ------------------------------------------------------------------
    # reconciliation
    # ------------------------------------------------------------------

    async def pending_reconciliation(self, workspace_id: uuid.UUID) -> dict[str, Any]:
        """Read-only view of unconverged facts (lazy command expiry aside)."""
        rows = (
            (
                await self._db.execute(
                    select(TaskLifecycleStateModel)
                    .where(
                        TaskLifecycleStateModel.workspace_id == workspace_id,
                        TaskLifecycleStateModel.lifecycle_state == TaskLifecycleState.RECONCILIATION_REQUIRED.value,
                        TaskLifecycleStateModel.deleted.is_(False),
                    )
                    .order_by(TaskLifecycleStateModel.updated_at)
                )
            )
            .scalars()
            .all()
        )
        tasks = [
            {
                "task_ref": {"issuer_domain": row.issuer_domain, "id": row.task_ref_id},
                "revision": row.revision,
                "recorded_at": row.recorded_at,
            }
            for row in rows
        ]
        expired_commands: list[dict[str, Any]] = []
        for command_id, attributed_ws in list(_command_workspaces.items()):
            if attributed_ws != workspace_id:
                continue
            record = await asyncio.to_thread(self._recorder.get, command_id)
            if record is None or record.state is not CommandState.REQUESTED:
                continue
            if _expired(record.expires_at):
                record = await asyncio.to_thread(self._recorder.transition, command_id, CommandState.EXPIRED)
                expired_commands.append(
                    {
                        "command_id": record.command_id,
                        "kind": record.kind.value,
                        "state": record.state.value,
                        "expires_at": record.expires_at,
                        "task_ref": record.task_ref.to_dict(),
                    }
                )
        return {
            "workspace_id": str(workspace_id),
            "tasks": tasks,
            "commands": expired_commands,
            # The action ledger has no production implementation yet (step6
            # track A); an empty list is a labeled absence, not a scan result.
            "actions": [],
            "ledger_source": self._ledger_source,
        }

    # ------------------------------------------------------------------
    # dispatch (dev mode: in-process)
    # ------------------------------------------------------------------

    async def _dispatch_background(
        self,
        *,
        task_ref: BackendRef,
        run_ref: BackendRef,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        engine_session: uuid.UUID,
        messages: list[dict[str, Any]],
        model: str | None,
        stream: bool,
        goal: str,
        user_id: uuid.UUID | None,
    ) -> None:
        from hecate.core.database import async_session_factory

        factory = self._session_factory or async_session_factory
        try:
            async with factory() as db:
                await self._dispatch(
                    db=db,
                    task_ref=task_ref,
                    run_ref=run_ref,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    engine_session=engine_session,
                    messages=messages,
                    model=model,
                    stream=stream,
                    goal=goal,
                    user_id=user_id,
                )
        except Exception:  # noqa: BLE001 — a background dispatch never crashes the loop
            logger.exception("background dispatch failed for task %s", task_ref.id)

    async def _dispatch(
        self,
        *,
        db: AsyncSession,
        task_ref: BackendRef,
        run_ref: BackendRef,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        engine_session: uuid.UUID,
        messages: list[dict[str, Any]],
        model: str | None,
        stream: bool,
        goal: str,
        user_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        task_id = uuid.UUID(task_ref.id)
        guard = _guard_for(task_id)
        guard.started = True
        try:
            if guard.cancel_requested:
                # Cancel won the race before any execution began — a real effect.
                await self._apply_state(task_ref, TaskLifecycleState.CANCELLED, workspace_id=workspace_id)
                await self._emit_state_event(db, task_ref, run_ref, workspace_id, TaskLifecycleState.CANCELLED)
                # Close this session's writes before the recorder's own
                # transaction applies the command (no shared transactions).
                await db.commit()
                if guard.pending_cancel_command_id:
                    applied = await asyncio.to_thread(
                        self._recorder.transition, guard.pending_cancel_command_id, CommandState.APPLIED
                    )
                    await self._emit_command_event(task_ref, applied, workspace_id, applied.state)
                    await db.commit()
                return {"status": "cancelled", "content": ""}

            await self._apply_state(task_ref, TaskLifecycleState.RUNNING, workspace_id=workspace_id)
            await self._emit_state_event(db, task_ref, run_ref, workspace_id, TaskLifecycleState.RUNNING)
            await db.commit()

            outcome: dict[str, Any] = {"status": "failed", "content": "", "error": "dispatch produced no result"}
            try:
                content = await self._execute_via_entry(
                    db,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    engine_session=engine_session,
                    messages=messages,
                    model=model,
                    stream=stream,
                    goal=goal,
                    user_id=user_id,
                    task_ref=task_ref,
                    run_ref=run_ref,
                )
                outcome = {"status": "succeeded", "content": content}
            except Exception as exc:  # noqa: BLE001 — an execution failure maps to task failure
                logger.warning("task %s execution failed: %s", task_ref.id, exc)
                outcome = {"status": "failed", "content": "", "error": str(exc)}

            terminal = TaskLifecycleState.SUCCEEDED if outcome["status"] == "succeeded" else TaskLifecycleState.FAILED
            # Stream-mode executions leave uncommitted event rows on this
            # session; close them before the seam store's own transaction.
            await db.commit()
            await self._apply_state(task_ref, terminal, workspace_id=workspace_id)
            await self._emit_state_event(db, task_ref, run_ref, workspace_id, terminal)
            try:
                run = await TaskRunRegistry(db).get_run(uuid.UUID(run_ref.id), workspace_id)
                await TaskRunRegistry(db).update_projection(
                    run.id,
                    workspace_id,
                    projection={
                        "state": outcome["status"],
                        "error": outcome.get("error"),
                        "result_preview": (outcome.get("content") or "")[:2000],
                    },
                )
            except TaskRunRegistryError:
                logger.warning("task %s run projection update skipped", task_ref.id)
            await PlatformEventService(db).emit(
                task_ref=task_ref,
                run_ref=run_ref,
                payload_schema_ref=RUN_TERMINAL,
                payload={"status": outcome["status"], "error": outcome.get("error")},
                actor=ActorRef(kind=ActorKind.PLATFORM, id="task-control"),
                workspace_id=workspace_id,
            )
            await db.commit()
            return outcome
        finally:
            # Terminal guards never arbitrate again; cancel on a terminal task
            # is rejected by state, so dropping the guard is safe.
            if await self._lifecycle_state(task_ref) in (
                TaskLifecycleState.SUCCEEDED.value,
                TaskLifecycleState.FAILED.value,
                TaskLifecycleState.CANCELLED.value,
            ):
                _guards.pop(task_id, None)

    async def _execute_via_entry(
        self,
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        engine_session: uuid.UUID,
        messages: list[dict[str, Any]],
        model: str | None,
        stream: bool,
        goal: str,
        user_id: uuid.UUID | None,
        task_ref: BackendRef,
        run_ref: BackendRef,
    ) -> str:
        """Run one execution through the platform entry service.

        Mirrors the scheduled agent executor's assembly (shared event
        store, agent tool surface, guardrail bundle) and correlates to the
        pre-created task via ``existing_task_id``; the pre-created run row
        carries the engine session as its backend reference. Stream mode
        persists the mapped envelopes so the run's event stream is durable.
        """
        from hecate_llm.service import llm_service

        from hecate.core.composition.entry_assembly import (
            build_tool_registry,
            get_shared_event_store,
            load_agent_tools,
        )
        from hecate.core.composition.guardrail_platform import assemble_guardrails
        from hecate.core.composition.runtime_port_adapter import create_runtime_port
        from hecate.execution.entry_service import CorrelationInput, EntryExecutionService

        agent = await db.get(AgentModel, agent_id)
        if agent is None:
            raise TaskControlValidationError(f"agent {agent_id} no longer resolves")

        event_store = None
        tool_registry = None
        effective_tools: list[dict[str, Any]] = []
        bundle = None
        if agent.tools:
            event_store = get_shared_event_store()
            tool_registry = build_tool_registry(db, workspace_id=workspace_id)
            effective_tools = await load_agent_tools(db, agent.tools or [], workspace_id=workspace_id)
            if effective_tools:
                bundle = await assemble_guardrails(
                    db,
                    workspace_id=workspace_id,
                    agent_id=agent.id,
                    guardrail_config=getattr(agent, "guardrail_config", None),
                    event_store=event_store,
                    session_id=None,
                    dlp_scanner=None,
                )

        port = create_runtime_port(db, llm_service, tool_registry=tool_registry)
        entry = EntryExecutionService(
            port=port,
            entry_name="task-control",
            db=db,
            event_store=event_store,
            access_policy=bundle.access_policy if bundle else None,
            approval_callback=bundle.approval_callback if bundle else None,
            tool_policy_rules=bundle.rules if bundle else None,
            middleware_chains=bundle.middleware_chains if bundle else None,
            denial_tracker=bundle.denial_tracker if bundle else None,
        )
        correlation = CorrelationInput(
            workspace_id=workspace_id,
            agent_id=agent.id,
            user_id=user_id,
            session_id=engine_session,
            goal=goal[:200],
            existing_task_id=uuid.UUID(task_ref.id),
        )
        model_name = model or (
            agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
        )
        outcome = await entry.execute(
            agent_mode="chat",
            messages=messages,
            model=model_name,
            tools=effective_tools or None,
            stream=stream,
            session_id=engine_session,
            agent_id=agent.id,
            workspace_id=workspace_id,
            correlation=correlation,
        )
        if stream:
            result_gen = outcome.result
            if isinstance(result_gen, dict):
                return str(result_gen.get("content", "") or "")
            mapper = RunEventMapper(task_ref, run_ref)
            events = PlatformEventService(db)
            content_parts: list[str] = []
            async for raw in result_gen:
                envelope = mapper.map_stream_event(raw if isinstance(raw, dict) else {"type": "raw"})
                await events.append_resequenced(envelope, workspace_id=workspace_id)
                if envelope.payload.get("type") == "message" and envelope.payload.get("content"):
                    content_parts.append(str(envelope.payload["content"]))
            return "".join(content_parts)
        result = outcome.result
        if not isinstance(result, dict):
            raise TaskControlValidationError(f"unexpected entry result type {type(result)}")
        return str(result.get("content", "") or "")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _run_summary(run: RunModel) -> dict[str, Any]:
        return {
            "run_id": str(run.id),
            "attempt_no": run.attempt_no,
            "deployment_id": str(run.deployment_id),
            "backend_ref": run.backend_ref,
            "projection": run.projection,
            "event_cursor": run.event_cursor,
        }

    async def _get_task(self, workspace_id: uuid.UUID, task_id: uuid.UUID) -> TaskModel:
        try:
            return await self._registry.get_task(task_id, workspace_id)
        except TaskNotFoundError as exc:
            raise TaskControlNotFoundError(str(exc)) from exc

    async def _default_deployment(self, agent_id: uuid.UUID, workspace_id: uuid.UUID) -> AgentDeploymentModel | None:
        row = await self._db.execute(
            select(AgentDeploymentModel).where(
                AgentDeploymentModel.agent_id == agent_id,
                AgentDeploymentModel.workspace_id == workspace_id,
                AgentDeploymentModel.is_default.is_(True),
                ~AgentDeploymentModel.deleted,
            )
        )
        return row.scalar_one_or_none()

    async def _identity_chain(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        user_id: uuid.UUID | None,
        deployment: AgentDeploymentModel,
    ) -> IdentityChain:
        row = await self._db.execute(
            select(AgentPrincipalModel).where(
                AgentPrincipalModel.agent_id == agent_id,
                AgentPrincipalModel.workspace_id == workspace_id,
                ~AgentPrincipalModel.deleted,
            )
        )
        principal = row.scalar_one_or_none()
        return IdentityChain(
            initiator=str(user_id) if user_id is not None else None,
            principal_id=str(principal.id) if principal is not None else "platform-system",
            workload=WorkloadIdentity(
                deployment=deployment_ref(deployment.issuer_domain, str(deployment.id)),
                workload_id="hecate:task-control",
            ),
            audience="hecate:task-control",
        )

    async def _apply_state(
        self,
        task_ref: BackendRef,
        target: TaskLifecycleState,
        *,
        workspace_id: uuid.UUID | None = None,
        expected_revision: int | None = None,
        reconciled: bool = False,
    ) -> TaskStateRecord:
        try:
            record = await asyncio.to_thread(
                self._store.apply_task_state,
                task_ref,
                target,
                expected_revision=expected_revision,
                reconciled=reconciled,
                recorded_at=_utc_now_iso(),
            )
        except InvalidTaskTransitionError as exc:
            raise TaskControlValidationError(str(exc)) from exc
        except ValueError as exc:  # stale revision / first-state violations
            raise TaskControlValidationError(str(exc)) from exc
        if workspace_id is not None and hasattr(self._store, "attach_workspace"):
            # Table-backed adapters persist workspace attribution for scoped
            # queries; the stub has no rows and ignores attribution.
            await asyncio.to_thread(self._store.attach_workspace, task_ref, workspace_id)
        return record

    async def _lifecycle_state(self, task_ref: BackendRef) -> str:
        record = await asyncio.to_thread(self._store.get_task_state, task_ref)
        return record.lifecycle_state.value if record is not None else UNRECORDED_LIFECYCLE

    async def _lifecycle_map(self, workspace_id: uuid.UUID, task_ids: list[str]) -> dict[str, str]:
        if not task_ids:
            return {}
        rows = (
            (
                await self._db.execute(
                    select(TaskLifecycleStateModel).where(
                        TaskLifecycleStateModel.workspace_id == workspace_id,
                        TaskLifecycleStateModel.task_ref_id.in_(task_ids),
                        TaskLifecycleStateModel.deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.task_ref_id: row.lifecycle_state for row in rows}

    async def _attribute_command(self, command_id: str, workspace_id: uuid.UUID) -> None:
        if self._backend == POSTGRES_BACKEND:
            await self._db.execute(
                update(ControlCommandModel)
                .where(ControlCommandModel.command_id == command_id)
                .values(workspace_id=workspace_id)
            )
            await self._db.flush()
        else:
            _command_workspaces[command_id] = workspace_id

    async def _emit_state_event(
        self,
        db: AsyncSession,
        task_ref: BackendRef,
        run_ref: BackendRef,
        workspace_id: uuid.UUID,
        state: TaskLifecycleState,
    ) -> None:
        """One governance event per lifecycle transition (dispatch-side)."""
        await PlatformEventService(db).emit(
            task_ref=task_ref,
            run_ref=run_ref,
            payload_schema_ref=TASK_STATE_CHANGED,
            payload={"lifecycle_state": state.value},
            actor=ActorRef(kind=ActorKind.PLATFORM, id="task-control"),
            workspace_id=workspace_id,
        )

    async def _emit_command_event(
        self,
        task_ref: BackendRef,
        record: ControlCommandRecord,
        workspace_id: uuid.UUID,
        state: CommandState,
    ) -> None:
        run_ref = record.run_ref or BackendRef(RefKind.RUN, task_ref.issuer_domain, "unassigned")
        await PlatformEventService(self._db).emit(
            task_ref=task_ref,
            run_ref=run_ref,
            payload_schema_ref=COMMAND_RECORDED if state is CommandState.REQUESTED else COMMAND_TRANSITIONED,
            payload={"command_id": record.command_id, "kind": record.kind.value, "state": state.value},
            actor=ActorRef(kind=ActorKind.SERVICE, id=record.issuer),
            workspace_id=workspace_id,
        )

    @staticmethod
    def _actor(user_id: uuid.UUID | None) -> ActorRef:
        if user_id is not None:
            return ActorRef(kind=ActorKind.HUMAN, id=str(user_id))
        return ActorRef(kind=ActorKind.SERVICE, id="task-control")
