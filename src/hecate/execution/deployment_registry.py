"""Single-writer registration service for agent deployments (plan step4).

The only reader/writer of ``agent_deployments``: other domains must not
import the model directly (enforced by
``tests/test_layering_domain.py::TestDeploymentRegistryTablesAreExecutionOwned``).
Registration validates the capability snapshot against the execution
contract (``BackendCapabilities``), keeps the denormalized axes columns
consistent with it, records hosted-backend dual-axis configuration with an
explicit ``unverified`` sentinel, and enforces at most one default
deployment per agent. Workspace isolation is enforced on every read by
joining the owning agent - a missing and a foreign deployment are
indistinguishable. Implementation-language metadata is recorded but never
consulted for authorization or capability decisions.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.capabilities import BackendCapabilities, OwnershipAxes
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.execution.principal_registry import PrincipalRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import (
    AccessLevel,
    AccessMode,
    AgentDeploymentModel,
    BackendType,
    HealthState,
)
from hecate.models.agent_principal import PrincipalLifecycle
from hecate.models.agent_version import AgentVersionModel
from hecate.models.audit import AuditLogModel

UNVERIFIED_HOSTED_CONFIG: dict[str, str] = {"verification": "unverified"}


class DeploymentRegistryError(Exception):
    """Base error for deployment registration failures."""


class DeploymentNotFoundError(DeploymentRegistryError):
    """Raised for missing deployments and cross-workspace reads alike."""


class DeploymentValidationError(DeploymentRegistryError):
    """Raised when snapshot/axes/hosted configuration data is invalid."""


class DeploymentRegistry:
    """The only writer of ``agent_deployments``."""

    def __init__(self, session: AsyncSession, principals: PrincipalRegistry | None = None) -> None:
        self._session = session
        self._principals = principals or PrincipalRegistry(session)

    async def register(
        self,
        agent_id: uuid.UUID,
        backend_type: BackendType,
        access_mode: AccessMode,
        axes: OwnershipAxes,
        *,
        agent_version_id: uuid.UUID | None = None,
        issuer_domain: str = "hecate",
        capability_snapshot: dict | None = None,
        access_level: AccessLevel = AccessLevel.UNVERIFIED,
        hosted_config: dict | None = None,
        is_default: bool = False,
        backend_version: str | None = None,
        endpoint: str | None = None,
        config_ref: str | None = None,
        credential_ref: str | None = None,
        transport_contract_version: str | None = None,
        implementation_language: str | None = None,
        registered_by: uuid.UUID | None = None,
    ) -> AgentDeploymentModel:
        """Register one AgentVersion-to-backend binding.

        ``agent_version_id`` defaults to the agent's published version, then
        its latest version; an agent with no versions cannot be registered.
        The snapshot (when provided) must round-trip through the execution
        contract and agree with ``axes``; hosted deployments without
        verifiable dual-axis data are stored with the ``unverified``
        sentinel and never inherit private-deployment or enforced semantics.
        """
        agent = await self._session.get(AgentModel, agent_id)
        if agent is None or agent.deleted:
            raise DeploymentNotFoundError(f"agent {agent_id} not found")

        principal = await self._principals.get_active_for_agent(agent_id)
        if principal is not None and principal.lifecycle is PrincipalLifecycle.REVOKED:
            raise DeploymentRegistryError(f"agent {agent_id} principal is revoked; new deployments are refused")

        version_id = agent_version_id or await self._resolve_version(agent)
        snapshot = await self._validated_snapshot(capability_snapshot, axes, backend_type)

        if backend_type is BackendType.HOSTED:
            stored_hosted_config = self._validated_hosted_config(hosted_config)
        elif hosted_config is not None:
            raise DeploymentValidationError("hosted_config is only valid for hosted backend deployments")
        else:
            stored_hosted_config = None

        deployment = AgentDeploymentModel(
            agent_id=agent_id,
            agent_version_id=version_id,
            workspace_id=agent.workspace_id,
            backend_type=backend_type,
            backend_version=backend_version,
            access_mode=access_mode,
            transport_contract_version=transport_contract_version,
            issuer_domain=issuer_domain,
            endpoint=endpoint,
            config_ref=config_ref,
            credential_ref=credential_ref,
            capability_snapshot=snapshot,
            axes_harness=axes.harness.value,
            axes_environment=axes.environment.value,
            axes_tool_execution=axes.tool_execution.value,
            access_level=access_level,
            health=HealthState.UNKNOWN,
            implementation_language=implementation_language,
            hosted_config=stored_hosted_config,
            is_default=False,
            registered_by=registered_by,
        )
        self._session.add(deployment)
        await self._session.flush()
        if is_default:
            await self.set_default(agent_id, deployment.id, actor_id=registered_by)
        self._audit(
            workspace_id=agent.workspace_id,
            actor=registered_by,
            action="AGENT_DEPLOYMENT_REGISTERED",
            resource_id=deployment.id,
            detail={
                "agent_id": str(agent_id),
                "agent_version_id": str(version_id),
                "backend_type": backend_type.value,
                "access_level": access_level.value,
            },
        )
        return deployment

    async def set_default(
        self,
        agent_id: uuid.UUID,
        deployment_id: uuid.UUID,
        *,
        actor_id: uuid.UUID | None = None,
    ) -> None:
        """Make ``deployment_id`` the agent's only default deployment."""
        deployment = await self.get_for_workspace(deployment_id, await self._agent_workspace(agent_id))
        if deployment.agent_id != agent_id:
            raise DeploymentValidationError(f"deployment {deployment_id} does not belong to agent {agent_id}")
        await self._session.execute(
            update(AgentDeploymentModel)
            .where(AgentDeploymentModel.agent_id == agent_id, AgentDeploymentModel.is_default.is_(True))
            .values(is_default=False)
        )
        deployment.is_default = True
        await self._session.flush()
        self._audit(
            workspace_id=deployment.workspace_id,
            actor=actor_id,
            action="AGENT_DEPLOYMENT_DEFAULT_SET",
            resource_id=deployment.id,
            detail={"agent_id": str(agent_id)},
        )

    async def get_for_workspace(self, deployment_id: uuid.UUID, workspace_id: uuid.UUID) -> AgentDeploymentModel:
        """Workspace-scoped read; missing and foreign rows are indistinguishable."""
        deployment = await self._session.get(AgentDeploymentModel, deployment_id)
        if deployment is None or deployment.deleted or deployment.workspace_id != workspace_id:
            raise DeploymentNotFoundError(f"deployment {deployment_id} not found in workspace")
        return deployment

    async def list_for_agent(self, agent_id: uuid.UUID, workspace_id: uuid.UUID) -> list[AgentDeploymentModel]:
        """All live deployments of an agent inside one workspace."""
        rows = (
            (
                await self._session.execute(
                    select(AgentDeploymentModel).where(
                        AgentDeploymentModel.agent_id == agent_id,
                        AgentDeploymentModel.workspace_id == workspace_id,
                        AgentDeploymentModel.deleted.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    def to_backend_ref(self, deployment: AgentDeploymentModel) -> BackendRef:
        """Resolve the row to its contract ``deployment`` BackendRef."""
        return BackendRef(RefKind.DEPLOYMENT, deployment.issuer_domain, str(deployment.id))

    def to_axes(self, deployment: AgentDeploymentModel) -> OwnershipAxes:
        """Resolve the denormalized axes columns back to the contract type."""
        return OwnershipAxes.from_dict(
            {
                "harness": deployment.axes_harness,
                "environment": deployment.axes_environment,
                "tool_execution": deployment.axes_tool_execution,
            }
        )

    async def _resolve_version(self, agent: AgentModel) -> uuid.UUID:
        if agent.published_version is not None:
            row = (
                await self._session.execute(
                    select(AgentVersionModel).where(
                        AgentVersionModel.agent_id == agent.id,
                        AgentVersionModel.version == agent.published_version,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                return row.id
        row = (
            await self._session.execute(
                select(AgentVersionModel)
                .where(AgentVersionModel.agent_id == agent.id)
                .order_by(AgentVersionModel.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise DeploymentValidationError(f"agent {agent.id} has no versions to deploy")
        return row.id

    @staticmethod
    async def _validated_snapshot(
        capability_snapshot: dict | None, axes: OwnershipAxes, backend_type: BackendType
    ) -> dict:
        if capability_snapshot is None:
            return {
                "contract_version": "0.1",
                "backend_type": backend_type.value,
                "ownership": axes.to_dict(),
            }
        try:
            parsed = BackendCapabilities.from_dict(capability_snapshot)
        except (KeyError, TypeError, ValueError) as exc:
            raise DeploymentValidationError(f"capability snapshot does not round-trip: {exc}") from exc
        if parsed.ownership != axes:
            raise DeploymentValidationError("capability snapshot ownership disagrees with the axes columns")
        return capability_snapshot

    @staticmethod
    def _validated_hosted_config(hosted_config: dict | None) -> dict:
        if hosted_config is None:
            return dict(UNVERIFIED_HOSTED_CONFIG)
        if not isinstance(hosted_config, dict):
            raise DeploymentValidationError("hosted_config must be a JSON object")
        verification = hosted_config.get("verification")
        if not isinstance(verification, str) or not verification:
            # No verifiable statement: keep the explicit sentinel rather
            # than an implicit default that reads as private/enforced.
            return {**hosted_config, **UNVERIFIED_HOSTED_CONFIG}
        return hosted_config

    async def _agent_workspace(self, agent_id: uuid.UUID) -> uuid.UUID:
        agent = await self._session.get(AgentModel, agent_id)
        if agent is None or agent.deleted:
            raise DeploymentNotFoundError(f"agent {agent_id} not found")
        return agent.workspace_id

    def _audit(
        self,
        *,
        workspace_id: uuid.UUID,
        actor: uuid.UUID | None,
        action: str,
        resource_id: uuid.UUID,
        detail: dict[str, str],
    ) -> None:
        self._session.add(
            AuditLogModel(
                org_id=workspace_id,
                workspace_id=workspace_id,
                user_id=actor or uuid.UUID(int=0),
                action=action,
                resource_type="agent_deployment",
                resource_id=resource_id,
                success=True,
                metadata_=detail,
            )
        )
