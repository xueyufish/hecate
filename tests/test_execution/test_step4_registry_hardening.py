"""Adversarial registration tests for Step4 identity and mapping boundaries."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from hecate.contracts.execution.capabilities import BackendCapabilities
from hecate.contracts.execution.references import authorization_ref, deployment_ref, run_ref, session_ref
from hecate.execution.deployment_registry import (
    DeploymentNotFoundError,
    DeploymentRegistry,
    DeploymentRegistryError,
    DeploymentValidationError,
)
from hecate.execution.principal_registry import PrincipalRegistry, PrincipalRegistryError
from hecate.execution.task_run_registry import (
    BackendSessionConflictError,
    TaskNotFoundError,
    TaskRunRegistry,
    TaskRunValidationError,
)
from hecate.models.agent_deployment import AccessLevel, AccessMode, BackendType
from hecate.models.agent_principal import AgentPrincipalModel, PrincipalLifecycle
from hecate.models.audit import AuditLogModel
from hecate.models.conversation import ConversationModel
from hecate.models.run import RunModel
from hecate.models.session import SessionModel
from hecate.models.user import UserModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from tests.test_execution.test_deployment_registries import (
    BUILTIN_AXES,
    HOSTED_AXES,
    OTHER_WS,
    ZERO_WS,
    _agent,
    _org_and_user,
    _version,
)
from tests.test_execution.test_task_run_registry import _chain, _deployment, _task


@pytest.fixture(autouse=True)
def _tenant_context(default_workspace):
    """Create the existing organization/workspace hierarchy for each case."""


async def test_principal_cannot_override_workspace_or_organization(db_session, default_org) -> None:
    agent = await _agent(db_session)
    org, user = await _org_and_user(db_session)
    registry = PrincipalRegistry(db_session)
    with pytest.raises(PrincipalRegistryError, match="not found in workspace"):
        await registry.register(agent.id, org.id, user.id, workspace_id=OTHER_WS)
    with pytest.raises(PrincipalRegistryError, match="organization does not belong"):
        await registry.register(agent.id, default_org.id, user.id)


async def test_principal_requires_owner_membership_and_paired_idp(db_session) -> None:
    agent = await _agent(db_session)
    org, user = await _org_and_user(db_session)
    outsider = UserModel(email="outsider@example.com", hashed_password=uuid.uuid4().hex)
    db_session.add(outsider)
    await db_session.flush()
    registry = PrincipalRegistry(db_session)
    with pytest.raises(PrincipalRegistryError, match="owner does not belong"):
        await registry.register(agent.id, org.id, outsider.id)
    with pytest.raises(PrincipalRegistryError, match="provided together"):
        await registry.register(agent.id, org.id, user.id, identity_provider="corp")


async def test_transition_and_audit_use_real_tenant(db_session) -> None:
    agent = await _agent(db_session)
    org, user = await _org_and_user(db_session)
    registry = PrincipalRegistry(db_session)
    principal = await registry.register(agent.id, org.id, user.id)
    with pytest.raises(PrincipalRegistryError, match="not found in workspace"):
        await registry.transition(principal.id, PrincipalLifecycle.REVOKED, workspace_id=OTHER_WS)
    assert principal.lifecycle is PrincipalLifecycle.ACTIVE
    audit = (
        await db_session.execute(
            select(AuditLogModel).where(
                AuditLogModel.action == "AGENT_PRINCIPAL_REGISTERED",
            )
        )
    ).scalar_one()
    assert audit.org_id == org.id != ZERO_WS


async def test_deployment_rejects_foreign_version_and_context(db_session) -> None:
    agent = await _agent(db_session)
    other = await _agent(db_session)
    version = await _version(db_session, other)
    registry = DeploymentRegistry(db_session)
    with pytest.raises(DeploymentValidationError, match="version does not belong"):
        await registry.register(
            agent.id,
            BackendType.SELF_HOSTED,
            AccessMode.REMOTE_SERVICE,
            BUILTIN_AXES,
            agent_version_id=version.id,
            workspace_id=ZERO_WS,
        )
    with pytest.raises(DeploymentNotFoundError, match="not found in workspace"):
        await registry.register(
            agent.id, BackendType.BUILTIN, AccessMode.IN_PROCESS, BUILTIN_AXES, workspace_id=OTHER_WS
        )


async def test_default_snapshot_roundtrips_and_rejects_mismatched_backend(db_session) -> None:
    agent = await _agent(db_session)
    await _version(db_session, agent)
    registry = DeploymentRegistry(db_session)
    deployment = await registry.register(
        agent.id,
        BackendType.BUILTIN,
        AccessMode.IN_PROCESS,
        BUILTIN_AXES,
        workspace_id=ZERO_WS,
    )
    parsed = BackendCapabilities.from_dict(deployment.capability_snapshot)
    assert parsed.backend_type == "builtin"
    with pytest.raises(DeploymentValidationError, match="backend_type disagrees"):
        await registry.register(
            agent.id,
            BackendType.HOSTED,
            AccessMode.REMOTE_SERVICE,
            BUILTIN_AXES,
            capability_snapshot=deployment.capability_snapshot,
            workspace_id=ZERO_WS,
        )
    with pytest.raises(DeploymentValidationError, match="trusted certification"):
        await registry.register(
            agent.id,
            BackendType.HOSTED,
            AccessMode.REMOTE_SERVICE,
            HOSTED_AXES,
            access_level=AccessLevel.ENFORCED,
            workspace_id=ZERO_WS,
        )
    with pytest.raises(DeploymentValidationError, match="trusted certification"):
        await registry.register(
            agent.id,
            BackendType.HOSTED,
            AccessMode.REMOTE_SERVICE,
            HOSTED_AXES,
            access_level=AccessLevel.COOPERATIVE,
            workspace_id=ZERO_WS,
        )


async def test_default_database_backstop_and_revocation_rejection_audit(db_session) -> None:
    agent = await _agent(db_session)
    await _version(db_session, agent)
    registry = DeploymentRegistry(db_session)
    first = await registry.register(
        agent.id,
        BackendType.BUILTIN,
        AccessMode.IN_PROCESS,
        BUILTIN_AXES,
        workspace_id=ZERO_WS,
        is_default=True,
    )
    second = await registry.register(
        agent.id,
        BackendType.SELF_HOSTED,
        AccessMode.LOCAL_PROCESS,
        BUILTIN_AXES,
        workspace_id=ZERO_WS,
    )
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            second.is_default = True
            await db_session.flush()
    assert first.is_default
    org, user = await _org_and_user(db_session)
    principal = await PrincipalRegistry(db_session).register(agent.id, org.id, user.id)
    await PrincipalRegistry(db_session).transition(principal.id, PrincipalLifecycle.REVOKED, workspace_id=ZERO_WS)
    with pytest.raises(DeploymentRegistryError, match="revoked"):
        await registry.register(
            agent.id, BackendType.HOSTED, AccessMode.REMOTE_SERVICE, HOSTED_AXES, workspace_id=ZERO_WS
        )
    rejection = (
        await db_session.execute(
            select(AuditLogModel).where(
                AuditLogModel.action == "AGENT_DEPLOYMENT_REJECTED",
            )
        )
    ).scalar_one()
    assert rejection.success is False
    assert rejection.org_id == org.id


@pytest.mark.parametrize("mismatch", ["deployment", "principal", "audience", "suspended"])
async def test_run_rejects_inconsistent_identity_before_allocation(db_session, mismatch) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    chain = _chain(deployment)
    if mismatch == "deployment":
        chain = replace(chain, workload=replace(chain.workload, deployment=deployment_ref("foreign", "other")))
    elif mismatch == "principal":
        chain = replace(chain, principal_id=str(uuid.uuid4()))
    elif mismatch == "audience":
        chain = replace(chain, audience=None)
    else:
        principal = await db_session.get(AgentPrincipalModel, deployment.agent_id)
        principal.lifecycle = PrincipalLifecycle.SUSPENDED
    with pytest.raises(TaskRunValidationError):
        await registry.create_run(
            task_id=task.id,
            workspace_id=ZERO_WS,
            deployment_id=deployment.id,
            identity_chain=chain,
            backend_run_ref=run_ref("backend", "r-1"),
        )
    assert await registry.list_runs_for_task(task.id, ZERO_WS) == []


async def test_run_freezes_configuration_and_failed_projection_is_atomic(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    deployment.config_ref = "config-v1"
    deployment.capability_snapshot = {"nested": {"value": "old"}}
    task = await _task(db_session, registry)
    chain = replace(_chain(deployment), on_behalf_of=authorization_ref("hecate", "grant-1"))
    run = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=chain,
        backend_run_ref=run_ref("backend", "r-1"),
    )
    deployment.config_ref = "config-v2"
    deployment.capability_snapshot["nested"]["value"] = "new"
    assert run.execution_snapshot["config_ref"] == "config-v1"
    assert run.execution_snapshot["capability_snapshot"]["nested"]["value"] == "old"
    assert run.identity_chain["audience"] == "enterprise-tools"
    assert run.control_owner == "platform"
    await registry.update_projection(run.id, ZERO_WS, projection={"state": "old"}, event_cursor=3)
    with pytest.raises(TaskRunValidationError):
        await registry.update_projection(run.id, ZERO_WS, projection={"state": "bad"}, event_cursor=2)
    await db_session.flush()
    assert run.projection == {"state": "old"}


async def test_session_binding_cannot_be_replaced_or_released_by_soft_delete(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    first = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "r-1"),
    )
    await registry.bind_backend_session(first.id, ZERO_WS, session_ref=session_ref("vendor", "s-1"))
    with pytest.raises(BackendSessionConflictError, match="overwritten"):
        await registry.bind_backend_session(first.id, ZERO_WS, session_ref=session_ref("vendor", "s-2"))
    first.deleted = True
    second = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "r-2"),
    )
    with pytest.raises(BackendSessionConflictError, match="already bound"):
        await registry.bind_backend_session(second.id, ZERO_WS, session_ref=session_ref("vendor", "s-1"))
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            second.backend_session_issuer = "vendor"
            second.backend_session_id = "s-1"
            second.backend_session = {"id": "s-1", "kind": "turn", "issuer_domain": "vendor"}
            await db_session.flush()


async def test_local_import_is_idempotent_and_issuer_scoped(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    kwargs = dict(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("host", "r-1"),
        local_source={"issuer_domain": "host", "local_run_id": "r-1"},
    )
    first = await registry.import_observation_run(**kwargs)
    assert (await registry.import_observation_run(**kwargs)).id == first.id
    kwargs["local_source"] = {"issuer_domain": "another-host", "local_run_id": "r-1"}
    second = await registry.import_observation_run(**kwargs)
    assert second.id != first.id
    assert first.control_owner == "host"
    assert first.execution_snapshot is None  # current configuration is not a historical fact
    assert len((await db_session.execute(select(RunModel))).scalars().all()) == 2


async def test_conversation_and_legacy_session_use_real_same_tenant_entities(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    task = await _task(db_session, registry)
    conversation = ConversationModel(agent_id=uuid.uuid4(), workspace_id=ZERO_WS)
    foreign = ConversationModel(agent_id=uuid.uuid4(), workspace_id=OTHER_WS)
    db_session.add_all([conversation, foreign])
    await db_session.flush()
    session = SessionModel(agent_id=conversation.agent_id, conversation_id=conversation.id, workspace_id=ZERO_WS)
    db_session.add(session)
    await db_session.flush()
    with pytest.raises(TaskNotFoundError):
        await registry.link_conversation(conversation_id=foreign.id, task_id=task.id, workspace_id=ZERO_WS)
    with pytest.raises(TaskNotFoundError):
        await registry.link_conversation(conversation_id=session.id, task_id=task.id, workspace_id=ZERO_WS)
    link = await registry.link_session(session_id=session.id, task_id=task.id, workspace_id=ZERO_WS)
    assert link.conversation_id == conversation.id != session.id
    assert (await registry.link_session(session_id=session.id, task_id=task.id, workspace_id=ZERO_WS)).id == link.id


async def test_untrusted_operator_and_failed_admission_do_not_enable_work(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    enrollment = await registry.register_standalone_enrollment(
        workspace_id=ZERO_WS,
        host_identity_ref={"issuer_domain": "corp", "id": "host"},
        trust_root_ref={"issuer_domain": "corp", "id": "root"},
        installed_versions={"contracts": ["0.1"], "capabilities": {"cancel": "unsupported"}},
    )
    with pytest.raises(TaskRunValidationError, match="administrator"):
        await registry.set_managed_opt_in(
            enrollment.id, ZERO_WS, managed_new_runs=True, operator_id=uuid.uuid4(), admitted=True
        )
    user = UserModel(email="reviewer@example.com", hashed_password=uuid.uuid4().hex)
    db_session.add(user)
    await db_session.flush()
    db_session.add(WorkspaceMemberModel(user_id=user.id, workspace_id=ZERO_WS, role=WorkspaceRole.ADMIN))
    await db_session.flush()
    with pytest.raises(TaskRunValidationError, match="require admitted"):
        await registry.set_managed_opt_in(
            enrollment.id, ZERO_WS, managed_new_runs=True, operator_id=user.id, admitted=False
        )
    with pytest.raises(TaskRunValidationError, match="trusted resolver"):
        await registry.set_managed_opt_in(
            enrollment.id, ZERO_WS, managed_new_runs=True, operator_id=user.id, admitted=True
        )
    assert not enrollment.managed_new_runs
    rejections = (
        (
            await db_session.execute(
                select(AuditLogModel).where(
                    AuditLogModel.action == "STANDALONE_ENROLLMENT_REJECTED",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rejections) == 3 and all(not audit.success for audit in rejections)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            enrollment.managed_new_runs = True
            await db_session.flush()
