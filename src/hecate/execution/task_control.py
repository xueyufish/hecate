"""Platform task control service (step6 platform track).

The application service behind ``/api/tasks``: idempotent submission,
Task lifecycle projection over the durable seam, control-command
receipts, run projections, and the pending-reconciliation view. State
vocabulary and transition rules come exclusively from
``contracts/execution/durable`` — this module never invents states.

Dispatch is **durable**: submission persists the input payload on the
durable task row and hands the task to the durable worker (lease-claimed,
reconciled across restarts, bounded retries). The execution callback lives
in :mod:`hecate.execution.task_dispatcher`; this service only triggers it —
through the worker when one is bound, or inline (same code path minus the
lease) when no worker is wired, e.g. on the stub binding in tests.

Cancellation is honest about what the platform can enforce. A queued or
waiting task is genuinely cancelled — the command path CAS-advances the
lifecycle, so a racing dispatch loses its claim and the receipt is
``applied``. A running execution has no cooperative abort channel, so its
command stays ``requested`` with an explicit note; commands against
terminal tasks are ``rejected``. ``applied`` never appears without a real
effect, and an HTTP success response never claims it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import false, func, select
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
from hecate.execution.governance_events import RUN_TERMINAL, PlatformEventService
from hecate.execution.task_dispatcher import PlatformTaskDispatcher, run_row_ref
from hecate.execution.task_run_registry import (
    TaskNotFoundError,
    TaskRunRegistry,
    TaskRunRegistryError,
)
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AgentDeploymentModel
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.run import RunModel
from hecate.models.task import TaskModel

logger = logging.getLogger(__name__)

PLATFORM_ISSUER = "hecate"
UNRECORDED_LIFECYCLE = "unrecorded"
# Callback declarations may only assert platform-terminal child states; a
# reconciling child is an unknown result, never a workflow continuation fact.
_TERMINAL_LIFECYCLE_STATES = frozenset(
    {
        TaskLifecycleState.SUCCEEDED.value,
        TaskLifecycleState.FAILED.value,
        TaskLifecycleState.CANCELLED.value,
    }
)
POSTGRES_BACKEND = "postgres"
_CANCEL_RUNNING_NOTE = (
    "running execution has no cooperative abort channel; the command stays requested and the task runs to its outcome"
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


# Strong references for fire-and-forget dispatch triggers (an unreferenced
# task can be garbage-collected mid-execution).
_background_tasks: set[asyncio.Task[None]] = set()


def _spawn_background(coro) -> None:  # noqa: ANN001 — the coroutine type varies by caller
    """Schedule one fire-and-forget dispatch, keeping a strong reference."""

    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@dataclass(frozen=True)
class _ApprovalWakeGate:
    """Outcome of the ``waiting_approval`` resume authorization gate.

    ``refuse``: wake refused, token NOT consumed (a later real decision can
    still resume the wait). ``reject_terminal``: consume the token and fail
    the task (rejected decision). ``authorize``: proceed, ``fact`` merged
    into the persisted wake input.
    """

    action: str
    detail: str = ""
    fact: dict[str, Any] | None = None


def task_ref_of(task_id: uuid.UUID | str) -> BackendRef:
    """Platform-issued task reference for one task row."""
    return task_ref(PLATFORM_ISSUER, str(task_id))


def command_receipt_summary(record: ControlCommandRecord) -> dict[str, Any]:
    """Return only public command receipt fields, never control payloads."""
    return {
        "command_id": record.command_id,
        "kind": record.kind.value,
        "state": record.state.value,
        "issued_at": record.issued_at,
        "issuer": record.issuer,
        "task_ref": record.task_ref.to_dict(),
        "run_ref": record.run_ref.to_dict() if record.run_ref is not None else None,
        "expires_at": record.expires_at,
        "expected_revision": record.expected_revision,
    }


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
        dispatcher: PlatformTaskDispatcher | None = None,
        worker: Any | None = None,
        relay: Any | None = None,
    ) -> None:
        self._db = db
        self._store = store
        self._recorder = recorder
        self._backend = backend
        self._ledger_source = ledger_source
        self._session_factory = session_factory
        self._worker = worker
        self._relay = relay
        if relay is None and hasattr(store, "session_factory"):
            from hecate_durable.worker import OutboxRelay

            from hecate.core.database import async_session_factory
            from hecate.execution.event_relay import PlatformOutboxProjector

            self._relay = OutboxRelay(
                store.session_factory,
                PlatformOutboxProjector(store, session_factory or async_session_factory),
                relay_key="platform",
            )
        self._registry = TaskRunRegistry(db)
        if dispatcher is not None:
            self._dispatcher = dispatcher
        elif worker is None:
            # Inline dispatch (stub binding or worker off): the callback must
            # open its sessions from THIS service's factory — the global one
            # may point at a different database in tests/embeddings.
            from hecate.execution.task_dispatcher import PlatformTaskDispatcher

            self._dispatcher = PlatformTaskDispatcher(store, session_factory)
        else:
            self._dispatcher = None

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
        """Idempotently submit one task; the durable worker dispatches it."""
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
        if deployment.backend_type != "builtin" or deployment.access_mode != "in_process":
            raise TaskControlValidationError("task-control dispatch currently supports builtin/in_process deployments")

        digest = canonical_request_digest({"goal": goal, "agent_id": str(agent_id), "input": input, "stream": stream})
        minted_task, minted_run, engine_session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        t_ref = task_ref_of(minted_task)
        run_backend_ref = run_ref(deployment.issuer_domain, str(engine_session))
        # The input payload rides the durable task row: a restart re-dispatches
        # from durable state, never from a lost in-process closure.
        chain = await self._identity_chain(workspace_id, agent_id, user_id, deployment)
        if "workflow" in input:
            # step6e: fail fast on a malformed declaration instead of
            # silently degrading the task into a plain agent run.
            from hecate.execution.workflow_child import WorkflowChildTaskAdapter

            try:
                WorkflowChildTaskAdapter.plan(input)
            except ValueError as exc:
                raise TaskControlValidationError(f"invalid workflow payload: {exc}") from exc
        persisted_input = {
            "goal": goal,
            "agent_id": str(agent_id),
            "deployment_id": str(deployment.id),
            "workspace_id": str(workspace_id),
            "user_id": str(user_id) if user_id else None,
            "messages": messages,
            "model": input.get("model"),
            "stream": stream,
            "identity_chain": chain.to_dict(),
            "platform_run_id": str(minted_run),
            "backend_run_ref": run_backend_ref.to_dict(),
            # step6e: the declarative workflow block rides the payload, and
            # workflow children carry their parent stamp through the same
            # durable surface.
            "workflow": input.get("workflow"),
            "workflow_parent": input.get("workflow_parent"),
            "workflow_step": input.get("workflow_step"),
            # step5e: durable approval wait opt-in — the dispatch assembly
            # parks on a policy-required approval instead of asking inline.
            "approval_wait": input.get("approval_wait") is True,
        }
        key = IdempotencyKey(
            key=idempotency_key or f"auto:{minted_task}",
            subject=str(user_id) if user_id is not None else "anonymous",
            workspace=str(workspace_id),
            request_digest=digest,
        )
        try:
            association: SubmissionAssociation = await asyncio.to_thread(
                self._store.submit_task,
                key=key,
                task_ref=t_ref,
                run_ref=BackendRef(RefKind.RUN, PLATFORM_ISSUER, str(minted_run)),
                input_payload=persisted_input,
            )
        except IdempotencyConflictError as exc:
            raise SubmissionConflictError(
                f"idempotency key {idempotency_key!r} is already registered with a different request digest"
            ) from exc
        if association.task_ref != t_ref:
            saved = await asyncio.to_thread(self._store.get_task_input, association.task_ref) or {}
            if "identity_chain" in saved:
                from hecate.execution.task_registration import ensure_submission_registration

                await ensure_submission_registration(
                    self._db, association.task_ref, saved, sql_store=hasattr(self._store, "engine")
                )
            return await self._replay_result(workspace_id, association)
        try:
            from hecate.execution.task_registration import ensure_submission_registration

            task, run = await ensure_submission_registration(
                self._db, t_ref, persisted_input, sql_store=hasattr(self._store, "engine")
            )
        except TaskRunRegistryError as exc:
            raise TaskControlValidationError(str(exc)) from exc
        # Release the registration transaction before dispatch writes through
        # the durable store's separate engine/session.
        await self._db.commit()

        if hasattr(self._store, "attach_workspace"):
            # Table-backed stores persist workspace attribution for scoped
            # queries; the stub records it in extra and ignores scoping.
            await asyncio.to_thread(self._store.attach_workspace, t_ref, workspace_id)
        r_ref = run_row_ref(run)

        if wait:
            await self._dispatch_now(t_ref)
            return await self._submit_wait_result(t_ref, r_ref, workspace_id)
        _spawn_background(self._dispatch_task(t_ref))
        return SubmitResult(
            task_ref=t_ref,
            run_ref=r_ref,
            lifecycle_state=await self._lifecycle_state(t_ref),
            replayed=False,
        )

    async def _dispatch_now(self, t_ref: BackendRef, *, db: AsyncSession | None = None) -> None:
        """Dispatch one task synchronously (the ``wait`` submission view)."""

        active_db = db or self._db

        if self._worker is not None:
            await self._worker.dispatch_once(t_ref)
            await self._pump_events()
            return
        if hasattr(self._store, "engine") and self._dispatcher is not None:
            from hecate_durable.worker import DurableWorker

            async def inline_dispatch(task_ref, record, lease) -> None:
                await self._dispatcher(task_ref, record, lease, db=active_db)

            worker = DurableWorker(self._store, inline_dispatch, leases=self._store.leases)
            await worker.dispatch_once(t_ref)
            await self._pump_events()
            return
        record = await asyncio.to_thread(self._store.get_task_state, t_ref)
        if record is None or record.lifecycle_state is not TaskLifecycleState.QUEUED:
            return
        if self._dispatcher is None:
            raise TaskControlValidationError("no dispatcher bound; cannot execute tasks")
        await asyncio.to_thread(
            self._store.apply_task_state, t_ref, TaskLifecycleState.RUNNING, expected_revision=record.revision
        )
        # Inline dispatch runs on this request's session (same DB); the
        # worker path opens its own sessions instead.
        await self._dispatcher(t_ref, record, None, db=active_db)
        await self._pump_events()

    async def _pump_events(self) -> None:
        if self._relay is not None:
            try:
                await self._relay.pump_once()
            except Exception:
                logger.exception("outbox projection deferred; authoritative state remains durable")

    async def _dispatch_task(self, t_ref: BackendRef) -> None:
        """Background dispatch trigger; same path as the worker's cycle."""

        try:
            if self._worker is not None:
                await self._worker.dispatch_once(t_ref)
                return
            if self._session_factory is None:
                await self._dispatch_now(t_ref)
                return
            async with self._session_factory() as session:
                await self._dispatch_now(t_ref, db=session)
                await session.commit()
        except Exception:  # noqa: BLE001 — a background dispatch never crashes the loop
            logger.exception("background dispatch failed for task %s", t_ref.id)

    async def _submit_wait_result(self, t_ref: BackendRef, r_ref: BackendRef, workspace_id: uuid.UUID) -> SubmitResult:
        state = await self._lifecycle_state(t_ref)
        result: dict[str, Any] | None = None
        if state in (TaskLifecycleState.SUCCEEDED.value, TaskLifecycleState.FAILED.value):
            # Terminal on return: surface the run projection as the outcome.
            try:
                run = await self._registry.get_run(uuid.UUID(r_ref.id), workspace_id)
                projection = run.projection or {}
                result = {
                    "status": projection.get("state", state),
                    "content": str(projection.get("result_preview") or ""),
                    "error": projection.get("error"),
                }
            except (TaskRunRegistryError, TaskControlNotFoundError):
                result = None
        return SubmitResult(
            task_ref=t_ref,
            run_ref=r_ref,
            lifecycle_state=state,
            replayed=False,
            result=result,
            reason=None if result is not None else "wait view returned before a terminal outcome",
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
        """Task responsibility record + lifecycle projection + runs (+ wait info)."""
        task = await self._get_task(workspace_id, task_id)
        runs = await self._registry.list_runs_for_task(task.id, workspace_id)
        t_ref = task_ref_of(task.id)
        record = await asyncio.to_thread(self._store.get_task_state, t_ref)
        detail: dict[str, Any] = {
            "task_id": str(task.id),
            "goal": task.goal,
            "issuer_domain": task.issuer_domain,
            "workspace_id": str(task.workspace_id),
            "lifecycle_state": record.lifecycle_state.value if record else UNRECORDED_LIFECYCLE,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "runs": [self._run_summary(run) for run in runs],
        }
        wait = (record.extra or {}).get("wait") if record else None
        if wait:
            detail["wait"] = wait
        return detail

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
            # Lifecycle states live on the durable task rows; workspace
            # attribution (set at submit) scopes them to this workspace.
            try:
                target = TaskLifecycleState(state)
            except ValueError as exc:
                raise TaskControlValidationError(f"unknown lifecycle state {state!r}") from exc
            records = await asyncio.to_thread(self._store.list_tasks, {target})
            task_ids = [
                record.task_ref.id for record in records if str(record.extra.get("workspace_id")) == str(workspace_id)
            ]
            parsed: list[uuid.UUID] = []
            for raw in task_ids:
                try:
                    parsed.append(uuid.UUID(raw))
                except ValueError:
                    continue
            base = base.where(TaskModel.id.in_(parsed) if parsed else false())
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

    async def list_run_actions(self, workspace_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any]:
        """Read one authorized Run's local action ledger without changing state.

        Both the Run and its parent Task must remain visible in the workspace.
        Platform ledger rows use the platform Run reference, independently of
        the engine session/backend reference. An unsupported store is labeled;
        query failures never become an empty successful response.
        """
        try:
            run = await self._registry.get_run(run_id, workspace_id)
            await self._registry.get_task(run.task_id, workspace_id)
        except TaskNotFoundError as exc:
            raise TaskControlNotFoundError(f"run {run_id} not found in workspace") from exc
        query = getattr(self._store, "list_run_actions", None)
        if not callable(query):
            return {
                "run_id": str(run.id),
                "task_id": str(run.task_id),
                "actions": [],
                "ledger_source": self._ledger_source,
                "supported": False,
            }
        try:
            rows = await asyncio.to_thread(query, run_row_ref(run))
            actions = [self._run_action_summary(row) for row in rows]
        except Exception as exc:
            # Driver exceptions may contain URLs, SQL parameters or result
            # payloads; log only safe attribution and never publish the text.
            logger.error("Action ledger query failed for run %s (%s)", run_id, type(exc).__name__)
            raise TaskControlError(f"action ledger query failed for run {run_id}") from exc
        return {
            "run_id": str(run.id),
            "task_id": str(run.task_id),
            "actions": actions,
            "ledger_source": self._ledger_source,
            "supported": True,
        }

    @staticmethod
    def _run_action_summary(row: dict[str, Any]) -> dict[str, Any]:
        """Whitelist projection of one ledger action row.

        Field-by-field construction on purpose: raw arguments, full result
        payloads, session ids and claim tokens never enter the response, and
        future ledger columns stay out by default.
        """
        intent = row.get("intent") or {}
        outcome = row.get("last_outcome") or {}
        return {
            "action_key": row.get("action_key"),
            "execution_id": row.get("execution_id"),
            "tool_call_id": row.get("tool_call_id"),
            "tool_name": intent.get("action_name"),
            "arguments_digest": intent.get("arguments_digest"),
            "side_effect_class": intent.get("side_effect_class"),
            "state": row.get("state"),
            "outcome": outcome.get("outcome"),
            "result_digest": outcome.get("result_digest"),
            "pending_reconciliation": row.get("pending_reconciliation"),
        }

    async def assert_run_observable(self, *, workspace_id: uuid.UUID, run_id: uuid.UUID) -> RunModel:
        """Raise the observation not-found error for an invisible run/task pair.

        REST read gates call this before any role decision or relay
        projection: the run and its parent task must both be live rows in
        the workspace (registry semantics keep missing, foreign and
        soft-deleted rows indistinguishable).
        """
        try:
            run = await self._registry.get_run(run_id, workspace_id)
            await self._registry.get_task(run.task_id, workspace_id)
        except TaskNotFoundError as exc:
            raise TaskControlNotFoundError(f"run {run_id} not found in workspace") from exc
        return run

    async def read_run_events(
        self,
        workspace_id: uuid.UUID,
        run_id: uuid.UUID,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> EventPage:
        run = await self.assert_run_observable(workspace_id=workspace_id, run_id=run_id)
        await self._pump_events()
        if run.projection.get("entry_name"):
            from hecate.core.composition.entry_assembly import get_shared_event_store
            from hecate.execution.entry_events import RunEventMapper

            mapper = RunEventMapper(
                BackendRef(RefKind.TASK, run.issuer_domain, str(run.task_id)),
                run_row_ref(run),
                event_store=get_shared_event_store(),
            )
            page = await mapper.read_events(cursor=cursor, limit=limit)
            # Backend snapshots and rejected raw candidates are evidence for
            # trusted diagnostics, not part of the public event projection.
            return replace(page, events=tuple(replace(event, extra={}) for event in page.events))
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
        run = await self.assert_run_observable(workspace_id=workspace_id, run_id=run_id)
        if run.projection.get("entry_name"):
            from hecate.contracts.execution.events import EventKind

            current = cursor
            while True:
                page = await self.read_run_events(workspace_id, run_id, cursor=current)
                for envelope in page.events:
                    yield envelope
                    current = str(envelope.source_sequence)
                    if envelope.kind is EventKind.TERMINAL:
                        return
                if not page.has_more:
                    await self._db.refresh(run)
                    if run.projection.get("state") in _TERMINAL_LIFECYCLE_STATES:
                        return
                await asyncio.sleep(poll_interval)
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
        command_id: str | None = None,
        payload: dict[str, Any] | None = None,
        payload_schema_ref: str | None = None,
        expected_revision: int | None = None,
        expires_at: str | None = None,
        detail_ns: dict[str, Any] | None = None,
    ) -> CommandIssueResult:
        """Record a command and act on what the platform can enforce."""
        if expires_at is not None:
            try:
                datetime.fromisoformat(expires_at)
            except (TypeError, ValueError) as exc:
                raise TaskControlValidationError("expires_at must be an ISO timestamp") from exc
        task = await self._get_task(workspace_id, task_id)
        t_ref = task_ref_of(task.id)
        existing = None
        # Authorize the resource and bind all immutable input before replay.
        if command_id is not None:
            existing = await asyncio.to_thread(self._recorder.get, command_id)
            if existing is not None:
                if (
                    existing.task_ref != t_ref
                    or existing.issuer != issuer
                    or existing.kind is not kind
                    or existing.payload != dict(payload or {})
                    or existing.payload_schema_ref != (payload_schema_ref if payload else None)
                    or existing.expires_at != expires_at
                    or existing.expected_revision != expected_revision
                    or existing.detail_ns != dict(detail_ns or {})
                    or existing.extra.get("workspace_id") != str(workspace_id)
                ):
                    raise TaskControlValidationError("command ID is bound to a different request")
                if existing.state in (CommandState.APPLIED, CommandState.REJECTED, CommandState.EXPIRED):
                    return CommandIssueResult(record=existing, detail="idempotent replay of a settled command")
        runs = await self._registry.list_runs_for_task(task.id, workspace_id)
        latest_run = runs[-1] if runs else None
        record = existing or ControlCommandRecord(
            command_id=command_id or str(uuid.uuid4()),
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
            # Workspace attribution rides the record (persisted with the
            # command row) so scoping works for both bindings uniformly.
            extra={"workspace_id": str(workspace_id)},
            detail_ns=dict(detail_ns) if detail_ns else {},
        )
        record = await asyncio.to_thread(self._recorder.record, record)
        # Close this session's write transaction before the recorder's own
        # transaction runs — the seam store never shares a transaction with
        # the application session (one independent session per transaction).
        await self._db.commit()

        # Managed executions own their task/run facts on the enrolled host.
        # Persist the command in the platform outbox and let the host report
        # its effect; applying the platform's local durable state here would
        # make delivery look like execution and could consume a wait twice.
        from hecate.execution.managed_channel import ManagedChannelError, ManagedDeliveryService

        try:
            managed_delivery = await ManagedDeliveryService(self._db).get_by_task_ref(workspace_id, t_ref.to_dict())
        except ManagedChannelError as exc:
            raise TaskControlValidationError(str(exc)) from exc
        if managed_delivery is not None:
            if _expired(record.expires_at):
                expired = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.EXPIRED)
                return CommandIssueResult(record=expired, detail="command expired before host delivery")
            if kind is ControlCommandKind.PAUSE:
                rejected = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
                return CommandIssueResult(record=rejected, detail="managed runner declares no pause capability")
            return CommandIssueResult(record=record, detail="command persisted for managed host delivery")

        if _expired(record.expires_at):
            expired = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.EXPIRED)
            return CommandIssueResult(record=expired, detail="command expired before execution")
        current = await asyncio.to_thread(self._store.get_task_state, t_ref)
        if record.expected_revision is not None and (current is None or current.revision != record.expected_revision):
            rejected = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            return CommandIssueResult(record=rejected, detail="expected task revision no longer matches")
        if kind is ControlCommandKind.CANCEL:
            detail = await self._request_cancel(t_ref, record, workspace_id)
        elif kind is ControlCommandKind.PROVIDE_INPUT:
            detail = await self._wake_waiting(
                t_ref, record, workspace_id, expected_state=TaskLifecycleState.WAITING_INPUT, payload=payload or {}
            )
        elif kind is ControlCommandKind.RESUME:
            detail = await self._wake_waiting(
                t_ref, record, workspace_id, expected_state=TaskLifecycleState.WAITING_APPROVAL, payload=payload or {}
            )
        else:
            # pause: builtin deployments declare no pause capability —
            # reject explicitly, never fake success.
            await self._db.commit()
            record = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            detail = "builtin deployments declare no pause capability"
        refreshed = await asyncio.to_thread(self._recorder.get, record.command_id)
        return CommandIssueResult(record=refreshed or record, detail=detail)

    async def submit_workflow_callback(
        self,
        *,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID,
        command_id: str,
        wait_token: str,
        child_task_id: uuid.UUID,
        declared_state: str,
        payload: dict[str, Any] | None = None,
        result_summary: dict[str, Any] | None = None,
        issuer: str = "workflow-callback",
    ) -> CommandIssueResult:
        """Verify a child task's real facts, then wake the waiting parent.

        step6e: the callback asserts a child outcome, but the platform only
        trusts ITS OWN records — the parent's wait contract must name this
        child, the child must live in the same workspace, and its recorded
        lifecycle state must be terminal and match the declared state. Any
        mismatch rejects the command without consuming the wait token. On a
        match the child's outcome summary rides the wake payload through the
        regular provide_input application (single-use token, merged input,
        requeue).
        """

        declared = declared_state.strip().lower()
        if declared not in _TERMINAL_LIFECYCLE_STATES:
            raise TaskControlValidationError(
                "declared_state must be one of: " + ", ".join(sorted(_TERMINAL_LIFECYCLE_STATES))
            )
        task = await self._get_task(workspace_id, task_id)
        t_ref = task_ref_of(task.id)
        callback_digest = canonical_request_digest(
            {
                "task_ref": t_ref.to_dict(),
                "workspace_id": str(workspace_id),
                "issuer": issuer,
                "child_task_id": str(child_task_id),
                "declared_state": declared,
                "wait_token": wait_token,
                "payload": payload or {},
                "result_summary": result_summary or {},
            }
        )
        existing = await asyncio.to_thread(self._recorder.get, command_id)
        if existing is not None:
            if (
                existing.task_ref != t_ref
                or existing.issuer != issuer
                or existing.extra.get("workspace_id") != str(workspace_id)
                or existing.detail_ns.get("callback_digest") != callback_digest
            ):
                raise TaskControlValidationError("callback command ID is bound to a different request")
            if existing.state in (CommandState.APPLIED, CommandState.REJECTED, CommandState.EXPIRED):
                return CommandIssueResult(record=existing, detail="idempotent replay of a settled command")
        current = await asyncio.to_thread(self._store.get_task_state, t_ref)
        wait = (current.extra or {}).get("wait") or {} if current else {}
        contract_child = (wait.get("contract_ref") or {}).get("await_task_ref")
        if not isinstance(contract_child, dict):
            rejected = await self._reject_callback(
                command_id,
                task,
                "task is not waiting on a child task callback",
                issuer=issuer,
                callback_digest=callback_digest,
            )
            return rejected
        if contract_child != task_ref_of(child_task_id).to_dict():
            rejected = await self._reject_callback(
                command_id,
                task,
                "callback child does not match the wait contract",
                issuer=issuer,
                callback_digest=callback_digest,
            )
            return rejected

        # The child's facts come from the platform's own records, scoped to
        # the same workspace (a foreign or nonexistent child can never pass).
        try:
            child = await self._get_task(workspace_id, child_task_id)
        except TaskControlNotFoundError:
            rejected = await self._reject_callback(
                command_id,
                task,
                "child task does not exist in this workspace",
                issuer=issuer,
                callback_digest=callback_digest,
            )
            return rejected
        child_record = await asyncio.to_thread(self._store.get_task_state, task_ref_of(child.id))
        child_state = child_record.lifecycle_state.value if child_record else UNRECORDED_LIFECYCLE
        if child_state not in _TERMINAL_LIFECYCLE_STATES:
            rejected = await self._reject_callback(
                command_id,
                task,
                f"child task is {child_state}, not in a terminal state",
                issuer=issuer,
                callback_digest=callback_digest,
            )
            return rejected
        if child_state != declared:
            rejected = await self._reject_callback(
                command_id,
                task,
                f"declared state {declared!r} does not match child task state {child_state!r}",
                issuer=issuer,
                callback_digest=callback_digest,
            )
            return rejected

        child_input = await asyncio.to_thread(self._store.get_task_input, task_ref_of(child.id)) or {}
        stamp = child_input.get("workflow_parent") or {}
        verified_payload = dict(payload or {})
        if stamp:
            if stamp.get("task_ref") != t_ref.to_dict() or stamp.get("step_index") != (
                wait.get("contract_ref") or {}
            ).get("step_index", stamp.get("step_index")):
                return await self._reject_callback(
                    command_id,
                    task,
                    "child workflow step does not match parent wait",
                    issuer=issuer,
                    callback_digest=callback_digest,
                )
            if "step_index" in verified_payload and verified_payload["step_index"] != stamp.get("step_index"):
                return await self._reject_callback(
                    command_id,
                    task,
                    "callback step_index does not match child records",
                    issuer=issuer,
                    callback_digest=callback_digest,
                )
            verified_payload["step_index"] = stamp["step_index"]
        # Result contents must come from the child's committed facts. A
        # caller's success summary cannot manufacture a missing artifact.
        facts = (child_record.extra or {}).get("result") or {}
        if stamp and not facts:
            return await self._reject_callback(
                command_id, task, "child result facts are missing", issuer=issuer, callback_digest=callback_digest
            )
        child_summary = {**(facts or result_summary or {}), "child_task_id": str(child.id), "child_state": child_state}
        merged_payload = {**verified_payload, "child_outcome": child_summary}
        return await self.issue_command(
            workspace_id=workspace_id,
            task_id=task_id,
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer=issuer,
            command_id=command_id,
            payload=merged_payload,
            payload_schema_ref="urn:hecate:workflow-callback:child-outcome/0",
            detail_ns={"wait_token": wait_token, "callback_digest": callback_digest},
            # The caller's command_id makes the callback idempotent end to
            # end: issue_command records under it and replays the receipt.
        )

    async def _reject_callback(
        self, command_id: str, task: Any, reason: str, *, issuer: str, callback_digest: str
    ) -> CommandIssueResult:
        """Record an explicit rejection receipt for one callback attempt."""
        t_ref = task_ref_of(task.id)
        record = ControlCommandRecord(
            command_id=command_id,
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer=issuer,
            task_ref=t_ref,
            issued_at=_utc_now_iso(),
            state=CommandState.REQUESTED,
            extra={"workspace_id": str(task.workspace_id)},
            detail_ns={"callback_digest": callback_digest},
        )
        record = await asyncio.to_thread(self._recorder.record, record)
        await self._db.commit()
        rejected = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
        return CommandIssueResult(record=rejected or record, detail=reason)

    async def _wake_waiting(
        self,
        t_ref: BackendRef,
        record: ControlCommandRecord,
        workspace_id: uuid.UUID,
        *,
        expected_state: TaskLifecycleState,
        payload: dict[str, Any],
    ) -> str:
        """Wake a durable wait with the single-use token (revision-CAS consumed)."""

        state = await self._lifecycle_state(t_ref)
        current = await asyncio.to_thread(self._store.get_task_state, t_ref)
        wait = (current.extra or {}).get("wait") or {} if current else {}
        if state != expected_state.value:
            await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            return f"task is {state}, not waiting for {expected_state.value}; wake rejected"
        # The token rides detail_ns (caller metadata, no payload schema
        # required); any provided input payload follows the envelope rule.
        token = str((record.detail_ns or {}).get("wait_token") or "")
        if not token or token != str(wait.get("wait_token")):
            await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            return "wake token missing or wrong; rejected"
        if wait.get("consumed"):
            await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            return "wake token already consumed; rejected"
        if _expired(wait.get("wait_expires_at")):
            await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            return "wake token expired; rejected"
        wake_payload = dict(payload)
        if expected_state is TaskLifecycleState.WAITING_APPROVAL:
            gate = await self._approval_wake_gate(current, wait, wake_payload, record)
            if gate.action == "refuse":
                # Token stays unconsumed: the wait remains resumable by a
                # later real decision until its own deadline.
                await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
                return gate.detail
            if gate.action == "reject_terminal":
                applied = await self._apply_approval_rejected(t_ref, record, current, wait, gate, workspace_id)
                return gate.detail if applied else "task moved on concurrently; wake rejected"
            if gate.fact:
                wake_payload.update(gate.fact)
        base_input = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
        merged_input = {**base_input, "provided": wake_payload}
        extra_update = {"wait": {**wait, "consumed": True}}
        if record.detail_ns.get("callback_digest") and "step_index" in wake_payload:
            callbacks = dict((current.extra or {}).get("_workflow_callbacks") or {})
            index = str(wake_payload["step_index"])
            if index in callbacks:
                await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
                return "workflow step callback already applied; rejected"
            callbacks[index] = {
                "child_task_id": (wake_payload.get("child_outcome") or {}).get("child_task_id"),
                "applied_fact_id": record.command_id,
            }
            extra_update["_workflow_callbacks"] = callbacks
        try:
            await asyncio.to_thread(
                self._store.apply_task_state,
                t_ref,
                TaskLifecycleState.QUEUED,
                expected_revision=record.expected_revision
                if record.expected_revision is not None
                else current.revision,
                extra_update=extra_update,
                input_payload=merged_input,
                **({"applied_command_id": record.command_id} if hasattr(self._store, "engine") else {}),
            )
        except (InvalidTaskTransitionError, ValueError):
            await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            return "task moved on concurrently; wake rejected"
        applied = (
            await asyncio.to_thread(self._recorder.get, record.command_id)
            if hasattr(self._store, "engine")
            else await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.APPLIED)
        )
        logger.info("task %s woken from %s; requeued with provided input", t_ref.id, expected_state.value)
        # A woken task must actually re-execute. Worker deployments leave
        # the requeued task to the durable worker's cycle; inline platform
        # deployments have no poller, so spawn the dispatch exactly like
        # submit does.
        if self._worker is None:
            _spawn_background(self._dispatch_task(t_ref))
        return f"wake accepted; task requeued (receipt {applied.state.value})"

    def _approval_ledger(self) -> Any | None:
        """The approval authority for wake gates; ``None`` fails closed."""

        from hecate.enterprise.approval_wait_ledger import task_approval_ledger

        return task_approval_ledger(self._db)

    async def _approval_wake_gate(
        self,
        current: Any,
        wait: dict[str, Any],
        wake_payload: dict[str, Any],
        record: ControlCommandRecord,
    ) -> _ApprovalWakeGate:
        """Authorization gate for ``waiting_approval`` resumes (step5e).

        The wake token is transport; the durable approval decision is the
        authorization. Reads the parked contract's ``approval_ref`` from the
        wait record and consults the ledger: pending/missing bindings refuse
        the wake WITHOUT consuming the token (a later real decision can still
        resume); a rejected decision consumes the token and fails the task
        with zero protected executions; an approved decision must still pass
        ``check_binding`` (revocation/expiry/params) — the wake re-verifies,
        the consumer later re-verifies again at dispatch.
        """

        from datetime import UTC, datetime

        from hecate_governance.approval import (
            ActionRequest,
            ApprovalGate,
            ApprovalJudgment,
            BindingCheck,
            check_binding,
        )

        contract = wait.get("contract_ref") or {}
        ref = str(contract.get("approval_ref") or "")
        if not ref:
            return _ApprovalWakeGate("refuse", "wait contract carries no approval reference; wake rejected")
        payload_ref = str(wake_payload.get("approval_ref") or "")
        if payload_ref and payload_ref != ref:
            return _ApprovalWakeGate("refuse", "payload approval reference does not match the wait contract; rejected")
        ledger = self._approval_ledger()
        if ledger is None:
            return _ApprovalWakeGate("refuse", "no approval authority available; wake rejected")
        try:
            binding = await ledger.get(ref)
        except Exception:
            logger.warning("approval authority unavailable for task %s", record.task_ref.id, exc_info=True)
            return _ApprovalWakeGate("refuse", "approval authority unavailable; wake rejected")
        if binding is None:
            return _ApprovalWakeGate("refuse", "no approval authority record for the parked action; wake rejected")
        # APPROVED: verify the binding against the parked action identity.
        # The request is built FROM the persisted contract so a drifted
        # binding (different tool/params than parked) fails the check.
        from hecate.execution.approval_wait import task_principal_fingerprint

        persisted = await asyncio.to_thread(self._store.get_task_input, record.task_ref) or {}
        actor = persisted.get("user_id") or f"agent:{persisted.get('agent_id')}"
        request = ActionRequest(
            invocation_id=str(contract.get("invocation_id") or f"wake:{record.command_id}"),
            tool_name=str(contract.get("tool_name") or ""),
            arguments_digest=str(contract.get("arguments_digest") or ""),
            target_resources=tuple(contract.get("target_resources") or ()),
            principal_fingerprint=task_principal_fingerprint(
                actor=str(actor),
                agent_principal=persisted.get("agent_id"),
            ),
            task_scope=record.task_ref.id,
        )
        verdict = check_binding(
            binding.model_copy(update={"judgment": ApprovalJudgment.APPROVED}), request, now=datetime.now(UTC)
        )
        if verdict.check is not BindingCheck.OK:
            detail = f"approval binding check failed ({verdict.check.value}); wake rejected"
            if verdict.detail:
                detail = f"approval binding check failed ({verdict.check.value}: {verdict.detail}); wake rejected"
            return _ApprovalWakeGate("refuse", detail)
        if binding.judgment is ApprovalJudgment.PENDING:
            return _ApprovalWakeGate("refuse", "approval decision is still pending; wake rejected")
        if binding.judgment is ApprovalJudgment.REJECTED:
            return _ApprovalWakeGate(
                "reject_terminal",
                "approval decision is rejected; task failed without executing the protected action",
            )
        verdict = await ApprovalGate(ledger).authorize(ref, request, now=datetime.now(UTC))
        if verdict.check is not BindingCheck.OK:
            return _ApprovalWakeGate("refuse", f"approval binding check failed ({verdict.detail}); wake rejected")
        if binding.consumed_by_invocation_id not in (None, request.invocation_id):
            return _ApprovalWakeGate("refuse", "approval already consumed by another action; wake rejected")
        return _ApprovalWakeGate("authorize", "", fact={"approval_ref": ref})

    async def _apply_approval_rejected(
        self,
        t_ref: BackendRef,
        record: ControlCommandRecord,
        current: Any,
        wait: dict[str, Any],
        gate: _ApprovalWakeGate,
        workspace_id: uuid.UUID,
    ) -> bool:
        """Consume the wait token and fail the task on a rejected decision."""

        try:
            outcome = {
                "status": "failed",
                "content": "",
                "error": "approval_rejected",
                "error_category": "approval_rejected",
            }
            await asyncio.to_thread(
                self._store.apply_task_state,
                t_ref,
                TaskLifecycleState.FAILED,
                expected_revision=record.expected_revision
                if record.expected_revision is not None
                else current.revision
                if current is not None
                else None,
                extra_update={
                    "wait": {**wait, "consumed": True},
                    "result": outcome,
                },
                **(
                    {
                        "applied_command_id": record.command_id,
                        "event_run_ref": record.run_ref,
                        "terminal_payload": outcome,
                    }
                    if hasattr(self._store, "engine")
                    else {}
                ),
            )
        except (InvalidTaskTransitionError, ValueError):
            logger.warning("task %s approval-rejected terminal skipped (moved concurrently)", t_ref.id)
            await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            return False
        if not hasattr(self._store, "engine"):
            await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.APPLIED)
        if record.run_ref is not None:
            await TaskRunRegistry(self._db).finalize_run(
                uuid.UUID(record.run_ref.id), workspace_id, state="failed", error_category="approval_rejected"
            )
            await self._db.commit()
        logger.info("task %s failed on rejected approval decision", t_ref.id)
        await self._converge_workflow_parent_on_rejection(t_ref, outcome)
        return True

    async def _converge_workflow_parent_on_rejection(self, t_ref: BackendRef, outcome: dict[str, Any]) -> None:
        """A rejected workflow child converges its waiting parent (step5e).

        The rejection terminal bypasses the dispatcher's ``_finish``, so
        without this the parent would sit in its durable wait until the
        token expired instead of applying its declared ``on_failure``
        policy. The callback rides the SAME verified path as any child
        terminal — the parent trusts only its own records — and its
        idempotency/verification semantics are inherited unchanged.
        Failures are logged, never surfaced: the child's terminal fact is
        already committed.
        """

        try:
            payload = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
            if not isinstance(payload.get("workflow_parent"), dict):
                return
            from hecate.execution.task_dispatcher import PlatformTaskDispatcher

            dispatcher = PlatformTaskDispatcher(self._store, self._session_factory)
            context = PlatformTaskDispatcher._context_of(t_ref, payload)
            await dispatcher._issue_child_callback(self._db, context, {**outcome, "status": "failed"})
        except Exception:  # noqa: BLE001 — convergence must not un-commit the terminal
            logger.warning("task %s rejection callback to workflow parent failed", t_ref.id, exc_info=True)

    async def _request_cancel(self, t_ref: BackendRef, record: ControlCommandRecord, workspace_id: uuid.UUID) -> str:
        state = await self._lifecycle_state(t_ref)
        if state in (
            TaskLifecycleState.SUCCEEDED.value,
            TaskLifecycleState.FAILED.value,
            TaskLifecycleState.CANCELLED.value,
        ):
            await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REJECTED)
            return f"task already terminal ({state}); cancel rejected"
        if state in (
            TaskLifecycleState.QUEUED.value,
            TaskLifecycleState.WAITING_INPUT.value,
            TaskLifecycleState.WAITING_APPROVAL.value,
        ):
            # Nothing is executing (or the wait can be abandoned): the CAS
            # on the lifecycle revision makes the cancel real. A dispatch
            # that raced the cancel loses its claim and skips.
            current = await asyncio.to_thread(self._store.get_task_state, t_ref)
            try:
                await asyncio.to_thread(
                    self._store.apply_task_state,
                    t_ref,
                    TaskLifecycleState.CANCELLED,
                    expected_revision=(
                        record.expected_revision
                        if record.expected_revision is not None
                        else current.revision
                        if current
                        else None
                    ),
                    **({"applied_command_id": record.command_id} if hasattr(self._store, "engine") else {}),
                )
            except (InvalidTaskTransitionError, ValueError):
                return "dispatch raced the cancel; the command stays requested"
            applied = (
                await asyncio.to_thread(self._recorder.get, record.command_id)
                if hasattr(self._store, "engine")
                else await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.APPLIED)
            )
            return f"cancel applied before dispatch (receipt {applied.state.value})"
        await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REQUESTED)
        return _CANCEL_RUNNING_NOTE

    async def assert_command_visible(self, *, workspace_id: uuid.UUID, command_id: str) -> None:
        """Raise the read not-found error for a command outside this workspace.

        REST read gates call this — a pure ``recorder.get``, never the
        lazy-expiry transition — before any role decision, so a rejected
        caller neither distinguishes missing from foreign commands nor
        advances a lapsed receipt. Mirrors ``assert_task_visible``.
        """
        record = await asyncio.to_thread(self._recorder.get, command_id)
        if record is None or str(record.extra.get("workspace_id")) != str(workspace_id):
            raise TaskControlNotFoundError(f"command {command_id} not found in workspace")

    async def get_command(self, workspace_id: uuid.UUID, command_id: str) -> ControlCommandRecord:
        """Read one command receipt with workspace scoping and lazy expiry."""
        record = await asyncio.to_thread(self._recorder.get, command_id)
        if record is None or str(record.extra.get("workspace_id")) != str(workspace_id):
            raise TaskControlNotFoundError(f"command {command_id} not found in workspace")
        if record.state is CommandState.REQUESTED and _expired(record.expires_at):
            record = await asyncio.to_thread(self._recorder.transition, command_id, CommandState.EXPIRED)
        return record

    async def list_task_commands(
        self,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read one visible task's command receipts without changing state.

        Pure recorded facts: states come back verbatim (a lapsed
        ``requested`` stays ``requested`` here — lazy expiry belongs to
        the single-command read and reconciliation), and query failures
        never become an empty successful response.
        """
        if not 1 <= limit <= 100 or offset < 0:
            raise TaskControlValidationError("Invalid command pagination")
        try:
            task = await self._get_task(workspace_id, task_id)
        except TaskControlNotFoundError as exc:
            raise TaskControlNotFoundError(f"task {task_id} not found in workspace") from exc
        try:
            items, total = await asyncio.to_thread(
                self._recorder.list_task_commands,
                str(workspace_id),
                task_ref(task.issuer_domain, str(task.id)),
                limit,
                offset,
            )
            return {"items": [command_receipt_summary(record) for record in items], "total": total}
        except Exception as exc:
            # Driver exceptions may contain URLs or SQL parameters; log only
            # safe attribution and never publish the text.
            logger.error("Command list query failed for task %s (%s)", task_id, type(exc).__name__)
            raise TaskControlError(f"command list query failed for task {task_id}") from exc

    # ------------------------------------------------------------------
    # reconciliation
    # ------------------------------------------------------------------

    async def pending_reconciliation(self, workspace_id: uuid.UUID) -> dict[str, Any]:
        """Read-only view of unconverged facts (lazy command expiry aside)."""
        records = await asyncio.to_thread(self._store.list_tasks, {TaskLifecycleState.RECONCILIATION_REQUIRED})
        tasks = [
            {
                "task_ref": record.task_ref.to_dict(),
                "revision": record.revision,
                "recorded_at": record.recorded_at,
                "last_error": record.extra.get("last_error"),
                "dispatch_attempts": record.extra.get("dispatch_attempts"),
            }
            for record in records
            if str((record.extra or {}).get("workspace_id")) == str(workspace_id)
        ]
        expired_commands: list[dict[str, Any]] = []
        for record in await asyncio.to_thread(self._recorder.list_pending_commands, str(workspace_id)):
            if _expired(record.expires_at):
                record = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.EXPIRED)
                expired_commands.append(
                    {
                        "command_id": record.command_id,
                        "kind": record.kind.value,
                        "state": record.state.value,
                        "expires_at": record.expires_at,
                        "task_ref": record.task_ref.to_dict(),
                    }
                )
        actions = await self._reconciliation_actions(workspace_id, tasks)
        relay_status = None
        if self._relay is not None:
            try:
                relay_status = await asyncio.to_thread(self._relay.status)
            except Exception:  # noqa: BLE001 — a relay status read never breaks the view
                logger.exception("relay status read failed")
        return {
            "workspace_id": str(workspace_id),
            "tasks": tasks,
            "commands": expired_commands,
            "actions": actions,
            "ledger_source": self._ledger_source,
            "event_relay": relay_status,
        }

    async def _reconciliation_actions(
        self, workspace_id: uuid.UUID, tasks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Per-action verdicts for reconciliation tasks (core ledger binding).

        The stub ledger carries no rows and returns an empty list — a
        labeled absence, not a scan result.
        """

        if not tasks or self._backend != POSTGRES_BACKEND or not hasattr(self._store, "list_run_actions"):
            return []
        actions: list[dict[str, Any]] = []
        for entry in tasks:
            try:
                task_id = uuid.UUID(entry["task_ref"]["id"])
            except (KeyError, ValueError):
                continue
            try:
                task = await self._get_task(workspace_id, task_id)
            except TaskControlNotFoundError:
                continue  # foreign/unresolvable task refs are not surfaced
            runs = await self._registry.list_runs_for_task(task.id, workspace_id)
            if not runs:
                continue
            run_actions = await asyncio.to_thread(self._store.list_run_actions, run_row_ref(runs[-1]))
            for action in run_actions:
                actions.append({"task_id": str(task.id), **action})
        return actions

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

    async def assert_task_visible(self, *, workspace_id: uuid.UUID, task_id: uuid.UUID) -> None:
        """Raise the command-issuance not-found error for an invisible task.

        REST write gates call this before any role decision so access
        checks never touch command storage or the replay path; missing,
        foreign and soft-deleted tasks stay indistinguishable (registry
        semantics).
        """
        await self._get_task(workspace_id, task_id)

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
        states: dict[str, str] = {}
        for raw in task_ids:
            record = await asyncio.to_thread(self._store.get_task_state, task_ref_of(raw))
            if record is not None:
                states[raw] = record.lifecycle_state.value
        return states
