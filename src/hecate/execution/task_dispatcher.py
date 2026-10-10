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

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.contracts.execution.durable import ControlCommandKind, TaskLifecycleState, TaskStateRecord
from hecate.contracts.execution.events import ActorKind, ActorRef
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind, deployment_ref, run_ref
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
        if latest is not None and self._run_is_terminal(latest):
            # Crash window: the run finished but the task-terminal write
            # never landed. Backfill from the run projection; never
            # re-execute a finished attempt.
            projection = latest.projection or {}
            outcome = {
                "status": projection.get("state", "failed"),
                "content": str(projection.get("result_preview") or ""),
                "error": projection.get("error"),
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
            await self._park(task_ref, wait, context, run_ref=r_ref, expected_revision=record.revision + 1, lease=lease)
            return
        except Exception as exc:
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
            child_state = (provided.get("child_outcome") or {}).get("child_state")
            if child_state != "succeeded":
                detail = f"workflow step {done_index} child ended {child_state}"
                if plan.on_failure == "await":
                    return {"status": "awaiting_reconciliation", "content": detail}
                return {"status": "failed", "content": "", "error": detail}
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
            {"await_task_ref": child_ref.to_dict()},
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

    async def _issue_child_callback(self, db: AsyncSession, context: _DispatchContext, outcome: dict[str, Any]) -> None:
        """Auto-callback: a workflow child's terminal fact wakes its parent.

        The child's input payload carries the parent reference (stamped at
        submit); the callback is issued through the REAL verified path
        (``submit_workflow_callback``) with the parent's live wait token —
        the platform trusts its own records, so no external caller is
        involved. Failures are logged, never surfaced: the child's terminal
        fact is already committed and must not be un-done by a callback
        problem.
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
            if not token:
                logger.warning("task %s workflow parent has no live wait; callback skipped", context.task_id)
                return
            service = self._task_control(db)
            await service.submit_workflow_callback(
                workspace_id=context.workspace_id,
                task_id=uuid.UUID(parent_ref.id),
                command_id=f"wf-callback-{context.task_id}",
                wait_token=token,
                child_task_id=context.task_id,
                declared_state=str(declared),
                payload={"step_index": parent.get("step_index")},
                result_summary={"content": str(outcome.get("content") or "")},
            )
        except Exception:  # noqa: BLE001 — terminal fact is committed; callback problems must not undo it
            logger.warning("task %s workflow auto-callback failed", context.task_id, exc_info=True)

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
        self, db: AsyncSession, latest: RunModel, context: _DispatchContext
    ) -> uuid.UUID | None:
        """step6d resumption gate for a protected-interrupted attempt.

        Returns the engine session to resume natively, or ``None`` (with the
        refusal reason recorded for the reconciliation trail) when any
        precondition fails: no recorded definition digest, resolved
        definition drift, a terminal projection on the run, or a missing
        engine-session snapshot. Admission (principal/deployment/version)
        has already been re-validated by the caller before this gate.
        """

        self._last_resume_refusal = None
        snapshot = latest.execution_snapshot or {}
        recorded = snapshot.get("definition_digest")
        if not recorded:
            self._last_resume_refusal = "resume refused: no recorded definition digest"
            return None
        if (latest.projection or {}).get("state") in _RUN_PROJECTION_TERMINAL:
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
        from hecate.core.composition.entry_assembly import get_shared_session_state_store

        try:
            state_store = get_shared_session_state_store()
            snapshot_state = await state_store.load(
                org_id=context.user_id,
                user_id=context.user_id,
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
                    session_id=None,
                    dlp_scanner=None,
                )

        port = create_runtime_port(db, llm_service, tool_registry=tool_registry)
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
                envelope = mapper.map_stream_event(raw if isinstance(raw, dict) else {"type": "raw"})
                await events.append_resequenced(envelope, workspace_id=context.workspace_id)
                if envelope.payload.get("type") == "message" and envelope.payload.get("content"):
                    content_parts.append(str(envelope.payload["content"]))
            return {"status": "succeeded", "content": "".join(content_parts)}
        result = outcome.result
        if not isinstance(result, dict):
            raise ValueError(f"unexpected entry result type {type(result)}")
        return {"status": "succeeded", "content": str(result.get("content", "") or "")}

    async def _park(
        self,
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
            **(
                {
                    "event_run_ref": r_ref,
                    "extra_update": {"result": outcome},
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
            await TaskRunRegistry(db).update_projection(
                run.id,
                context.workspace_id,
                projection={
                    "state": terminal.value,
                    "error": outcome.get("error"),
                    "result_preview": (outcome.get("content") or "")[:2000],
                },
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
