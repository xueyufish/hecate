"""Platform dispatch callback for the durable worker (step6 worker change).

The worker claims a queued task and invokes this callback; the callback owns
everything execution-shaped:

- loading the persisted input payload (the submit path stores it on the
  durable task row, so a restart re-dispatches from durable state, not from
  a lost in-process closure);
- resolving the run attempt. The first dispatch (task revision 0 at claim
  time) executes the run minted at submit; every later dispatch — retry,
  reconciliation of an interrupted attempt, or a wake from a durable wait —
  creates a NEW attempt run (the plan rule: a retry is a new Run, never a
  reused run_id). A latest run that already carries a terminal projection
  but left the task non-terminal (crash window between the two writes) is
  backfilled without re-executing.
- running the execution through the entry service and persisting the run
  stream exactly like the previous in-process dispatch did;
- driving the lifecycle onward through the durable seam so every transition
  and its governance event commit in the same transaction as the state
  change. Terminal outcomes append the durable ``run_terminal`` event; the
  outbox relay projects it into the platform read model.

A :class:`TaskWaitingSignalError` parks the task in a durable wait instead of a
terminal state — the wake path lives on the task-control command surface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from hecate_runtime.errors import ApprovalWaitSignal
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.contracts.execution.durable import ControlCommandKind, TaskLifecycleState, TaskStateRecord
from hecate.contracts.execution.events import ActorKind, ActorRef
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind, deployment_ref, run_ref
from hecate.execution.delivery_acceptance_view import delivery_projection
from hecate.execution.entry_events import RunEventMapper
from hecate.execution.governance_events import PlatformEventService
from hecate.execution.task_run_registry import TaskRunRegistry, TaskRunRegistryError
from hecate.execution.workflow_child import WorkflowChildTaskAdapter, WorkflowPlan
from hecate.models.agent import AgentModel
from hecate.models.run import RunModel

logger = logging.getLogger(__name__)

PLATFORM_ISSUER = "hecate"

_RUN_TERMINAL_EVENT = "run_terminal"
_RUN_PROJECTION_TERMINAL = {"succeeded", "failed", "cancelled"}


def _definition_digest(snapshot: dict[str, Any], effective_tools: list[dict[str, Any]], model_name: str) -> str:
    """Freeze the resolved execution definition of one dispatch (step6d).

    Covers tool ORDER plus each resolved tool's identity (name/permission/
    schema), the model reference, and the persona/guardrail/manifest refs
    from the frozen execution snapshot. Recorded on the run at first
    dispatch and recomputed at resume; a mismatch means the interrupted
    session would continue under a different definition than it started
    with, so the gate refuses instead of silently re-homing the run.
    """

    from hecate_durable.contracts.durable import canonical_request_digest

    return canonical_request_digest(
        {
            "tools": [
                {
                    "name": tool.get("name"),
                    "permission": tool.get("permission"),
                    "schema_ref": tool.get("schema_ref") or tool.get("schema"),
                }
                for tool in effective_tools
            ],
            "tool_refs": snapshot.get("config_snapshot", {}).get("tools") or [],
            "model": model_name,
            "guardrail_config": snapshot.get("config_snapshot", {}).get("guardrail_config"),
            "ref_manifest": snapshot.get("ref_manifest") or [],
        }
    )


class TaskWaitingSignalError(Exception):
    """Raised by an execution wrapper to park the task in a durable wait.

    ``wake_kind`` names the command that may resume the task
    (``provide_input`` for input waits, ``resume`` for approval waits —
    the closed command vocabulary keeps approval decisions on ``resume``;
    policy belongs to step7). ``contract_ref`` records what the wait is
    for; it is persisted with the state and surfaced on the task detail.
    """

    def __init__(
        self,
        wake_kind: ControlCommandKind,
        contract_ref: dict[str, Any],
        *,
        expires_in_seconds: float | None = None,
    ) -> None:
        if wake_kind not in (ControlCommandKind.PROVIDE_INPUT, ControlCommandKind.RESUME):
            raise ValueError(f"wake kind must be provide_input or resume, got {wake_kind!r}")
        super().__init__(f"task waits for {wake_kind.value}: {contract_ref}")
        self.wake_kind = wake_kind
        self.contract_ref = dict(contract_ref)
        self.expires_in_seconds = expires_in_seconds


def run_row_ref(run: RunModel) -> BackendRef:
    """Platform run-row reference for one execution attempt."""
    return BackendRef(RefKind.RUN, run.issuer_domain, str(run.id))


@dataclass(frozen=True)
class _DispatchContext:
    task_id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    user_id: uuid.UUID | None
    goal: str
    messages: list[dict[str, Any]]
    model: str | None
    stream: bool
    provided: dict[str, Any] | None
    # step6e: declarative child-step plan (parent side) and the parent
    # stamp (child side). Both come from the persisted task input.
    workflow_plan: WorkflowPlan | None = None
    workflow_parent: dict[str, Any] | None = None
    workflow_step: dict[str, Any] | None = None
    # step5e: durable approval wait opt-in from the task input payload.
    approval_wait: bool = False
    # Agent-controller loop opt-in: the durable decision chain is the
    # continuation position, so re-dispatches continue the SAME run instead
    # of minting attempt runs or resuming a Pregel engine session.
    controller_loop: bool = False
    controller_max_turns: int | None = None
    controller_deadline: str | None = None
    output_schema: dict[str, Any] | None = None
    platform_run_id: str | None = None
    # Versioned acceptance declaration: the goal/standard version plus the
    # deterministic required checks that gate task success.
    acceptance: dict[str, Any] | None = None


class PlatformTaskDispatcher:
    """Execution callback handed to the durable worker; owns run attempts."""

    def __init__(
        self,
        store: Any,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._store = store
        self._session_factory = session_factory

    async def __call__(
        self,
        task_ref: BackendRef,
        record: TaskStateRecord,
        lease: Any,
        *,
        db: AsyncSession | None = None,
    ) -> None:
        payload = await asyncio.to_thread(self._store.get_task_input, task_ref) or {}
        context = self._context_of(task_ref, payload)
        if db is not None:
            # Inline (in-request) dispatch: run on the caller's session so
            # the execution lands on the same database the request used.
            await self._run(task_ref, db, record, context, lease=lease)
            return
        async with self._db() as session:
            await self._run(task_ref, session, record, context, lease=lease)

    async def _run(
        self, task_ref: BackendRef, db: AsyncSession, record: TaskStateRecord, context: _DispatchContext, *, lease: Any
    ) -> None:
        registry = TaskRunRegistry(db)
        payload = await asyncio.to_thread(self._store.get_task_input, task_ref) or {}
        if "identity_chain" in payload:
            from hecate.execution.task_registration import ensure_submission_registration

            await ensure_submission_registration(db, task_ref, payload, sql_store=hasattr(self._store, "engine"))
        task = await registry.get_task(context.task_id, context.workspace_id)
        runs = await registry.list_runs_for_task(context.task_id, context.workspace_id)
        latest = runs[-1] if runs else None

        resume_interrupted = False
        # step5e: a consumed resume-wait contract IS the authorization fact
        # that this dispatch continues an approved approval park. The task
        # path records no run-projection stop category (the entry service's
        # shared-task correlation carries no run_ref), so the wait contract —
        # not the projection — routes the wake.
        wait_state = (record.extra or {}).get("wait") or {}
        approval_wake = (
            context.approval_wait
            and wait_state.get("consumed") is True
            and wait_state.get("wake_kind") == "resume"
            and bool((wait_state.get("contract_ref") or {}).get("approval_ref"))
            and latest is not None
            and (wait_state.get("contract_ref") or {}).get("run_id") == str(latest.id)
            and (
                not self._run_is_terminal(latest)
                or (latest.projection or {}).get("error_category") == "waiting_approval"
            )
        )
        if latest is not None and self._run_is_terminal(latest) and not approval_wake:
            projection = latest.projection or {}
            if projection.get("error_category") == "waiting_approval":
                # Defensive: a terminal waiting_approval projection without a
                # consumed wait would mean the park lost its contract — fail
                # closed instead of continuing.
                outcome: dict[str, str | None] = {
                    "status": "failed",
                    "content": "",
                    "error": "approval park lost its wait contract; fail closed",
                }
                await self._finish(
                    db,
                    task_ref,
                    run_row_ref(latest),
                    context,
                    outcome,
                    run=latest,
                    expected_revision=record.revision + 1,
                    lease=lease,
                )
                return
            # Crash window: the run finished but the task-terminal write
            # never landed. Backfill from the run projection; never
            # re-execute a finished attempt.
            raw_error = projection.get("error")
            outcome = {
                "status": projection.get("state", "failed"),
                "content": str(projection.get("result_preview") or ""),
                "error": str(raw_error) if raw_error is not None else None,
            }
            await self._finish(
                db,
                task_ref,
                run_row_ref(latest),
                context,
                outcome,
                run=latest,
                expected_revision=record.revision + 1,
                lease=lease,
            )
            return

        if latest is not None:
            try:
                await registry.validate_run_execution(latest)
            except (TaskRunRegistryError, ValueError) as exc:
                await db.commit()
                await asyncio.to_thread(
                    self._store.apply_task_state,
                    task_ref,
                    TaskLifecycleState.RECONCILIATION_REQUIRED,
                    expected_revision=record.revision + 1,
                    extra_update={"reconciliation_reason": f"execution admission no longer valid: {exc}"},
                    **({"lease": lease} if lease is not None else {}),
                )
                return

        if record.revision == 0 and latest is not None and not (latest.projection or {}):
            run = latest  # the attempt minted at submit; never executed
        elif context.controller_loop and latest is not None:
            # The controller's continuation position is the durable
            # accepted-decision chain keyed by the task's stable run, not a
            # Pregel engine session: re-dispatches (crash recovery, retry,
            # approval/input wake) continue the SAME run and replay the
            # chain from storage. Admission was re-validated above.
            run = latest
            engine_session = self._engine_session_of(latest)
            resume_interrupted = True
        elif approval_wake and latest is not None:
            # An authorized approval wake continues the parked attempt
            # NATIVELY (same run / engine session): the action ledger's
            # recovery decisions are session-keyed, so a fresh session would
            # lose the completed-action receipts and re-execute writes. The
            # resumption gate still enforces digest/snapshot admission; the
            # terminal-projection refusal is lifted for waiting_approval
            # parks only (design D4).
            resume_session = await self._resume_engine_session(db, latest, context, approval_wake=True)
            if resume_session is None:
                reason = self._last_resume_refusal or "approval wake could not resume the parked attempt"
                await asyncio.to_thread(
                    self._store.apply_task_state,
                    task_ref,
                    TaskLifecycleState.RECONCILIATION_REQUIRED,
                    expected_revision=record.revision + 1,
                    extra_update={"reconciliation_reason": reason},
                    **({"lease": lease} if lease is not None else {}),
                )
                return
            run = latest
            engine_session = resume_session
            resume_interrupted = True
        else:
            if latest is not None and hasattr(self._store, "list_run_actions"):
                actions = await asyncio.to_thread(self._store.list_run_actions, run_row_ref(latest))
                if any((action.get("intent") or {}).get("side_effect_class") != "readonly" for action in actions):
                    # step6d: an interrupted attempt with protected actions
                    # resumes NATIVELY on the same run/engine session when the
                    # resumption gate passes (snapshot loadable + frozen
                    # definition digest matches); the ledger arbitrates the
                    # actions (decided backfill, undecided stop). Chat history
                    # alone never qualifies. Anything the gate refuses keeps
                    # the conservative reconciliation.
                    resume_session = await self._resume_engine_session(db, latest, context)
                    if resume_session is not None:
                        run = latest
                        engine_session = resume_session
                        resume_interrupted = True
                    else:
                        reason = self._last_resume_refusal or "interrupted attempt contains protected actions"
                        await asyncio.to_thread(
                            self._store.apply_task_state,
                            task_ref,
                            TaskLifecycleState.RECONCILIATION_REQUIRED,
                            expected_revision=record.revision + 1,
                            extra_update={"reconciliation_reason": reason},
                            **({"lease": lease} if lease is not None else {}),
                        )
                        return
            if lease is not None and hasattr(self._store, "engine"):
                await db.run_sync(lambda session: self._store.leases.assert_valid(session, lease))
            if not resume_interrupted:
                # A gate-approved resume continues on ``latest``; minting a
                # fresh attempt here would re-home the interrupted session
                # onto a clean run and re-execute its protected actions.
                run = await self._new_attempt(db, task, latest, context)
        r_ref = run_row_ref(run)
        engine_session = self._engine_session_of(run)
        try:
            if context.workflow_plan is not None:
                outcome = await self._execute_workflow(db, context, task_ref=task_ref, lease=lease)
            elif context.controller_loop:
                outcome = await self._execute_controller(
                    db,
                    context,
                    engine_session=engine_session,
                    task_ref=task_ref,
                    run_ref=r_ref,
                    lease=lease,
                    resume_interrupted=resume_interrupted,
                    run=run,
                )
            else:
                outcome = await self._execute(
                    db,
                    context,
                    engine_session=engine_session,
                    task_ref=task_ref,
                    run_ref=r_ref,
                    lease=lease,
                    resume_interrupted=resume_interrupted,
                    run=run if isinstance(run, RunModel) else None,
                )
        except TaskWaitingSignalError as wait:
            await db.commit()
            await self._park(
                db, task_ref, wait, context, run_ref=r_ref, expected_revision=record.revision + 1, lease=lease
            )
            return
        except ApprovalWaitSignal as signal:
            # step5e: the durable-wait callback has no synchronous answerer.
            # The run attempt is already finalized (waiting_approval stop
            # category) by the entry service; park the task under a PENDING
            # authority binding. Any persistence failure fails the task —
            # a protected action must never run without its wait contract.
            await db.commit()
            try:
                wait_error = await self._park_approval(db, context, task_ref, signal)
                if wait_error is None:
                    raise RuntimeError("approval authority unavailable")
                wait_error.contract_ref["run_id"] = str(run.id)
                wait_error.contract_ref["engine_session_id"] = str(engine_session)
                await self._park(
                    db, task_ref, wait_error, context, run_ref=r_ref, expected_revision=record.revision + 1, lease=lease
                )
            except Exception:
                logger.warning("task %s could not persist approval wait", task_ref.id, exc_info=True)
                await db.rollback()
                run = await registry.get_run(uuid.UUID(r_ref.id), context.workspace_id)
                await self._finish(
                    db,
                    task_ref,
                    r_ref,
                    context,
                    {
                        "status": "failed",
                        "content": "",
                        "error": "approval wait could not be persisted; fail closed",
                        "error_category": "approval_wait_persistence_failed",
                    },
                    run=run,
                    expected_revision=record.revision + 1,
                    lease=lease,
                )
            return
        except Exception as exc:
            from hecate_runtime.errors import ExecutionStoppedError

            if isinstance(exc, ExecutionStoppedError) and exc.reason_category == "needs_review":
                raise
            if isinstance(exc, ExecutionStoppedError) and exc.reason_category.startswith("approval_"):
                await self._finish(
                    db,
                    task_ref,
                    r_ref,
                    context,
                    {
                        "status": "failed",
                        "content": "",
                        "error": exc.reason_category,
                        "error_category": exc.reason_category,
                    },
                    run=run,
                    expected_revision=record.revision + 1,
                    lease=lease,
                )
                return
            if context.workflow_parent is None:
                raise
            # A workflow child converges deterministically: an execution
            # failure becomes a failed terminal (which auto-calls the
            # parent) instead of the worker's retry budget — orchestration
            # handling is the parent's declared on_failure.
            await db.commit()
            await self._finish(
                db,
                task_ref,
                r_ref,
                context,
                {"status": "failed", "content": "", "error": str(exc)},
                run=run,
                expected_revision=record.revision + 1,
                lease=lease,
            )
            return
        if hasattr(self._store, "list_run_actions"):
            actions = await asyncio.to_thread(self._store.list_run_actions, r_ref)
            if any(action.get("pending_reconciliation") or action.get("active_claim") for action in actions):
                await db.commit()
                await asyncio.to_thread(
                    self._store.apply_task_state,
                    task_ref,
                    TaskLifecycleState.RECONCILIATION_REQUIRED,
                    expected_revision=record.revision + 1,
                    extra_update={"reconciliation_reason": "action outcome is not established"},
                    **({"lease": lease} if lease is not None else {}),
                )
                return
        await self._finish(
            db,
            task_ref,
            r_ref,
            context,
            outcome,
            run=run,
            expected_revision=record.revision + 1,
            lease=lease,
        )

    def _validate_step_artifacts(
        self,
        db: AsyncSession,
        task_ref: BackendRef,
        step: dict[str, Any],
        child_outcome: dict[str, Any],
    ) -> str | None:
        """Validate the delivered child facts against the step declaration.

        Returns the first violation message, or None when the step declared
        no contract. Output schema checks the delivered content (JSON when it
        parses); artifact checks the expected key in the child's artifacts.
        """
        from hecate.core.output_schema import output_schema_error

        output_schema = step.get("output_schema")
        if output_schema is not None:
            content = child_outcome.get("content", "")
            import json as _json

            try:
                document = _json.loads(content) if isinstance(content, str) else content
            except _json.JSONDecodeError as exc:
                return f"delivered content is not valid JSON: {exc.msg}"
            error = output_schema_error(document, output_schema)
            if error is not None:
                return f"delivered output violates the declared schema: {error}"
        artifact_key = step.get("artifact")
        if artifact_key is not None:
            artifacts = child_outcome.get("artifacts") or {}
            if not isinstance(artifacts, dict) or artifact_key not in artifacts or artifacts[artifact_key] is None:
                return f"expected artifact {artifact_key!r} missing from the child outcome"
        return None

    async def _execute_workflow(
        self,
        db: AsyncSession,
        context: _DispatchContext,
        *,
        task_ref: BackendRef,
        lease: Any,
    ) -> dict[str, Any]:
        """Drive one declarative workflow step (step6e named adapter).

        Progress derives exclusively from persisted facts: the wake payload
        (``provided``) carries the last completed step index and the child's
        verified outcome, so a restarted dispatcher re-derives the next step
        and never re-submits one. Each step is a REAL child task submitted
        through ``TaskControlService.submit``; the parent parks on the
        child's ``await_task_ref`` until the verified auto-callback wakes it.
        """

        plan = context.workflow_plan
        if plan is None:  # unreachable: the caller branches on the plan
            return {"status": "failed", "content": "", "error": "workflow plan missing"}
        provided = context.provided or {}
        done_index = provided.get("step_index")
        if done_index is not None:
            if type(done_index) is not int or not 0 <= done_index < len(plan.steps):
                return {"status": "failed", "content": "", "error": "invalid workflow callback step_index"}
            child_outcome = provided.get("child_outcome") or {}
            child_state = child_outcome.get("child_state")
            if child_state != "succeeded":
                # step5e: an approval rejection converges here through the
                # verified callback too — its category is distinguishable
                # from a plain execution failure on the committed facts.
                child_category = child_outcome.get("error_category")
                suffix = f" ({child_category})" if child_category else ""
                detail = f"workflow step {done_index} failed (source=execution): child ended {child_state}{suffix}"
                if plan.on_failure == "await":
                    return {"status": "awaiting_reconciliation", "content": detail, "failure_source": "execution"}
                return {"status": "failed", "content": "", "error": detail, "failure_source": "execution"}
            # step5e: artifact/output validation gates the parent's advance.
            # The parent never advances on the child's self-reported success
            # alone — the declared contract must hold on the delivered facts.
            validation_error = self._validate_step_artifacts(
                db, task_ref, plan.steps[int(done_index)], provided.get("child_outcome") or {}
            )
            if validation_error is not None:
                detail = f"workflow step {done_index} failed (source=validation): {validation_error}"
                if plan.on_failure == "await":
                    return {"status": "awaiting_reconciliation", "content": detail, "failure_source": "validation"}
                return {"status": "failed", "content": "", "error": detail, "failure_source": "validation"}
        next_index = 0 if done_index is None else int(done_index) + 1
        if next_index >= len(plan.steps):
            return {"status": "succeeded", "content": f"workflow completed {len(plan.steps)} step(s)"}
        service = self._task_control(db)
        child_ref = await WorkflowChildTaskAdapter.submit_step(
            service,
            context.workspace_id,
            context.agent_id,
            task_ref,
            plan.steps[next_index],
            next_index,
        )
        raise TaskWaitingSignalError(
            ControlCommandKind.PROVIDE_INPUT,
            {"await_task_ref": child_ref.to_dict(), "step_index": next_index},
        )

    def _task_control(self, db: AsyncSession) -> Any:
        # Lazy import: task_control imports this module at its own module
        # level (the dispatch spawn), so the reverse edge stays function-local.
        from hecate.execution.task_control import TaskControlService

        return TaskControlService(
            db,
            store=self._store,
            recorder=self._store,
            backend="postgres",
            ledger_source="core",
            session_factory=self._session_factory,
        )

    async def _issue_child_callback(
        self, db: AsyncSession, context: _DispatchContext, outcome: dict[str, Any], *, retry_errors: bool = False
    ) -> None:
        """Auto-callback: a workflow child's terminal fact wakes its parent.

        The child's input payload carries the parent reference (stamped at
        submit); the callback is issued through the REAL verified path
        (``submit_workflow_callback``) with the parent's live wait token —
        the platform trusts its own records, so no external caller is
        involved. The immediate path logs failures after the terminal commit;
        the outbox path propagates them to its durable retry cursor. Neither
        path undoes the child's committed terminal fact.
        """

        parent = context.workflow_parent or {}
        raw_ref = parent.get("task_ref") or {}
        declared = outcome.get("status")
        if not isinstance(raw_ref, dict) or declared not in ("succeeded", "failed", "cancelled"):
            return
        try:
            parent_ref = BackendRef(RefKind.TASK, str(raw_ref.get("issuer_domain")), str(raw_ref.get("id")))
            state = await asyncio.to_thread(self._store.get_task_state, parent_ref)
            wait = (state.extra or {}).get("wait") or {} if state else {}
            token = str(wait.get("wait_token") or "")
            if (
                not token
                or state.lifecycle_state is not TaskLifecycleState.WAITING_INPUT
                or wait.get("consumed")
                or (wait.get("contract_ref") or {}).get("await_task_ref")
                != BackendRef(RefKind.TASK, PLATFORM_ISSUER, str(context.task_id)).to_dict()
            ):
                return
            service = self._task_control(db)
            receipt = await service.submit_workflow_callback(
                workspace_id=context.workspace_id,
                task_id=uuid.UUID(parent_ref.id),
                command_id=f"wf-callback-{context.task_id}-{token}",
                wait_token=token,
                child_task_id=context.task_id,
                declared_state=str(declared),
                payload={"step_index": parent.get("step_index")},
            )
            if retry_errors and receipt.record.state.value != "applied":
                current = await asyncio.to_thread(self._store.get_task_state, parent_ref)
                current_wait = (current.extra or {}).get("wait") or {} if current else {}
                if (
                    current is not None
                    and current.lifecycle_state is TaskLifecycleState.WAITING_INPUT
                    and current_wait.get("wait_token") == token
                    and not current_wait.get("consumed")
                ):
                    raise RuntimeError(f"workflow callback was not applied: {receipt.detail}")
        except Exception:  # noqa: BLE001 — the outbox retries without undoing the terminal
            if retry_errors:
                raise
            logger.warning("task %s workflow auto-callback deferred to outbox", context.task_id, exc_info=True)

    # -- internals -------------------------------------------------------------

    def _db(self) -> AsyncSession:
        from hecate.core.database import async_session_factory

        factory = self._session_factory or async_session_factory
        return factory()

    @staticmethod
    def _context_of(task_ref: BackendRef, payload: dict[str, Any]) -> _DispatchContext:
        try:
            workspace_id = uuid.UUID(str(payload["workspace_id"]))
            agent_id = uuid.UUID(str(payload["agent_id"]))
        except (KeyError, ValueError) as exc:
            raise ValueError(f"task {task_ref.id} input payload is missing agent/workspace attribution") from exc
        user_raw = payload.get("user_id")
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError(f"task {task_ref.id} input payload has no messages list")
        controller = payload.get("controller") if isinstance(payload.get("controller"), dict) else {}
        return _DispatchContext(
            task_id=uuid.UUID(task_ref.id),
            workspace_id=workspace_id,
            agent_id=agent_id,
            user_id=uuid.UUID(str(user_raw)) if user_raw else None,
            goal=str(payload.get("goal") or ""),
            messages=messages,
            model=payload.get("model"),
            stream=bool(payload.get("stream")),
            provided=payload.get("provided"),
            workflow_plan=WorkflowChildTaskAdapter.plan(payload),
            workflow_parent=(payload["workflow_parent"] if isinstance(payload.get("workflow_parent"), dict) else None),
            workflow_step=payload.get("workflow_step"),
            approval_wait=payload.get("approval_wait") is True,
            controller_loop=payload.get("controller_loop") is True,
            controller_max_turns=controller.get("max_turns") if isinstance(controller.get("max_turns"), int) else None,
            controller_deadline=controller.get("deadline") if isinstance(controller.get("deadline"), str) else None,
            output_schema=payload.get("output_schema") if isinstance(payload.get("output_schema"), dict) else None,
            platform_run_id=payload.get("platform_run_id") if isinstance(payload.get("platform_run_id"), str) else None,
            acceptance=payload.get("acceptance") if isinstance(payload.get("acceptance"), dict) else None,
        )

    @staticmethod
    def _run_is_terminal(run: RunModel) -> bool:
        return (run.projection or {}).get("state") in _RUN_PROJECTION_TERMINAL

    @staticmethod
    def _engine_session_of(run: RunModel) -> uuid.UUID:
        backend = run.backend_ref if isinstance(run.backend_ref, dict) else {}
        return uuid.UUID(str(backend.get("id")))

    _last_resume_refusal: str | None = None

    async def _resume_engine_session(
        self, db: AsyncSession, latest: RunModel, context: _DispatchContext, *, approval_wake: bool = False
    ) -> uuid.UUID | None:
        """step6d resumption gate for a protected-interrupted attempt.

        Returns the engine session to resume natively, or ``None`` (with the
        refusal reason recorded for the reconciliation trail) when any
        precondition fails: no recorded definition digest, resolved
        definition drift, a terminal projection on the run, or a missing
        engine-session snapshot. Admission (principal/deployment/version)
        has already been re-validated by the caller before this gate.

        ``approval_wake`` lifts ONLY the terminal-projection refusal: a
        parked approval attempt finalizes its run with the waiting_approval
        stop category, and an authorized resume continues that same
        attempt/session (session-keyed receipt recovery demands it). Every
        other gate — digest match, snapshot loadable — still applies.
        """

        self._last_resume_refusal = None
        snapshot = latest.execution_snapshot or {}
        recorded = snapshot.get("definition_digest")
        if not recorded:
            self._last_resume_refusal = "resume refused: no recorded definition digest"
            return None
        if (latest.projection or {}).get("state") in _RUN_PROJECTION_TERMINAL and not (
            approval_wake and (latest.projection or {}).get("error_category") == "waiting_approval"
        ):
            self._last_resume_refusal = "resume refused: run already reached a terminal projection"
            return None

        from hecate.core.composition.entry_assembly import load_agent_tools

        config = snapshot.get("config_snapshot") or {}
        tool_refs = config.get("tools") or []
        effective_tools: list[dict[str, Any]] = []
        if tool_refs:
            try:
                effective_tools = await load_agent_tools(db, tool_refs, workspace_id=context.workspace_id)
            except Exception as exc:  # noqa: BLE001 — drift/failure both refuse
                self._last_resume_refusal = f"resume refused: tool resolution failed: {exc}"
                return None
        model_name = config.get("model_config", {}).get("model", "gpt-4o")
        digest = _definition_digest(snapshot, effective_tools, model_name)
        if digest != recorded:
            self._last_resume_refusal = "resume refused: execution definition digest drifted"
            return None

        engine_session = self._engine_session_of(latest)
        from hecate_runtime.execution_assembly import system_execution_tenant

        from hecate.core.composition.entry_assembly import get_shared_session_state_store

        # The tenant key mirrors the save side (execution_assembly): the
        # verified user for human-initiated tasks, the agent-principal
        # derived key for system-initiated ones (workflow children —
        # step5e). Both derive from the same persisted task payload.
        tenant = context.user_id or system_execution_tenant(context.agent_id)
        if tenant is None:
            self._last_resume_refusal = "resume refused: execution carries no user or agent attribution"
            return None

        try:
            state_store = get_shared_session_state_store()
            snapshot_state = await state_store.load(
                org_id=tenant,
                user_id=tenant,
                session_id=engine_session,
            )
        except Exception as exc:  # noqa: BLE001 — unloadable is unresumable
            self._last_resume_refusal = f"resume refused: engine session snapshot not loadable: {exc}"
            return None
        if snapshot_state is None:
            self._last_resume_refusal = "resume refused: engine session snapshot missing"
            return None
        return engine_session

    async def _new_attempt(
        self, db: AsyncSession, task: Any, latest: RunModel | None, context: _DispatchContext
    ) -> RunModel:
        """A re-dispatch executes under a NEW attempt run (new run_id)."""

        engine_session = uuid.uuid4()
        deployment_id = latest.deployment_id if latest is not None else None
        if deployment_id is None:
            raise ValueError(f"task {task.id} has no prior run to derive the deployment from")
        if task.initiator_ref:
            chain = IdentityChain.from_dict(task.initiator_ref)
        else:
            chain = IdentityChain(
                initiator=None,
                principal_id="platform-system",
                workload=WorkloadIdentity(
                    deployment=deployment_ref("hecate", str(deployment_id)),
                    workload_id="hecate:task-control",
                ),
                audience="hecate:task-control",
            )
        try:
            run = await TaskRunRegistry(db).create_run(
                task_id=task.id,
                workspace_id=context.workspace_id,
                deployment_id=deployment_id,
                identity_chain=chain,
                backend_run_ref=run_ref(PLATFORM_ISSUER, str(engine_session)),
            )
        except TaskRunRegistryError as exc:
            raise ValueError(f"task {task.id} attempt run could not be created: {exc}") from exc
        await db.commit()
        return run

    async def _execute(
        self,
        db: AsyncSession,
        context: _DispatchContext,
        *,
        engine_session: uuid.UUID,
        task_ref: BackendRef,
        run_ref: BackendRef,
        lease: Any = None,
        resume_interrupted: bool = False,
        run: RunModel | None = None,
    ) -> dict[str, Any]:
        """Run one execution through the platform entry service (shared assembly)."""

        from hecate_llm.service import llm_service

        from hecate.core.composition.entry_assembly import (
            build_tool_registry,
            get_shared_event_store,
            load_agent_tools,
        )
        from hecate.core.composition.guardrail_platform import assemble_guardrails
        from hecate.core.composition.runtime_port_adapter import create_runtime_port
        from hecate.execution.entry_service import CorrelationInput, EntryExecutionService
        from hecate.models.agent_version import AgentVersionModel

        agent = await db.get(AgentModel, context.agent_id)
        if agent is None:
            raise ValueError(f"agent {context.agent_id} no longer resolves for task {task_ref.id}")
        run = await TaskRunRegistry(db).get_run(uuid.UUID(run_ref.id), context.workspace_id)
        snapshot = run.execution_snapshot or {}
        version = await db.get(AgentVersionModel, uuid.UUID(snapshot["agent_version_id"]))
        if version is None or version.deleted:
            raise ValueError("the frozen agent version no longer resolves")
        config = snapshot.get("config_snapshot") or {}
        tool_refs = config.get("tools") or []
        event_store = None
        tool_registry = None
        effective_tools: list[dict[str, Any]] = []
        bundle = None
        if tool_refs:
            event_store = get_shared_event_store()
            tool_registry = build_tool_registry(db, workspace_id=context.workspace_id)
            effective_tools = await load_agent_tools(db, tool_refs, workspace_id=context.workspace_id)
            if effective_tools:
                bundle = await assemble_guardrails(
                    db,
                    workspace_id=context.workspace_id,
                    agent_id=agent.id,
                    guardrail_config=config.get("guardrail_config"),
                    event_store=event_store,
                    session_id=engine_session,
                    user_id=context.user_id,
                    dlp_scanner=None,
                )
        await self._wire_approval_backend(db, context, bundle, resume_interrupted=resume_interrupted)

        port = create_runtime_port(
            db,
            llm_service,
            tool_registry=tool_registry,
            workspace_id=context.workspace_id,
            agent_id=agent.id,
            identity={
                "actor": str(context.user_id) if context.user_id else f"agent:{agent.id}",
                "agent_principal": str(agent.id),
                "task_scope": str(context.task_id) if context.approval_wait else None,
            },
        )
        action_hook = None
        if hasattr(self._store, "claim_ex"):
            from hecate_durable.runtime_hook import SqlActionLedgerHook

            action_hook = SqlActionLedgerHook(
                self._store,
                task_ref=task_ref,
                run_ref=run_ref,
                holder=lease.holder if lease is not None else "platform-inline",
                lease=lease,
            )
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
            action_hook=action_hook,
        )
        correlation = CorrelationInput(
            workspace_id=context.workspace_id,
            agent_id=agent.id,
            user_id=context.user_id,
            session_id=engine_session,
            goal=context.goal[:200],
            existing_task_id=uuid.UUID(task_ref.id),
        )
        model_name = context.model or (config.get("model_config") or {}).get("model", "gpt-4o")
        digest = _definition_digest(snapshot, effective_tools, model_name)
        if run is not None and (run.execution_snapshot or {}).get("definition_digest") is None:
            # First dispatch of this attempt freezes the resolved definition
            # (step6d); the resume gate recomputes and compares it. The
            # commit is immediate ON PURPOSE: a crash mid-execution must
            # still leave the digest recorded, or the interrupted attempt
            # could never qualify for native continuation.
            run.execution_snapshot = {**snapshot, "definition_digest": digest}
            await db.commit()
        outcome = await entry.execute(
            resume_interrupted=resume_interrupted,
            agent_mode="chat",
            messages=[
                *(
                    [
                        {
                            "role": "system",
                            "content": "Return final output as JSON satisfying this schema: "
                            + json.dumps(context.workflow_step["output_schema"]),
                        }
                    ]
                    if context.workflow_step and context.workflow_step.get("output_schema") is not None
                    else []
                ),
                *context.messages,
                *(
                    [{"role": "user", "content": json.dumps({"provided_input": context.provided}, ensure_ascii=False)}]
                    if context.provided is not None
                    else []
                ),
            ],
            model=model_name,
            tools=effective_tools or None,
            stream=context.stream,
            session_id=engine_session,
            agent_id=agent.id,
            agent_version=version.version,
            skill_ref_manifest=snapshot.get("ref_manifest") or [],
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            correlation=correlation,
        )
        if action_hook is not None and action_hook.has_failed_outcomes:
            raise RuntimeError("action outcome persistence failed; reconciliation required")
        if context.stream:
            result_gen = outcome.result
            if isinstance(result_gen, dict):
                return {"status": "succeeded", "content": str(result_gen.get("content", "") or "")}
            mapper = RunEventMapper(task_ref, run_ref)
            events = PlatformEventService(db)
            content_parts: list[str] = []
            async for raw in result_gen:
                envelope = await mapper.map_stream_event(raw if isinstance(raw, dict) else {"type": "raw"})
                await events.append_resequenced(envelope, workspace_id=context.workspace_id)
                if envelope.payload.get("type") == "message" and envelope.payload.get("content"):
                    content_parts.append(str(envelope.payload["content"]))
            return {"status": "succeeded", "content": "".join(content_parts)}
        result = outcome.result
        if not isinstance(result, dict):
            raise ValueError(f"unexpected entry result type {type(result)}")
        return self._child_result(result, context)

    async def _execute_controller(
        self,
        db: AsyncSession,
        context: _DispatchContext,
        *,
        engine_session: uuid.UUID,
        task_ref: BackendRef,
        run_ref: BackendRef,
        lease: Any = None,
        resume_interrupted: bool = False,
        run: RunModel | None = None,
    ) -> dict[str, Any]:
        """Run the opt-in agent-controller loop over the shared assembly.

        Same assembly as the entry-service path (frozen agent version,
        resolved tools, guardrail bundle, runtime port, durable action
        hook); the execution mechanism is the graph-free controller whose
        continuation is the durable accepted-decision chain keyed by the
        task's stable run. A durable approval wait propagates its signal
        to the caller's park path; the final answer is delivery-checked
        against the task's declared output schema before the task may
        succeed — an accepted answer is never by itself a completion fact.
        """

        from hecate_durable.runtime_hook import SqlAcceptedDecisionStore
        from hecate_llm.service import llm_service
        from hecate_runtime.accepted_decision import DecisionOwnership, RunRef
        from hecate_runtime.agent_controller import (
            AgentController,
            ControllerExecutionDriver,
            ControllerLimits,
            ControllerRunSpec,
            PortTurnResponderSource,
        )
        from hecate_runtime.agent_turn import AgentTurn
        from hecate_runtime.delivery_acceptance import declared_acceptance
        from hecate_runtime.execution_service import RuntimeExecutionRequest, runtime_execution_service
        from hecate_runtime.tool_call_executor import ToolCallExecutor

        from hecate.core.composition.entry_assembly import (
            build_tool_registry,
            get_shared_event_store,
            load_agent_tools,
        )
        from hecate.core.composition.guardrail_platform import assemble_guardrails
        from hecate.core.composition.runtime_port_adapter import create_runtime_port
        from hecate.models.agent_version import AgentVersionModel

        agent = await db.get(AgentModel, context.agent_id)
        if agent is None:
            raise ValueError(f"agent {context.agent_id} no longer resolves for task {task_ref.id}")
        run = await TaskRunRegistry(db).get_run(uuid.UUID(run_ref.id), context.workspace_id)
        snapshot = run.execution_snapshot or {}
        version = await db.get(AgentVersionModel, uuid.UUID(snapshot["agent_version_id"]))
        if version is None or version.deleted:
            raise ValueError("the frozen agent version no longer resolves")
        config = snapshot.get("config_snapshot") or {}
        tool_refs = config.get("tools") or []
        event_store = None
        tool_registry = None
        effective_tools: list[dict[str, Any]] = []
        bundle = None
        if tool_refs:
            event_store = get_shared_event_store()
            tool_registry = build_tool_registry(db, workspace_id=context.workspace_id)
            effective_tools = await load_agent_tools(db, tool_refs, workspace_id=context.workspace_id)
            if effective_tools:
                bundle = await assemble_guardrails(
                    db,
                    workspace_id=context.workspace_id,
                    agent_id=agent.id,
                    guardrail_config=config.get("guardrail_config"),
                    event_store=event_store,
                    session_id=engine_session,
                    user_id=context.user_id,
                    dlp_scanner=None,
                )
        await self._wire_approval_backend(db, context, bundle, resume_interrupted=resume_interrupted)

        port = create_runtime_port(
            db,
            llm_service,
            tool_registry=tool_registry,
            workspace_id=context.workspace_id,
            agent_id=agent.id,
            identity={
                "actor": str(context.user_id) if context.user_id else f"agent:{agent.id}",
                "agent_principal": str(agent.id),
                "task_scope": str(context.task_id) if context.approval_wait else None,
            },
        )
        hook = None
        if hasattr(self._store, "claim_ex"):
            from hecate_durable.runtime_hook import SqlActionLedgerHook

            hook = SqlActionLedgerHook(
                self._store,
                task_ref=task_ref,
                run_ref=run_ref,
                holder=lease.holder if lease is not None else "platform-inline",
                lease=lease,
            )
        if context.platform_run_id is None:
            raise ValueError("controller tasks require the platform run stamped at submission")
        controller_run = RunRef(issuer_domain=PLATFORM_ISSUER, run_id=context.platform_run_id)
        ownership = (
            DecisionOwnership(lease_key=lease.lease_key, fence_token=lease.fencing_token, holder=lease.holder)
            if lease is not None
            else None
        )
        model_name = context.model or (config.get("model_config") or {}).get("model", "gpt-4o")
        tool_definitions = {
            definition["function"]["name"]: definition
            for definition in effective_tools
            if isinstance(definition, dict) and isinstance(definition.get("function"), dict)
        }
        base_refs = {
            "task": str(context.task_id),
            "agent_version": str(version.id),
            "agent_version_no": version.version,
            "model": model_name,
            "tools": sorted(tool_definitions),
            # Frozen goal/acceptance references: every committed decision
            # pins the goal and acceptance version it ran under (the spec's
            # declared standard adds the full declaration digest), so a
            # re-presented chain under a different standard conflicts.
            "goal": context.goal,
            "acceptance_version": (context.acceptance or {}).get("version"),
        }
        store = SqlAcceptedDecisionStore(self._store)
        executor = ToolCallExecutor(
            port=port,
            event_store=event_store,
            access_policy=bundle.access_policy if bundle else None,
            approval_callback=bundle.approval_callback if bundle else None,
            tool_rules=bundle.rules if bundle else None,
            middleware_chains=bundle.middleware_chains if bundle else None,
            denial_tracker=bundle.denial_tracker if bundle else None,
            action_hook=hook,
        )
        source = PortTurnResponderSource(
            port,
            config={**(config.get("model_config") or {}), "model": model_name},
            tool_definitions=tool_definitions,
            base_refs=base_refs,
        )
        limits = ControllerLimits(
            max_turns=context.controller_max_turns or 16,
            deadline=context.controller_deadline,
        )
        revisions, revisions_error = await self._controller_input_revisions(context, task_ref, controller_run)
        if revisions_error is not None:
            return {
                "status": "awaiting_reconciliation",
                "content": "",
                "error": revisions_error,
                "error_category": "input_history_unavailable",
            }
        messages = [
            {"role": "system", "content": config.get("persona") or "You are a helpful assistant."},
            *(
                [
                    {
                        "role": "system",
                        "content": "Return final output as JSON satisfying this schema: "
                        + json.dumps(context.output_schema),
                    }
                ]
                if context.output_schema is not None
                else []
            ),
            *context.messages,
        ]
        spec = ControllerRunSpec(
            run_ref=controller_run,
            base_messages=messages,
            ownership=ownership,
            limits=limits,
            execution_context={
                "session_id": str(engine_session),
                "agent_id": str(agent.id),
                "workspace_id": str(context.workspace_id),
                "on_behalf_of_user": str(context.user_id) if context.user_id else None,
            },
            # Consumed input wakes ride as durable revisions at their causal
            # turn boundaries — never folded into the frozen base, and every
            # decision pins the revision ids it consumed.
            input_revisions=revisions,
            # An explicitly declared acceptance standard pins its version
            # and digest on every decision; the delivery gate below then
            # evaluates under the same frozen declaration.
            acceptance=declared_acceptance(context.acceptance),
        )
        first_responder = source.responder_for(messages, {**base_refs, "controller": "agent-loop/1", "turn": 1})
        controller = AgentController(AgentTurn(store, first_responder, executor), source)

        def should_cancel() -> bool:
            current = self._store.get_task_state(task_ref)
            return current is None or current.lifecycle_state is not TaskLifecycleState.RUNNING

        driver = ControllerExecutionDriver(controller, spec, should_cancel=should_cancel)
        result = await runtime_execution_service.execute(
            RuntimeExecutionRequest(
                runtime=driver, session_id=engine_session, initial_input={}, should_cancel=should_cancel
            )
        )
        if result.state.value == "failed":
            if result.detail.get("requires_reconciliation"):
                return {
                    "status": "awaiting_reconciliation",
                    "content": "",
                    "error": result.error or "accepted decision history is unavailable",
                    "error_category": "decision_history_unavailable",
                }
            raise RuntimeError(result.error or "controller execution failed")
        if result.state.value in ("unknown", "cancelled"):
            return {"status": "unknown", "content": "", "error": f"controller execution {result.state.value}"}
        outcome = driver.outcome
        if outcome is None:  # pragma: no cover - a completed stream always carries one
            return {"status": "unknown", "content": "", "error": "controller stream ended without an outcome"}
        if outcome.is_final_answer:
            return self._evaluate_delivery(
                outcome.final_text or "",
                context,
                task_ref=task_ref,
                run_ref=controller_run,
                final_decision_step_id=outcome.final_decision_step_id,
                final_decision_digest=outcome.final_decision_digest,
            )
        if outcome.reason == "cancel_requested":
            return {"status": "cancelled", "content": "", "error": "controller loop cancelled"}
        return {
            "status": "failed",
            "content": "",
            "error": f"controller loop stopped: {outcome.reason}",
            "error_category": f"controller_{outcome.reason}",
        }

    async def _controller_input_revisions(
        self, context: _DispatchContext, task_ref: BackendRef, controller_run: Any
    ) -> tuple[tuple[Any, ...], str | None]:
        """The task's durable input history, or an explicit-stop reason.

        Recorded revisions are the authority: replay folds each one at its
        causal turn boundary and committed decisions restore by the revision
        ids they pinned. Business data in ``provided`` is pre-revision
        history without its own causal binding, even if later revision
        rows exist. Refuse to fabricate or silently drop that history.
        """

        from hecate_runtime.agent_controller import ControllerInputRevision

        try:
            rows = await asyncio.to_thread(
                self._store.list_input_revisions,
                task_ref,
                BackendRef(RefKind.RUN, controller_run.issuer_domain, controller_run.run_id),
            )
        except Exception:
            return (), "input revision history is unreadable; manual reconciliation required"
        provided = context.provided
        content = {k: v for k, v in provided.items() if k != "approval_ref"} if isinstance(provided, dict) else {}
        if content:
            return (), "legacy provided input has no causal revision binding; manual reconciliation required"
        return tuple(
            ControllerInputRevision(row["revision_id"], dict(row["content"]), int(row["effective_turn"]))
            for row in rows
        ), None

    @classmethod
    def _evaluate_delivery(
        cls,
        text: str,
        context: _DispatchContext,
        *,
        task_ref: BackendRef,
        run_ref: Any,
        final_decision_step_id: str | None = None,
        final_decision_digest: str | None = None,
    ) -> dict[str, Any]:
        """Delivery gate: execution, delivery and acceptance stay separate facts.

        An accepted final answer is the delivered content; it submits the
        task as succeeded only when every declared required check passes
        under the declared acceptance standard (the same shared evaluation
        the Runner applies — one declaration, one delivery, one verdict in
        either host). The record binds the full task/run namespace, the
        final decision's identity and digest, the standard's version and
        digest, the content digest, the checker version, each check's
        verdict and the platform identity accountable for the evaluation.
        A failed or unverified check keeps the delivered content visible
        and never grants success; a task without any acceptance
        declaration succeeds on its delivery but its record says
        ``not_declared`` — never a completed business-goal acceptance.
        """

        from hecate_runtime.delivery_acceptance import evaluate_delivery, evaluation_declaration

        declaration = evaluation_declaration(context.acceptance, context.output_schema)
        final_decision = (
            {"decision_step_id": final_decision_step_id, "content_digest": final_decision_digest}
            if final_decision_step_id is not None and final_decision_digest is not None
            else None
        )
        record = evaluate_delivery(
            text,
            declaration,
            final_decision=final_decision,
            evaluator={
                "host": "platform",
                "workspace_id": str(context.workspace_id),
                "submitted_by": str(context.user_id) if context.user_id else None,
            },
            task_ref={"issuer": task_ref.issuer_domain, "id": task_ref.id},
            run_ref={"issuer": run_ref.issuer_domain, "id": run_ref.run_id},
        )
        if record["status"] in ("passed", "not_declared"):
            return {"status": "succeeded", "content": text, "acceptance": record}
        problem = next(check["detail"] for check in record["checks"] if check["status"] != "passed")
        return {
            "status": "failed",
            "content": text,
            "error": problem,
            "error_category": record["status"],
            "acceptance": record,
        }

    @staticmethod
    def _child_result(result: dict[str, Any], context: _DispatchContext) -> dict[str, Any]:
        content = str(result.get("content", "") or "")
        artifacts = dict(result.get("artifacts") or {})
        key = (context.workflow_step or {}).get("artifact")
        if key is not None:
            try:
                document = json.loads(content)
            except json.JSONDecodeError:
                document = None
            if isinstance(document, dict) and key in document:
                artifacts[key] = document[key]
        return {"status": "succeeded", "content": content, "artifacts": artifacts}

    async def _park(
        self,
        db: AsyncSession,
        task_ref: BackendRef,
        wait: TaskWaitingSignalError,
        context: _DispatchContext,
        *,
        run_ref: BackendRef,
        expected_revision: int,
        lease: Any,
    ) -> None:
        """Persist the durable wait: state + single-use token + deadline."""

        token = uuid.uuid4().hex
        expires_at = (
            (datetime.now(UTC) + timedelta(seconds=wait.expires_in_seconds)).isoformat()
            if wait.expires_in_seconds is not None
            else None
        )
        target = (
            TaskLifecycleState.WAITING_INPUT
            if wait.wake_kind is ControlCommandKind.PROVIDE_INPUT
            else TaskLifecycleState.WAITING_APPROVAL
        )
        record = await asyncio.to_thread(self._store.get_task_state, task_ref)
        if record is None or record.lifecycle_state is not TaskLifecycleState.RUNNING:
            return  # cancelled or otherwise moved while the execution ran
        if context.approval_wait and target is TaskLifecycleState.WAITING_APPROVAL:
            await TaskRunRegistry(db).finalize_run(
                uuid.UUID(run_ref.id), context.workspace_id, state="failed", error_category="waiting_approval"
            )
            await db.commit()
        await asyncio.to_thread(
            self._store.apply_task_state,
            task_ref,
            target,
            expected_revision=expected_revision,
            **({"event_run_ref": run_ref} if hasattr(self._store, "engine") else {}),
            **({"lease": lease} if lease is not None else {}),
            extra_update={
                "wait": {
                    "wake_kind": wait.wake_kind.value,
                    "contract_ref": wait.contract_ref,
                    "wait_token": token,
                    "wait_expires_at": expires_at,
                    "consumed": False,
                }
            },
        )
        logger.info("task %s parked in %s (token issued)", task_ref.id, target.value)
        if context.workflow_plan is not None:
            await self._wake_finished_child(db, task_ref, context, wait, token)

    async def _park_approval(
        self,
        db: AsyncSession,
        context: _DispatchContext,
        task_ref: BackendRef,
        signal: Any,
    ) -> TaskWaitingSignalError | None:
        """Mint the PENDING binding, then build the approval wait signal.

        Returns ``None`` when the binding cannot be persisted — the caller
        fails the task (fail closed) instead of parking without an authority
        record. ``task_scope`` anchors to the durable task id (stable across
        attempts); the fingerprint derives from the same dispatch identity
        envelope the consumer will present, so ``check_binding`` matches
        byte-for-byte at resume.
        """

        from hecate.execution.approval_wait import DEFAULT_PARK_TTL_MINUTES, mint_pending_approval_binding

        ledger = self._approval_ledger(db)
        if ledger is None:
            return None
        actor = str(context.user_id) if context.user_id else f"agent:{context.agent_id}"
        try:
            ref, binding = mint_pending_approval_binding(
                ledger,
                task_id=context.task_id,
                actor=actor,
                agent_principal=str(context.agent_id),
                tool_name=signal.tool_name,
                arguments=signal.arguments,
            )
            await ledger.save(binding)
        except Exception:
            logger.warning("task %s approval binding mint failed; parking refused", task_ref.id, exc_info=True)
            return None
        ttl_seconds = DEFAULT_PARK_TTL_MINUTES * 60
        return TaskWaitingSignalError(
            ControlCommandKind.RESUME,
            {
                "approval_ref": ref,
                "tool_name": signal.tool_name,
                "arguments_digest": binding.arguments_digest,
                "principal_fingerprint": binding.principal_fingerprint,
                "target_resources": list(binding.target_resources),
                "task_scope": binding.task_scope,
                "invocation_id": signal.context.get("invocation_id"),
                "risk_level": signal.risk_level,
            },
            expires_in_seconds=ttl_seconds,
        )

    def _approval_ledger(self, db: AsyncSession) -> Any | None:
        """The approval authority for this dispatch, or ``None`` when the
        session cannot host one (stub sessions fail closed at the caller)."""

        from hecate.enterprise.approval_wait_ledger import task_approval_ledger

        return task_approval_ledger(db)

    async def _wire_approval_backend(
        self,
        db: AsyncSession,
        context: _DispatchContext,
        bundle: Any | None,
        *,
        resume_interrupted: bool,
    ) -> None:
        """Swap the approval callback's answerer for the durable-wait pair.

        First dispatch (opt-in via the task payload): the inner backend
        raises the wait signal so the dispatcher parks. Authorized wake: the
        inner backend consumes the approved binding at the protected action's
        dispatch; a different protected call re-signals (a new park).
        Crash-recovery resumes of an opt-in task without an approved ref also
        re-signal — the parked action is still undecided.
        """

        if bundle is None or not context.approval_wait:
            return
        callback = bundle.approval_callback
        if callback is None:
            return
        from hecate_runtime.security.approval import FailingClosedApprovalCallback

        # The authority already exists. The chat wrapper would mint a second,
        # differently scoped grant and lose revocation/consumption linkage.
        callback = FailingClosedApprovalCallback(
            event_store=callback.event_store,
            session_id=callback.session_id,
            workspace_id=callback.workspace_id,
            agent_id=callback.agent_id,
        )
        bundle.approval_callback = callback
        approval_ref: str | None = None
        if resume_interrupted and isinstance(context.provided, dict):
            raw = context.provided.get("approval_ref")
            approval_ref = str(raw) if raw else None
        if approval_ref:
            from hecate.execution.approval_wait import DurableBindingConsumer

            ledger = self._approval_ledger(db)
            if ledger is None:
                from hecate_runtime.errors import ExecutionStoppedError

                raise ExecutionStoppedError("approval_unavailable", "approval authority is unavailable")
            binding = await ledger.get(approval_ref)
            state = await asyncio.to_thread(
                self._store.get_task_state, BackendRef(RefKind.TASK, PLATFORM_ISSUER, str(context.task_id))
            )
            wait = (state.extra or {}).get("wait") or {} if state else {}
            contract = wait.get("contract_ref") or {}
            if state is not None and (wait.get("consumed") is not True or contract.get("approval_ref") != approval_ref):
                from hecate_runtime.errors import ExecutionStoppedError

                raise ExecutionStoppedError("approval_mismatch", "approval does not match the consumed wait")
            callback._inner_backend = DurableBindingConsumer(
                ledger,
                approval_ref=approval_ref,
                task_id=context.task_id,
                actor=str(context.user_id) if context.user_id else f"agent:{context.agent_id}",
                agent_principal=str(context.agent_id),
                binding_tool_name=binding.tool_name if binding else None,
                binding_arguments_digest=binding.arguments_digest if binding else None,
                binding_invocation_id=contract.get("invocation_id"),
            )
            return
        from hecate.execution.approval_wait import DurableWaitApprovalBackend

        callback._inner_backend = DurableWaitApprovalBackend()

    async def _wake_finished_child(
        self,
        db: AsyncSession,
        parent_ref: BackendRef,
        context: _DispatchContext,
        wait: TaskWaitingSignalError,
        token: str,
    ) -> None:
        # A fast child can finish before its parent commits the wait. Recheck
        # after that commit so the child's skipped early callback is not lost.
        raw = wait.contract_ref.get("await_task_ref")
        if not isinstance(raw, dict):
            return
        child_ref = BackendRef(RefKind.TASK, str(raw.get("issuer_domain")), str(raw.get("id")))
        child = await asyncio.to_thread(self._store.get_task_state, child_ref)
        if child is None or child.lifecycle_state.value not in _RUN_PROJECTION_TERMINAL:
            return
        try:
            await self._task_control(db).submit_workflow_callback(
                workspace_id=context.workspace_id,
                task_id=uuid.UUID(parent_ref.id),
                command_id=f"wf-callback-{child_ref.id}-{token}",
                wait_token=token,
                child_task_id=uuid.UUID(child_ref.id),
                declared_state=child.lifecycle_state.value,
                payload={"step_index": wait.contract_ref.get("step_index")},
            )
        except Exception:
            logger.warning("task %s completed child callback failed", parent_ref.id, exc_info=True)

    async def _finish(
        self,
        db: AsyncSession,
        task_ref: BackendRef,
        r_ref: BackendRef,
        context: _DispatchContext,
        outcome: dict[str, Any],
        *,
        run: RunModel,
        expected_revision: int,
        lease: Any,
    ) -> None:
        terminal = {
            "succeeded": TaskLifecycleState.SUCCEEDED,
            "failed": TaskLifecycleState.FAILED,
            "cancelled": TaskLifecycleState.CANCELLED,
        }.get(outcome.get("status"), TaskLifecycleState.RECONCILIATION_REQUIRED)
        # Stream-mode executions leave uncommitted event rows on this
        # session; close them before the seam store's own transaction.
        await db.commit()
        await asyncio.to_thread(
            self._store.apply_task_state,
            task_ref,
            terminal,
            expected_revision=expected_revision,
            **({"lease": lease} if lease is not None else {}),
            extra_update={"result": outcome},
            **(
                {
                    "event_run_ref": r_ref,
                    **(
                        {"terminal_payload": outcome}
                        if terminal is not TaskLifecycleState.RECONCILIATION_REQUIRED
                        else {}
                    ),
                }
                if hasattr(self._store, "engine")
                else {}
            ),
        )
        try:
            projection = {
                "state": terminal.value,
                "error": outcome.get("error"),
                "error_category": outcome.get("error_category"),
                **delivery_projection(outcome, run.projection or {}),
            }
            await TaskRunRegistry(db).update_projection(
                run.id,
                context.workspace_id,
                projection=projection,
            )
        except TaskRunRegistryError:
            logger.warning("task %s run projection update skipped", task_ref.id)
        if not hasattr(self._store, "engine") and terminal is not TaskLifecycleState.RECONCILIATION_REQUIRED:
            await PlatformEventService(db).emit(
                task_ref=task_ref,
                run_ref=r_ref,
                payload_schema_ref="hecate.platform.run_terminal/0",
                payload={"status": outcome["status"], "error": outcome.get("error")},
                actor=ActorRef(kind=ActorKind.PLATFORM, id="task-control"),
                workspace_id=context.workspace_id,
            )
        await db.commit()
        if context.workflow_parent is not None:
            # step6e: the child's terminal fact is committed — auto-callback
            # the waiting parent through the verified path.
            await self._issue_child_callback(db, context, outcome)
