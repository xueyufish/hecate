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
from hecate.models.agent import AgentModel
from hecate.models.run import RunModel

logger = logging.getLogger(__name__)

PLATFORM_ISSUER = "hecate"

_RUN_TERMINAL_EVENT = "run_terminal"
_RUN_PROJECTION_TERMINAL = {"succeeded", "failed", "cancelled"}


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
            await self._run(task_ref, db, record, context)
            return
        async with self._db() as session:
            await self._run(task_ref, session, record, context)

    async def _run(
        self, task_ref: BackendRef, db: AsyncSession, record: TaskStateRecord, context: _DispatchContext
    ) -> None:
        registry = TaskRunRegistry(db)
        task = await registry.get_task(context.task_id, context.workspace_id)
        runs = await registry.list_runs_for_task(context.task_id, context.workspace_id)
        latest = runs[-1] if runs else None

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
            await self._finish(db, task_ref, run_row_ref(latest), context, outcome, run=latest)
            return

        if record.revision == 0 and latest is not None and not (latest.projection or {}):
            run = latest  # the attempt minted at submit; never executed
        else:
            run = await self._new_attempt(db, task, latest, context)
        r_ref = run_row_ref(run)
        engine_session = self._engine_session_of(run)
        try:
            outcome = await self._execute(
                db,
                context,
                engine_session=engine_session,
                task_ref=task_ref,
                run_ref=r_ref,
            )
        except TaskWaitingSignalError as wait:
            await db.commit()
            await self._park(task_ref, wait, context)
            return
        await self._finish(db, task_ref, r_ref, context, outcome, run=run)

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
        )

    @staticmethod
    def _run_is_terminal(run: RunModel) -> bool:
        return (run.projection or {}).get("state") in _RUN_PROJECTION_TERMINAL

    @staticmethod
    def _engine_session_of(run: RunModel) -> uuid.UUID:
        backend = run.backend_ref if isinstance(run.backend_ref, dict) else {}
        return uuid.UUID(str(backend.get("id")))

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

        agent = await db.get(AgentModel, context.agent_id)
        if agent is None:
            raise ValueError(f"agent {context.agent_id} no longer resolves for task {task_ref.id}")
        event_store = None
        tool_registry = None
        effective_tools: list[dict[str, Any]] = []
        bundle = None
        if agent.tools:
            event_store = get_shared_event_store()
            tool_registry = build_tool_registry(db, workspace_id=context.workspace_id)
            effective_tools = await load_agent_tools(db, agent.tools or [], workspace_id=context.workspace_id)
            if effective_tools:
                bundle = await assemble_guardrails(
                    db,
                    workspace_id=context.workspace_id,
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
            workspace_id=context.workspace_id,
            agent_id=agent.id,
            user_id=context.user_id,
            session_id=engine_session,
            goal=context.goal[:200],
            existing_task_id=uuid.UUID(task_ref.id),
        )
        model_name = context.model or (
            agent.model_config_db.get("model", "gpt-4o") if isinstance(agent.model_config_db, dict) else "gpt-4o"
        )
        outcome = await entry.execute(
            agent_mode="chat",
            messages=context.messages,
            model=model_name,
            tools=effective_tools or None,
            stream=context.stream,
            session_id=engine_session,
            agent_id=agent.id,
            workspace_id=context.workspace_id,
            correlation=correlation,
        )
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

    async def _park(self, task_ref: BackendRef, wait: TaskWaitingSignalError, context: _DispatchContext) -> None:
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
    ) -> None:
        terminal = TaskLifecycleState.SUCCEEDED if outcome.get("status") == "succeeded" else TaskLifecycleState.FAILED
        # Stream-mode executions leave uncommitted event rows on this
        # session; close them before the seam store's own transaction.
        await db.commit()
        await asyncio.to_thread(self._store.apply_task_state, task_ref, terminal)
        try:
            await TaskRunRegistry(db).update_projection(
                run.id,
                context.workspace_id,
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
            run_ref=r_ref,
            payload_schema_ref="hecate.platform.run_terminal/0",
            payload={"status": outcome["status"], "error": outcome.get("error")},
            actor=ActorRef(kind=ActorKind.PLATFORM, id="task-control"),
            workspace_id=context.workspace_id,
        )
        await db.commit()
        # The durable run-terminal event rides the outbox so the relay
        # projects it into the read model with the other governance events.
        await asyncio.to_thread(
            self._store.emit_event,
            task_ref,
            r_ref,
            event_type=_RUN_TERMINAL_EVENT,
            payload={"status": outcome["status"], "error": outcome.get("error")},
        )
