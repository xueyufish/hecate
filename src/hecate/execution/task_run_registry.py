"""Single-writer service for platform tasks, runs, and id mappings (step4).

The only reader/writer of ``tasks``, ``runs``, ``standalone_enrollments``,
and ``conversation_task_links`` - the same single-writer discipline as the
deployment registry, enforced by the layering guard. Semantics implemented:

- **Retries are new rows**: ``create_run`` allocates ``max(attempt_no) + 1``
  under the ``(task_id, attempt_no)`` unique index; a run id is never
  reused to feign recovery, and a retried run never inherits the previous
  attempt's backend session.
- **One vendor session backs at most one run**: ``bind_backend_session``
  refuses an (issuer domain, session) pair already bound elsewhere; the
  partial unique index is the concurrency backstop.
- **Imported observations gain no rights**: ``import_observation_run``
  creates an ``imported_observation``-origin row and nothing else - the
  registry has no queueing or dispatch API at all, so an imported run
  structurally cannot receive new work.
- **Enrollment is operator-gated**: registration without a resolvable
  trust root and host identity is rejected; ``managed_new_runs`` defaults
  false and only flips through ``set_managed_opt_in`` with an operator and
  admission result - never through a network event.
- **Conversation links are lazy and honest**: a legacy session links to a
  task on first touch; when its governance data does not resolve, the link
  is stored ``pending`` instead of fabricating a platform identity.

Workspace isolation follows the deployment registry's uniform-not-found
pattern: a missing row and a foreign-workspace row are indistinguishable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.identity import IdentityChain
from hecate.contracts.execution.references import BackendRef, RefKind, require_kind
from hecate.models.agent_deployment import AgentDeploymentModel
from hecate.models.audit import AuditLogModel
from hecate.models.conversation_link import ConversationTaskLinkModel, GovernanceStatus
from hecate.models.run import RunModel, RunOrigin
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

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
    ) -> TaskModel:
        """Create the platform responsibility record for one business goal.

        ``initiator_ref`` must round-trip as an ``IdentityChain``: the task
        names who asked for the work, and a malformed chain is a caller bug,
        not bad data to store.
        """
        if not goal or not goal.strip():
            raise TaskRunValidationError("task goal must be a non-empty string")
        self._validated_identity_ref(initiator_ref)
        task = TaskModel(
            goal=goal,
            initiator_ref=initiator_ref,
            acceptance=acceptance if acceptance is not None else {},
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
    ) -> RunModel:
        """Open one execution attempt: a new row with the next attempt number.

        The identity chain is frozen at creation (serialized once; later
        identity or delegation changes never rewrite it). The backend run
        reference must be contract-kind ``run``. A retry is this method
        called again on the same task - it lands on a new row by
        construction and never inherits the prior attempt's backend session.
        """
        task = await self.get_task(task_id, workspace_id)
        require_kind(backend_run_ref, RefKind.RUN)
        await self._require_workspace_deployment(deployment_id, workspace_id)

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
            origin=RunOrigin.PLATFORM,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(self, run_id: uuid.UUID, workspace_id: uuid.UUID) -> RunModel:
        run = await self._session.get(RunModel, run_id)
        if run is None or run.deleted or run.workspace_id != workspace_id:
            raise TaskNotFoundError(f"run {run_id} not found in workspace")
        return run

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
        run = await self.get_run(run_id, workspace_id)
        if not isinstance(projection, dict):
            raise TaskRunValidationError("projection must be a JSON object")
        run.projection = projection
        if event_cursor is not None:
            if event_cursor < run.event_cursor:
                raise TaskRunValidationError("event cursor must not move backwards")
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

        Creates exactly one ``imported_observation`` row with the
        host-local provenance attached. Imported rows gain no dispatch,
        scheduling, or approval rights by construction: this registry
        offers no queueing API, so no code path can route new work to an
        observation. Re-importing the same host-local run dedupes via the
        caller-supplied ``local_source`` probe (``find_local_source_run``).
        """
        task = await self.get_task(task_id, workspace_id)
        require_kind(backend_run_ref, RefKind.RUN)
        await self._require_workspace_deployment(deployment_id, workspace_id)
        if not isinstance(local_source, dict) or not local_source.get("local_run_id"):
            raise TaskRunValidationError("local_source must carry the host-local local_run_id")

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
            local_source=local_source,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def find_local_source_run(self, local_run_id: str, workspace_id: uuid.UUID) -> RunModel | None:
        """Dedupe probe for host-local run ids within one workspace."""
        rows = (
            (
                await self._session.execute(
                    select(RunModel).where(
                        RunModel.workspace_id == workspace_id,
                        RunModel.deleted.is_(False),
                        RunModel.local_source.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            if row.local_source is not None and row.local_source.get("local_run_id") == local_run_id:
                return row
        return None

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
        run = await self.get_run(run_id, workspace_id)
        if session_ref.kind not in (RefKind.SESSION, RefKind.TURN):
            raise TaskRunValidationError(
                f"backend session binding needs a session/turn reference, got {session_ref.kind.value}"
            )
        existing = (
            (
                await self._session.execute(
                    select(RunModel).where(
                        RunModel.backend_session.isnot(None),
                        RunModel.deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        for other in existing:
            if (
                other.id != run.id
                and other.backend_session is not None
                and self._session_key(other.backend_session) == self._session_key(session_ref.to_dict())
            ):
                raise BackendSessionConflictError(
                    f"backend session {session_ref.issuer_domain}/{session_ref.id} is already bound to run {other.id}"
                )
        run.backend_session = session_ref.to_dict()
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

        The host identity and trust root references must resolve to named,
        non-empty entries - a host that cannot name its trust root is not
        enrolled, never defaulted. ``managed_new_runs`` starts false; only
        :meth:`set_managed_opt_in` with an operator flips it.
        """
        self._validated_named_ref(host_identity_ref, "host_identity_ref")
        self._validated_named_ref(trust_root_ref, "trust_root_ref")
        enrollment = StandaloneEnrollmentModel(
            host_identity_ref=host_identity_ref,
            trust_root_ref=trust_root_ref,
            installed_versions=installed_versions if installed_versions is not None else {},
            admission=AdmissionResult.PENDING,
            workspace_id=workspace_id,
        )
        self._session.add(enrollment)
        await self._session.flush()
        self._audit(
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
    ) -> StandaloneEnrollmentModel:
        """Operator-reviewed transition of the managed new-runs opt-in.

        A network reconnect has no operator and therefore no path here -
        the audit row records who decided, and scheduling rights change
        only through this door.
        """
        enrollment = await self._session.get(StandaloneEnrollmentModel, enrollment_id)
        if enrollment is None or enrollment.deleted or enrollment.workspace_id != workspace_id:
            raise TaskNotFoundError(f"enrollment {enrollment_id} not found in workspace")
        enrollment.managed_new_runs = managed_new_runs
        enrollment.admission = AdmissionResult.ADMITTED if admitted else AdmissionResult.REJECTED
        enrollment.operator_id = operator_id
        enrollment.admitted_at = _now()
        await self._session.flush()
        self._audit(
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
        initiator_resolves: bool = True,
    ) -> ConversationTaskLinkModel:
        """Lazily associate a legacy conversation with a task.

        Idempotent per (conversation, task). When the session's governance
        data does not resolve to a governed initiator, the link is stored
        ``pending`` - visible debt, never a fabricated platform identity.
        """
        task = await self.get_task(task_id, workspace_id)
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
        link = ConversationTaskLinkModel(
            conversation_id=conversation_id,
            task_id=task.id,
            workspace_id=task.workspace_id,
            governance_status=GovernanceStatus.OK if initiator_resolves else GovernanceStatus.PENDING,
        )
        self._session.add(link)
        await self._session.flush()
        return link

    async def list_tasks_for_conversation(self, conversation_id: uuid.UUID, workspace_id: uuid.UUID) -> list[uuid.UUID]:
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

    async def _require_workspace_deployment(self, deployment_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
        deployment = await self._session.get(AgentDeploymentModel, deployment_id)
        if deployment is None or deployment.deleted or deployment.workspace_id != workspace_id:
            raise TaskNotFoundError(f"deployment {deployment_id} not found in workspace")

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
        if not issuer or not isinstance(issuer, str) or not entry or not isinstance(entry, str):
            raise TaskRunValidationError(f"{field} must name a resolvable issuer_domain and id")

    @staticmethod
    def _session_key(session_dict: dict[str, Any]) -> tuple[str, str]:
        return (str(session_dict.get("issuer_domain")), str(session_dict.get("id")))

    def _audit(
        self,
        *,
        workspace_id: uuid.UUID,
        action: str,
        resource_id: uuid.UUID,
        detail: dict[str, str],
        actor: uuid.UUID | None = None,
    ) -> None:
        self._session.add(
            AuditLogModel(
                org_id=workspace_id,
                workspace_id=workspace_id,
                user_id=actor or uuid.UUID(int=0),
                action=action,
                resource_type="task_run",
                resource_id=resource_id,
                success=True,
                metadata_=detail,
            )
        )
