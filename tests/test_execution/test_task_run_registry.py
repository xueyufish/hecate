"""Registry-service and model tests for the task-run single-writer layer.

Covers the ``task-run`` spec scenarios: task responsibility fields without
execution state, retry-as-new-run with frozen identity chains, backend
session uniqueness, projection writes never touching executor facts,
imported observations gaining no rights, trust-root-validated enrollment
with operator-gated opt-in, lazy conversation links with governance-pending
handling, workspace isolation, and the unique-index backstops.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from hecate.contracts.execution.references import run_ref, session_ref, turn_ref
from hecate.execution.task_run_registry import (
    BackendSessionConflictError,
    TaskNotFoundError,
    TaskRunRegistry,
    TaskRunValidationError,
)
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.audit import AuditLogModel
from hecate.models.conversation import ConversationModel
from hecate.models.conversation_link import ConversationTaskLinkModel, GovernanceStatus
from hecate.models.run import RunOrigin
from hecate.models.standalone_enrollment import AdmissionResult, StandaloneEnrollmentModel
from hecate.models.task import TaskModel
from hecate.models.user import UserModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole


@pytest.fixture(autouse=True)
def _tenant_context(default_workspace):
    """Use real tenant records for registration and audit."""


ZERO_WS = uuid.UUID("00000000-0000-0000-0000-000000000000")
OTHER_WS = uuid.UUID("00000000-0000-0000-0000-000000000001")

_INITIATOR = {
    "initiator": "user-1",
    "principal_id": "principal-1",
    "workload": {
        "deployment": {"kind": "deployment", "issuer_domain": "hecate", "id": "d-1"},
        "workload_id": "workload-1",
    },
}


def _chain(deployment: AgentDeploymentModel | None = None, principal: str | None = None):
    from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
    from hecate.contracts.execution.references import deployment_ref

    return IdentityChain(
        initiator="user-1",
        principal_id=principal or (str(deployment.agent_id) if deployment else "principal-1"),
        workload=WorkloadIdentity(
            deployment=deployment_ref(
                deployment.issuer_domain if deployment else "hecate", str(deployment.id) if deployment else "d-1"
            ),
            workload_id="w-1",
        ),
        audience="enterprise-tools",
    )


async def _deployment(db_session) -> AgentDeploymentModel:
    """One live builtin deployment to attach runs to."""
    agent = AgentModel(workspace_id=ZERO_WS, name=f"agent-{uuid.uuid4().hex[:8]}")
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
    user = UserModel(email=f"owner-{agent.id}@example.com", hashed_password=uuid.uuid4().hex)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        AgentPrincipalModel(
            id=agent.id,
            agent_id=agent.id,
            workspace_id=ZERO_WS,
            organization_id=ZERO_WS,
            owner_user_id=user.id,
        )
    )
    await db_session.flush()
    deployment = AgentDeploymentModel(
        agent_id=agent.id,
        agent_version_id=version.id,
        workspace_id=ZERO_WS,
        backend_type=BackendType.BUILTIN,
        access_mode=AccessMode.IN_PROCESS,
        issuer_domain="hecate",
        capability_snapshot={},
        axes_harness="hecate",
        axes_environment="none",
        axes_tool_execution="hecate_gateway",
    )
    db_session.add(deployment)
    await db_session.flush()
    return deployment


async def _task(db_session, registry: TaskRunRegistry, **overrides) -> TaskModel:
    kwargs = dict(
        goal="produce the quarterly summary",
        initiator_ref=dict(_INITIATOR),
        workspace_id=ZERO_WS,
    )
    kwargs.update(overrides)
    return await registry.create_task(**kwargs)


# --- task ---------------------------------------------------------------


async def test_task_records_responsibility_without_execution_state(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    task = await _task(db_session, registry, acceptance={"done": "summary published"}, responsibility="team-a")

    stored = await registry.get_task(task.id, ZERO_WS)
    assert stored.goal == "produce the quarterly summary"
    assert stored.acceptance == {"done": "summary published"}
    assert stored.responsibility == "team-a"
    columns = {c.name for c in TaskModel.__table__.columns}
    assert not any("status" in c or "projection" in c for c in columns), "task must not pre-empt step6 state"


async def test_task_rejects_malformed_initiator_chain(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    with pytest.raises(TaskRunValidationError, match="IdentityChain"):
        await registry.create_task(goal="x", initiator_ref={"principal_id": "p"}, workspace_id=ZERO_WS)


async def test_task_cross_workspace_read_is_uniform_not_found(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    task = await _task(db_session, registry)
    with pytest.raises(TaskNotFoundError, match="not found in workspace"):
        await registry.get_task(task.id, OTHER_WS)


# --- run / retry --------------------------------------------------------


async def test_retry_creates_new_run_with_incremented_attempt(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)

    first = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "br-1"),
    )
    second = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "br-2"),
    )

    assert first.attempt_no == 1
    assert second.attempt_no == 2
    assert second.id != first.id
    assert second.backend_session is None, "a retry never inherits the prior session"
    runs = await registry.list_runs_for_task(task.id, ZERO_WS)
    assert [r.attempt_no for r in runs] == [1, 2]


async def test_run_pins_the_identity_chain_snapshot(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)

    run = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "br-1"),
    )
    stored = dict(run.identity_chain)
    # Later delegation change in the caller's world: the frozen row keeps A.
    fresh = _chain(deployment, "principal-B")
    assert stored["principal_id"] == str(deployment.agent_id)
    assert fresh.principal_id == "principal-B"
    from hecate.contracts.execution.identity import IdentityChain

    assert IdentityChain.from_dict(stored).principal_id == str(deployment.agent_id)


async def test_run_rejects_wrong_reference_kind(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    with pytest.raises(ValueError, match="kind mismatch"):
        await registry.create_run(
            task_id=task.id,
            workspace_id=ZERO_WS,
            deployment_id=deployment.id,
            identity_chain=_chain(deployment),
            backend_run_ref=session_ref("backend", "s-1"),
        )


async def test_run_foreign_deployment_is_rejected(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    task = await _task(db_session, registry)
    with pytest.raises(TaskNotFoundError, match="not found in workspace"):
        await registry.create_run(
            task_id=task.id,
            workspace_id=ZERO_WS,
            deployment_id=uuid.uuid4(),
            identity_chain=_chain(),
            backend_run_ref=run_ref("backend", "br-1"),
        )


# --- backend session binding --------------------------------------------


async def test_backend_session_binding_is_unique_across_runs(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    first = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "br-1"),
    )
    second = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "br-2"),
    )

    await registry.bind_backend_session(first.id, ZERO_WS, session_ref=turn_ref("vendor", "turn-9"))
    with pytest.raises(BackendSessionConflictError, match="already bound"):
        await registry.bind_backend_session(second.id, ZERO_WS, session_ref=turn_ref("vendor", "turn-9"))
    # The existing binding is untouched.
    reloaded = await registry.get_run(first.id, ZERO_WS)
    assert reloaded.backend_session["id"] == "turn-9"


async def test_backend_session_rejects_non_session_kinds(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    run = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "br-1"),
    )
    with pytest.raises(TaskRunValidationError, match="session/turn"):
        await registry.bind_backend_session(run.id, ZERO_WS, session_ref=run_ref("backend", "br-9"))


# --- projection ---------------------------------------------------------


async def test_projection_update_touches_only_projection_fields(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    run = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "br-1"),
    )
    frozen_identity = dict(run.identity_chain)
    frozen_backend = dict(run.backend_ref)

    await registry.update_projection(run.id, ZERO_WS, projection={"backend_state": "running"}, event_cursor=3)

    reloaded = await registry.get_run(run.id, ZERO_WS)
    assert reloaded.projection == {"backend_state": "running"}
    assert reloaded.event_cursor == 3
    assert reloaded.identity_chain == frozen_identity
    assert reloaded.backend_ref == frozen_backend


async def test_projection_cursor_never_moves_backwards(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    run = await registry.create_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("backend", "br-1"),
    )
    await registry.update_projection(run.id, ZERO_WS, projection={}, event_cursor=5)
    with pytest.raises(TaskRunValidationError, match="backwards"):
        await registry.update_projection(run.id, ZERO_WS, projection={}, event_cursor=2)


# --- imported observations -----------------------------------------------


async def test_imported_observation_creates_only_its_row(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    before_tasks = len((await db_session.execute(select(TaskModel))).scalars().all())
    before_links = len((await db_session.execute(select(ConversationTaskLinkModel))).scalars().all())

    task = await _task(db_session, registry)
    run = await registry.import_observation_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("local-host", "lr-1"),
        local_source={"issuer_domain": "local-host", "local_run_id": "lr-1"},
    )

    assert run.origin is RunOrigin.IMPORTED_OBSERVATION
    assert run.local_source == {"issuer_domain": "local-host", "local_run_id": "lr-1"}
    # No conversation link, no extra task row: observation creates nothing else.
    assert len((await db_session.execute(select(ConversationTaskLinkModel))).scalars().all()) == before_links
    tasks_after = len((await db_session.execute(select(TaskModel))).scalars().all())
    assert tasks_after == before_tasks + 1  # exactly the task we created above


async def test_imported_observation_requires_local_run_id(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    with pytest.raises(TaskRunValidationError, match="local_run_id"):
        await registry.import_observation_run(
            task_id=task.id,
            workspace_id=ZERO_WS,
            deployment_id=deployment.id,
            identity_chain=_chain(deployment),
            backend_run_ref=run_ref("local-host", "lr-1"),
            local_source={"issuer_domain": "local-host"},
        )


async def test_local_source_dedupe_probe_finds_imported_run(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    deployment = await _deployment(db_session)
    task = await _task(db_session, registry)
    await registry.import_observation_run(
        task_id=task.id,
        workspace_id=ZERO_WS,
        deployment_id=deployment.id,
        identity_chain=_chain(deployment),
        backend_run_ref=run_ref("local-host", "lr-1"),
        local_source={"issuer_domain": "local-host", "local_run_id": "lr-1"},
    )
    found = await registry.find_local_source_run(
        "lr-1", ZERO_WS, issuer_domain="local-host", deployment_id=deployment.id
    )
    assert found is not None
    assert (
        await registry.find_local_source_run(
            "lr-missing",
            ZERO_WS,
            issuer_domain="local-host",
            deployment_id=deployment.id,
        )
        is None
    )


# --- standalone enrollment ------------------------------------------------


async def test_enrollment_requires_resolvable_trust_root(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    with pytest.raises(TaskRunValidationError, match="trust_root_ref"):
        await registry.register_standalone_enrollment(
            workspace_id=ZERO_WS,
            host_identity_ref={"issuer_domain": "corp", "id": "host-1"},
            trust_root_ref={"issuer_domain": ""},
        )


async def test_enrollment_defaults_to_no_managed_runs_and_audits(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    enrollment = await registry.register_standalone_enrollment(
        workspace_id=ZERO_WS,
        host_identity_ref={"issuer_domain": "corp", "id": "host-1"},
        trust_root_ref={"issuer_domain": "corp", "id": "root-1"},
        installed_versions={"contracts": ["0.1"]},
    )
    assert enrollment.managed_new_runs is False
    assert enrollment.admission is AdmissionResult.PENDING
    audit = (
        await db_session.execute(
            select(AuditLogModel).where(AuditLogModel.action == "STANDALONE_ENROLLMENT_REGISTERED")
        )
    ).scalar_one()
    assert audit.resource_id == enrollment.id


async def test_managed_opt_in_requires_operator_and_leaves_audit(db_session) -> None:
    async def resolve(workspace_id, host, root, versions):
        return workspace_id == ZERO_WS and host["id"] == "host-1" and root["id"] == "root-1"

    registry = TaskRunRegistry(db_session, enrollment_resolver=resolve)
    enrollment = await registry.register_standalone_enrollment(
        workspace_id=ZERO_WS,
        host_identity_ref={"issuer_domain": "corp", "id": "host-1"},
        trust_root_ref={"issuer_domain": "corp", "id": "root-1"},
        installed_versions={"contracts": ["0.1"], "capabilities": {"cancel": "unsupported"}},
    )
    user = UserModel(email="admin@example.com", hashed_password=uuid.uuid4().hex)
    db_session.add(user)
    await db_session.flush()
    operator = user.id
    db_session.add(WorkspaceMemberModel(user_id=operator, workspace_id=ZERO_WS, role=WorkspaceRole.ADMIN))
    await db_session.flush()
    await registry.set_managed_opt_in(
        enrollment.id, ZERO_WS, managed_new_runs=True, operator_id=operator, admitted=True
    )
    reloaded = await db_session.get(StandaloneEnrollmentModel, enrollment.id)
    assert reloaded.managed_new_runs is True
    assert reloaded.operator_id == operator
    assert reloaded.admitted_at is not None
    audit = (
        await db_session.execute(select(AuditLogModel).where(AuditLogModel.action == "STANDALONE_ENROLLMENT_ADMITTED"))
    ).scalar_one()
    assert audit.user_id == operator


# --- conversation links ----------------------------------------------------


async def test_conversation_link_is_lazy_idempotent_and_multi_task(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    task_a = await _task(db_session, registry)
    task_b = await _task(db_session, registry)
    conversation_row = ConversationModel(agent_id=uuid.uuid4(), workspace_id=ZERO_WS)
    db_session.add(conversation_row)
    await db_session.flush()
    conversation = conversation_row.id

    first = await registry.link_conversation(conversation_id=conversation, task_id=task_a.id, workspace_id=ZERO_WS)
    again = await registry.link_conversation(conversation_id=conversation, task_id=task_a.id, workspace_id=ZERO_WS)
    assert first.id == again.id  # idempotent per (conversation, task)

    await registry.link_conversation(conversation_id=conversation, task_id=task_b.id, workspace_id=ZERO_WS)
    linked = await registry.list_tasks_for_conversation(conversation, ZERO_WS)
    assert set(linked) == {task_a.id, task_b.id}


async def test_unresolvable_governance_stays_pending(db_session) -> None:
    registry = TaskRunRegistry(db_session)
    task = await _task(db_session, registry)
    conversation = ConversationModel(agent_id=uuid.uuid4(), workspace_id=ZERO_WS)
    db_session.add(conversation)
    await db_session.flush()
    link = await registry.link_conversation(
        conversation_id=conversation.id, task_id=task.id, workspace_id=ZERO_WS, initiator_resolves=False
    )
    assert link.governance_status is GovernanceStatus.PENDING
    # Pending is stored but no platform identity is fabricated: the link has
    # no principal/owner columns at all.
    assert not any("principal" in c.name or "owner" in c.name for c in ConversationTaskLinkModel.__table__.columns)
