"""Single-writer service for platform tasks, runs, and id mappings (step4).

The only reader/writer of ``tasks``, ``runs``, ``standalone_enrollments``,
and ``conversation_task_links`` - the same single-writer discipline as the
deployment registry, enforced by the layering guard. Semantics implemented:

- **Retries are new rows**: ``create_run`` allocates ``max(attempt_no) + 1``
  while holding the owning Task row lock, with a unique-index backstop; a run id is never
  reused to feign recovery, and a retried run never inherits the previous
  attempt's backend session.
- **One vendor session backs at most one run**: ``bind_backend_session``
  refuses an (issuer domain, session) pair already bound elsewhere; the
  partial unique index is the concurrency backstop.
- **Imported observations gain no rights**: ``import_observation_run``
  records historical claims with host control ownership; future dispatch and
  approval paths must enforce that origin. This registry has no dispatch API.
- **Enrollment is operator-gated**: named references register pending claims;
  managed opt-in requires a real administrator and trusted host/root resolution.
- **Conversation links are lazy and honest**: a legacy session links to a
  task on first touch; when its governance data does not resolve, the link
  is stored ``pending`` instead of fabricating a platform identity.

Workspace isolation follows the deployment registry's uniform-not-found
pattern: a missing row and a foreign-workspace row are indistinguishable.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.identity import IdentityChain
from hecate.contracts.execution.references import BackendRef, RefKind, require_kind
from hecate.execution.registry_support import is_workspace_admin, registry_audit, require_workspace
from hecate.models.agent_deployment import AgentDeploymentModel
from hecate.models.agent_principal import AgentPrincipalModel, PrincipalLifecycle
from hecate.models.agent_version import AgentVersionModel
from hecate.models.conversation import ConversationModel
from hecate.models.conversation_link import ConversationTaskLinkModel, GovernanceStatus
from hecate.models.run import RunModel, RunOrigin
from hecate.models.session import SessionModel
from hecate.models.standalone_enrollment import AdmissionResult, StandaloneEnrollmentModel
from hecate.models.task import TaskModel


class TaskRunRegistryError(Exception):
    """Base error for task/run registration failures."""


class TaskNotFoundError(TaskRunRegistryError):
    """Raised for missing tasks/runs and cross-workspace reads alike."""


class TaskRunValidationError(TaskRunRegistryError):
    """Raised when references, sessions, or enrollment data are invalid."""


class BackendSessionConflictError(TaskRunRegistryError):
    """Raised when a vendor session is already bound to another run."""


def _now() -> datetime:
    return datetime.now(UTC)


class TaskRunRegistry:
    """The only writer of the task/run/enrollment/link tables."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        enrollment_resolver: Callable[[uuid.UUID, dict, dict, dict], Awaitable[bool]] | None = None,
    ) -> None:
        self._session = session
        self._enrollment_resolver = enrollment_resolver

    # ------------------------------------------------------------------
    # Task
    # ------------------------------------------------------------------

    async def create_task(
        self,
        *,
        goal: str,
        initiator_ref: dict[str, Any],
        workspace_id: uuid.UUID,
        issuer_domain: str = "hecate",
        acceptance: dict[str, Any] | None = None,
        responsibility: str | None = None,
        task_id: uuid.UUID | None = None,
    ) -> TaskModel:
        """Create the platform responsibility record for one business goal.

        ``initiator_ref`` must round-trip as an ``IdentityChain``: the task
        names who asked for the work, and a malformed chain is a caller bug,
        not bad data to store. ``task_id`` presets the row's identifier so
        the durable-submission arbitration (which registers the Task/Run
        association before any row exists) and the row land on the same id.
        """
        if not isinstance(goal, str) or not goal.strip():
            raise TaskRunValidationError("task goal must be a non-empty string")
        self._validated_identity_ref(initiator_ref)
        try:
            await require_workspace(self._session, workspace_id)
        except ValueError as exc:
            raise TaskNotFoundError("workspace not found") from exc
        task = TaskModel(
            id=task_id,
            goal=goal,
            initiator_ref=deepcopy(initiator_ref),
            acceptance=deepcopy(acceptance) if acceptance is not None else {},
            responsibility=responsibility,
            workspace_id=workspace_id,
            issuer_domain=issuer_domain,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def get_task(self, task_id: uuid.UUID, workspace_id: uuid.UUID) -> TaskModel:
        """Workspace-scoped read; missing and foreign rows are indistinguishable."""
        task = await self._session.get(TaskModel, task_id)
        if task is None or task.deleted or task.workspace_id != workspace_id:
            raise TaskNotFoundError(f"task {task_id} not found in workspace")
        return task

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    async def create_run(
        self,
        *,
        task_id: uuid.UUID,
        workspace_id: uuid.UUID,
        deployment_id: uuid.UUID,
        identity_chain: IdentityChain,
        backend_run_ref: BackendRef,
        run_id: uuid.UUID | None = None,
    ) -> RunModel:
        """Open one execution attempt: a new row with the next attempt number.

        The identity chain is frozen at creation (serialized once; later
        identity or delegation changes never rewrite it). The backend run
        reference must be contract-kind ``run``. A retry is this method
        called again on the same task - it lands on a new row by
        construction and never inherits the prior attempt's backend session.
        ``run_id`` presets the row identifier for pre-registered
        associations (see :meth:`create_task`).
        """
        task = await self._locked_task(task_id, workspace_id)
        require_kind(backend_run_ref, RefKind.RUN)
        deployment = await self._require_workspace_deployment(deployment_id, workspace_id)
        await self._validate_run_identity(identity_chain, deployment, require_active=True)
        snapshot = await self._execution_snapshot(deployment)

        current_max = (
            await self._session.execute(select(func.max(RunModel.attempt_no)).where(RunModel.task_id == task.id))
        ).scalar_one()
        run = RunModel(
            id=run_id,
            task_id=task.id,
            deployment_id=deployment_id,
            attempt_no=(current_max or 0) + 1,
            workspace_id=task.workspace_id,
            issuer_domain=task.issuer_domain,
            identity_chain=identity_chain.to_dict(),
            backend_ref=backend_run_ref.to_dict(),
            origin=RunOrigin.PLATFORM,
            execution_snapshot=snapshot,
            control_owner="platform",
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(self, run_id: uuid.UUID, workspace_id: uuid.UUID) -> RunModel:
        """Read a live run inside its owning workspace."""
        run = await self._session.get(RunModel, run_id)
        if run is None or run.deleted or run.workspace_id != workspace_id:
            raise TaskNotFoundError(f"run {run_id} not found in workspace")
        return run

    async def validate_run_execution(self, run: RunModel) -> None:
        """Revalidate current authority and the attempt's frozen deployment."""
        deployment = await self._require_workspace_deployment(run.deployment_id, run.workspace_id)
        await self._validate_run_identity(IdentityChain.from_dict(run.identity_chain), deployment, require_active=True)
        if run.execution_snapshot != await self._execution_snapshot(deployment):
            raise TaskRunValidationError("deployment or version changed since the attempt was admitted")

    async def list_runs_for_task(self, task_id: uuid.UUID, workspace_id: uuid.UUID) -> list[RunModel]:
        """All attempts of one task, ordered by attempt number."""
        await self.get_task(task_id, workspace_id)
        rows = (
            (
                await self._session.execute(
                    select(RunModel)
                    .where(
                        RunModel.task_id == task_id,
                        RunModel.workspace_id == workspace_id,
                        RunModel.deleted.is_(False),
                    )
                    .order_by(RunModel.attempt_no)
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def update_projection(
        self,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        projection: dict[str, Any],
        event_cursor: int | None = None,
    ) -> RunModel:
        """Platform-side observation update: touches projection fields only.

        This is the one write path into the projection field group; the
        executor-owned lifecycle is not represented here at all, so the
        platform cannot overwrite facts it does not store.
        """
        run = await self._locked_run(run_id, workspace_id)
        if not isinstance(projection, dict):
            raise TaskRunValidationError("projection must be a JSON object")
        if event_cursor is not None and (
            not isinstance(event_cursor, int) or isinstance(event_cursor, bool) or event_cursor < run.event_cursor
        ):
            raise TaskRunValidationError("event cursor must not move backwards")
        run.projection = deepcopy(projection)
        if event_cursor is not None:
            run.event_cursor = event_cursor
        await self._session.flush()
        return run

    async def import_observation_run(
        self,
        *,
        task_id: uuid.UUID,
        workspace_id: uuid.UUID,
        deployment_id: uuid.UUID,
        identity_chain: IdentityChain,
        backend_run_ref: BackendRef,
        local_source: dict[str, Any],
    ) -> RunModel:
        """Record a run imported from a local host's records, as observation.

        Creates one observation row with host-local provenance and host
        control ownership. Dispatch and approval paths must enforce those
        fields when introduced; the registry itself grants no authority.
        Re-importing the same source returns the existing row, while a
        conflicting task/backend mapping is rejected.
        """
        task = await self._locked_task(task_id, workspace_id)
        require_kind(backend_run_ref, RefKind.RUN)
        deployment = await self._require_workspace_deployment(deployment_id, workspace_id)
        await self._validate_run_identity(identity_chain, deployment, require_active=False)
        if not isinstance(local_source, dict) or not local_source.get("local_run_id"):
            raise TaskRunValidationError("local_source must carry the host-local local_run_id")
        self._validated_named_ref(
            {"issuer_domain": local_source.get("issuer_domain"), "id": local_source.get("local_run_id")},
            "local_source",
        )
        existing = await self.find_local_source_run(
            local_source["local_run_id"],
            workspace_id,
            issuer_domain=local_source["issuer_domain"],
            deployment_id=deployment_id,
        )
        if existing is not None:
            if existing.task_id != task.id or existing.backend_ref != backend_run_ref.to_dict():
                raise TaskRunValidationError("local source is already mapped to another task/backend run")
            return existing

        current_max = (
            await self._session.execute(select(func.max(RunModel.attempt_no)).where(RunModel.task_id == task.id))
        ).scalar_one()
        run = RunModel(
            task_id=task.id,
            deployment_id=deployment_id,
            attempt_no=(current_max or 0) + 1,
            workspace_id=task.workspace_id,
            issuer_domain=task.issuer_domain,
            identity_chain=identity_chain.to_dict(),
            backend_ref=backend_run_ref.to_dict(),
            origin=RunOrigin.IMPORTED_OBSERVATION,
            local_source=deepcopy(local_source),
            local_source_issuer=local_source["issuer_domain"],
            local_run_id=local_source["local_run_id"],
            control_owner="host",
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def find_local_source_run(
        self,
        local_run_id: str,
        workspace_id: uuid.UUID,
        *,
        issuer_domain: str,
        deployment_id: uuid.UUID,
    ) -> RunModel | None:
        """Dedupe probe for host-local run ids within one workspace."""
        return (
            await self._session.execute(
                select(RunModel).where(
                    RunModel.workspace_id == workspace_id,
                    RunModel.deployment_id == deployment_id,
                    RunModel.local_source_issuer == issuer_domain,
                    RunModel.local_run_id == local_run_id,
                )
            )
        ).scalar_one_or_none()

    async def bind_backend_session(
        self,
        run_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        session_ref: BackendRef,
    ) -> RunModel:
        """Bind one vendor session/turn reference to this run.

        A session may only ever back one run: an (issuer domain, session id)
        pair already bound to another run is refused here and by the partial
        unique index underneath. The reference must be contract-kind
        ``session`` or ``turn``.
        """
        run = await self._locked_run(run_id, workspace_id)
        if session_ref.kind not in (RefKind.SESSION, RefKind.TURN):
            raise TaskRunValidationError(
                f"backend session binding needs a session/turn reference, got {session_ref.kind.value}"
            )
        if run.backend_session is not None:
            if run.backend_session == session_ref.to_dict():
                return run
            raise BackendSessionConflictError("backend session binding cannot be overwritten")
        other = (
            await self._session.execute(
                select(RunModel.id).where(
                    RunModel.backend_session_issuer == session_ref.issuer_domain,
                    RunModel.backend_session_id == session_ref.id,
                )
            )
        ).scalar_one_or_none()
        if other is not None:
            raise BackendSessionConflictError("backend session is already bound to another run")
        run.backend_session = session_ref.to_dict()
        run.backend_session_issuer = session_ref.issuer_domain
        run.backend_session_id = session_ref.id
        await self._session.flush()
        return run

    # ------------------------------------------------------------------
    # Standalone enrollment
    # ------------------------------------------------------------------

    async def register_standalone_enrollment(
        self,
        *,
        workspace_id: uuid.UUID,
        host_identity_ref: dict[str, Any],
        trust_root_ref: dict[str, Any],
        installed_versions: dict[str, Any] | None = None,
    ) -> StandaloneEnrollmentModel:
        """Record a standalone host's enrollment request.

        Host identity and trust root references must be syntactically named
        entries; this does not authenticate either. The request stays pending
        with ``managed_new_runs`` false until operator-reviewed trusted
        resolution succeeds in :meth:`set_managed_opt_in`.
        """
        try:
            await require_workspace(self._session, workspace_id)
            self._validated_named_ref(host_identity_ref, "host_identity_ref")
            self._validated_named_ref(trust_root_ref, "trust_root_ref")
            if installed_versions is not None and not isinstance(installed_versions, dict):
                raise TaskRunValidationError("installed_versions must be a JSON object")
        except TaskRunValidationError as exc:
            await self._audit(
                workspace_id=workspace_id,
                action="STANDALONE_ENROLLMENT_REJECTED",
                resource_id=None,
                detail={"reason": str(exc)},
                success=False,
            )
            raise
        except ValueError as exc:
            raise TaskNotFoundError("workspace not found") from exc
        enrollment = StandaloneEnrollmentModel(
            host_identity_ref=deepcopy(host_identity_ref),
            trust_root_ref=deepcopy(trust_root_ref),
            installed_versions=deepcopy(installed_versions) if installed_versions is not None else {},
            admission=AdmissionResult.PENDING,
            workspace_id=workspace_id,
        )
        self._session.add(enrollment)
        await self._session.flush()
        await self._audit(
            workspace_id=workspace_id,
            action="STANDALONE_ENROLLMENT_REGISTERED",
            resource_id=enrollment.id,
            detail={"admission": AdmissionResult.PENDING.value},
        )
        return enrollment

    async def set_managed_opt_in(
        self,
        enrollment_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        managed_new_runs: bool,
        operator_id: uuid.UUID,
        admitted: bool,
        managed_scope: list[str] | None = None,
    ) -> StandaloneEnrollmentModel:
        """Operator-reviewed transition of the managed new-runs opt-in.

        A network reconnect has no operator and therefore no path here -
        the audit row records who decided, and scheduling rights change
        only through this door. ``managed_scope`` is the operator-declared
        data-domain allowlist placed into every pull lease; empty/absent
        authorizes nothing (deny-by-default).
        """
        if managed_scope is not None:
            if (
                not isinstance(managed_scope, list)
                or any(not isinstance(domain, str) or not domain.strip() for domain in managed_scope)
                or len(managed_scope) != len(set(managed_scope))
            ):
                raise TaskRunValidationError("managed scope must be a list of unique non-empty strings")
            managed_scope = [domain.strip() for domain in managed_scope]
        enrollment = await self._session.get(StandaloneEnrollmentModel, enrollment_id)
        if enrollment is None or enrollment.deleted or enrollment.workspace_id != workspace_id:
            raise TaskNotFoundError(f"enrollment {enrollment_id} not found in workspace")
        reason = None
        if not isinstance(operator_id, uuid.UUID) or not await is_workspace_admin(
            self._session, workspace_id, operator_id
        ):
            reason = "operator must be an active workspace administrator"
        elif managed_new_runs and not admitted:
            reason = "managed new runs require admitted enrollment"
        elif admitted:
            versions = enrollment.installed_versions
            contracts = versions.get("contracts")
            capabilities = versions.get("capabilities")
            if (
                not isinstance(contracts, list)
                or not contracts
                or not all(isinstance(version, str) and version.strip() for version in contracts)
                or not isinstance(capabilities, dict)
                or not capabilities
            ):
                reason = "admission requires installed contract versions and capabilities"
            elif self._enrollment_resolver is None:
                reason = "host identity and trust root do not resolve through a trusted resolver"
            else:
                try:
                    resolved = await self._enrollment_resolver(
                        workspace_id,
                        deepcopy(enrollment.host_identity_ref),
                        deepcopy(enrollment.trust_root_ref),
                        deepcopy(versions),
                    )
                except Exception:
                    reason = "trusted resolver failed; admission remains pending"
                else:
                    if resolved is not True:
                        reason = "host identity and trust root do not resolve through a trusted resolver"
        if reason is not None:
            await self._audit(
                workspace_id=workspace_id,
                actor=operator_id,
                action="STANDALONE_ENROLLMENT_REJECTED",
                resource_id=enrollment.id,
                detail={"reason": reason},
                success=False,
            )
            raise TaskRunValidationError(reason)
        enrollment.managed_new_runs = managed_new_runs
        enrollment.managed_scope = list(managed_scope) if managed_scope is not None else []
        enrollment.admission = AdmissionResult.ADMITTED if admitted else AdmissionResult.REJECTED
        enrollment.operator_id = operator_id
        enrollment.admitted_at = _now()
        await self._session.flush()
        await self._audit(
            workspace_id=workspace_id,
            actor=operator_id,
            action="STANDALONE_ENROLLMENT_ADMITTED" if admitted else "STANDALONE_ENROLLMENT_REJECTED",
            resource_id=enrollment.id,
            detail={"managed_new_runs": str(managed_new_runs).lower()},
        )
        return enrollment

    # ------------------------------------------------------------------
    # Conversation links
    # ------------------------------------------------------------------

    async def link_conversation(
        self,
        *,
        conversation_id: uuid.UUID,
        task_id: uuid.UUID,
        workspace_id: uuid.UUID,
        initiator_resolves: bool = False,
    ) -> ConversationTaskLinkModel:
        """Lazily associate a legacy conversation with a task.

        Idempotent per (conversation, task). When the session's governance
        data does not resolve to a governed initiator, the link is stored
        ``pending`` - visible debt, never a fabricated platform identity.
        """
        task = await self._locked_task(task_id, workspace_id)
        await self._require_conversation(conversation_id, workspace_id)
        if initiator_resolves:
            raise TaskRunValidationError("caller cannot assert resolved governance; use the future authorization flow")
        existing = (
            await self._session.execute(
                select(ConversationTaskLinkModel).where(
                    ConversationTaskLinkModel.conversation_id == conversation_id,
                    ConversationTaskLinkModel.task_id == task.id,
                    ConversationTaskLinkModel.deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        previous = (
            await self._session.execute(
                select(ConversationTaskLinkModel.id).where(ConversationTaskLinkModel.task_id == task.id)
            )
        ).scalar_one_or_none()
        if previous is not None:
            raise TaskRunValidationError("task already has an originating conversation mapping")
        link = ConversationTaskLinkModel(
            conversation_id=conversation_id,
            task_id=task.id,
            workspace_id=task.workspace_id,
            governance_status=GovernanceStatus.PENDING,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def list_tasks_for_conversation(self, conversation_id: uuid.UUID, workspace_id: uuid.UUID) -> list[uuid.UUID]:
        """List tasks after validating the actual conversation's workspace."""
        await self._require_conversation(conversation_id, workspace_id)
        rows = (
            (
                await self._session.execute(
                    select(ConversationTaskLinkModel.task_id).where(
                        ConversationTaskLinkModel.conversation_id == conversation_id,
                        ConversationTaskLinkModel.workspace_id == workspace_id,
                        ConversationTaskLinkModel.deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def link_session(
        self,
        *,
        session_id: uuid.UUID,
        task_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> ConversationTaskLinkModel:
        """Resolve a legacy session to its real conversation without replaying execution."""
        session = await self._session.get(SessionModel, session_id)
        if session is None or session.deleted or session.workspace_id != workspace_id:
            raise TaskNotFoundError("session not found in workspace")
        if session.conversation_id is None:
            raise TaskRunValidationError("legacy session has no conversation mapping; remediation is required")
        return await self.link_conversation(
            conversation_id=session.conversation_id,
            task_id=task_id,
            workspace_id=workspace_id,
        )

    async def _require_conversation(self, conversation_id: uuid.UUID, workspace_id: uuid.UUID) -> ConversationModel:
        conversation = await self._session.get(ConversationModel, conversation_id)
        if conversation is None or conversation.deleted or conversation.workspace_id != workspace_id:
            raise TaskNotFoundError("conversation not found in workspace")
        return conversation

    async def _locked_task(self, task_id: uuid.UUID, workspace_id: uuid.UUID) -> TaskModel:
        task = (
            await self._session.execute(
                select(TaskModel)
                .where(
                    TaskModel.id == task_id,
                    TaskModel.workspace_id == workspace_id,
                    TaskModel.deleted.is_(False),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if task is None:
            raise TaskNotFoundError("task not found in workspace")
        return task

    async def _locked_run(self, run_id: uuid.UUID, workspace_id: uuid.UUID) -> RunModel:
        run = (
            await self._session.execute(
                select(RunModel)
                .where(
                    RunModel.id == run_id,
                    RunModel.workspace_id == workspace_id,
                    RunModel.deleted.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if run is None:
            raise TaskNotFoundError("run not found in workspace")
        return run

    async def _require_workspace_deployment(
        self,
        deployment_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> AgentDeploymentModel:
        deployment = await self._session.get(AgentDeploymentModel, deployment_id)
        if deployment is None or deployment.deleted or deployment.workspace_id != workspace_id:
            raise TaskNotFoundError(f"deployment {deployment_id} not found in workspace")
        return deployment

    async def _validate_run_identity(
        self,
        chain: IdentityChain,
        deployment: AgentDeploymentModel,
        *,
        require_active: bool,
    ) -> None:
        if chain.workload.deployment != BackendRef(RefKind.DEPLOYMENT, deployment.issuer_domain, str(deployment.id)):
            raise TaskRunValidationError("identity workload deployment does not match selected deployment")
        try:
            principal_id = uuid.UUID(chain.principal_id)
        except ValueError as exc:
            if not require_active:
                return
            raise TaskRunValidationError("principal does not resolve") from exc
        principal = await self._session.get(AgentPrincipalModel, principal_id)
        if not require_active and (principal is None or principal.deleted):
            return
        if (
            principal is None
            or principal.deleted
            or principal.agent_id != deployment.agent_id
            or principal.workspace_id != deployment.workspace_id
        ):
            raise TaskRunValidationError("principal does not belong to selected deployment")
        if require_active and principal.lifecycle is not PrincipalLifecycle.ACTIVE:
            raise TaskRunValidationError("principal is not active")
        if require_active and not chain.audience:
            raise TaskRunValidationError("new platform run requires an explicit audience")

    async def _execution_snapshot(self, deployment: AgentDeploymentModel) -> dict[str, Any]:
        version = await self._session.get(AgentVersionModel, deployment.agent_version_id)
        if version is None or version.deleted or version.agent_id != deployment.agent_id:
            raise TaskRunValidationError("deployment version does not resolve to its agent")
        return deepcopy(
            {
                "agent_version_id": str(version.id),
                "config_snapshot": version.config_snapshot,
                "pinned_refs": version.pinned_refs,
                "ref_manifest": version.ref_manifest,
                "backend_type": deployment.backend_type.value,
                "backend_version": deployment.backend_version,
                "access_mode": deployment.access_mode.value,
                "issuer_domain": deployment.issuer_domain,
                "transport_contract_version": deployment.transport_contract_version,
                "endpoint": deployment.endpoint,
                "config_ref": deployment.config_ref,
                "credential_ref": deployment.credential_ref,
                "capability_snapshot": deployment.capability_snapshot,
                "hosted_config": deployment.hosted_config,
                "access_level": deployment.access_level.value,
            }
        )

    @staticmethod
    def _validated_identity_ref(initiator_ref: dict[str, Any]) -> None:
        try:
            IdentityChain.from_dict(initiator_ref)
        except (KeyError, TypeError, ValueError) as exc:
            raise TaskRunValidationError(f"initiator_ref does not round-trip as an IdentityChain: {exc}") from exc

    @staticmethod
    def _validated_named_ref(ref: dict[str, Any], field: str) -> None:
        if not isinstance(ref, dict):
            raise TaskRunValidationError(f"{field} must be a JSON object")
        issuer = ref.get("issuer_domain")
        entry = ref.get("id")
        if not isinstance(issuer, str) or not issuer.strip() or not isinstance(entry, str) or not entry.strip():
            raise TaskRunValidationError(f"{field} must name a resolvable issuer_domain and id")

    async def _audit(
        self,
        *,
        workspace_id: uuid.UUID,
        action: str,
        resource_id: uuid.UUID | None,
        detail: dict[str, str],
        actor: uuid.UUID | None = None,
        success: bool = True,
    ) -> None:
        await registry_audit(
            self._session,
            workspace_id=workspace_id,
            actor=actor,
            action=action,
            resource_type="task_run",
            resource_id=resource_id,
            detail=detail,
            success=success,
        )
