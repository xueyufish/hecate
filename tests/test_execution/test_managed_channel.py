"""Managed channel API tests: operator surface, host channel, delivery and
projection (task group 2 of managed-runner-enrollment).

Runs the mounted /managed routes through the shared test client over the
stub durable binding: operator routes ride the standard session auth; host
routes ride the deployment-domain HMAC credential. The delivery/projection
flow is exercised end to end — queue → pull → accept → upload with dedup.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.channel.api.managed import router as managed_router
from hecate.core.auth_context import AuthContext
from hecate.core.composition.managed_secrets import clear_managed_secrets, register_managed_secret
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution import managed_credentials as mc
from hecate.execution.task_run_registry import (
    TaskRunRegistry,  # noqa: F401  (registers audit_logs before schema creation)
)
from hecate.execution.trust_roots import TrustRootRegistry
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from tests.conftest import DEFAULT_WORKSPACE_ID, test_session_factory

WS = DEFAULT_WORKSPACE_ID
ADMIN = uuid.uuid4()
ROOT_NAME = "host-root-test"
ISSUER = "managed-test-issuer"
SECRET = b"managed-channel-secret-test"


@pytest.fixture
def secrets():
    register_managed_secret(ISSUER, SECRET)
    yield
    clear_managed_secrets()


@pytest.fixture
def auth_context() -> AuthContext:
    from hecate.models.workspace_member import WorkspaceRole

    return AuthContext(
        user_id=ADMIN,
        org_id=WS,
        workspace_id=WS,
        role=WorkspaceRole.ADMIN,
        auth_method="jwt",
        api_key_scope=None,
    )


@pytest.fixture
async def client(auth_context: AuthContext, secrets):
    app = FastAPI()
    app.include_router(managed_router)

    async def override_get_db():
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_context] = lambda: auth_context
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _seed_workspace(db_session: AsyncSession) -> None:
    from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

    db_session.add(OrganizationModel(id=WS, name="org", slug=f"org-{WS.hex[:12]}", owner_id=ADMIN))
    db_session.add(WorkspaceModel(id=WS, org_id=WS, name="ws", slug=f"ws-{WS.hex}"))
    db_session.add(UserModel(id=ADMIN, email="admin@example.com", hashed_password=uuid.uuid4().hex))
    db_session.add(WorkspaceMemberModel(workspace_id=WS, user_id=ADMIN, role=WorkspaceRole.ADMIN))
    await db_session.commit()


def _auth_header(credential: dict) -> dict[str, str]:
    import json

    return {"Authorization": "ManagedBearer " + json.dumps(credential)}


async def _enroll_host(client: AsyncClient, db_session: AsyncSession, *, admit: bool = True) -> dict:
    """Provision the trust root, register the host, and (optionally) admit it."""

    registry = TrustRootRegistry(db_session)
    await registry.register(
        workspace_id=WS,
        name=ROOT_NAME,
        material_digest=mc.material_digest(SECRET),
        issuer_domain=ISSUER,
        config_fingerprint="cfg-1",
    )
    await db_session.commit()
    nonce = mc.new_nonce()
    host_ref = {"issuer_domain": ISSUER, "id": "host-test", "name": ROOT_NAME}
    root_ref = {"issuer_domain": ISSUER, "id": "root-test", "name": ROOT_NAME}
    versions = {
        "contracts": ["0.1"],
        "capabilities": {"managed_channel": "pull"},
        "challenge": {"nonce": nonce, "answer": mc.solve_challenge(SECRET, nonce)},
    }
    response = await client.post(
        "/managed/enrollments",
        json={
            "workspace_id": str(WS),
            "host_identity_ref": host_ref,
            "trust_root_ref": root_ref,
            "installed_versions": versions,
        },
    )
    assert response.status_code == 201, response.text
    enrollment_id = response.json()["enrollment_id"]
    if admit:
        optin = await client.post(
            f"/managed/enrollments/{enrollment_id}/opt-in",
            json={"managed_new_runs": True, "admitted": True},
        )
        assert optin.status_code == 200, optin.text
    return {
        "enrollment_id": enrollment_id,
        "host_ref": host_ref,
        "root_ref": root_ref,
        "versions": versions,
    }


async def _host_credential(client: AsyncClient) -> dict:
    nonce = mc.new_nonce()
    response = await client.post(
        "/managed/host/credential",
        json={
            "workspace_id": str(WS),
            "trust_root": ROOT_NAME,
            "nonce": nonce,
            "answer": mc.solve_challenge(SECRET, nonce),
            "host_id": "host-test",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["credential"]


async def test_credential_exchange_and_pull_requires_credential(client: AsyncClient, db_session: AsyncSession, secrets):
    await _seed_workspace(db_session)
    await _enroll_host(client, db_session)

    # No credential: 401.
    denied = await client.get("/managed/host/pull")
    assert denied.status_code == 401

    # Challenge-answer exchange succeeds and the credential authorizes pull.
    credential = await _host_credential(client)
    pulled = await client.get("/managed/host/pull?cursor=0", headers=_auth_header(credential))
    assert pulled.status_code == 200, pulled.text
    body = pulled.json()
    assert body["deliveries"] == [] and body["lease"]


async def test_credential_rejects_forged_challenge(client: AsyncClient, db_session: AsyncSession, secrets):
    await _seed_workspace(db_session)
    await _enroll_host(client, db_session)
    response = await client.post(
        "/managed/host/credential",
        json={
            "workspace_id": str(WS),
            "trust_root": ROOT_NAME,
            "nonce": mc.new_nonce(),
            "answer": "forged",
            "host_id": "host-test",
        },
    )
    assert response.status_code == 401


async def test_delivery_queue_pull_accept_reconciliation(client: AsyncClient, db_session: AsyncSession, secrets):
    await _seed_workspace(db_session)
    info = await _enroll_host(client, db_session)
    credential = await _host_credential(client)
    headers = _auth_header(credential)

    from hecate.execution.managed_channel import ManagedDeliveryService

    platform_task_ref = {"issuer_domain": "hecate", "id": str(uuid.uuid4())}
    platform_run_ref = {"issuer_domain": "hecate", "id": str(uuid.uuid4())}
    async with test_session_factory() as session:
        service = ManagedDeliveryService(session)
        await service.queue_delivery(
            workspace_id=WS,
            enrollment_id=uuid.UUID(info["enrollment_id"]),
            delivery_id=uuid.uuid4(),
            task_ref=platform_task_ref,
            run_ref=platform_run_ref,
            input_payload={"messages": [{"role": "user", "content": "go"}]},
        )
        await session.commit()

    # Pull sees the pending delivery + a fresh lease.
    pulled = await client.get("/managed/host/pull?cursor=0", headers=headers)
    assert pulled.status_code == 200
    deliveries = pulled.json()["deliveries"]
    assert len(deliveries) == 1 and deliveries[0]["state"] == "pending"
    delivery_row_id = deliveries[0]["delivery_row_id"]

    local_task_ref = {"issuer_domain": "host", "id": str(uuid.uuid4())}
    local_run_ref = {"issuer_domain": "host", "id": str(uuid.uuid4())}
    accepted = await client.post(
        "/managed/host/accept",
        json={
            "delivery_row_id": delivery_row_id,
            "local_task_ref": local_task_ref,
            "local_run_ref": local_run_ref,
        },
        headers=headers,
    )
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "delivered"

    # Re-accept with the same refs: idempotent (lost-response reconciliation).
    again = await client.post(
        "/managed/host/accept",
        json={
            "delivery_row_id": delivery_row_id,
            "local_task_ref": local_task_ref,
            "local_run_ref": local_run_ref,
        },
        headers=headers,
    )
    assert again.status_code == 200

    # Conflicting refs: 409, the first mapping is never overwritten.
    conflict = await client.post(
        "/managed/host/accept",
        json={
            "delivery_row_id": delivery_row_id,
            "local_task_ref": {"issuer_domain": "host", "id": str(uuid.uuid4())},
            "local_run_ref": local_run_ref,
        },
        headers=headers,
    )
    assert conflict.status_code == 409


async def test_projection_deduplicates_and_maps_to_platform_run(client: AsyncClient, db_session: AsyncSession, secrets):
    await _seed_workspace(db_session)
    info = await _enroll_host(client, db_session)
    credential = await _host_credential(client)
    headers = _auth_header(credential)

    from hecate.execution.managed_channel import ManagedDeliveryService
    from hecate.models.managed_delivery import ManagedDeliveryModel

    platform_task_ref = {"kind": "task", "issuer_domain": "hecate", "id": str(uuid.uuid4())}
    platform_run_ref = {"kind": "run", "issuer_domain": "hecate", "id": str(uuid.uuid4())}
    local_task_ref = {"kind": "task", "issuer_domain": "host", "id": str(uuid.uuid4())}
    local_run_ref = {"kind": "run", "issuer_domain": "host", "id": str(uuid.uuid4())}
    async with test_session_factory() as session:
        service = ManagedDeliveryService(session)
        row = await service.queue_delivery(
            workspace_id=WS,
            enrollment_id=uuid.UUID(info["enrollment_id"]),
            delivery_id=uuid.uuid4(),
            task_ref=platform_task_ref,
            run_ref=platform_run_ref,
            input_payload={"messages": []},
        )
        row_id = row.id
        accepted = await service.accept(
            delivery_row_id=row_id, local_task_ref=local_task_ref, local_run_ref=local_run_ref
        )
        assert accepted.state == "delivered"
        await session.commit()

    # A real platform Run row is needed for the projection's run mapping.
    db = db_session
    from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
    from hecate.contracts.execution.references import deployment_ref, run_ref
    from hecate.models.agent import AgentModel
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.agent_version import AgentVersionModel

    agent = AgentModel(workspace_id=WS, name="proj-agent")
    db.add(agent)
    await db.flush()
    version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={}, content_hash="d" * 64)
    db.add(version)
    await db.flush()
    from hecate.models.agent_principal import AgentPrincipalModel

    db.add(
        AgentPrincipalModel(
            id=agent.id,
            agent_id=agent.id,
            workspace_id=WS,
            organization_id=WS,
            owner_user_id=ADMIN,
        )
    )
    deployment = AgentDeploymentModel(
        agent_id=agent.id,
        agent_version_id=version.id,
        workspace_id=WS,
        backend_type=BackendType.BUILTIN,
        access_mode=AccessMode.IN_PROCESS,
        issuer_domain="hecate",
        capability_snapshot={},
        axes_harness="hecate",
        axes_environment="none",
        axes_tool_execution="hecate_gateway",
        is_default=True,
    )
    db.add(deployment)
    await db.commit()
    chain = IdentityChain(
        initiator=None,
        principal_id=str(agent.id),
        workload=WorkloadIdentity(
            deployment=deployment_ref("hecate", str(deployment.id)), workload_id="hecate:managed"
        ),
        audience="hecate:managed-channel",
    )
    registry = TaskRunRegistry(db)
    task = await registry.create_task(goal="managed", initiator_ref=chain.to_dict(), workspace_id=WS)
    run = await registry.create_run(
        task_id=task.id,
        workspace_id=WS,
        deployment_id=deployment.id,
        identity_chain=chain,
        backend_run_ref=run_ref("hecate", str(uuid.uuid4())),
    )
    # Point the delivery's run_ref at the platform run (what queue_delivery
    # receives in production).
    delivery_row = await db.get(ManagedDeliveryModel, row_id)
    delivery_row.run_ref = {"issuer_domain": "hecate", "id": str(run.id)}
    await db.commit()

    envelope = {
        "contract_version": "0.1",
        "kind": "event",
        "event_id": f"evt-{uuid.uuid4()}",
        "task_ref": local_task_ref,
        "run_ref": local_run_ref,
        "source_sequence": 1,
        "occurred_at": datetime.now(UTC).isoformat(),
        "received_at": datetime.now(UTC).isoformat(),
        "payload_schema_ref": "urn:hecate:durable:event:task_state",
        "payload": {"event_type": "task_state", "state": "succeeded", "revision": 2},
        "actor": {"kind": "service", "id": "host-test"},
        "source": "standalone_host",
    }
    uploaded = await client.post(
        "/managed/host/events",
        json={"local_task_ref": local_task_ref, "envelopes": [envelope]},
        headers=headers,
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["projected"] == 1

    # Replay the same envelope: deduplicated.
    replay = await client.post(
        "/managed/host/events",
        json={"local_task_ref": local_task_ref, "envelopes": [envelope]},
        headers=headers,
    )
    assert replay.json()["skipped_duplicates"] == 1

    # The platform Run projection folded the host's terminal-ish state.
    from sqlalchemy import select

    from hecate.models.run import RunModel

    # Read through a fresh session: db_session's identity map holds the run
    # created earlier in this test, and expired-attribute access would try
    # sync IO outside the greenlet.
    async with test_session_factory() as check:
        fresh = (await check.execute(select(RunModel).where(RunModel.id == run.id))).scalar_one()
        assert (fresh.projection or {}).get("state") == "succeeded"

    # A successor attempt is not projected until the host has persisted its
    # platform association. This also proves the successor gets its own Run
    # row instead of rewriting the original waiting attempt.
    successor_local_run_ref = {
        "kind": "run",
        "issuer_domain": "host",
        "id": str(uuid.uuid4()),
    }
    successor_event = {
        **envelope,
        "event_id": f"evt-{uuid.uuid4()}",
        "run_ref": successor_local_run_ref,
        "source_sequence": 1,
        "payload": {"event_type": "run_terminal", "status": "succeeded", "content": "resumed"},
    }
    unassociated = await client.post(
        "/managed/host/events",
        json={"local_task_ref": local_task_ref, "envelopes": [successor_event]},
        headers=headers,
    )
    assert unassociated.status_code == 422

    association = await client.post(
        "/managed/host/attempts",
        json={"local_task_ref": local_task_ref, "local_run_ref": successor_local_run_ref},
        headers=headers,
    )
    assert association.status_code == 200, association.text
    successor_platform_ref = association.json()["platform_run_ref"]
    replayed_association = await client.post(
        "/managed/host/attempts",
        json={"local_task_ref": local_task_ref, "local_run_ref": successor_local_run_ref},
        headers=headers,
    )
    assert replayed_association.json()["platform_run_ref"] == successor_platform_ref

    projected_successor = await client.post(
        "/managed/host/events",
        json={"local_task_ref": local_task_ref, "envelopes": [successor_event]},
        headers=headers,
    )
    assert projected_successor.status_code == 200, projected_successor.text
    successor_platform_id = uuid.UUID(successor_platform_ref["id"])
    async with test_session_factory() as check:
        successor = (await check.execute(select(RunModel).where(RunModel.id == successor_platform_id))).scalar_one()
        original = (await check.execute(select(RunModel).where(RunModel.id == run.id))).scalar_one()
        assert successor.attempt_no == 2
        assert successor.control_owner == "host"
        assert successor.local_run_id == successor_local_run_ref["id"]
        assert (successor.projection or {}).get("state") == "succeeded"
        assert (original.projection or {}).get("state") == "succeeded"
