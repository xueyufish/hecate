"""Model-level tests for AgentPrincipalModel / AgentDeploymentModel.

Covers the persistence contract before the registry service lands (tasks
1.2): table names, uniqueness, enum columns, FK wiring, and the columns the
spec's scenarios rely on (default-deployment marker, hosted_config sentinel
shape is enforced at the registry layer, not here).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import (
    AccessLevel,
    AccessMode,
    AgentDeploymentModel,
    BackendType,
    HealthState,
)
from hecate.models.agent_principal import AgentPrincipalModel, PrincipalLifecycle
from hecate.models.agent_version import AgentVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel


def hash_password(raw: str) -> str:
    """bcrypt hash in the same shape production stores."""
    import bcrypt

    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


ZERO_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


async def _agent_with_version(db_session) -> tuple[AgentModel, AgentVersionModel]:
    agent = AgentModel(workspace_id=ZERO_WORKSPACE, name="dep-test-agent")
    db_session.add(agent)
    await db_session.flush()
    version = AgentVersionModel(
        agent_id=agent.id,
        version=1,
        config_snapshot={"model": "stub"},
        content_hash="a" * 64,
    )
    db_session.add(version)
    await db_session.flush()
    return agent, version


async def test_principal_and_deployment_round_trip(db_session) -> None:
    agent, version = await _agent_with_version(db_session)
    org = OrganizationModel(name="org", slug=f"org-{uuid.uuid4().hex[:8]}", owner_id=uuid.uuid4())
    db_session.add(org)
    await db_session.flush()
    user = UserModel(
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("securepass123"),
    )
    db_session.add(user)
    await db_session.flush()

    principal = AgentPrincipalModel(
        agent_id=agent.id,
        workspace_id=ZERO_WORKSPACE,
        organization_id=org.id,
        owner_user_id=user.id,
        lifecycle=PrincipalLifecycle.ACTIVE,
        registered_by=user.id,
    )
    db_session.add(principal)
    deployment = AgentDeploymentModel(
        agent_id=agent.id,
        agent_version_id=version.id,
        workspace_id=ZERO_WORKSPACE,
        backend_type=BackendType.BUILTIN,
        access_mode=AccessMode.IN_PROCESS,
        axes_harness="hecate",
        axes_environment="none",
        axes_tool_execution="hecate_gateway",
        issuer_domain="hecate",
        capability_snapshot={"contract_version": "0.1", "backend_type": "builtin"},
        access_level=AccessLevel.UNVERIFIED,
        health=HealthState.UNKNOWN,
        is_default=True,
    )
    db_session.add(deployment)
    await db_session.flush()

    loaded_principal = (
        await db_session.execute(select(AgentPrincipalModel).where(AgentPrincipalModel.agent_id == agent.id))
    ).scalar_one()
    assert loaded_principal.lifecycle is PrincipalLifecycle.ACTIVE

    loaded = (
        await db_session.execute(select(AgentDeploymentModel).where(AgentDeploymentModel.agent_id == agent.id))
    ).scalar_one()
    assert loaded.backend_type is BackendType.BUILTIN
    assert loaded.is_default is True
    assert loaded.issuer_domain == "hecate"


async def test_duplicate_builtin_deployment_for_same_version_rejected(db_session) -> None:
    agent, version = await _agent_with_version(db_session)
    first = AgentDeploymentModel(
        agent_id=agent.id,
        agent_version_id=version.id,
        workspace_id=ZERO_WORKSPACE,
        backend_type=BackendType.BUILTIN,
        access_mode=AccessMode.IN_PROCESS,
        axes_harness="hecate",
        axes_environment="none",
        axes_tool_execution="hecate_gateway",
        issuer_domain="hecate",
        capability_snapshot={},
    )
    duplicate = AgentDeploymentModel(
        agent_id=agent.id,
        agent_version_id=version.id,
        workspace_id=ZERO_WORKSPACE,
        backend_type=BackendType.BUILTIN,
        access_mode=AccessMode.IN_PROCESS,
        axes_harness="hecate",
        axes_environment="none",
        axes_tool_execution="hecate_gateway",
        issuer_domain="hecate",
        capability_snapshot={},
    )
    db_session.add(first)
    await db_session.flush()
    db_session.add(duplicate)
    with pytest.raises(Exception, match="uq_agent_deployments_builtin_version|UNIQUE"):
        await db_session.flush()


async def test_two_backends_for_same_version_coexist(db_session) -> None:
    agent, version = await _agent_with_version(db_session)
    for backend, domain in ((BackendType.BUILTIN, "hecate"), (BackendType.HOSTED, "vendor-x")):
        db_session.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=ZERO_WORKSPACE,
                backend_type=backend,
                access_mode=AccessMode.REMOTE_SERVICE if backend is BackendType.HOSTED else AccessMode.IN_PROCESS,
                axes_harness="vendor" if backend is BackendType.HOSTED else "hecate",
                axes_environment="vendor" if backend is BackendType.HOSTED else "none",
                axes_tool_execution="vendor_internal" if backend is BackendType.HOSTED else "hecate_gateway",
                issuer_domain=domain,
                capability_snapshot={},
            )
        )
    await db_session.flush()
    rows = (
        (await db_session.execute(select(AgentDeploymentModel).where(AgentDeploymentModel.agent_id == agent.id)))
        .scalars()
        .all()
    )
    assert {row.backend_type for row in rows} == {BackendType.BUILTIN, BackendType.HOSTED}
