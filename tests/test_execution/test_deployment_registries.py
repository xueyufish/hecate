"""Registry-service tests for the deployment/principal single-writer layer.

Covers the ``agent-deployment`` spec scenarios: principal owner resolution,
persona never auto-creating a principal, lifecycle transitions and terminal
revocation, revoked principals blocking new deployments, two backends per
version coexisting, BackendRef resolution, language metadata neutrality,
unverified hosted-config sentinel, snapshot/axes validation, default
uniqueness, registration audit records, and workspace isolation (missing
and foreign rows indistinguishable).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from hecate.contracts.execution.capabilities import (
    EnvironmentOwner,
    HarnessOwner,
    OwnershipAxes,
    ToolExecutionPoint,
)
from hecate.contracts.execution.references import RefKind
from hecate.execution.deployment_registry import (
    DeploymentNotFoundError,
    DeploymentRegistry,
    DeploymentRegistryError,
    DeploymentValidationError,
)
from hecate.execution.principal_registry import (
    PrincipalRegistry,
    PrincipalRegistryError,
    PrincipalTransitionError,
)
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessLevel, AccessMode, BackendType
from hecate.models.agent_principal import PrincipalLifecycle
from hecate.models.agent_version import AgentVersionModel
from hecate.models.audit import AuditLogModel
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel


def hash_password(raw: str) -> str:
    """bcrypt hash in the same shape production stores."""
    import bcrypt

    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


ZERO_WS = uuid.UUID("00000000-0000-0000-0000-000000000000")
OTHER_WS = uuid.UUID("00000000-0000-0000-0000-000000000001")
BUILTIN_AXES = OwnershipAxes(
    harness=HarnessOwner.HECATE,
    environment=EnvironmentOwner.NONE,
    tool_execution=ToolExecutionPoint.HECATE_GATEWAY,
)
HOSTED_AXES = OwnershipAxes(
    harness=HarnessOwner.VENDOR,
    environment=EnvironmentOwner.VENDOR,
    tool_execution=ToolExecutionPoint.VENDOR_INTERNAL,
)


async def _agent(db_session, *, published_version: int | None = None) -> AgentModel:
    agent = AgentModel(workspace_id=ZERO_WS, name=f"agent-{uuid.uuid4().hex[:8]}", published_version=published_version)
    db_session.add(agent)
    await db_session.flush()
    return agent


async def _version(db_session, agent: AgentModel, version: int = 1) -> AgentVersionModel:
    row = AgentVersionModel(
        agent_id=agent.id,
        version=version,
        config_snapshot={"model": "stub"},
        content_hash="a" * 64,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _org_and_user(db_session) -> tuple[OrganizationModel, UserModel]:
    org = OrganizationModel(name="org", slug=f"org-{uuid.uuid4().hex[:8]}", owner_id=uuid.uuid4())
    db_session.add(org)
    await db_session.flush()
    user = UserModel(email=f"owner-{uuid.uuid4().hex[:8]}@example.com", hashed_password=hash_password("securepass123"))
    db_session.add(user)
    await db_session.flush()
    return org, user


# --- principal registry -------------------------------------------------


async def test_register_resolves_owner_and_org(db_session) -> None:
    agent = await _agent(db_session)
    org, user = await _org_and_user(db_session)
    registry = PrincipalRegistry(db_session)

    principal = await registry.register(agent.id, org.id, user.id, registered_by=user.id)

    assert principal.lifecycle is PrincipalLifecycle.ACTIVE
    assert principal.workspace_id == ZERO_WS
    row = (
        await db_session.execute(select(AuditLogModel).where(AuditLogModel.resource_type == "agent_principal"))
    ).scalar_one()
    assert row.action == "AGENT_PRINCIPAL_REGISTERED"


async def test_register_rejects_unresolvable_owner(db_session) -> None:
    agent = await _agent(db_session)
    org, _user = await _org_and_user(db_session)
    registry = PrincipalRegistry(db_session)

    with pytest.raises(PrincipalRegistryError, match="does not resolve"):
        await registry.register(agent.id, org.id, uuid.uuid4())


async def test_register_rejects_second_principal_and_inactive_owner(db_session) -> None:
    agent = await _agent(db_session)
    org, user = await _org_and_user(db_session)
    registry = PrincipalRegistry(db_session)
    await registry.register(agent.id, org.id, user.id)

    with pytest.raises(PrincipalRegistryError, match="already has a live principal"):
        await registry.register(agent.id, org.id, user.id)

    user.active = False
    agent2 = await _agent(db_session)
    with pytest.raises(PrincipalRegistryError, match="active user"):
        await registry.register(agent2.id, org.id, user.id)


async def test_lifecycle_transitions_and_terminal_revocation(db_session) -> None:
    agent = await _agent(db_session)
    org, user = await _org_and_user(db_session)
    registry = PrincipalRegistry(db_session)
    principal = await registry.register(agent.id, org.id, user.id)

    suspended = await registry.transition(principal.id, PrincipalLifecycle.SUSPENDED, actor_id=user.id)
    assert suspended.lifecycle is PrincipalLifecycle.SUSPENDED
    reactivated = await registry.transition(principal.id, PrincipalLifecycle.ACTIVE)
    assert reactivated.lifecycle is PrincipalLifecycle.ACTIVE
    revoked = await registry.transition(principal.id, PrincipalLifecycle.REVOKED)

    with pytest.raises(PrincipalTransitionError, match="not allowed"):
        await registry.transition(principal.id, PrincipalLifecycle.ACTIVE)
    assert revoked.lifecycle is PrincipalLifecycle.REVOKED


async def test_persona_string_never_creates_principal(db_session) -> None:
    agent = await _agent(db_session)
    agent.persona = "helpful trading assistant"
    registry = PrincipalRegistry(db_session)

    # The only registration path demands resolvable org+user; a persona
    # alone cannot produce a principal, and none is auto-created.
    assert await registry.get_active_for_agent(agent.id) is None
    with pytest.raises(PrincipalRegistryError):
        await registry.register(agent.id, uuid.uuid4(), uuid.uuid4())
    assert await registry.get_active_for_agent(agent.id) is None


# --- deployment registry -------------------------------------------------


async def test_same_version_two_backends_and_backend_ref(db_session) -> None:
    agent = await _agent(db_session)
    version = await _version(db_session, agent)
    registry = DeploymentRegistry(db_session)

    builtin = await registry.register(
        agent.id, BackendType.BUILTIN, AccessMode.IN_PROCESS, BUILTIN_AXES, is_default=True
    )
    hosted = await registry.register(
        agent.id,
        BackendType.HOSTED,
        AccessMode.REMOTE_SERVICE,
        HOSTED_AXES,
        agent_version_id=version.id,
        issuer_domain="vendor-x",
        implementation_language="typescript",
    )

    assert builtin.agent_version_id == version.id  # published_version=None -> latest
    ref = registry.to_backend_ref(hosted)
    assert ref.kind is RefKind.DEPLOYMENT
    assert ref.issuer_domain == "vendor-x"
    assert ref.id == str(hosted.id)
    assert registry.to_axes(hosted) == HOSTED_AXES

    rows = await registry.list_for_agent(agent.id, ZERO_WS)
    assert len(rows) == 2


async def test_language_metadata_does_not_change_standing(db_session) -> None:
    agent = await _agent(db_session)
    await _version(db_session, agent)
    registry = DeploymentRegistry(db_session)

    py_deploy = await registry.register(
        agent.id,
        BackendType.SELF_HOSTED,
        AccessMode.LOCAL_PROCESS,
        BUILTIN_AXES,
        issuer_domain="site-a",
        implementation_language="python",
    )
    rs_deploy = await registry.register(
        agent.id,
        BackendType.SELF_HOSTED,
        AccessMode.LOCAL_PROCESS,
        BUILTIN_AXES,
        issuer_domain="site-b",
        implementation_language="rust",
    )

    assert py_deploy.access_level is AccessLevel.UNVERIFIED
    assert rs_deploy.access_level is AccessLevel.UNVERIFIED
    assert py_deploy.access_level == rs_deploy.access_level


async def test_hosted_config_unverified_sentinel_and_validation(db_session) -> None:
    agent = await _agent(db_session)
    await _version(db_session, agent)
    registry = DeploymentRegistry(db_session)

    none_config = await registry.register(
        agent.id, BackendType.HOSTED, AccessMode.REMOTE_SERVICE, HOSTED_AXES, issuer_domain="vendor-a"
    )
    assert none_config.hosted_config == {"verification": "unverified"}

    silent_config = await registry.register(
        agent.id,
        BackendType.HOSTED,
        AccessMode.REMOTE_SERVICE,
        HOSTED_AXES,
        issuer_domain="vendor-b",
        hosted_config={"region": "us"},
    )
    assert silent_config.hosted_config["verification"] == "unverified"

    verified_config = await registry.register(
        agent.id,
        BackendType.HOSTED,
        AccessMode.REMOTE_SERVICE,
        HOSTED_AXES,
        issuer_domain="vendor-c",
        hosted_config={"region": "us", "verification": "vendor-dpa-2026-09", "residency": "us"},
    )
    assert verified_config.hosted_config["verification"] == "vendor-dpa-2026-09"

    with pytest.raises(DeploymentValidationError, match="JSON object"):
        await registry.register(
            agent.id,
            BackendType.HOSTED,
            AccessMode.REMOTE_SERVICE,
            HOSTED_AXES,
            issuer_domain="vendor-d",
            hosted_config="us-only",  # type: ignore[arg-type]
        )
    with pytest.raises(DeploymentValidationError, match="only valid for hosted"):
        await registry.register(
            agent.id, BackendType.BUILTIN, AccessMode.IN_PROCESS, BUILTIN_AXES, hosted_config={"region": "us"}
        )


async def test_snapshot_validation_rejects_bad_shape_and_axis_mismatch(db_session) -> None:
    agent = await _agent(db_session)
    await _version(db_session, agent)
    registry = DeploymentRegistry(db_session)

    with pytest.raises(DeploymentValidationError, match="round-trip"):
        await registry.register(
            agent.id,
            BackendType.BUILTIN,
            AccessMode.IN_PROCESS,
            BUILTIN_AXES,
            capability_snapshot={"contract_version": "0.1", "ownership": {"harness": "bogus"}},
        )

    conflicting = {
        "contract_version": "0.1",
        "backend_type": "builtin",
        "ownership": HOSTED_AXES.to_dict(),
        "capabilities": {
            name: "unsupported" for name in ("provide_input", "resolve_approval", "pause", "resume", "export_context")
        },
    }
    with pytest.raises(DeploymentValidationError, match="disagrees"):
        await registry.register(
            agent.id, BackendType.BUILTIN, AccessMode.IN_PROCESS, BUILTIN_AXES, capability_snapshot=conflicting
        )


async def test_default_flag_is_unique_per_agent_and_audited(db_session) -> None:
    agent = await _agent(db_session)
    await _version(db_session, agent)
    registry = DeploymentRegistry(db_session)
    first = await registry.register(agent.id, BackendType.BUILTIN, AccessMode.IN_PROCESS, BUILTIN_AXES, is_default=True)
    second = await registry.register(agent.id, BackendType.SELF_HOSTED, AccessMode.LOCAL_PROCESS, BUILTIN_AXES)

    await registry.set_default(agent.id, second.id)

    rows = {row.id: row for row in await registry.list_for_agent(agent.id, ZERO_WS)}
    assert rows[first.id].is_default is False
    assert rows[second.id].is_default is True
    actions = {
        row.action
        for row in (
            await db_session.execute(select(AuditLogModel).where(AuditLogModel.resource_type == "agent_deployment"))
        ).scalars()
    }
    assert {"AGENT_DEPLOYMENT_REGISTERED", "AGENT_DEPLOYMENT_DEFAULT_SET"} <= actions


async def test_revoked_principal_blocks_new_deployments(db_session) -> None:
    agent = await _agent(db_session)
    await _version(db_session, agent)
    org, user = await _org_and_user(db_session)
    principals = PrincipalRegistry(db_session)
    principal = await principals.register(agent.id, org.id, user.id)
    await principals.transition(principal.id, PrincipalLifecycle.REVOKED)

    registry = DeploymentRegistry(db_session)
    with pytest.raises(DeploymentRegistryError, match="revoked"):
        await registry.register(agent.id, BackendType.BUILTIN, AccessMode.IN_PROCESS, BUILTIN_AXES)


async def test_workspace_isolation_is_indistinguishable(db_session) -> None:
    agent = await _agent(db_session)
    await _version(db_session, agent)
    registry = DeploymentRegistry(db_session)
    deployment = await registry.register(agent.id, BackendType.BUILTIN, AccessMode.IN_PROCESS, BUILTIN_AXES)

    foreign = await registry.get_for_workspace(deployment.id, ZERO_WS)
    assert foreign.id == deployment.id
    with pytest.raises(DeploymentNotFoundError):
        await registry.get_for_workspace(deployment.id, OTHER_WS)
    with pytest.raises(DeploymentNotFoundError):
        await registry.get_for_workspace(uuid.uuid4(), ZERO_WS)
    assert await registry.list_for_agent(agent.id, OTHER_WS) == []


async def test_agent_without_versions_cannot_register(db_session) -> None:
    agent = await _agent(db_session)
    registry = DeploymentRegistry(db_session)
    with pytest.raises(DeploymentValidationError, match="no versions"):
        await registry.register(agent.id, BackendType.BUILTIN, AccessMode.IN_PROCESS, BUILTIN_AXES)
