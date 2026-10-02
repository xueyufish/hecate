"""Single-writer registration service for agent principals (plan step4).

All reads and writes of ``agent_principals`` go through this module: other
domains must not import the model directly (enforced by
``tests/test_layering_domain.py::TestDeploymentRegistryTablesAreExecutionOwned``).
The owner must resolve to a live organization and an active user; persona
strings never create or stand in for a principal. Agents without mappable
governance data are recorded as governance-pending audit rows instead of
being silently auto-mapped.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.execution.registry_support import registry_audit, require_workspace
from hecate.models.agent import AgentModel
from hecate.models.agent_principal import AgentPrincipalModel, PrincipalLifecycle
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace_member import WorkspaceMemberModel

_ALLOWED_TRANSITIONS: dict[PrincipalLifecycle, set[PrincipalLifecycle]] = {
    PrincipalLifecycle.ACTIVE: {PrincipalLifecycle.SUSPENDED, PrincipalLifecycle.REVOKED},
    PrincipalLifecycle.SUSPENDED: {PrincipalLifecycle.ACTIVE, PrincipalLifecycle.REVOKED},
    # Revocation is terminal; a future replacement flow must explicitly preserve history.
    PrincipalLifecycle.REVOKED: set(),
}


class PrincipalRegistryError(Exception):
    """Base error for principal registration failures."""


class PrincipalNotFoundError(PrincipalRegistryError):
    """Raised for missing principals and cross-workspace reads alike."""


class PrincipalValidationError(PrincipalRegistryError):
    """Raised when owner/organization references do not resolve."""


class PrincipalTransitionError(PrincipalRegistryError):
    """Raised on lifecycle transitions the state machine forbids."""


class PrincipalRegistry:
    """The only writer of ``agent_principals``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register(
        self,
        agent_id: uuid.UUID,
        organization_id: uuid.UUID,
        owner_user_id: uuid.UUID,
        *,
        workspace_id: uuid.UUID | None = None,
        registered_by: uuid.UUID | None = None,
        identity_provider: str | None = None,
        idp_subject: str | None = None,
    ) -> AgentPrincipalModel:
        """Bind one agent to a resolvable responsibility subject.

        The agent must exist and must not already carry a live principal;
        the organization and owner must resolve (owner to an active user).
        ``workspace_id`` defaults to the agent's own workspace.
        """
        agent = await self._session.get(AgentModel, agent_id)
        if agent is None or agent.deleted:
            raise PrincipalNotFoundError(f"agent {agent_id} not found")
        effective_workspace = workspace_id or agent.workspace_id
        if effective_workspace != agent.workspace_id:
            raise PrincipalNotFoundError(f"agent {agent_id} not found in workspace")
        try:
            workspace = await require_workspace(self._session, effective_workspace)
        except ValueError as exc:
            raise PrincipalNotFoundError("workspace not found") from exc

        organization = await self._session.get(OrganizationModel, organization_id)
        if organization is None or organization.deleted:
            raise PrincipalValidationError(f"organization {organization_id} does not resolve")
        if workspace.org_id != organization.id:
            raise PrincipalValidationError("organization does not belong to the agent workspace")
        owner = await self._session.get(UserModel, owner_user_id)
        if owner is None or owner.deleted or not owner.active:
            raise PrincipalValidationError(f"owner user {owner_user_id} does not resolve to an active user")
        member = (
            await self._session.execute(
                select(WorkspaceMemberModel).where(
                    WorkspaceMemberModel.workspace_id == workspace.id,
                    WorkspaceMemberModel.user_id == owner.id,
                    WorkspaceMemberModel.deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if organization.owner_id != owner.id and member is None:
            raise PrincipalValidationError("owner does not belong to the organization/workspace")
        if bool(identity_provider) != bool(idp_subject):
            raise PrincipalValidationError("identity_provider and idp_subject must be provided together")

        existing = (
            await self._session.execute(select(AgentPrincipalModel).where(AgentPrincipalModel.agent_id == agent_id))
        ).scalar_one_or_none()
        if existing is not None:
            raise PrincipalValidationError(f"agent {agent_id} already has a live principal")

        principal = AgentPrincipalModel(
            agent_id=agent_id,
            workspace_id=effective_workspace,
            organization_id=organization_id,
            owner_user_id=owner_user_id,
            lifecycle=PrincipalLifecycle.ACTIVE,
            identity_provider=identity_provider,
            idp_subject=idp_subject,
            registered_by=registered_by,
        )
        self._session.add(principal)
        await self._session.flush()
        await self._audit(
            workspace_id=effective_workspace,
            actor=registered_by,
            action="AGENT_PRINCIPAL_REGISTERED",
            resource_id=principal.id,
            detail={
                "agent_id": str(agent_id),
                "organization_id": str(organization_id),
                "owner_user_id": str(owner_user_id),
            },
        )
        return principal

    async def transition(
        self,
        principal_id: uuid.UUID,
        to_lifecycle: PrincipalLifecycle,
        *,
        workspace_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
    ) -> AgentPrincipalModel:
        """Apply one explicit lifecycle transition; revocation is terminal."""
        principal = await self.get_for_workspace(principal_id, workspace_id)
        if to_lifecycle not in _ALLOWED_TRANSITIONS[principal.lifecycle]:
            raise PrincipalTransitionError(
                f"transition {principal.lifecycle.value} -> {to_lifecycle.value} is not allowed"
            )
        previous = principal.lifecycle
        principal.lifecycle = to_lifecycle
        await self._session.flush()
        await self._audit(
            workspace_id=principal.workspace_id,
            actor=actor_id,
            action="AGENT_PRINCIPAL_TRANSITION",
            resource_id=principal.id,
            detail={"from": previous.value, "to": to_lifecycle.value, "agent_id": str(principal.agent_id)},
        )
        return principal

    async def get_for_workspace(self, principal_id: uuid.UUID, workspace_id: uuid.UUID) -> AgentPrincipalModel:
        """Workspace-scoped read; missing and foreign rows are indistinguishable."""
        principal = await self._session.get(AgentPrincipalModel, principal_id)
        if principal is None or principal.deleted or principal.workspace_id != workspace_id:
            raise PrincipalNotFoundError(f"principal {principal_id} not found in workspace")
        return principal

    async def get_active_for_agent(self, agent_id: uuid.UUID) -> AgentPrincipalModel | None:
        """Live principal of an agent, whatever its lifecycle; ``None`` if unregistered."""
        principal = (
            await self._session.execute(
                select(AgentPrincipalModel).where(
                    AgentPrincipalModel.agent_id == agent_id, AgentPrincipalModel.deleted.is_(False)
                )
            )
        ).scalar_one_or_none()
        return principal

    async def _audit(
        self,
        *,
        workspace_id: uuid.UUID,
        actor: uuid.UUID | None,
        action: str,
        resource_id: uuid.UUID,
        detail: dict[str, str],
    ) -> None:
        await registry_audit(
            self._session,
            workspace_id=workspace_id,
            actor=actor,
            action=action,
            resource_type="agent_principal",
            resource_id=resource_id,
            detail=detail,
        )
