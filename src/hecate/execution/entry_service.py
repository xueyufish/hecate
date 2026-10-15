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

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any

from anyio import CancelScope
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
        registry_session_factory: Any | None = None,
    ) -> None:
        # Lazy sibling-domain import: the execution domain must not import
        # studio at module level (tests/test_layering_domain.py).
        from hecate.core.composition.entry_assembly import (
            get_shared_event_store,
            get_shared_registry_session_factory,
            get_shared_session_state_store,
        )
        from hecate.studio.workflows.execution_service import WorkflowExecutionService

        self._db = db
        self._entry_name = entry_name
        self._event_store = event_store if event_store is not None else get_shared_event_store()
        if registry_session_factory is None:
            registry_session_factory = get_shared_registry_session_factory()
        # With a factory, registry writes (Task/Run registration, terminal
        # projections) commit on their own connection and survive a caller
        # rollback; without one the legacy savepoint path keeps the caller's
        # transaction semantics and durability is not guaranteed.
        self._registry_session_factory = registry_session_factory
        self._service = WorkflowExecutionService(
            port=port,
            db=db,
            event_store=self._event_store,
            checkpoint_store=checkpoint_store if checkpoint_store is not None else get_shared_session_state_store(),
            access_policy=access_policy,
            approval_callback=approval_callback,
            tool_policy_rules=tool_policy_rules,
            middleware_chains=middleware_chains,
            denial_tracker=denial_tracker,
            suggestion_service=suggestion_service,
            action_hook=action_hook,
        )

    async def execute(
        self,
        *,
        correlation: CorrelationInput | None = None,
        process_result: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        **execute_kwargs: Any,
    ) -> ExecutionOutcome:
        """Delegate to the platform adapter and correlate the execution.

        Keyword arguments are forwarded to ``WorkflowExecutionService.execute``
        unchanged. ``session_id`` is minted when absent so the correlation's
        backend run reference points at the engine session that actually
        executed. Correlation runs BEFORE execution (step5d contract
        closure): a failed execution leaves a registered Task/Run whose
        projection this service then finalizes, instead of vanishing.
        ``process_result`` validates or repairs the final synchronous
        response before the Run can be projected as successful.
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

        correlation_result = await self._correlate(correlation, session_id=session_id)
        workspace_id = correlation.workspace_id
        # One mapper per execution keeps the run's envelope sequence monotonic
        # across stream events and the terminal envelope.
        mapper = self._mapper(correlation_result)
        try:
            result = await self._service.execute(**execute_kwargs)
            if not hasattr(result, "__aiter__"):
                await self._record_engine_events(correlation_result, result, mapper=mapper)
                if process_result is not None:
                    if not isinstance(result, dict):
                        raise TypeError("result processing requires a non-streaming dict")
                    result = await process_result(result)
        except (Exception, asyncio.CancelledError, GeneratorExit) as exc:
            with CancelScope(shield=True):
                await self._finalize_failure(correlation_result, workspace_id, exc, mapper=mapper)
            raise
        if hasattr(result, "__aiter__"):
            return ExecutionOutcome(
                result=self._finalizing_stream(result, correlation_result, workspace_id),
                correlation=correlation_result,
            )
        accepted, rejected = await self._extract_artifacts(result, correlation_result, workspace_id)
        if mapper is not None:
            with contextlib.suppress(Exception):
                await mapper.write_terminal_envelope(
                    state="succeeded",
                    artifacts=accepted,
                    extra={"rejected_artifacts": rejected} if rejected else None,
                )
        await self._finalize_success(
            correlation_result, workspace_id, result, artifacts=accepted or None, mapper=mapper
        )
        return ExecutionOutcome(result=result, correlation=correlation_result)

    async def _extract_artifacts(
        self, result: Any, correlation_result: CorrelationResult, workspace_id: uuid.UUID | None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read the engine-declared artifact references off a final result.

        The key is popped so artifact declarations never reach protocol
        output. Issuer identity alone does not establish tenant ownership:
        platform references resolve to existing, live evidence records in
        the verified workspace. Unknown storage namespaces are refused.
        """
        if not isinstance(result, dict):
            return [], []
        declared = result.pop("declared_artifacts", None)
        if declared is None or correlation_result.task_ref is None:
            return [], []
        from hecate.execution.entry_events import extract_declared_artifacts
        from hecate.models.evidence import EvidenceModel

        ids: set[uuid.UUID] = set()
        if isinstance(declared, list):
            for item in declared:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    with contextlib.suppress(ValueError):
                        ids.add(uuid.UUID(item["id"]))
        verified: set[str] = set()
        if ids and workspace_id is not None:
            statement = select(EvidenceModel.id).where(
                EvidenceModel.id.in_(ids), EvidenceModel.workspace_id == workspace_id, ~EvidenceModel.deleted
            )
            try:
                if self._registry_session_factory is not None:
                    async with self._registry_session_factory() as session:
                        verified = {str(value) for value in (await session.scalars(statement)).all()}
                elif self._db is not None:
                    with self._db.no_autoflush:
                        verified = {str(value) for value in (await self._db.scalars(statement)).all()}
            except Exception:
                logger.warning("Entry %s artifact ownership could not be verified", self._entry_name, exc_info=True)
        return extract_declared_artifacts(
            declared, issuer_domain=correlation_result.task_ref.issuer_domain, verified_artifact_ids=verified
        )

    def _mapper(self, correlation_result: CorrelationResult) -> Any | None:
        if correlation_result.task_ref is None or correlation_result.run_ref is None:
            return None
        from hecate.execution.entry_events import RunEventMapper

        return RunEventMapper(
            correlation_result.task_ref,
            correlation_result.run_ref,
            event_store=self._event_store,
        )

    @staticmethod
    def _stop_category(exc: BaseException) -> str | None:
        """Map an execution exception to the entry stop category, if typed."""
        from hecate_runtime.errors import ExecutionStoppedError, MaxSuperstepsError

        if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
            return "cancelled"
        if isinstance(exc, ExecutionStoppedError):
            return getattr(exc, "reason_category", None) or "execution_failed"
        if isinstance(exc, MaxSuperstepsError):
            return "iteration_limit"
        try:
            from hecate.core.composition.governance import BudgetRefusedError
        except ImportError:  # pragma: no cover - composition always present in prod
            return None
        if isinstance(exc, BudgetRefusedError):
            return "budget_exhausted"
        return None

    @contextlib.asynccontextmanager
    async def _finalize_txn(self) -> AsyncIterator[Any]:
        """One transaction for the terminal projection write.

        With a registry session factory the projection commits on its own
        connection (surviving a caller rollback); the legacy path keeps a
        savepoint inside the caller's transaction. Neither path ever
        commits the caller's business transaction.
        """
        if self._registry_session_factory is not None:
            from hecate.execution.task_run_registry import TaskRunRegistry

            async with self._registry_session_factory() as session, session.begin():
                yield TaskRunRegistry(session)
        else:
            from hecate.execution.task_run_registry import TaskRunRegistry

            async with self._db.begin_nested():
                yield TaskRunRegistry(self._db)

    async def _finalize_failure(
        self,
        correlation_result: CorrelationResult,
        workspace_id: uuid.UUID | None,
        exc: BaseException,
        *,
        mapper: Any | None = None,
    ) -> None:
        """Write the failed terminal view: evidence first, then projection.

        Engine events drained before the failure ride the exception as
        ``engine_events`` and are mapped here so a failed synchronous run
        keeps the same envelope view a streaming failure produces. The
        registered correlation also rides the exception as
        ``platform_correlation`` so protocol adapters can render the
        Task/Run references in terminal payloads.
        """
        with contextlib.suppress(Exception):
            # The annotation must never mask the original error.
            exc.platform_correlation = correlation_result.to_dict()
        if correlation_result.run_ref is None or workspace_id is None:
            return
        category = self._stop_category(exc)
        state = "cancelled" if category == "cancelled" else "failed"
        if mapper is not None:
            partial = getattr(exc, "engine_events", None)
            if isinstance(partial, list):
                for event in partial:
                    if isinstance(event, dict):
                        with contextlib.suppress(Exception):
                            await mapper.map_stream_event(event)
            with contextlib.suppress(Exception):
                await mapper.write_terminal_envelope(
                    state=state,
                    stop_category=category or "execution_failed",
                    error_type=type(exc).__name__,
                )
        try:
            async with self._finalize_txn() as registry:
                await registry.finalize_run(
                    uuid.UUID(correlation_result.run_ref.id),
                    workspace_id,
                    state=state,
                    error_category=category or "execution_failed",
                )
        except Exception as exc_inner:  # noqa: BLE001 — finalization must not mask the error
            logger.warning("Entry %s failure finalization skipped: %s", self._entry_name, exc_inner)

    async def _finalize_success(
        self,
        correlation_result: CorrelationResult,
        workspace_id: uuid.UUID | None,
        result: Any,
        *,
        artifacts: list[dict[str, Any]] | None = None,
        mapper: Any | None = None,
    ) -> None:
        if correlation_result.run_ref is None or workspace_id is None:
            return
        try:
            async with self._finalize_txn() as registry:
                await registry.finalize_run(
                    uuid.UUID(correlation_result.run_ref.id),
                    workspace_id,
                    state="succeeded",
                    summary=self._result_summary(result),
                    artifacts=artifacts,
                )
        except Exception as exc:  # noqa: BLE001 — finalization must not break the response
            logger.warning("Entry %s success finalization skipped: %s", self._entry_name, exc)

    @staticmethod
    def _result_summary(result: Any) -> str | None:
        """Contract-level digest of a successful result (truncated, safe)."""
        content = None
        if isinstance(result, dict):
            choices = result.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message") or {}
                content = message.get("content") if isinstance(message, dict) else None
            elif "content" in result:
                content = result.get("content")
        if content is None:
            return None
        text = str(content).strip()
        return text[:500] if text else None

    def _finalizing_stream(self, gen: Any, correlation_result: CorrelationResult, workspace_id: uuid.UUID | None):
        """Wrap a streaming result so its terminal projection is written."""

        async def wrapped():
            from hecate.execution.entry_events import RunEventMapper

            mapper = (
                RunEventMapper(correlation_result.task_ref, correlation_result.run_ref, event_store=self._event_store)
                if correlation_result.task_ref is not None and correlation_result.run_ref is not None
                else None
            )
            summary = ""
            declared_artifacts: list[dict[str, Any]] | None = None
            try:
                async for chunk in gen:
                    if isinstance(chunk, dict):
                        if mapper is not None:
                            await mapper.map_stream_event(chunk)
                        if chunk.get("type") == "message" and isinstance(chunk.get("content"), str):
                            summary = (summary + chunk["content"])[:500]
                        elif chunk.get("type") == "values":
                            state = chunk.get("state") or {}
                            messages = state.get("messages")
                            if isinstance(messages, list) and messages:
                                last = messages[-1]
                                if isinstance(last, dict) and last.get("role") == "assistant":
                                    summary = self._result_summary({"content": last.get("content")}) or summary
                            if state.get("declared_artifacts") is not None:
                                declared_artifacts = state.get("declared_artifacts")
                    yield chunk
            except (Exception, asyncio.CancelledError, GeneratorExit) as exc:
                # Cancellation exceptions bypass Exception, and an ASGI cancel
                # scope must not cancel the final evidence/projection writes.
                with CancelScope(shield=True):
                    await self._finalize_failure(correlation_result, workspace_id, exc, mapper=mapper)
                    if hasattr(gen, "aclose"):
                        with contextlib.suppress(Exception, asyncio.CancelledError):
                            await gen.aclose()
                raise
            accepted, rejected = await self._extract_artifacts(
                {"declared_artifacts": declared_artifacts}, correlation_result, workspace_id
            )
            if mapper is not None:
                with contextlib.suppress(Exception):
                    await mapper.write_terminal_envelope(
                        state="succeeded",
                        artifacts=accepted,
                        extra={"rejected_artifacts": rejected} if rejected else None,
                    )
            await self._finalize_success(
                correlation_result, workspace_id, {"content": summary}, artifacts=accepted or None
            )

        return wrapped()

    async def _record_engine_events(
        self, correlation_result: CorrelationResult, result: Any, *, mapper: Any | None = None
    ) -> None:
        """Map a non-streaming run's drained engine events to envelopes.

        The platform adapter attaches the drained engine stream under
        ``_engine_events``; mapping them here gives synchronous and
        streaming executions the same envelope view over one Task/Run.
        The key is popped, so engine events never reach protocol output.
        """
        if not isinstance(result, dict):
            return
        events = result.pop("_engine_events", None)
        if mapper is None or correlation_result.task_ref is None or correlation_result.run_ref is None:
            return
        if not isinstance(events, list) or not events:
            return
        for event in events:
            if isinstance(event, dict):
                await mapper.map_stream_event(event)

    async def _correlate(self, correlation: CorrelationInput, *, session_id: uuid.UUID) -> CorrelationResult:
        """Task/Run/conversation registration (fail-open).

        With a registry session factory the registration commits on its own
        connection before execution starts, so the record of an execution
        that happened survives a caller rollback. The legacy savepoint path
        keeps the registration inside the caller's transaction.
        """
        if self._db is None:
            return CorrelationResult(status=CORRELATION_MISSING, reason="entry service has no db session")
        if correlation.workspace_id is None:
            return CorrelationResult(status=CORRELATION_MISSING, reason="no workspace context")
        from hecate.execution.task_run_registry import TaskRunRegistry, TaskRunRegistryError

        try:
            if self._registry_session_factory is not None:
                async with self._registry_session_factory() as registry_session, registry_session.begin():
                    registry = TaskRunRegistry(registry_session)
                    if correlation.existing_task_id is not None:
                        return await self._correlate_shared_task(registry, correlation)
                    return await self._correlate_per_request(registry, correlation, session_id=session_id)
            registry = TaskRunRegistry(self._db)
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
            await registry.update_projection(
                run.id, correlation.workspace_id, projection={"entry_name": self._entry_name}
            )
        await self._link_conversation(registry, correlation, task.id)
        return CorrelationResult(
            status=CORRELATION_REGISTERED if run_ref_result is not None else CORRELATION_MISSING,
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
        """Attach the engine session's conversation to the task (best-effort).

        The link runs on the registry's own session (a savepoint inside
        whichever transaction the registration used) so it never touches
        the caller's transaction on the independent-session path.
        """
        if correlation.session_id is None:
            return
        try:
            async with registry.session.begin_nested():
                await registry.link_session(
                    session_id=correlation.session_id,
                    task_id=task_id,
                    workspace_id=correlation.workspace_id,
                )
        except Exception as exc:  # noqa: BLE001 — linking is additive provenance
            logger.info("Entry %s conversation link skipped: %s", self._entry_name, exc)
