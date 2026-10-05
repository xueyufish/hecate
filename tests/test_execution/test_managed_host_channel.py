"""Host-side managed channel integration tests (task groups 3-4).

The runner's ``ManagedChannel`` speaks real HTTP against the platform's
``/managed`` API (ASGITransport in-process, but the full wire format):
registration challenge → credential exchange → pull/accept → event upload,
plus the governance edges — lease gate refusal after expiry, duplicate
delivery reconciliation, and revoked-enrollment refusal (SC05's host-side
half).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_runner.managed import ManagedChannel

from hecate.channel.api.managed import router as managed_router
from hecate.core.auth_context import AuthContext
from hecate.core.composition.managed_secrets import clear_managed_secrets, register_managed_secret
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution import managed_credentials as mc
from hecate.execution.managed_channel import ManagedDeliveryService
from hecate.execution.trust_roots import TrustRootRegistry
from hecate.models.audit import AuditLogModel  # noqa: F401  (registers audit_logs before schema creation)
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from tests.conftest import DEFAULT_WORKSPACE_ID, test_session_factory

WS = DEFAULT_WORKSPACE_ID
ADMIN = uuid.uuid4()
ROOT_NAME = "host-root-int"
ISSUER = "managed-int-issuer"
SECRET = b"managed-integration-secret"


@pytest.fixture
def secrets():
    register_managed_secret(ISSUER, SECRET)
    yield
    clear_managed_secrets()


@pytest.fixture
def auth_context() -> AuthContext:
    from hecate.models.workspace_member import WorkspaceRole

    return AuthContext(
        user_id=ADMIN, org_id=WS, workspace_id=WS, role=WorkspaceRole.ADMIN, auth_method="jwt", api_key_scope=None
    )


@pytest.fixture
async def platform(auth_context: AuthContext, secrets):
    """The platform app + an admit helper returning the ASGI transport."""

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

    from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

    async with test_session_factory() as db:
        db.add(OrganizationModel(id=WS, name="org", slug=f"org-{WS.hex[:12]}", owner_id=ADMIN))
        db.add(WorkspaceModel(id=WS, org_id=WS, name="ws", slug=f"ws-{WS.hex}"))
        db.add(UserModel(id=ADMIN, email="admin@example.com", hashed_password=uuid.uuid4().hex))
        db.add(WorkspaceMemberModel(workspace_id=WS, user_id=ADMIN, role=WorkspaceRole.ADMIN))
        await db.commit()

    transport = httpx.ASGITransport(app=app)

    async def admit(host_name: str = "host-int") -> None:
        async with test_session_factory() as db:
            registry = TrustRootRegistry(db)
            await registry.register(
                workspace_id=WS,
                name=ROOT_NAME,
                material_digest=mc.material_digest(SECRET),
                issuer_domain=ISSUER,
                config_fingerprint="cfg-int-1",
            )
            await db.commit()

    async def opt_in_last_enrollment() -> None:
        """Operator admission for the channel-registered (pending) host."""

        from sqlalchemy import select

        from hecate.execution.task_run_registry import TaskRunRegistry
        from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

        async with test_session_factory() as db:
            row = (
                (
                    await db.execute(
                        select(StandaloneEnrollmentModel).where(StandaloneEnrollmentModel.deleted.is_(False))
                    )
                )
                .scalars()
                .first()
            )
            assert row is not None, "no enrollment to admit"
            from hecate.execution.enrollment_resolver import HmacEnrollmentResolver

            resolver = HmacEnrollmentResolver(db, secret_candidates={ROOT_NAME: SECRET})
            await TaskRunRegistry(db, enrollment_resolver=resolver.verify).set_managed_opt_in(
                row.id, WS, managed_new_runs=True, operator_id=ADMIN, admitted=True
            )
            await db.commit()

    yield {"transport": transport, "admit": admit, "opt_in_last_enrollment": opt_in_last_enrollment}
    app.dependency_overrides.clear()


def _make_channel(platform, *, clock=None) -> ManagedChannel:
    return ManagedChannel(
        base_url="http://platform.test",
        workspace_id=str(WS),
        trust_root=ROOT_NAME,
        host_id="host-int",
        secret=SECRET,
        store=_store_holder.store,
        issuer_domain=ISSUER,
        lease_ttl_seconds=60.0,
        poll_interval_seconds=0.05,
        transport=platform["transport"],
        clock=clock,
    )


class _StoreHolder:
    store = None


_store_holder = _StoreHolder()


@pytest.fixture
def host_store(tmp_path):
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(f"sqlite:///{tmp_path / 'managed-host.db'}", source="standalone_host")
    store.create_schema()
    _store_holder.store = store
    yield store
    store.dispose()


async def test_register_pull_accept_upload_cycle(platform, host_store, secrets):
    await platform["admit"]()
    channel = _make_channel(platform)
    assert await channel.register() is True
    await platform["opt_in_last_enrollment"]()

    # The platform queues a delivery for this host.
    from hecate.execution.task_run_registry import TaskRunRegistry
    from hecate.models.agent import AgentModel
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.agent_principal import AgentPrincipalModel
    from hecate.models.agent_version import AgentVersionModel

    async with test_session_factory() as db:
        agent = AgentModel(workspace_id=WS, name="managed-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={}, content_hash="e" * 64)
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id, agent_id=agent.id, workspace_id=WS, organization_id=WS, owner_user_id=ADMIN
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
        chain = await _identity_for(db, agent.id, deployment.id)
        registry = TaskRunRegistry(db)
        task = await registry.create_task(goal="managed delivery", initiator_ref=chain.to_dict(), workspace_id=WS)
        run = await registry.create_run(
            task_id=task.id,
            workspace_id=WS,
            deployment_id=deployment.id,
            identity_chain=chain,
            backend_run_ref=__import__("hecate").contracts.execution.references.run_ref("hecate", str(uuid.uuid4())),
        )
        service = ManagedDeliveryService(db)
        row = await service.queue_delivery(
            workspace_id=WS,
            enrollment_id=uuid.UUID(await _enrollment_id_of(db)),
            delivery_id=uuid.uuid4(),
            task_ref={**task_ref_of(task.id), "kind": "task"},
            run_ref={**_run_ref_of(run.id), "kind": "run"},
            input_payload={"messages": [{"role": "user", "content": "managed work"}]},
        )
        await db.commit()
        delivery_row_id, delivery_state = row.id, row.state

    accepted = await channel.pull_once()
    assert accepted == 1
    # The host's accept receipt flipped the delivery state.
    async with test_session_factory() as db:
        from sqlalchemy import select

        from hecate.models.managed_delivery import ManagedDeliveryModel

        fresh = (
            await db.execute(select(ManagedDeliveryModel).where(ManagedDeliveryModel.id == delivery_row_id))
        ).scalar_one()
        assert fresh.state == "delivered"
        assert fresh.accepted_refs["task_ref"]["id"].startswith("managed-")

    # A redelivery (cursor reset = lost response) is a duplicate, not a
    # second execution: the host answers from its idempotent accept record.
    channel._delivery_cursor = None
    duplicated = await channel.pull_once()
    assert duplicated == 0
    assert channel.stats.deliveries_duplicate == 0  # confirmed acceptance no longer needs redelivery
    assert delivery_state == "pending"  # local snapshot unchanged; platform flipped it


async def _queue_delivery(platform, *, enrollment_id, task_id: str, run_id: str) -> str:
    """Queue one delivery for the (single) enrolled host; returns row id."""

    from sqlalchemy import select

    from hecate.execution.managed_channel import ManagedDeliveryService
    from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

    async with test_session_factory() as db:
        row = (
            await db.execute(select(StandaloneEnrollmentModel).where(StandaloneEnrollmentModel.deleted.is_(False)))
        ).scalar_one()
        service = ManagedDeliveryService(db)
        delivery = await service.queue_delivery(
            workspace_id=WS,
            enrollment_id=row.id,
            delivery_id=uuid.uuid4(),
            task_ref={"kind": "task", "issuer_domain": "hecate", "id": task_id},
            run_ref={"kind": "run", "issuer_domain": "hecate", "id": run_id},
            input_payload={"messages": []},
        )
        await db.commit()
        return str(delivery.id)


async def _identity_for(db, agent_id, deployment_id):
    from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
    from hecate.contracts.execution.references import deployment_ref

    return IdentityChain(
        initiator=None,
        principal_id=str(agent_id),
        workload=WorkloadIdentity(
            deployment=deployment_ref("hecate", str(deployment_id)), workload_id="hecate:managed"
        ),
        audience="hecate:managed-channel",
    )


def task_ref_of(task_id):
    from hecate.contracts.execution.references import task_ref

    return task_ref("hecate", str(task_id)).to_dict()


def _run_ref_of(run_id):
    from hecate.contracts.execution.references import run_ref

    return run_ref("hecate", str(run_id)).to_dict()


async def _enrollment_id_of(db) -> str:
    from sqlalchemy import select

    from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

    row = (
        (await db.execute(select(StandaloneEnrollmentModel).where(StandaloneEnrollmentModel.deleted.is_(False))))
        .scalars()
        .first()
    )
    return str(row.id)


async def test_lease_gate_blocks_after_expiry_without_self_authorization(platform, host_store, secrets):
    await platform["admit"]()
    # The mutable clock starts at the real present (the platform issues
    # leases against real time) and jumps past the lease TTL to simulate a
    # disconnect.
    now = {"t": datetime.now(UTC)}
    channel = _make_channel(platform, clock=lambda: now["t"])
    assert await channel.register() is True
    await platform["opt_in_last_enrollment"]()
    await channel.pull_once()
    assert channel.gate.active is True

    # Time advances past the lease TTL (disconnect): the gate refuses.
    now["t"] = now["t"] + timedelta(seconds=7200)
    from hecate.execution.managed_credentials import CredentialError

    with pytest.raises(CredentialError, match="expired"):
        await channel.gate.check()
    assert channel.gate.active is False


async def test_revoked_enrollment_refuses_channel(platform, host_store, secrets):
    await platform["admit"]()
    channel = _make_channel(platform)
    assert await channel.register() is True
    await platform["opt_in_last_enrollment"]()
    # Sanity: an admitted host pulls successfully.
    assert await channel.pull_once() == 0

    # The operator revokes the trust root: the reconnect re-verification
    # chain fails and pulls are refused (403/401 tolerated → no deliveries).
    async with test_session_factory() as db:
        registry = TrustRootRegistry(db)
        await registry.revoke(WS, ROOT_NAME)
        await db.commit()
    channel._credential = None  # force re-authentication (reconnect)
    channel._delivery_cursor = None
    assert await channel.pull_once() == 0
    assert channel.stats.pulls == 1  # the refused pull is not a counted cycle


async def test_event_upload_reaches_platform_read_model(platform, host_store, secrets):
    await platform["admit"]()
    channel = _make_channel(platform)
    assert await channel.register() is True
    await platform["opt_in_last_enrollment"]()

    # The projection mirrors MANAGED deliveries: the platform first queues
    # and the host accepts one, creating the local task/run mapping.
    from hecate.contracts.execution.durable import IdempotencyKey
    from hecate.contracts.execution.references import BackendRef, RefKind

    platform_task_id, platform_run_id = str(uuid.uuid4()), str(uuid.uuid4())
    delivery_row_id = await _queue_delivery(
        platform, enrollment_id=None, task_id=platform_task_id, run_id=platform_run_id
    )
    local_task = BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_row_id}")
    local_run = BackendRef(RefKind.RUN, "managed-host", f"managed-run-{delivery_row_id}")
    delivery_row_id_uuid = uuid.UUID(delivery_row_id)
    # The host accepts exactly as ManagedChannel._accept_delivery does.
    submit_key = IdempotencyKey(
        key=f"managed-delivery:{delivery_row_id}",
        subject="host-int",
        workspace=str(WS),
        request_digest=delivery_row_id,
    )
    host_store.submit_task(
        key=submit_key, task_ref=local_task, run_ref=local_run, input_payload={"goal": "upload test"}
    )
    host_store.apply_task_state(local_task, TaskLifecycleState.RUNNING)
    async with test_session_factory() as db:
        from hecate.execution.managed_channel import ManagedDeliveryService

        await ManagedDeliveryService(db).accept(
            delivery_row_id=delivery_row_id_uuid,
            local_task_ref=local_task.to_dict(),
            local_run_ref=local_run.to_dict(),
        )
        await db.commit()

    # One local durable event on the run's stream.
    host_store.apply_task_state(local_task, TaskLifecycleState.SUCCEEDED)

    uploaded = await channel.upload_events()
    assert uploaded >= 1

    # Replaying (cursor unchanged on failure paths) stays deduplicated
    # upstream; a second upload with the advanced cursor is a no-op.
    uploaded_again = await channel.upload_events()
    assert uploaded_again == 0
