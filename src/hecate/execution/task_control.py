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
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from hecate_runtime.accepted_decision import RunRef
from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.contracts.execution.durable import (
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyConflictError,
    IdempotencyKey,
    InvalidCommandTransitionError,
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
from hecate.execution.delivery_acceptance_view import read_acceptance_view
from hecate.execution.durable import ControlCommandRecorder, DurableTaskStore
from hecate.execution.governance_events import RUN_TERMINAL, PlatformEventService
from hecate.execution.task_dispatcher import PlatformTaskDispatcher, run_row_ref
from hecate.execution.task_input import (
    TASK_LIVE_INPUT_SCHEMA_REF,
    TASK_USER_INPUT_SCHEMA_REF,
    human_input_contract,
    input_wait_timeout,
    live_input_payload,
)
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


# Persisted per-step facts on the workflow parent's extra
# (``workflow_step_states``): the reclaim sweep writes them once the parent
# can no longer use the step, and the fenced creation/reservation reads them
# to refuse late dispatch into a closed step.
_WORKFLOW_STEP_CLOSED = "closed_unexecuted"
_WORKFLOW_STEP_HELD = "held_for_reconciliation"


def _same_command_request(existing: ControlCommandRecord, candidate: ControlCommandRecord) -> bool:
    """One command identity, one intent.

    Compare caller-owned intent, as the normal replay gate does. Receipt
    state, server-selected Run and effect metadata can advance while a
    concurrent submitter is still recording its request.
    """

    fields = (
        "command_id",
        "kind",
        "issuer",
        "task_ref",
        "expires_at",
        "expected_revision",
        "payload",
        "payload_schema_ref",
        "detail_ns",
    )
    return all(getattr(existing, key) == getattr(candidate, key) for key in fields) and (
        existing.extra.get("workspace_id") == candidate.extra.get("workspace_id")
    )


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
    expires_at: str | None = None
    clock: Callable[[], datetime] | None = None


def task_ref_of(task_id: uuid.UUID | str) -> BackendRef:
    """Platform-issued task reference for one task row."""
    return task_ref(PLATFORM_ISSUER, str(task_id))


def _is_child_task_input(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("agent_child_parent"), dict) or isinstance(payload.get("workflow_parent"), dict)


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
    # Whitelisted live-supplement delivery facts (received / bound /
    # consumed / not_applied with their authoritative locations); ``None``
    # for every other command vocabulary.
    delivery: dict[str, Any] | None = None


class _NullAccessGuard:
    """No-op stand-in so stream loops read the same with or without a guard."""

    async def before_read(self) -> None:
        pass

    async def before_send(self) -> None:
        pass

    async def idle(self, poll_interval: float) -> None:
        await asyncio.sleep(poll_interval)


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
        _verified_agent_child: bool = False,
        _verified_workflow_step: bool = False,
        dispatch_lease: Any = None,
        dispatch_parent_ref: BackendRef | None = None,
        dispatch_step_index: int | None = None,
    ) -> SubmitResult:
        """Idempotently submit one task; the durable worker dispatches it.

        ``dispatch_lease`` / ``dispatch_parent_ref`` / ``dispatch_step_index``
        are the host adapter's fence (workflow steps only): the child's
        creation commits only inside the parent's still-valid dispatch lease
        and while the step is open. Idempotent replays of an already-committed
        submission stay available regardless of the fence.
        """
        if (dispatch_lease is None) != (dispatch_parent_ref is None) or (dispatch_parent_ref is None) != (
            dispatch_step_index is None
        ):
            raise TaskControlValidationError("dispatch fencing needs lease, parent ref and step index together")
        if dispatch_lease is not None and not _verified_workflow_step:
            raise TaskControlValidationError("dispatch fencing is reserved for the host workflow adapter")
        if not goal or not goal.strip():
            raise TaskControlValidationError("goal must be a non-empty string")
        messages = input.get("messages")
        if not isinstance(messages, list) or not messages:
            raise TaskControlValidationError("input.messages must be a non-empty list")
        if not _verified_workflow_step and any(
            input.get(key) is not None for key in ("workflow_parent", "workflow_step")
        ):
            raise TaskControlValidationError("workflow_parent and workflow_step are reserved for the host adapter")
        if not _verified_workflow_step and idempotency_key and idempotency_key.startswith("workflow:"):
            raise TaskControlValidationError("workflow submission keys are reserved for the host adapter")
        workflow_step = input.get("workflow_step")
        workflow_controller_step = (
            _verified_workflow_step and isinstance(workflow_step, dict) and workflow_step.get("mode") == "controller"
        )
        if input.get("controller_loop") is True:
            from hecate_runtime.agent_controller import ControllerLimits
            from hecate_runtime.agent_harness import validate_harness_declaration

            from hecate.core.output_schema import check_output_schema

            options = input.get("controller", {})
            if not isinstance(options, dict):
                raise TaskControlValidationError("input.controller must be an object")
            try:
                if "skill_catalog" in options and type(options["skill_catalog"]) is not bool:
                    raise ValueError("controller.skill_catalog must be a boolean")
                ControllerLimits(max_turns=options.get("max_turns", 16), deadline=options.get("deadline"))
                from hecate_runtime.agent_controller import ControllerContinuationPolicy, NoProgressPolicy

                progress_policy = NoProgressPolicy.from_declaration(options.get("no_progress"))
                if progress_policy is not None:
                    progress_policy.validate_observations()
                input_wait_timeout(options.get("input_wait_timeout_seconds"))
                from hecate.execution.timer_wait import timer_wait_policy

                timer_policy = timer_wait_policy(options.get("timer_wait"))
                if timer_policy is not None:
                    if input.get("workflow") is not None:
                        raise ValueError("controller.timer_wait is unsupported for Workflow tasks")
                    if not hasattr(self._store, "engine") or not all(
                        callable(getattr(self._store, method, None))
                        for method in ("get_run_action", "action_receipt_time", "wait_now")
                    ):
                        raise ValueError("controller.timer_wait requires the platform SQL action ledger")
                from hecate.execution.agent_child import agent_child_policy

                agent_children = agent_child_policy(options.get("agent_children"))
                if agent_children is not None:
                    if input.get("workflow") is not None or input.get("workflow_parent") is not None:
                        raise ValueError("controller.agent_children is unsupported for Workflow tasks")
                    if not hasattr(self._store, "engine"):
                        raise ValueError("controller.agent_children requires the platform SQL action ledger")
                continuation_policy = ControllerContinuationPolicy.from_declaration(options.get("continuation"))
                if continuation_policy is not None and not isinstance(input.get("acceptance"), dict):
                    raise ValueError("controller.continuation requires a declared acceptance block")
                schema = input.get("output_schema")
                if schema is not None:
                    if not isinstance(schema, dict):
                        raise ValueError("output_schema must be an object")
                    check_output_schema(schema)
                self._validate_acceptance_declaration(input.get("acceptance"))
                # The platform derives persona, output schema and task
                # sections from its own frozen facts; callers author only
                # the semantic sections.
                validate_harness_declaration(
                    input.get("harness"), allowed_types=frozenset({"instructions", "skills", "memory"})
                )
            except ValueError as exc:
                raise TaskControlValidationError(str(exc)) from exc
        elif isinstance(input.get("controller"), dict) and "timer_wait" in input["controller"]:
            raise TaskControlValidationError("controller.timer_wait requires controller_loop")
        elif isinstance(input.get("controller"), dict) and "agent_children" in input["controller"]:
            raise TaskControlValidationError("controller.agent_children requires controller_loop")
        elif isinstance(input.get("controller"), dict) and "skill_catalog" in input["controller"]:
            raise TaskControlValidationError("controller.skill_catalog requires controller_loop")
        elif input.get("harness") is not None:
            raise TaskControlValidationError("input.harness requires controller_loop")
        if input.get("agent_child_parent") is not None:
            if not _verified_agent_child:
                raise TaskControlValidationError("agent_child_parent is reserved for the host delegation tool")
            # A persisted Agent child call: one level deep, plain chat
            # execution, never a workflow parent or a delegator itself.
            from hecate.execution.agent_child import agent_child_stamp

            try:
                if agent_child_stamp(input["agent_child_parent"]) is None:
                    raise ValueError("agent_child_parent stamp is incomplete")
            except ValueError as exc:
                raise TaskControlValidationError(str(exc)) from exc
            conflicts = sorted({"workflow", "workflow_parent", "workflow_step"}.intersection(input))
            if conflicts:
                raise TaskControlValidationError(
                    f"agent_child_parent conflicts with reserved fields: {', '.join(conflicts)}"
                )
            if input.get("controller_loop") is not True:
                # Host-built children always run the durable controller
                # chain (recovery, effects-not-repeated, cooperative
                # cancel); nothing else may impersonate a child.
                raise TaskControlValidationError("agent_child_parent requires the controller chain")
            controller_block = input.get("controller")
            if isinstance(controller_block, dict) and "agent_children" in controller_block:
                raise TaskControlValidationError("agent children cannot declare their own agent children")
        agent = await self._db.get(AgentModel, agent_id)
        if agent is None or agent.deleted or agent.workspace_id != workspace_id:
            raise TaskControlNotFoundError(f"agent {agent_id} not found in workspace")
        deployment = await self._default_deployment(agent_id, workspace_id)
        if deployment is None:
            raise TaskControlValidationError(f"agent {agent_id} has no default deployment")
        if deployment.backend_type != "builtin" or deployment.access_mode != "in_process":
            raise TaskControlValidationError("task-control dispatch currently supports builtin/in_process deployments")
        if input.get("agent_child_parent") is not None:
            from hecate.models.agent_version import AgentVersionModel

            child_principal = await self._db.scalar(
                select(AgentPrincipalModel.id).where(
                    AgentPrincipalModel.agent_id == agent_id,
                    AgentPrincipalModel.workspace_id == workspace_id,
                    AgentPrincipalModel.deleted.is_(False),
                )
            )
            if child_principal is None:
                raise TaskControlValidationError("agent child target requires an active agent principal")
            version = await self._db.get(AgentVersionModel, deployment.agent_version_id)
            if version is None or version.deleted:
                raise TaskControlValidationError("agent child target version does not resolve")
            config = version.config_snapshot or {}
            if any(
                config.get(key)
                for key in (
                    "knowledge_base_ids",
                    "knowledge_bases",
                    "skills",
                    "skill_ids",
                    "skill_discovery_enabled",
                    "memory",
                    "memory_policy",
                )
            ):
                # Tool delegation runs under the verified parent ∧ target
                # rule intersection; knowledge, skill and memory surfaces
                # have no expressable intersection yet and stay refused.
                raise TaskControlValidationError(
                    "agent child knowledge/skill/memory delegation is unsupported until their "
                    "authorization intersection is enforced"
                )

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
                plan = WorkflowChildTaskAdapter.plan(input)
                if plan is not None and plan.has_controller_steps and not hasattr(self._store, "submission_for_key"):
                    raise ValueError("controller workflow steps require the platform SQL durable store")
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
            "workflow_step": workflow_step,
            **({"workflow_controller_step": True} if workflow_controller_step else {}),
            # Persistent Agent child call: the child carries its parent's
            # stamp (task ref, call identity, frozen target narrowing) the
            # same durable way workflow children carry theirs.
            "agent_child_parent": input.get("agent_child_parent"),
            # step5e: durable approval wait opt-in — the dispatch assembly
            # parks on a policy-required approval instead of asking inline.
            "approval_wait": input.get("approval_wait") is True,
            # Agent-controller loop opt-in: an explicit execution binding.
            # The durable decision chain (keyed by this task's run) becomes
            # the continuation position; re-dispatches replay it instead of
            # minting attempt runs.
            "controller_loop": input.get("controller_loop") is True,
            "controller": input.get("controller") if isinstance(input.get("controller"), dict) else None,
            "output_schema": input.get("output_schema") if isinstance(input.get("output_schema"), dict) else None,
            # Versioned acceptance declaration: the goal/standard version
            # this task runs under plus its deterministic required checks.
            # The dispatcher freezes the references into every decision and
            # gates task success on the recorded acceptance evaluation.
            "acceptance": input.get("acceptance") if isinstance(input.get("acceptance"), dict) else None,
            # Harness context composition: caller-authored semantic sections
            # frozen verbatim, stamped even when empty so every new
            # controller-loop task pins its composition digest on every
            # decision. Tasks submitted before this binding carry no block
            # and keep their manually composed base input byte-for-byte.
            "harness": (
                (input.get("harness") if isinstance(input.get("harness"), dict) else {"version": 1})
                if input.get("controller_loop") is True
                else None
            ),
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
                **(
                    {
                        "dispatch_lease": dispatch_lease,
                        "dispatch_parent_ref": dispatch_parent_ref,
                        "dispatch_step_index": dispatch_step_index,
                    }
                    if dispatch_lease is not None
                    else {}
                ),
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
        if not workflow_controller_step:
            _spawn_background(self._dispatch_task(t_ref))
        return SubmitResult(
            task_ref=t_ref,
            run_ref=r_ref,
            lifecycle_state=await self._lifecycle_state(t_ref),
            replayed=False,
        )

    @staticmethod
    def _validate_acceptance_declaration(acceptance: Any) -> None:
        """Fail fast on a malformed acceptance declaration at submission.

        Supported required-check kinds: ``output_schema`` (a JSON Schema)
        and ``content_contains`` (a substring). An unknown kind is an
        authoring error, not an ``unverified`` verdict — unavailability is
        reserved for evaluation-time gaps this validator cannot see. The
        validation itself is the shared runtime module the Runner also
        applies, so one declaration shape admits in either host.
        """

        from hecate_runtime.delivery_acceptance import validate_acceptance_declaration

        validate_acceptance_declaration(acceptance)

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
                # A background dispatch must never drive the caller's
                # request session: its teardown commit races the dispatch's
                # SQL on that shared session ("prepared" state). Without a
                # bound worker or factory, open a private session on the
                # same engine — pooled in production, shared-connection in
                # the in-memory test setup, but never the request's session.
                factory = async_sessionmaker(self._db.bind, class_=AsyncSession, expire_on_commit=False)
                async with factory() as session:
                    await self._dispatch_now(t_ref, db=session)
                    await session.commit()
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
                # The registration created this run on the request session
                # (identity map) with an empty projection, and the durable
                # worker commits the real one through its own session —
                # `session.get` would return the cached pre-worker row, so
                # refresh before reading the outcome.
                await self._db.refresh(run)
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
            # Nullable on purpose: no durable record means no revision exists,
            # and callers must refuse to submit a revision-bound command rather
            # than guess one from projections or local state.
            "revision": record.revision if record else None,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "runs": [self._run_summary(run) for run in runs],
        }
        step_states = (record.extra or {}).get("workflow_step_states") if record else None
        if isinstance(step_states, dict):
            detail["workflow_step_states"] = {
                key: value
                for key, value in step_states.items()
                if isinstance(key, str) and key.isdecimal() and value in (_WORKFLOW_STEP_CLOSED, _WORKFLOW_STEP_HELD)
            }
        wait = (record.extra or {}).get("wait") if record else None
        if wait:
            detail["wait"] = wait
        live_capability = await self._live_input_capability(workspace_id, t_ref, record)
        if live_capability is not None:
            # Server-owned composer capability for running controller tasks:
            # unsupported shapes state their own reason instead of letting
            # the page derive permission locally.
            detail["live_input"] = live_capability
        task_input = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
        if isinstance(task_input.get("agent_child_parent"), dict):
            # The child side: the durable delegation stamp it was accepted
            # under — read-only identity facts, never a command surface.
            detail["agent_child_parent"] = {
                "alias": task_input["agent_child_parent"].get("alias"),
                "call_id": task_input["agent_child_parent"].get("call_id"),
                "parent_task_ref": task_input["agent_child_parent"].get("task_ref"),
            }
        if record is not None and (record.extra or {}).get("agent_child_calls") is not None:
            # The parent side: the accepted-call count and, when a wait is
            # bound, its live consumption facts (the child's own state is
            # readable through the child task's detail).
            summary: dict[str, Any] = {"accepted_calls": record.extra["agent_child_calls"]}
            if isinstance(wait, dict) and wait.get("wake_kind") == "agent_child":
                contract = wait.get("contract_ref") or {}
                summary["pending"] = {
                    "alias": contract.get("alias"),
                    "call_id": contract.get("call_id"),
                    "child_task_ref": contract.get("child_task_ref"),
                    "consumed": wait.get("consumed"),
                }
            detail["agent_children"] = summary
        if record is not None:
            # Whitelisted projection of the parent's persisted cumulative
            # child-execution spend, already folded in by consumed agent-child
            # wakes and verified workflow step callbacks. Absence means the
            # counter was never recorded (a pre-feature task, or steps without
            # a budget declaration) — it is NOT zero; a recorded 0 IS real.
            # Invalid shapes are withheld rather than coerced, and this GET
            # writes nothing.
            for key in ("agent_child_budget_used", "workflow_budget_used"):
                value = (record.extra or {}).get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    detail[key] = value
        continuation = (record.extra or {}).get("continuation") if record else None
        if isinstance(continuation, dict) and isinstance(continuation.get("attempts"), list):
            # Minimal read-only continuation summary: trigger reason,
            # per-attempt candidate identity, verdict facts and position.
            # A GET never evaluates checks, continues a chain or moves state.
            detail["continuation"] = {
                "max_continuations": continuation.get("max_continuations"),
                "attempts": [
                    {
                        "attempt": item.get("attempt"),
                        "candidate_decision_step_id": item.get("candidate_decision_step_id"),
                        "candidate_digest": item.get("candidate_digest"),
                        "status": item.get("status"),
                        "standard_version": item.get("standard_version"),
                        "effective_turn": item.get("effective_turn"),
                        "failed_checks": (
                            item.get("feedback", {}).get("failed_checks")
                            if isinstance(item.get("feedback"), dict)
                            else None
                        ),
                    }
                    for item in continuation["attempts"]
                    if isinstance(item, dict)
                ],
            }
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

    async def get_run_action_evidence(
        self, workspace_id: uuid.UUID, run_id: uuid.UUID, action_key: str
    ) -> dict[str, Any]:
        """Read one action's evidence receipt without changing any state.

        The Run and its parent Task must remain visible in the workspace,
        and the action must belong to exactly that Run and Task. The
        receipt is a projection of recorded facts: no ledger row, event,
        model call or tool call is written, claimed or re-executed by a
        read. An unsupported store is labeled; a query failure never
        becomes an empty successful response.
        """
        from hecate.execution.action_evidence import read_action_evidence

        run = await self.assert_run_observable(workspace_id=workspace_id, run_id=run_id)
        task = await self._get_task(workspace_id, run.task_id)
        if not action_key or len(action_key) > 512:
            raise TaskControlValidationError("action_key must be a nonblank string of at most 512 characters")
        query = getattr(self._store, "get_run_action", None)
        if not callable(query):
            return {
                "run_id": str(run.id),
                "task_id": str(run.task_id),
                "supported": False,
                "ledger_source": self._ledger_source,
            }
        try:
            receipt = await read_action_evidence(
                store=self._store,
                run_ref=run_row_ref(run),
                runtime_run_ref=RunRef(issuer_domain=run.issuer_domain, run_id=str(run.id)),
                task_ref=BackendRef(RefKind.TASK, task.issuer_domain, str(task.id)),
                action_key=action_key,
                ledger_source=self._ledger_source,
            )
        except Exception as exc:
            # Driver exceptions may contain URLs, SQL parameters or result
            # payloads; log only safe attribution and never publish the text.
            logger.error("Action evidence query failed for run %s (%s)", run_id, type(exc).__name__)
            raise TaskControlError(f"action evidence query failed for run {run_id}") from exc
        if receipt is None:
            raise TaskControlNotFoundError(f"action {action_key!r} not found in run {run_id}")
        return {
            "run_id": str(run.id),
            "task_id": str(run.task_id),
            "supported": True,
            "ledger_source": self._ledger_source,
            "receipt": receipt.to_dict(),
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
        access_guard: Any | None = None,
    ) -> Any:
        """Subscribe view: yield stored envelopes until the terminal event.

        When ``access_guard`` is supplied (an object with ``before_read``,
        ``before_send``, and ``idle`` coroutines — see
        :class:`hecate.execution.run_stream_guard.PeriodicAccessGuard`), the
        loop re-verifies the caller's access before each event read and
        relay projection, before relaying a batch it just obtained, and on
        the idle cadence; a failing check raises through and ends the
        stream. The guard runs inside this generator so a revocation ends
        the stream at a safe await point instead of cancelling an in-flight
        read.
        """
        guard = access_guard if access_guard is not None else _NullAccessGuard()
        await guard.before_read()
        run = await self.assert_run_observable(workspace_id=workspace_id, run_id=run_id)
        if run.projection.get("entry_name"):
            from hecate.contracts.execution.events import EventKind

            current = cursor
            while True:
                await guard.before_read()
                page = await self.read_run_events(workspace_id, run_id, cursor=current)
                await guard.before_send()
                for envelope in page.events:
                    # Each yield can suspend on client backpressure for
                    # longer than the access window, even within one page.
                    await guard.before_send()
                    yield envelope
                    current = str(envelope.source_sequence)
                    if envelope.kind is EventKind.TERMINAL:
                        return
                if not page.has_more:
                    await guard.before_read()
                    await self._db.refresh(run)
                    if run.projection.get("state") in _TERMINAL_LIFECYCLE_STATES:
                        return
                await guard.idle(poll_interval)
        run_ref = run_row_ref(run)
        events = PlatformEventService(self._db)
        current = cursor
        while True:
            await guard.before_read()
            page = await events.read_run_events(run_ref, workspace_id=workspace_id, cursor=current)
            await guard.before_send()
            for envelope in page.events:
                await guard.before_send()
                yield envelope
                current = str(envelope.source_sequence)
                if envelope.payload_schema_ref == RUN_TERMINAL:
                    return
            # Everything stored has been consumed: stop only when the newest
            # envelope is the terminal marker, otherwise keep polling.
            await guard.before_read()
            latest = await events.latest_run_envelope(run_ref, workspace_id=workspace_id)
            if (
                latest is not None
                and latest.payload_schema_ref == RUN_TERMINAL
                and (current is None or latest.source_sequence <= int(current))
            ):
                return
            await guard.idle(poll_interval)

    # ------------------------------------------------------------------
    # control commands
    # ------------------------------------------------------------------

    async def _transition_rejected(self, command_id: str) -> Any:
        """Record REJECTED unless another delivery already settled the command.

        Deterministic callback ids race: a concurrent submission of the SAME
        command may have applied (or settled) the shared row between this
        attempt's gate check and its own bookkeeping. A terminal receipt
        stands — this loser claims nothing and reports the settled record.
        """

        try:
            return await asyncio.to_thread(self._recorder.transition, command_id, CommandState.REJECTED)
        except InvalidCommandTransitionError:
            return await asyncio.to_thread(self._recorder.get, command_id)

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
        _verified_workflow_callback: bool = False,
        _verified_agent_child_callback: bool = False,
    ) -> CommandIssueResult:
        """Record a command and act on what the platform can enforce."""
        if payload_schema_ref == TASK_USER_INPUT_SCHEMA_REF and (
            kind is not ControlCommandKind.PROVIDE_INPUT or not isinstance(payload, dict)
        ):
            raise TaskControlValidationError("task-user-input requires provide_input with a JSON object payload")
        if payload_schema_ref == TASK_LIVE_INPUT_SCHEMA_REF and (
            kind is not ControlCommandKind.PROVIDE_INPUT
            or not isinstance(payload, dict)
            or live_input_payload(payload) is None
        ):
            raise TaskControlValidationError("task-live-input requires provide_input with a versioned content envelope")
        if payload_schema_ref == TASK_LIVE_INPUT_SCHEMA_REF and (
            not command_id
            or not command_id.strip()
            or type(expected_revision) is not int
            or expected_revision < 0
            or detail_ns
        ):
            raise TaskControlValidationError(
                "task-live-input requires a command ID and nonnegative expected revision without control metadata"
            )
        if (detail_ns or {}).get("callback_digest") and not (
            _verified_workflow_callback or _verified_agent_child_callback
        ):
            raise TaskControlValidationError("callback metadata is reserved for verified child callbacks")
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
                    delivery = await self._live_input_delivery_map(t_ref, [existing])
                    return CommandIssueResult(
                        record=existing,
                        detail="idempotent replay of a settled command",
                        delivery=delivery.get(existing.command_id),
                    )
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
        try:
            record = await asyncio.to_thread(self._recorder.record, record)
        except ValueError:
            # The recorder keeps one content per command id. When a concurrent
            # submitter of this same request recorded it first, its receipt
            # may already contain an applied effect or later delivery state.
            # The immutable request still matches. A second apply
            # here would race the single-use wake and let the loser misreport
            # — or park — a healthy parent, so surface the in-flight receipt.
            concurrent = await asyncio.to_thread(self._recorder.get, record.command_id)
            if concurrent is None or not _same_command_request(concurrent, record):
                raise
            await self._db.commit()
            delivery = await self._live_input_delivery_map(t_ref, [concurrent])
            return CommandIssueResult(
                record=concurrent,
                detail="command recorded concurrently; replaying its current receipt",
                delivery=delivery.get(concurrent.command_id),
            )
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
        if kind is ControlCommandKind.PROVIDE_INPUT and record.payload_schema_ref == TASK_LIVE_INPUT_SCHEMA_REF:
            # Live supplements settle at receipt: durable pending revision,
            # applied command — and never the managed-host delivery path a
            # runner cannot honor. The running loop parks itself at the
            # next decision boundary, so nothing is dispatched here.
            detail = await self._queue_live_input(t_ref, record, managed=managed_delivery is not None)
            refreshed = await asyncio.to_thread(self._recorder.get, record.command_id) or record
            delivery = await self._live_input_delivery_map(t_ref, [refreshed])
            return CommandIssueResult(record=refreshed, detail=detail, delivery=delivery.get(record.command_id))
        if managed_delivery is not None:
            if _expired(record.expires_at):
                expired = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.EXPIRED)
                return CommandIssueResult(record=expired, detail="command expired before host delivery")
            if kind is ControlCommandKind.PAUSE:
                rejected: Any = await self._transition_rejected(record.command_id)
                return CommandIssueResult(record=rejected, detail="managed runner declares no pause capability")
            return CommandIssueResult(record=record, detail="command persisted for managed host delivery")

        if _expired(record.expires_at):
            expired = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.EXPIRED)
            return CommandIssueResult(record=expired, detail="command expired before execution")
        current = await asyncio.to_thread(self._store.get_task_state, t_ref)
        if record.expected_revision is not None and (current is None or current.revision != record.expected_revision):
            rejected = await self._transition_rejected(record.command_id)
            return CommandIssueResult(record=rejected, detail="expected task revision no longer matches")
        if kind is ControlCommandKind.CANCEL:
            detail = await self._request_cancel(t_ref, record, workspace_id)
        elif kind is ControlCommandKind.PROVIDE_INPUT:
            detail = await self._wake_waiting(
                t_ref,
                record,
                workspace_id,
                expected_state=TaskLifecycleState.WAITING_INPUT,
                payload=payload or {},
                verified_workflow_callback=_verified_workflow_callback,
                verified_agent_child_callback=_verified_agent_child_callback,
            )
        elif kind is ControlCommandKind.RESUME:
            detail = await self._wake_waiting(
                t_ref, record, workspace_id, expected_state=TaskLifecycleState.WAITING_APPROVAL, payload=payload or {}
            )
        else:
            # pause: builtin deployments declare no pause capability —
            # reject explicitly, never fake success.
            await self._db.commit()
            record = await self._transition_rejected(record.command_id)
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
        # A controller step's workspace hold settles here — the verified
        # callback is the settlement authority, exactly as for Agent child
        # calls: whoever drives the callback (terminal path, outbox relay or
        # a recovery process) cannot bypass it. Idempotent per step identity;
        # budget-less (chat) steps are a no-op.
        budget, _changed = await self.settle_workflow_step_budget(
            workspace_id=workspace_id, child_task_id=child_task_id
        )
        if budget is not None:
            child_summary["budget"] = budget
        return await self.issue_command(
            workspace_id=workspace_id,
            task_id=task_id,
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer=issuer,
            command_id=command_id,
            payload=merged_payload,
            payload_schema_ref="urn:hecate:workflow-callback:child-outcome/0",
            detail_ns={"wait_token": wait_token, "callback_digest": callback_digest},
            _verified_workflow_callback=True,
            # The caller's command_id makes the callback idempotent end to
            # end: issue_command records under it and replays the receipt.
        )

    async def submit_agent_child_callback(
        self,
        *,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID,
        command_id: str,
        wait_token: str,
        child_task_id: uuid.UUID,
        declared_state: str,
        issuer: str = "agent-child-callback",
    ) -> CommandIssueResult:
        """Verify a real Agent child's terminal facts, then wake the parent.

        The platform trusts only its own records: the parent's wait must be
        an unconsumed ``agent_child`` wait whose contract names this child
        and the accepting call identity, the child's persisted parent stamp
        must agree, and the child must be terminal in this workspace with
        committed result facts. The child's committed outcome — content,
        acceptance record and unknown-until-reported usage — becomes the
        wake payload's ``agent_child_result``; the parent's controller
        consumes it as a durable ``agent_child_result`` revision, never a
        human ``provide_input`` revision. Any mismatch rejects the command
        without consuming the token.
        """

        from hecate.execution.agent_child import (
            AGENT_CHILD_RESULT_SCHEMA_REF,
            agent_child_contract,
            agent_child_stamp,
            validate_child_delivery,
            verify_child_call,
        )

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
        wait = (current.extra or {}).get("wait") if current else None
        contract = (wait or {}).get("contract_ref") if isinstance(wait, dict) else None
        if (
            not isinstance(wait, dict)
            or wait.get("wake_kind") != "agent_child"
            or agent_child_contract(contract) is None
        ):
            return await self._reject_callback(
                command_id,
                task,
                "task is not waiting on an agent child callback",
                issuer=issuer,
                callback_digest=callback_digest,
            )
        if wait.get("consumed"):
            return await self._reject_callback(
                command_id,
                task,
                "the agent child wait is already consumed",
                issuer=issuer,
                callback_digest=callback_digest,
            )
        if str((contract or {}).get("child_task_ref", {}).get("id") or "") != str(child_task_id):
            return await self._reject_callback(
                command_id,
                task,
                "callback child does not match the wait contract",
                issuer=issuer,
                callback_digest=callback_digest,
            )
        try:
            child = await self._get_task(workspace_id, child_task_id)
        except TaskControlNotFoundError:
            return await self._reject_callback(
                command_id,
                task,
                "child task does not exist in this workspace",
                issuer=issuer,
                callback_digest=callback_digest,
            )
        child_ref = task_ref_of(child.id)
        try:
            await verify_child_call(self._store, t_ref, contract, wait=wait)
        except (KeyError, TypeError, ValueError) as exc:
            return await self._reject_callback(
                command_id, task, str(exc), issuer=issuer, callback_digest=callback_digest
            )
        child_record = await asyncio.to_thread(self._store.get_task_state, child_ref)
        child_state = child_record.lifecycle_state.value if child_record else UNRECORDED_LIFECYCLE
        if child_state not in _TERMINAL_LIFECYCLE_STATES:
            return await self._reject_callback(
                command_id,
                task,
                f"child task is {child_state}, not in a terminal state",
                issuer=issuer,
                callback_digest=callback_digest,
            )
        if child_state != declared:
            return await self._reject_callback(
                command_id,
                task,
                f"declared state {declared!r} does not match child task state {child_state!r}",
                issuer=issuer,
                callback_digest=callback_digest,
            )
        child_input = await asyncio.to_thread(self._store.get_task_input, child_ref) or {}
        stamp = agent_child_stamp(child_input.get("agent_child_parent"))
        if (
            stamp is None
            or stamp.get("task_ref") != t_ref.to_dict()
            or stamp.get("call_id") != contract.get("call_id")
            or stamp.get("alias") != contract.get("alias")
        ):
            return await self._reject_callback(
                command_id,
                task,
                "child parent stamp does not match the accepted call",
                issuer=issuer,
                callback_digest=callback_digest,
            )
        facts = (child_record.extra or {}).get("result") or {}
        if not facts:
            if child_state == "cancelled":
                facts = {"status": "cancelled", "error_category": "cancelled"}
            else:
                return await self._reject_callback(
                    command_id, task, "child result facts are missing", issuer=issuer, callback_digest=callback_digest
                )
        try:
            validate_child_delivery(
                facts,
                child_state=child_state,
                task_ref=child_ref,
                run_ref=await asyncio.to_thread(self._store.run_for_task, child_ref),
                schema=contract.get("output_schema"),
                workspace_id=str(workspace_id),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return await self._reject_callback(
                command_id, task, str(exc), issuer=issuer, callback_digest=callback_digest
            )
        # The budget settlement derives from the child's DURABLE slice
        # state, never from the result's self-reported numbers: the slice
        # block is written under the child's own lifecycle CAS during its
        # dispatches, so it is the trusted record of what the call spent
        # and what stayed unknown.
        budget_facts, _ = await self.settle_agent_child_budget(workspace_id=workspace_id, child_task_id=child_task_id)
        child_summary = {
            "child_task_id": str(child.id),
            "child_state": child_state,
            "alias": stamp.get("alias"),
            "call_id": stamp.get("call_id"),
            # The child's committed result facts; usage is unknown unless a
            # future integration reports it — never fabricated as zero.
            "content": facts.get("content") if child_state == "succeeded" else None,
            "error": facts.get("error"),
            "error_category": facts.get("error_category"),
            "acceptance": facts.get("acceptance"),
            "usage": None,
            "budget": budget_facts,
        }
        # Close the delegation's quota loop: the acceptance hold settles to
        # the trusted used figure (unknown holds stay held, proven-zero
        # releases). The settle is idempotent per call identity and runs
        # BEFORE the wake: a failure here leaves the callback un-issued so
        # the child's outbox retry re-runs it — no permanent orphan.
        return await self.issue_command(
            workspace_id=workspace_id,
            task_id=task_id,
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer=issuer,
            command_id=command_id,
            payload={"agent_child_result": child_summary},
            payload_schema_ref=AGENT_CHILD_RESULT_SCHEMA_REF,
            detail_ns={"wait_token": wait_token, "callback_digest": callback_digest},
            _verified_agent_child_callback=True,
        )

    async def settle_agent_child_budget(
        self, *, workspace_id: uuid.UUID, child_task_id: uuid.UUID
    ) -> tuple[dict[str, Any] | None, bool]:
        """Settle a terminal child's metering independently of the parent's wake.

        The settled action receipt, rather than a mutable current wait, binds
        the parent and child. This also covers cancelled parents and callbacks
        that finish before the parent parks.
        """
        from hecate.execution.agent_child import (
            AGENT_CHILD_CONTRACT_KIND,
            agent_child_acceptance,
            agent_child_stamp,
            derive_slice_usage,
            stamp_budget_units,
            verify_child_call,
        )

        child_ref = task_ref_of(child_task_id)
        payload = await asyncio.to_thread(self._store.get_task_input, child_ref) or {}
        stamp = agent_child_stamp(payload.get("agent_child_parent"))
        if stamp is None or stamp_budget_units(stamp) is None:
            return None, False
        await self._get_task(workspace_id, child_task_id)
        if payload.get("workspace_id") != str(workspace_id):
            raise TaskControlValidationError("agent child budget belongs to another workspace")
        child = await asyncio.to_thread(self._store.get_task_state, child_ref)
        if child is None or child.lifecycle_state not in {
            TaskLifecycleState.SUCCEEDED,
            TaskLifecycleState.FAILED,
            TaskLifecycleState.CANCELLED,
            TaskLifecycleState.RECONCILIATION_REQUIRED,
        }:
            return None, False
        parent_ref = BackendRef.from_dict(stamp["task_ref"])
        parent_run = await asyncio.to_thread(self._store.run_for_task, parent_ref)
        if parent_run is None:
            return None, False
        receipt = await asyncio.to_thread(self._store.get_run_action, parent_run, stamp["call_id"])
        result = (receipt or {}).get("result_payload")
        if isinstance(result, str):
            import json

            result = json.loads(result)
        call = agent_child_acceptance(result)
        if call is None:
            return None, False  # Acceptance may still be committing in another worker.
        parent_input = await asyncio.to_thread(self._store.get_task_input, parent_ref) or {}
        from hecate.execution.agent_child import agent_child_policy

        policy = agent_child_policy((parent_input.get("controller") or {}).get("agent_children"))
        target = (policy or {}).get("targets", {}).get(stamp["alias"]) or {}
        contract = {
            **call,
            "kind": AGENT_CHILD_CONTRACT_KIND,
            "run_id": parent_run.id,
            "arguments_digest": (receipt or {}).get("intent", {}).get("arguments_digest"),
            "output_schema": target.get("output_schema"),
        }
        await verify_child_call(self._store, parent_ref, contract)
        allocated = stamp_budget_units(contract)
        block = (child.extra or {}).get("budget_slice")
        if block is None and child.lifecycle_state is TaskLifecycleState.CANCELLED and child.revision == 1:
            # Initial submission is revision zero; only a pre-dispatch cancel
            # can be terminal at revision one. No model request was possible.
            block = {"allocated": allocated, "entries": {}}
        budget = derive_slice_usage(block, allocated=allocated)
        if budget is None:
            return None, False
        changed = await self._settle_agent_child_reservation(
            workspace_id=workspace_id, parent_task_id=parent_ref.id, call_id=stamp["call_id"], budget=budget
        )
        return budget, changed

    async def _settle_agent_child_reservation(
        self, *, workspace_id: uuid.UUID, parent_task_id: str, call_id: str, budget: dict[str, Any]
    ) -> bool:
        """Settle one delegation's workspace hold to its trusted used figure.

        Idempotent per call identity: a replay settles the same figure and
        is confirmed; a revise merges the delta. Unknown usage keeps its
        hold (never charged as zero); a proven-zero call releases the hold.
        """

        from hecate_governance.usage import UsageRecord, UsageStatus

        from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE, QuotaBudgetLedger

        usage_status = {
            "actual": UsageStatus.ACTUAL,
            "estimated": UsageStatus.ESTIMATED,
            "unknown": UsageStatus.UNKNOWN,
        }.get(budget.get("status"), UsageStatus.UNVERIFIED)
        ledger = QuotaBudgetLedger(
            self._db,
            workspace_id=workspace_id,
            session_factory=self._session_factory,
            unit_scale=MODEL_TOKEN_UNIT_SCALE,
        )
        key = f"agent-child:{parent_task_id}:{call_id}"
        previous = await ledger.usage_for(key)
        if previous is not None and previous.status is UsageStatus.UNKNOWN and usage_status is UsageStatus.UNKNOWN:
            return False
        outcome = await ledger.settle(
            UsageRecord(
                invocation_id=key,
                units=int(budget.get("used") or 0),
                status=usage_status,
            )
        )
        from hecate_governance.usage import SettleStatus

        if outcome.status is SettleStatus.CONFLICT:
            raise TaskControlValidationError("agent child budget settlement requires reconciliation")
        if self._session_factory is None:
            await self._db.commit()
        return outcome.status is not SettleStatus.IDEMPOTENT_CONFIRMED

    async def reserve_workflow_step_budget(
        self,
        *,
        workspace_id: uuid.UUID,
        parent_task_id: uuid.UUID,
        step_index: int,
        units: int,
        dispatch_lease: Any = None,
    ) -> Any:
        """Hold one workflow step's declared slice on the workspace quota.

        Called before the step child is submitted, so a refused call leaves
        no child and no hold. Same workspace ledger, same token scale as the
        Agent child path — never a parallel budget authority.

        ``dispatch_lease`` checks the creation fence while holding the
        quota scope lock: a stale executor whose dispatch lease was taken over, or a
        step the reclaim sweep already closed, is refused BEFORE it can
        re-occupy quota. ``StaleFenceError`` propagates (the executor is
        fenced; the current owner drives on); a closed step raises
        ``ValueError`` for the caller's refusal mapping.
        """

        if type(step_index) is not int or step_index < 0:
            raise ValueError("workflow step index must be a non-negative integer")

        async def check_dispatch() -> None:
            parent_ref = task_ref_of(parent_task_id)
            if dispatch_lease is not None:
                guard = getattr(self._store, "assert_workflow_step_dispatchable", None)
                if guard is None:
                    raise ValueError("workflow step dispatch fencing requires the platform SQL durable store")
                await asyncio.to_thread(guard, parent_ref, step_index, dispatch_lease)
            else:
                parent = await asyncio.to_thread(self._store.get_task_state, parent_ref)
                if (
                    parent is None
                    or parent.lifecycle_state
                    in {TaskLifecycleState.FAILED, TaskLifecycleState.SUCCEEDED, TaskLifecycleState.CANCELLED}
                    or str(step_index) in ((parent.extra or {}).get("workflow_step_states") or {})
                ):
                    raise ValueError("workflow step is closed or its parent is terminal")

        from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE, QuotaBudgetLedger

        ledger = QuotaBudgetLedger(
            self._db,
            workspace_id=workspace_id,
            session_factory=self._session_factory,
            unit_scale=MODEL_TOKEN_UNIT_SCALE,
        )
        try:
            outcome = await ledger.reserve(f"workflow-step:{parent_task_id}:{step_index}", units, guard=check_dispatch)
            if self._session_factory is None:
                await self._db.commit()
            return outcome
        except BaseException:
            if self._session_factory is None:
                await self._db.rollback()
            raise

    async def release_workflow_step_budget(
        self,
        *,
        workspace_id: uuid.UUID,
        parent_task_id: uuid.UUID,
        step_index: int,
        dispatch_lease: Any = None,
    ) -> Any:
        """Release an unsubmitted hold while the failed dispatcher still owns it."""
        from hecate_durable.storage.lease import StaleFenceError
        from hecate_governance.usage import ReleaseStatus

        from hecate.execution.workflow_child import WorkflowChildTaskAdapter
        from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE, QuotaBudgetLedger

        ledger = QuotaBudgetLedger(
            self._db,
            workspace_id=workspace_id,
            session_factory=self._session_factory,
            unit_scale=MODEL_TOKEN_UNIT_SCALE,
        )
        key = f"workflow-step:{parent_task_id}:{step_index}"
        async with ledger.locked_scope() as transaction:
            state = await transaction.reservation_state(key)
            if state is None or state["released"] or state["settled"]:
                return ReleaseStatus.NO_RESERVATION
            if dispatch_lease is not None:
                parent_ref = task_ref_of(parent_task_id)
                try:
                    await asyncio.to_thread(
                        self._store.assert_workflow_step_dispatchable, parent_ref, step_index, dispatch_lease
                    )
                except (StaleFenceError, ValueError):
                    return ReleaseStatus.HELD_FOR_RECONCILIATION
                association = await asyncio.to_thread(
                    self._store.submission_for_key, WorkflowChildTaskAdapter.submission_key(parent_ref, step_index)
                )
                if association is not None:
                    return ReleaseStatus.HELD_FOR_RECONCILIATION
            return await transaction.release(key)

    async def reclaim_workflow_step_reservations(
        self, *, workspace_id: uuid.UUID, parent_task_id: uuid.UUID
    ) -> dict[str, Any]:
        """Close and reclaim the step holds a terminal workflow parent can no
        longer use.

        The recovery half of the reservation contract: a hold taken before
        the child exists can outlive its use when the parent converges
        (deadline reached, cancelled, failed) before resubmission, or when a
        dispatcher dies between the reserve and the child creation. For every
        declared controller slice the sweep classifies the durable facts and
        acts on provable ones only:

        - an already-closed step just finishes its release (idempotent — the
          interrupted close→release gap re-converges here, and repeating the
          sweep is a no-op);
        - a submitted child keeps its hold for settlement — the association
          is verified against the child's persisted parent stamp before the
          hold is trusted to anyone;
        - an unsubmitted step is closed first (``workflow_step_states`` on
          the parent extra), THEN released — the reverse order can mint a
          second hold through a late reservation replay, the close cannot;
        - an association read failure or a stamp/fact mismatch keeps the
          quota and marks the step ``held_for_reconciliation`` — never a
          silent reclaim of money the platform cannot account for.

        The relay re-runs this sweep on the parent's terminal ``task_state``
        events, so a crash mid-sweep is retried durably until the facts
        converge. Non-workflow parents and non-terminal parents return
        without touching anything.
        """

        from hecate.execution.workflow_child import WorkflowChildTaskAdapter

        parent_ref = task_ref_of(parent_task_id)
        parent = await asyncio.to_thread(self._store.get_task_state, parent_ref)
        if parent is None:
            return {"status": "absent"}
        if parent.lifecycle_state not in {
            TaskLifecycleState.SUCCEEDED,
            TaskLifecycleState.FAILED,
            TaskLifecycleState.CANCELLED,
        }:
            return {"status": "not_terminal", "parent_state": parent.lifecycle_state.value}
        payload = await asyncio.to_thread(self._store.get_task_input, parent_ref) or {}
        raw = payload.get("workflow")
        if raw is None:
            return {"status": "not_workflow"}
        try:
            plan = WorkflowChildTaskAdapter.plan(
                {"workflow": raw} if isinstance(raw, dict) else {"workflow": {"steps": []}}
            )
        except ValueError:
            plan = None
        if plan is None:
            return {"status": "not_workflow"}
        if payload.get("workspace_id") not in (None, str(workspace_id)):
            return {"status": "workspace_mismatch"}
        lookup = getattr(self._store, "submission_for_key", None)
        states = dict((parent.extra or {}).get("workflow_step_states") or {})
        steps: dict[str, str] = {}
        for index, step in enumerate(plan.steps):
            budget = step.get("budget_units")
            if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
                continue
            key = f"workflow-step:{parent_task_id}:{index}"
            steps[str(index)] = await self._reclaim_one_step(
                workspace_id,
                parent_ref,
                index,
                key,
                states,
                submission_for_key=lookup,
            )
        return {"status": "swept", "parent_state": parent.lifecycle_state.value, "steps": steps}

    async def _reclaim_one_step(
        self,
        workspace_id: uuid.UUID,
        parent_ref: BackendRef,
        index: int,
        key: str,
        states: dict[str, str],
        *,
        submission_for_key: Any,
    ) -> str:
        from hecate.execution.workflow_child import WorkflowChildTaskAdapter
        from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE, QuotaBudgetLedger

        ledger = QuotaBudgetLedger(
            self._db,
            workspace_id=workspace_id,
            session_factory=self._session_factory,
            unit_scale=MODEL_TOKEN_UNIT_SCALE,
        )
        marker = states.get(str(index))
        if marker == _WORKFLOW_STEP_HELD:
            return "held_reconciliation"
        if marker == _WORKFLOW_STEP_CLOSED:
            return await self._release_closed_step(ledger, key)
        if submission_for_key is None:
            # Without the association surface no absence is provable; the
            # hold stays and the state is observable instead of guessed.
            await self._mark_workflow_step(parent_ref, index, _WORKFLOW_STEP_HELD)
            return "held_reconciliation"
        try:
            submission_key = WorkflowChildTaskAdapter.submission_key(parent_ref, index)
            association = await asyncio.to_thread(submission_for_key, submission_key)
        except Exception:  # noqa: BLE001 — the association surface itself failed
            await self._mark_workflow_step(parent_ref, index, _WORKFLOW_STEP_HELD)
            return "association_read_failed"
        if association is not None:
            consistent = await self._step_child_binding_consistent(association, parent_ref, index, workspace_id)
            if consistent:
                return "kept_submitted"
            await self._mark_workflow_step(parent_ref, index, _WORKFLOW_STEP_HELD)
            return "held_reconciliation"
        marker = await self._mark_workflow_step(parent_ref, index, _WORKFLOW_STEP_CLOSED)
        if marker != _WORKFLOW_STEP_CLOSED:
            return "held_reconciliation"
        return await self._release_closed_step(ledger, key)

    async def _release_closed_step(self, ledger: Any, key: str) -> str:
        from hecate_governance.usage import ReleaseStatus

        # A reservation may have passed its preflight before the close marker.
        # Wait for its quota transaction before deciding that no hold exists.
        async with ledger.locked_scope() as transaction:
            state = await transaction.reservation_state(key)
            if state is None or state.get("settled"):
                return "settled"
            if state.get("released"):
                return "already_reclaimed"
            if state.get("unknown_hold"):
                return "held_unknown"
            outcome = await transaction.release(key)
            if outcome is ReleaseStatus.RELEASED:
                return "reclaimed"
            if outcome in (ReleaseStatus.NO_RESERVATION, ReleaseStatus.ALREADY_SETTLED):
                return "already_reclaimed"
            return "held_unknown"

    async def _mark_workflow_step(self, parent_ref: BackendRef, index: int, value: str) -> str:
        writer = getattr(self._store, "update_task_extra", None)
        if writer is None:
            raise TaskControlValidationError("workflow step marker cannot be persisted")
        for _ in range(3):
            current = await asyncio.to_thread(self._store.get_task_state, parent_ref)
            if current is None:
                raise TaskControlValidationError("workflow step marker cannot be persisted")
            states = dict((current.extra or {}).get("workflow_step_states") or {})
            if states.get(str(index)):
                return str(states[str(index)])
            states[str(index)] = value
            try:
                await asyncio.to_thread(
                    writer, parent_ref, {"workflow_step_states": states}, expected_revision=current.revision
                )
                return value
            except (InvalidTaskTransitionError, ValueError):
                continue  # raced another marker writer; re-read and merge again
        raise TaskControlValidationError("workflow step marker did not converge; retry recovery")

    async def _step_child_binding_consistent(
        self, association: Any, parent_ref: BackendRef, index: int, workspace_id: uuid.UUID
    ) -> bool:
        payload = await asyncio.to_thread(self._store.get_task_input, association.task_ref) or {}
        stamp = payload.get("workflow_parent")
        if not isinstance(stamp, dict):
            return False
        raw_ref = stamp.get("task_ref")
        return (
            raw_ref == parent_ref.to_dict()
            and type(stamp.get("step_index")) is int
            and stamp["step_index"] == index
            and payload.get("workflow_controller_step") is True
            and payload.get("workspace_id") == str(workspace_id)
        )

    async def settle_workflow_step_budget(
        self, *, workspace_id: uuid.UUID, child_task_id: uuid.UUID
    ) -> tuple[dict[str, Any] | None, bool]:
        """Settle a terminal controller step's metering from its durable slice.

        The child's ``workflow_parent`` stamp (written at submission) binds
        parent, step and slice allocation — the same trusted-slice source the
        Agent child path settles from, never the result's self-reported
        numbers. Idempotent per step identity.
        """

        from hecate.execution.agent_child import derive_slice_usage

        child_ref = task_ref_of(child_task_id)
        payload = await asyncio.to_thread(self._store.get_task_input, child_ref) or {}
        stamp = payload.get("workflow_parent")
        step = payload.get("workflow_step")
        if (
            payload.get("workflow_controller_step") is not True
            or not isinstance(stamp, dict)
            or not isinstance(step, dict)
        ):
            return None, False
        raw_ref = stamp.get("task_ref")
        allocated = step.get("budget_units")
        if not isinstance(raw_ref, dict) or isinstance(allocated, bool) or not isinstance(allocated, int):
            return None, False
        await self._get_task(workspace_id, child_task_id)
        if payload.get("workspace_id") != str(workspace_id):
            raise TaskControlValidationError("workflow step budget belongs to another workspace")
        child = await asyncio.to_thread(self._store.get_task_state, child_ref)
        if child is None or child.lifecycle_state not in {
            TaskLifecycleState.SUCCEEDED,
            TaskLifecycleState.FAILED,
            TaskLifecycleState.CANCELLED,
            TaskLifecycleState.RECONCILIATION_REQUIRED,
        }:
            return None, False
        block = (child.extra or {}).get("budget_slice")
        if block is None and child.lifecycle_state is TaskLifecycleState.CANCELLED and child.revision == 1:
            # Initial submission is revision zero; only a pre-dispatch cancel
            # can be terminal at revision one. No model request was possible.
            block = {"allocated": allocated, "entries": {}}
        budget = derive_slice_usage(block, allocated=allocated)
        if budget is None:
            return None, False
        changed = await self._settle_workflow_step_reservation(
            workspace_id=workspace_id,
            parent_task_id=str(raw_ref.get("id")),
            step_index=int(stamp.get("step_index")),
            budget=budget,
        )
        return budget, changed

    async def _settle_workflow_step_reservation(
        self, *, workspace_id: uuid.UUID, parent_task_id: str, step_index: int, budget: dict[str, Any]
    ) -> bool:
        """Settle one step's workspace hold to its trusted used figure.

        Identical semantics to the Agent child settlement: actual/estimated
        figures settle (revisions merge the delta), unknown usage keeps its
        hold (never charged as zero), a proven-zero call releases.
        """

        from hecate_governance.usage import UsageRecord, UsageStatus

        from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE, QuotaBudgetLedger

        usage_status = {
            "actual": UsageStatus.ACTUAL,
            "estimated": UsageStatus.ESTIMATED,
            "unknown": UsageStatus.UNKNOWN,
        }.get(budget.get("status"), UsageStatus.UNVERIFIED)
        ledger = QuotaBudgetLedger(
            self._db,
            workspace_id=workspace_id,
            session_factory=self._session_factory,
            unit_scale=MODEL_TOKEN_UNIT_SCALE,
        )
        key = f"workflow-step:{parent_task_id}:{step_index}"
        previous = await ledger.usage_for(key)
        if previous is not None and previous.status is UsageStatus.UNKNOWN and usage_status is UsageStatus.UNKNOWN:
            return False
        outcome = await ledger.settle(
            UsageRecord(invocation_id=key, units=int(budget.get("used") or 0), status=usage_status)
        )
        from hecate_governance.usage import SettleStatus

        if outcome.status is SettleStatus.CONFLICT:
            raise TaskControlValidationError("workflow step budget settlement requires reconciliation")
        if self._session_factory is None:
            await self._db.commit()
        return outcome.status is not SettleStatus.IDEMPOTENT_CONFIRMED

    async def _cancel_pending_workflow_child(
        self, t_ref: BackendRef, current: Any, workspace_id: uuid.UUID, *, retry_errors: bool = False
    ) -> None:
        """Persist a cancel request for the child an unconsumed workflow wait holds.

        Mirror of the Agent child propagation: a cancel request is not a
        stop fact — the child converges through its own dispatch rules, an
        already-terminal child's cancel is rejected honestly, and failures
        never undo the parent's committed cancel.
        """

        wait = (current.extra or {}).get("wait") if current else None
        if not isinstance(wait, dict) or wait.get("consumed"):
            return
        raw_child = (wait.get("contract_ref") or {}).get("await_task_ref")
        if not isinstance(raw_child, dict) or not isinstance(raw_child.get("id"), str) or not raw_child["id"]:
            return
        try:
            child_ref = task_ref_of(uuid.UUID(raw_child["id"]))
            payload = await asyncio.to_thread(self._store.get_task_input, child_ref) or {}
            stamp = payload.get("workflow_parent") or {}
            if (
                payload.get("workflow_controller_step") is not True
                or payload.get("workspace_id") != str(workspace_id)
                or stamp.get("task_ref") != t_ref.to_dict()
                or stamp.get("step_index") != (wait.get("contract_ref") or {}).get("step_index")
                or child_ref.to_dict() != raw_child
            ):
                return
            receipt = await self.issue_command(
                workspace_id=workspace_id,
                task_id=uuid.UUID(raw_child["id"]),
                kind=ControlCommandKind.CANCEL,
                issuer="workflow-parent-cancel",
                command_id=f"workflow-cancel:{t_ref.id}:{raw_child['id']}",
            )
            logger.info(
                "workflow child %s cancel request for parent %s: %s",
                raw_child["id"],
                t_ref.id,
                receipt.record.state.value,
            )
        except Exception:  # noqa: BLE001 — propagation must not un-commit the parent's cancel
            if retry_errors:
                raise
            logger.warning("task %s workflow child cancel request failed", t_ref.id, exc_info=True)

    async def converge_workflow_step_reconciliation(self, *, workspace_id: uuid.UUID, child_ref: BackendRef) -> None:
        """Converge a bound parent when its controller step needs reconciliation."""
        payload = await asyncio.to_thread(self._store.get_task_input, child_ref) or {}
        stamp = payload.get("workflow_parent") or {}
        if payload.get("workflow_controller_step") is not True or payload.get("workspace_id") != str(workspace_id):
            return
        parent_ref = BackendRef.from_dict(stamp["task_ref"])
        child = await asyncio.to_thread(self._store.get_task_state, child_ref)
        if child is None or child.lifecycle_state is not TaskLifecycleState.RECONCILIATION_REQUIRED:
            return
        await self.settle_workflow_step_budget(workspace_id=workspace_id, child_task_id=uuid.UUID(child_ref.id))
        parent = await asyncio.to_thread(self._store.get_task_state, parent_ref)
        if parent is None or parent.lifecycle_state is not TaskLifecycleState.WAITING_INPUT:
            return
        wait = parent.extra.get("wait") or {}
        contract = wait.get("contract_ref") or {}
        if wait.get("consumed") or contract.get("await_task_ref") != child_ref.to_dict():
            return
        if contract.get("step_index") != stamp.get("step_index"):
            return
        await self._db.commit()
        try:
            await asyncio.to_thread(
                self._store.apply_task_state,
                parent_ref,
                TaskLifecycleState.RECONCILIATION_REQUIRED,
                expected_revision=parent.revision,
                extra_update={
                    "wait": {**wait, "consumed": True},
                    "reconciliation_reason": "workflow controller step requires reconciliation",
                    "workflow_reconciliation_child": child_ref.to_dict(),
                },
            )
        except (InvalidTaskTransitionError, ValueError):
            fresh = await asyncio.to_thread(self._store.get_task_state, parent_ref)
            if fresh is not None and fresh.lifecycle_state is TaskLifecycleState.WAITING_INPUT:
                raise

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
        try:
            record = await asyncio.to_thread(self._recorder.record, record)
        except ValueError:
            existing = await asyncio.to_thread(self._recorder.get, command_id)
            if (
                existing is None
                or existing.task_ref != t_ref
                or existing.issuer != issuer
                or existing.extra.get("workspace_id") != str(task.workspace_id)
                or existing.detail_ns.get("callback_digest") != callback_digest
            ):
                raise
            await self._db.commit()
            return CommandIssueResult(record=existing, detail="replaying the concurrent callback receipt")
        await self._db.commit()
        rejected: Any = await self._transition_rejected(record.command_id)
        return CommandIssueResult(record=rejected or record, detail=reason)

    async def _wake_waiting(
        self,
        t_ref: BackendRef,
        record: ControlCommandRecord,
        workspace_id: uuid.UUID,
        *,
        expected_state: TaskLifecycleState,
        payload: dict[str, Any],
        verified_workflow_callback: bool = False,
        verified_agent_child_callback: bool = False,
    ) -> str:
        """Wake a durable wait with the single-use token (revision-CAS consumed)."""

        state = await self._lifecycle_state(t_ref)
        current = await asyncio.to_thread(self._store.get_task_state, t_ref)
        wait = (current.extra or {}).get("wait") or {} if current else {}
        if state != expected_state.value:
            await self._transition_rejected(record.command_id)
            return f"task is {state}, not waiting for {expected_state.value}; wake rejected"
        # The token rides detail_ns (caller metadata, no payload schema
        # required); any provided input payload follows the envelope rule.
        token = str((record.detail_ns or {}).get("wait_token") or "")
        if not token or token != str(wait.get("wait_token")):
            await self._transition_rejected(record.command_id)
            return "wake token missing or wrong; rejected"
        if wait.get("consumed"):
            await self._transition_rejected(record.command_id)
            return "wake token already consumed; rejected"
        if wait.get("wake_kind") == "timer":
            # A timer wait wakes only through its own expiry convergence; no
            # caller command may consume the token or shortcut the clock.
            await self._transition_rejected(record.command_id)
            return "timer waits reject manual wake; expiry is the only wake"
        if wait.get("wake_kind") == "agent_child" and not verified_agent_child_callback:
            # An Agent child wait consumes only through the verified
            # callback that checked the child's own terminal facts; a
            # caller's provide_input can never fabricate a child result.
            await self._transition_rejected(record.command_id)
            return "agent child waits reject manual wake; use the verified callback"
        if _expired(wait.get("wait_expires_at")):
            await self._transition_rejected(record.command_id)
            return "wake token expired; rejected"
        contract_ref = wait.get("contract_ref")
        contract_ref = contract_ref if isinstance(contract_ref, dict) else {}
        if (
            expected_state is TaskLifecycleState.WAITING_INPUT
            and isinstance(contract_ref.get("await_task_ref"), dict)
            and not verified_workflow_callback
        ):
            # A workflow child-result wait is only wakeable through the
            # trusted callback invocation after verification of child
            # facts; caller metadata carries no such authority. A human
            # input form must never advance it.
            await self._transition_rejected(record.command_id)
            return "workflow child waits reject provide_input; use the verified callback"
        wake_payload = dict(payload)
        approval_deadline: str | None = None
        approval_clock: Callable[[], datetime] | None = None
        if expected_state is TaskLifecycleState.WAITING_APPROVAL:
            gate = await self._approval_wake_gate(current, wait, wake_payload, record)
            if gate.action == "refuse":
                # Token stays unconsumed: the wait remains resumable by a
                # later real decision until its own deadline.
                await self._transition_rejected(record.command_id)
                return gate.detail
            if gate.action == "reject_terminal":
                applied = await self._apply_approval_rejected(t_ref, record, current, wait, gate, workspace_id)
                return gate.detail if applied else "task moved on concurrently; wake rejected"
            if gate.fact:
                wake_payload.update(gate.fact)
            approval_deadline = gate.expires_at
            approval_clock = gate.clock
        base_input = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
        controller_loop = base_input.get("controller_loop") is True
        input_revision: dict[str, Any] | None = None
        budget_used_delta: int | None = None
        if controller_loop:
            # Controller input wakes record an immutable revision (never a
            # latest-snapshot overwrite); approval-resume wakes keep
            # ``provided`` as the pure control transport the approval
            # consumer reads, and it never reaches the model.
            merged_input = dict(base_input)
            if expected_state is TaskLifecycleState.WAITING_INPUT and wait.get("wake_kind") == "agent_child":
                # A verified Agent child result becomes a durable revision
                # under its own source — never a human provide_input
                # revision, and never a latest-snapshot overwrite. The
                # accepting action is acknowledged so the wake's replay
                # does not re-park on its settled envelope.
                from hecate.execution.agent_child import AGENT_CHILD_RESULT_SOURCE

                result = wake_payload.get("agent_child_result")
                if not isinstance(result, dict) or not result.get("call_id"):
                    await self._transition_rejected(record.command_id)
                    return "agent child result facts are missing from the verified callback"
                run_for_task = getattr(self._store, "run_for_task", None)
                linkage = await asyncio.to_thread(run_for_task, t_ref) if run_for_task else None
                if linkage is None:
                    await self._transition_rejected(record.command_id)
                    return "controller task has no run linkage; child result revision rejected"
                input_revision = {
                    "source": AGENT_CHILD_RESULT_SOURCE,
                    "source_ref": record.command_id,
                    "content": result,
                    "wait_token": token,
                }
                acknowledged = list(base_input.get("_input_wait_acknowledgements", []))
                call_id = str(contract_ref.get("call_id") or "")
                if call_id and call_id not in acknowledged:
                    acknowledged.append(call_id)
                merged_input["_input_wait_acknowledgements"] = acknowledged
                budget = result.get("budget")
                if isinstance(budget, dict) and isinstance(budget.get("used"), int):
                    # The parent's durable, cumulative spend: a new child
                    # task can never reset it — the counter lives here and
                    # is folded into the wake's own CAS write below.
                    budget_used_delta = int(budget["used"])
            elif expected_state is TaskLifecycleState.WAITING_INPUT:
                revision_content = {k: v for k, v in wake_payload.items() if k != "approval_ref"}
                contract = human_input_contract(contract_ref.get("contract"))
                fields = contract["fields"] if contract is not None else []
                if any(field not in revision_content for field in fields):
                    await self._transition_rejected(record.command_id)
                    return "provided input does not satisfy the wait contract"
                if revision_content:
                    run_for_task = getattr(self._store, "run_for_task", None)
                    linkage = await asyncio.to_thread(run_for_task, t_ref) if run_for_task else None
                    if linkage is None:
                        await self._transition_rejected(record.command_id)
                        return "controller task has no run linkage; input revision rejected"
                    input_revision = {
                        "source": "provide_input",
                        "source_ref": record.command_id,
                        "content": revision_content,
                        "wait_token": token,
                    }
                execution_id = contract_ref.get("execution_id")
                if isinstance(execution_id, str) and execution_id:
                    acknowledged = list(base_input.get("_input_wait_acknowledgements", []))
                    if execution_id not in acknowledged:
                        acknowledged.append(execution_id)
                    merged_input["_input_wait_acknowledgements"] = acknowledged
            else:
                # The approval consumer still needs the authority reference;
                # it is control data and must never become an input revision.
                merged_input["provided"] = wake_payload
        else:
            merged_input = {**base_input, "provided": wake_payload}
        extra_update = {"wait": {**wait, "consumed": True}}
        if budget_used_delta is not None:
            extra_update["agent_child_budget_used"] = (
                int((current.extra or {}).get("agent_child_budget_used") or 0) + budget_used_delta
            )
        if verified_workflow_callback and "step_index" in wake_payload:
            callbacks = dict((current.extra or {}).get("_workflow_callbacks") or {})
            index = str(wake_payload["step_index"])
            if index in callbacks:
                await self._transition_rejected(record.command_id)
                return "workflow step callback already applied; rejected"
            callbacks[index] = {
                "child_task_id": (wake_payload.get("child_outcome") or {}).get("child_task_id"),
                "applied_fact_id": record.command_id,
            }
            extra_update["_workflow_callbacks"] = callbacks
            # A controller step's trusted slice figure accumulates on the
            # parent: later steps and restarts never regain the spent
            # budget. The declared step slice binds the delta — steps
            # without a declaration stay unmetered.
            steps = ((base_input.get("workflow") or {}).get("steps")) or []
            step = steps[int(wake_payload["step_index"])] if int(wake_payload["step_index"]) < len(steps) else {}
            child_budget = (wake_payload.get("child_outcome") or {}).get("budget") or {}
            if isinstance(step.get("budget_units"), int) and isinstance(child_budget.get("used"), int):
                extra_update["workflow_budget_used"] = int(
                    (current.extra or {}).get("workflow_budget_used") or 0
                ) + int(child_budget["used"])
        wait_run = await asyncio.to_thread(self._store.run_for_task, t_ref) if hasattr(self._store, "engine") else None
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
                input_revision=input_revision,
                **(
                    {
                        "expected_wait": wait,
                        "expected_wait_state": expected_state,
                        "event_run_ref": wait_run,
                        "wait_expired": False,
                        "approval_deadline": approval_deadline,
                        "wait_clock": approval_clock,
                    }
                    if expected_state in (TaskLifecycleState.WAITING_APPROVAL, TaskLifecycleState.WAITING_INPUT)
                    and hasattr(self._store, "engine")
                    else {}
                ),
                **({"applied_command_id": record.command_id} if hasattr(self._store, "engine") else {}),
            )
        except (InvalidTaskTransitionError, ValueError):
            await self._transition_rejected(record.command_id)
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

    async def _queue_live_input(
        self,
        t_ref: BackendRef,
        record: ControlCommandRecord,
        *,
        managed: bool,
    ) -> str:
        """Receive a live supplement while a platform controller task runs.

        The receipt is the durable fact: the pending revision persists and
        the command applies on receipt — ``applied`` never means consumed.
        Binding and consumption belong to the lease-holding loop's decision
        boundary, so a running pass parks itself and nothing is dispatched
        here. Unsupported shapes are rejected explicitly, never faked into
        the wake path.
        """

        if _expired(record.expires_at):
            expired = await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.EXPIRED)
            return f"live input expired before receipt (receipt {expired.state.value})"
        if managed:
            await self._transition_rejected(record.command_id)
            return "managed runner hosts do not accept live input; rejected"
        if not hasattr(self._store, "record_live_input_revision"):
            await self._transition_rejected(record.command_id)
            return "live input requires the platform durable store; rejected"
        content = live_input_payload(record.payload)
        if content is None:
            await self._transition_rejected(record.command_id)
            return "live input payload is not a versioned content envelope; rejected"
        state = await self._lifecycle_state(t_ref)
        if state != TaskLifecycleState.RUNNING.value:
            await self._transition_rejected(record.command_id)
            return f"task is {state}, not running; live input rejected"
        base_input = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
        if base_input.get("controller_loop") is not True:
            await self._transition_rejected(record.command_id)
            return "live input requires a controller_loop task; rejected"
        if _is_child_task_input(base_input):
            await self._transition_rejected(record.command_id)
            return "child tasks do not accept live input; rejected"
        try:
            revision_id = await asyncio.to_thread(
                self._store.record_live_input_revision,
                t_ref,
                command_id=record.command_id,
                content=content,
                expected_revision=record.expected_revision,
            )
        except ValueError:
            # Lost CAS, not-running race or a conflicting duplicate: the
            # authoritative store decided; the receipt rejects without
            # publishing internal state.
            await self._db.commit()
            await self._transition_rejected(record.command_id)
            return "live input rejected: task moved on concurrently or request is stale"
        await self._db.commit()
        logger.info(
            "task %s received live input (revision %s); binds at the next decision boundary",
            t_ref.id,
            revision_id,
        )
        return "live input received; it binds at the next not-yet-started decision boundary"

    async def _live_input_capability(
        self, workspace_id: uuid.UUID, t_ref: BackendRef, record: Any
    ) -> dict[str, Any] | None:
        """Whether the detail page may offer a live-supplement composer.

        Only platform-local ``controller_loop`` tasks carry the block at
        all. Within them, a managed runner host, a non-running state or a
        missing durable store each renders its own explicit reason; the
        page never derives write permission locally.
        """

        if not hasattr(self._store, "record_live_input_revision"):
            return None
        base_input = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
        if base_input.get("controller_loop") is not True:
            return None
        if _is_child_task_input(base_input):
            return {"supported": False, "reason": "child tasks do not accept live input"}
        if record is None or record.lifecycle_state is not TaskLifecycleState.RUNNING:
            return {"supported": False, "reason": "task is not running; live input needs the running state"}
        from hecate.execution.managed_channel import ManagedDeliveryService

        try:
            managed = await ManagedDeliveryService(self._db).get_by_task_ref(workspace_id, t_ref.to_dict())
        except Exception:
            return {"supported": False, "reason": "live input support could not be verified"}
        if managed is not None:
            return {"supported": False, "reason": "managed runner hosts do not accept live input"}
        return {"supported": True, "reason": None}

    async def _live_input_delivery_map(self, t_ref: BackendRef, records: list[ControlCommandRecord]) -> dict[str, Any]:
        """Whitelisted delivery facts for live-supplement receipts, by command id.

        The pending revision and its pinning decisions are required to
        interpret an applied receipt. A read failure must not present that
        receipt as proof of delivery.
        """

        applied = {
            record.command_id
            for record in records
            if record.payload_schema_ref == TASK_LIVE_INPUT_SCHEMA_REF and record.state is CommandState.APPLIED
        }
        if not applied:
            return {}
        try:
            rows = await asyncio.to_thread(self._store.list_input_revisions_with_delivery, t_ref)
            delivery = {row["source_ref"]: row for row in rows if row["source_ref"] in applied}
        except Exception as exc:
            logger.warning("task %s live input delivery read failed (%s)", t_ref.id, type(exc).__name__)
            raise TaskControlError("live input delivery is unavailable") from exc
        if applied - delivery.keys():
            raise TaskControlError("live input delivery is unavailable")
        return delivery

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
        from hecate_governance.clock import TimeAnchor, TrustedClock

        def trusted_now() -> datetime:
            now, problem = TrustedClock().trusted_now(
                TimeAnchor(binding.anchor_wall, binding.anchor_monotonic, binding.clock_epoch)
            )
            if problem:
                raise ValueError("approval wait commit time cannot be trusted")
            return now

        return _ApprovalWakeGate(
            "authorize",
            "",
            fact={"approval_ref": ref},
            expires_at=binding.expires_at.isoformat() if binding.expires_at is not None else None,
            clock=trusted_now if binding.expires_at is not None or wait.get("wait_expires_at") is not None else None,
        )

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
            await self._transition_rejected(record.command_id)
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
        policy. Delegates to the shared convergence used by the
        wait-expiry path: the callback rides the SAME verified path as
        any child terminal, with idempotency inherited unchanged.
        """

        from hecate.execution.approval_wait_expiration import converge_workflow_parent

        await converge_workflow_parent(self._db, self._store, self._session_factory, t_ref, outcome)

    async def _request_cancel(self, t_ref: BackendRef, record: ControlCommandRecord, workspace_id: uuid.UUID) -> str:
        state = await self._lifecycle_state(t_ref)
        if state in (
            TaskLifecycleState.SUCCEEDED.value,
            TaskLifecycleState.FAILED.value,
            TaskLifecycleState.CANCELLED.value,
        ):
            await self._transition_rejected(record.command_id)
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
            terminal_fields: dict[str, Any] = {}
            if hasattr(self._store, "engine"):
                payload = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
                if payload.get("workflow_controller_step") is True:
                    run_ref = await asyncio.to_thread(self._store.run_for_task, t_ref)
                    outcome = {"status": "cancelled", "content": "", "error": "workflow step cancelled"}
                    terminal_fields = {
                        "extra_update": {"result": outcome},
                        "event_run_ref": run_ref,
                        "terminal_payload": outcome,
                    }
            try:
                await asyncio.to_thread(
                    self._store.apply_task_state,
                    t_ref,
                    TaskLifecycleState.CANCELLED,
                    **terminal_fields,
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
            await self._cancel_pending_agent_child(t_ref, current, workspace_id)
            await self._cancel_pending_workflow_child(t_ref, current, workspace_id)
            if terminal_fields:
                try:
                    await self.settle_workflow_step_budget(workspace_id=workspace_id, child_task_id=uuid.UUID(t_ref.id))
                except Exception:  # noqa: BLE001 — the terminal outbox retries settlement
                    logger.warning("task %s workflow cancel settlement deferred", t_ref.id, exc_info=True)
            return f"cancel applied before dispatch (receipt {applied.state.value})"
        await asyncio.to_thread(self._recorder.transition, record.command_id, CommandState.REQUESTED)
        return _CANCEL_RUNNING_NOTE

    async def _cancel_pending_agent_child(self, t_ref: BackendRef, current: Any, workspace_id: uuid.UUID) -> None:
        """Persist a cancel request for the child an unconsumed agent wait holds.

        A cancel request is not a stop fact: the child converges through its
        own dispatch rules, and an already-terminal child's cancel command
        is rejected honestly. Failures are logged — the parent's committed
        cancel never depends on the propagation.
        """

        wait = (current.extra or {}).get("wait") if current else None
        from hecate.execution.agent_child import agent_child_contract, verify_child_call

        contract = agent_child_contract((wait or {}).get("contract_ref")) if isinstance(wait, dict) else None
        if (
            contract is None
            or wait.get("wake_kind") != "agent_child"
            or wait.get("consumed")
            or not contract.get("child_task_ref", {}).get("id")
        ):
            return
        try:
            await verify_child_call(self._store, t_ref, contract, wait=wait)
            child_task_id = uuid.UUID(str(contract["child_task_ref"]["id"]))
            await self.issue_command(
                workspace_id=workspace_id,
                task_id=child_task_id,
                kind=ControlCommandKind.CANCEL,
                issuer="agent-child-cancel",
                command_id=f"agent-child-cancel:{t_ref.id}:{contract['call_id']}",
            )
        except Exception:  # noqa: BLE001 — propagation must not un-commit the cancel
            logger.warning("task %s agent child cancel propagation failed", t_ref.id, exc_info=True)

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

    async def get_command_receipt(self, workspace_id: uuid.UUID, command_id: str) -> dict[str, Any]:
        """Return a whitelisted receipt with its current live delivery facts."""
        record = await self.get_command(workspace_id, command_id)
        summary = command_receipt_summary(record)
        delivery = await self._live_input_delivery_map(record.task_ref, [record])
        if command_id in delivery:
            summary["delivery"] = delivery[command_id]
        return summary

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
            delivery = await self._live_input_delivery_map(task_ref(task.issuer_domain, str(task.id)), items)
            return {
                "items": [
                    {**command_receipt_summary(record), "delivery": delivery[record.command_id]}
                    if record.command_id in delivery
                    else command_receipt_summary(record)
                    for record in items
                ],
                "total": total,
            }
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
        # The stored acceptance view is surfaced as its own read-validated
        # field instead of raw inside the projection: a malformed or foreign
        # entry is reported uninterpretable rather than reaching a client as
        # a verdict, and absence stays an honest "no record".
        projection = dict(run.projection or {})
        acceptance = read_acceptance_view(
            projection.pop("delivery_acceptance", None),
            task_ref={"issuer": run.issuer_domain, "id": str(run.task_id)},
            run_ref={"issuer": run.issuer_domain, "id": str(run.id)},
            workspace_id=str(run.workspace_id),
        )
        # Shared checker errors can embed complete output values or schema
        # parameters, beyond the deliberately bounded delivery preview.
        if projection.get("error_category") in ("acceptance_failed", "acceptance_unverified"):
            projection["error"] = "Delivery acceptance failed or could not be verified."
        summary = {
            "run_id": str(run.id),
            "attempt_no": run.attempt_no,
            "deployment_id": str(run.deployment_id),
            "backend_ref": run.backend_ref,
            "projection": projection,
            "event_cursor": run.event_cursor,
        }
        if acceptance is not None:
            summary["delivery_acceptance"] = acceptance
        return summary

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
