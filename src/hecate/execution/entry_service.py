"""Platform entry-point execution service (step5d).

Single consumption point for migrated entry chains (HTTP chat, MCP,
IM injection, evaluation workflow runs). Entry modules keep protocol
adaptation only; execution assembly (``WorkflowExecutionService`` over
the shared runtime assembly) and platform correlation
(``TaskRunRegistry``) happen here, so every migrated entry gets the
same guardrail wiring, event store, and checkpoint store.

Correlation is fail-open by contract: the registry records the Task/Run
responsibility rows, but a registry failure must never take chat down.
When correlation cannot complete, the outcome carries an explicit
``missing`` marker (and the reason) instead of silently skipping — the
platform must not report a correlated execution that it did not record.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind, deployment_ref, run_ref

logger = logging.getLogger(__name__)

CORRELATION_REGISTERED = "registered"
CORRELATION_MISSING = "missing"


@dataclass(frozen=True)
class CorrelationInput:
    """Per-execution correlation facts gathered by the entry adapter.

    ``existing_task_id`` switches to the shared-task mode (evaluation):
    runs/associations attach to a task the caller created for the whole
    batch, instead of minting one task per execution.
    """

    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    goal: str | None = None
    existing_task_id: uuid.UUID | None = None


@dataclass(frozen=True)
class CorrelationResult:
    """What the platform recorded for one entry execution."""

    status: str
    reason: str | None = None
    task_ref: BackendRef | None = None
    run_ref: BackendRef | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"status": self.status}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.task_ref is not None:
            out["task_ref"] = self.task_ref.to_dict()
        if self.run_ref is not None:
            out["run_ref"] = self.run_ref.to_dict()
        return out


_MISSING = CorrelationResult(status=CORRELATION_MISSING, reason="no correlation input")


@dataclass
class ExecutionOutcome:
    """The engine result plus what the platform recorded about it.

    ``result`` is exactly what ``WorkflowExecutionService.execute``
    returns (dict for non-streaming, async generator for streaming), so
    protocol adapters keep their existing rendering paths.
    """

    result: Any
    correlation: CorrelationResult = field(default_factory=lambda: _MISSING)

    @property
    def correlated(self) -> bool:
        return self.correlation.status == CORRELATION_REGISTERED


class EntryExecutionService:
    """One entry chain's execution service over the shared assembly.

    Constructs the platform adapter (``WorkflowExecutionService``) with
    the entry's resolved wiring so entry modules stop assembling
    execution themselves. Streaming and non-streaming are two views of
    the same delegated execution; no separate lifecycle is introduced.
    """

    def __init__(
        self,
        *,
        port: Any,
        entry_name: str,
        db: AsyncSession | None = None,
        event_store: Any | None = None,
        checkpoint_store: Any | None = None,
        access_policy: Any | None = None,
        approval_callback: Any | None = None,
        tool_policy_rules: list | None = None,
        middleware_chains: dict | None = None,
        denial_tracker: Any | None = None,
        suggestion_service: Any | None = None,
        action_hook: Any | None = None,
    ) -> None:
        # Lazy sibling-domain import: the execution domain must not import
        # studio at module level (tests/test_layering_domain.py).
        from hecate.core.composition.entry_assembly import get_shared_event_store, get_shared_session_state_store
        from hecate.studio.workflows.execution_service import WorkflowExecutionService

        self._db = db
        self._entry_name = entry_name
        self._service = WorkflowExecutionService(
            port=port,
            db=db,
            event_store=event_store if event_store is not None else get_shared_event_store(),
            checkpoint_store=checkpoint_store if checkpoint_store is not None else get_shared_session_state_store(),
            access_policy=access_policy,
            approval_callback=approval_callback,
            tool_policy_rules=tool_policy_rules,
            middleware_chains=middleware_chains,
            denial_tracker=denial_tracker,
            suggestion_service=suggestion_service,
            action_hook=action_hook,
        )

    async def execute(self, *, correlation: CorrelationInput | None = None, **execute_kwargs: Any) -> ExecutionOutcome:
        """Delegate to the platform adapter and correlate the execution.

        Keyword arguments are forwarded to ``WorkflowExecutionService.execute``
        unchanged. ``session_id`` is minted when absent so the correlation's
        backend run reference points at the engine session that actually
        executed.
        """
        correlation = correlation or CorrelationInput()
        session_id = execute_kwargs.get("session_id")
        if session_id is None:
            # Reuse the caller's session when given; mint only when neither
            # side carries one, so the correlation's backend run reference
            # points at the engine session that actually executed.
            session_id = correlation.session_id or uuid.uuid4()
            execute_kwargs["session_id"] = session_id
        elif isinstance(session_id, str):
            session_id = uuid.UUID(session_id)
        if correlation.session_id is not None and correlation.session_id != session_id:
            raise ValueError("execution and correlation session identifiers must match")
        execute_kwargs["session_id"] = session_id
        correlation = replace(correlation, session_id=session_id)

        result = await self._service.execute(**execute_kwargs)
        correlation_result = await self._correlate(correlation, session_id=session_id)
        return ExecutionOutcome(result=result, correlation=correlation_result)

    async def _correlate(self, correlation: CorrelationInput, *, session_id: uuid.UUID) -> CorrelationResult:
        """Best-effort Task/Run/conversation registration (fail-open)."""
        if self._db is None:
            return CorrelationResult(status=CORRELATION_MISSING, reason="entry service has no db session")
        if correlation.workspace_id is None:
            return CorrelationResult(status=CORRELATION_MISSING, reason="no workspace context")
        from hecate.execution.task_run_registry import TaskRunRegistry, TaskRunRegistryError

        registry = TaskRunRegistry(self._db)
        try:
            # Registry flush failures must not poison the caller's transaction
            # or leave a partial Task reported as a completed registration.
            async with self._db.begin_nested():
                if correlation.existing_task_id is not None:
                    return await self._correlate_shared_task(registry, correlation)
                return await self._correlate_per_request(registry, correlation, session_id=session_id)
        except TaskRunRegistryError as exc:
            logger.warning(
                "Entry %s correlation failed: %s",
                self._entry_name,
                exc,
            )
            return CorrelationResult(status=CORRELATION_MISSING, reason="correlation registry rejected registration")
        except Exception as exc:  # noqa: BLE001 — correlation must never break execution
            logger.warning("Entry %s correlation failed unexpectedly: %s", self._entry_name, exc)
            return CorrelationResult(status=CORRELATION_MISSING, reason="correlation registration failed")

    async def _correlate_shared_task(self, registry: Any, correlation: CorrelationInput) -> CorrelationResult:
        from hecate.execution.task_run_registry import TaskNotFoundError

        try:
            task = await registry.get_task(correlation.existing_task_id, correlation.workspace_id)
        except TaskNotFoundError as exc:
            raise TaskNotFoundError(f"shared task does not resolve in workspace: {exc}") from exc
        return CorrelationResult(
            status=CORRELATION_REGISTERED,
            task_ref=BackendRef(RefKind.TASK, task.issuer_domain, str(task.id)),
        )

    async def _correlate_per_request(
        self, registry: Any, correlation: CorrelationInput, *, session_id: uuid.UUID
    ) -> CorrelationResult:
        goal = correlation.goal or f"{self._entry_name} execution"
        deployment = await self._default_deployment(correlation)
        chain = await self._identity_chain(correlation, deployment)
        task = await registry.create_task(
            goal=goal,
            initiator_ref=chain.to_dict(),
            workspace_id=correlation.workspace_id,
        )
        run_ref_result: BackendRef | None = None
        reason: str | None = None
        if deployment is None:
            reason = "agent has no default deployment; run not opened"
        else:
            run = await registry.create_run(
                task_id=task.id,
                workspace_id=correlation.workspace_id,
                deployment_id=deployment.id,
                identity_chain=chain,
                backend_run_ref=run_ref(deployment.issuer_domain, str(session_id)),
            )
            run_ref_result = BackendRef(RefKind.RUN, run.issuer_domain, str(run.id))
        await self._link_conversation(registry, correlation, task.id)
        return CorrelationResult(
            status=CORRELATION_REGISTERED,
            reason=reason,
            task_ref=BackendRef(RefKind.TASK, task.issuer_domain, str(task.id)),
            run_ref=run_ref_result,
        )

    async def _identity_chain(self, correlation: CorrelationInput, deployment: Any | None) -> IdentityChain:
        """Build the four-slot chain for one entry execution.

        The workload slot must carry the resolved deployment reference so
        ``create_run``'s identity validation matches; without a deployment
        the chain still round-trips (task creation validates the shape
        only) and no run is opened.
        """
        from hecate.execution.task_run_registry import TaskRunValidationError
        from hecate.models.agent_principal import AgentPrincipalModel

        principal_id = "platform-system"
        if correlation.agent_id is not None:
            row = await self._db.execute(
                select(AgentPrincipalModel).where(
                    AgentPrincipalModel.agent_id == correlation.agent_id,
                    AgentPrincipalModel.workspace_id == correlation.workspace_id,
                    ~AgentPrincipalModel.deleted,
                )
            )
            principal = row.scalar_one_or_none()
            if principal is None and deployment is not None:
                raise TaskRunValidationError("agent has no registered principal")
            principal_id = str(principal.id) if principal is not None else str(correlation.agent_id)
        if deployment is not None:
            workload = WorkloadIdentity(
                deployment=deployment_ref(deployment.issuer_domain, str(deployment.id)),
                workload_id=f"hecate:{self._entry_name}",
            )
        else:
            workload = WorkloadIdentity(
                deployment=deployment_ref("hecate", str(correlation.agent_id or correlation.workspace_id)),
                workload_id=f"hecate:{self._entry_name}",
            )
        return IdentityChain(
            initiator=str(correlation.user_id) if correlation.user_id is not None else None,
            principal_id=principal_id,
            workload=workload,
            audience=f"hecate:{self._entry_name}",
        )

    async def _default_deployment(self, correlation: CorrelationInput) -> Any | None:
        from hecate.models.agent_deployment import AgentDeploymentModel

        row = await self._db.execute(
            select(AgentDeploymentModel).where(
                AgentDeploymentModel.agent_id == correlation.agent_id,
                AgentDeploymentModel.workspace_id == correlation.workspace_id,
                AgentDeploymentModel.is_default.is_(True),
                ~AgentDeploymentModel.deleted,
            )
        )
        return row.scalar_one_or_none()

    async def _link_conversation(self, registry: Any, correlation: CorrelationInput, task_id: uuid.UUID) -> None:
        """Attach the engine session's conversation to the task (best-effort)."""
        if correlation.session_id is None:
            return
        try:
            async with self._db.begin_nested():
                await registry.link_session(
                    session_id=correlation.session_id,
                    task_id=task_id,
                    workspace_id=correlation.workspace_id,
                )
        except Exception as exc:  # noqa: BLE001 — linking is additive provenance
            logger.info("Entry %s conversation link skipped: %s", self._entry_name, exc)
