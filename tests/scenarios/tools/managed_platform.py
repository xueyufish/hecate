"""Shared managed-platform assembly for scenario tests.

Extracted from the SC05 reconnect suite: the FastAPI managed router with
its database overrides, trust-root/enrollment helpers, delivery queueing,
and a real TCP server for installed Runner processes. The app's database
is a dedicated file-backed SQLite engine (NOT the suite's shared
in-memory one): a real Runner polls the served app while the test
queries the same rows, and two coroutines interleaving statements on the
single shared in-memory connection make commits fail with "SQL
statements in progress". The platform-side durable command recorder is
bound to a file-backed store so recorded commands survive a
control-plane server restart; the binding is restored on exit
(``set_durable_suite`` is process-global).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from hecate_runner.managed import ManagedChannel

from hecate.channel.api.managed import router as managed_router
from hecate.core.auth_context import AuthContext
from hecate.core.composition.durable_platform import DurableSuite, set_durable_suite
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution import managed_credentials as mc
from hecate.execution.managed_channel import ManagedDeliveryService
from hecate.execution.trust_roots import TrustRootRegistry
from hecate.models.audit import AuditLogModel  # noqa: F401  (registers audit_logs before schema creation)
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from tests.conftest import DEFAULT_WORKSPACE_ID

WS = DEFAULT_WORKSPACE_ID
ADMIN = uuid.uuid4()
ROOT_NAME = "host-root-sc05"
ISSUER = "managed-sc05-issuer"
SECRET = b"sc05-managed-channel-secret"

STEP6_POSTGRES_URL = os.environ.get("HECATE_STEP6_POSTGRES_URL")


@asynccontextmanager
async def build_managed_stack(
    auth_context: AuthContext,
    tmp_path: Any,
    *,
    host_database_url: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Platform app (ASGI) + an enrolled host channel sharing the wire."""

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # Import every model module the managed stack touches (some closure
    # bodies import lazily; the tables must exist before create_all).
    from hecate.models import (  # noqa: F401
        agent,
        agent_deployment,
        agent_principal,
        agent_version,
        audit,
        managed_delivery,
        organization,
        run,
        standalone_enrollment,
        task,
        trust_root,
        user,
        workspace,
        workspace_member,
    )
    from hecate.models.base import Base
    from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

    app_engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'platform-app.db'}",
        connect_args={"timeout": 30},
    )
    session_factory = async_sessionmaker(app_engine, expire_on_commit=False)
    async with app_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    app.include_router(managed_router)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_context] = lambda: auth_context

    async with session_factory() as db:
        db.add(OrganizationModel(id=WS, name="org", slug=f"org-{WS.hex[:12]}", owner_id=ADMIN))
        db.add(WorkspaceModel(id=WS, org_id=WS, name="ws", slug=f"ws-{WS.hex}"))
        db.add(UserModel(id=ADMIN, email="admin@example.com", hashed_password=uuid.uuid4().hex))
        db.add(WorkspaceMemberModel(workspace_id=WS, user_id=ADMIN, role=WorkspaceRole.ADMIN))
        await db.commit()

    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(host_database_url or f"sqlite:///{tmp_path / 'managed-host.db'}", source="standalone_host")
    store.create_schema()
    # The control plane's command recorder: file-backed so a TCP-server
    # restart (same process, durable state) does not lose recorded commands.
    # Suite binding is process-global; restore whatever was there on exit.
    platform_store = SqlDurableStore(f"sqlite:///{tmp_path / 'platform-durable.db'}", source="platform")
    platform_store.create_schema()
    previous_suite = get_or_none_durable_suite()
    set_durable_suite(
        DurableSuite(
            store=platform_store,
            recorder=platform_store,
            ledger=platform_store,
            backend="stub",
            ledger_source="core",
        )
    )
    transport = httpx.ASGITransport(app=app)

    async def admit() -> None:
        async with session_factory() as session:
            registry = TrustRootRegistry(session)
            await registry.register(
                workspace_id=WS,
                name=ROOT_NAME,
                material_digest=mc.material_digest(SECRET),
                issuer_domain=ISSUER,
                config_fingerprint="cfg-sc05",
            )
            await session.commit()

    async def opt_in() -> None:
        from sqlalchemy import select

        from hecate.execution.enrollment_resolver import HmacEnrollmentResolver
        from hecate.execution.task_run_registry import TaskRunRegistry
        from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

        async with session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(StandaloneEnrollmentModel).where(StandaloneEnrollmentModel.deleted.is_(False))
                    )
                )
                .scalars()
                .first()
            )
            assert row is not None
            resolver = HmacEnrollmentResolver(session, secret_candidates={ROOT_NAME: SECRET})
            await TaskRunRegistry(session, enrollment_resolver=resolver.verify).set_managed_opt_in(
                row.id, WS, managed_new_runs=True, operator_id=ADMIN, admitted=True
            )
            await session.commit()

    async def revoke_root() -> None:
        async with session_factory() as session:
            await TrustRootRegistry(session).revoke(WS, ROOT_NAME)
            await session.commit()

    execution_binding: tuple[uuid.UUID, uuid.UUID] | None = None

    async def queue_delivery(input_payload: dict) -> str:
        from sqlalchemy import select

        from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
        from hecate.contracts.execution.references import deployment_ref
        from hecate.contracts.execution.references import run_ref as mk_run_ref
        from hecate.contracts.execution.references import task_ref as mk_task_ref
        from hecate.execution.task_run_registry import TaskRunRegistry
        from hecate.models.agent import AgentModel
        from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
        from hecate.models.agent_principal import AgentPrincipalModel
        from hecate.models.agent_version import AgentVersionModel
        from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

        nonlocal execution_binding
        async with session_factory() as session:
            row = (
                (
                    await session.execute(
                        select(StandaloneEnrollmentModel).where(StandaloneEnrollmentModel.deleted.is_(False))
                    )
                )
                .scalars()
                .first()
            )
            assert row is not None
            if execution_binding is not None:
                agent_id, deployment_id = execution_binding
                agent = await session.get(AgentModel, agent_id)
                deployment = await session.get(AgentDeploymentModel, deployment_id)
                principal = await session.get(AgentPrincipalModel, agent_id)
                if agent is None or deployment is None or principal is None:
                    execution_binding = None

            if execution_binding is None:
                agent = AgentModel(workspace_id=WS, name=f"sc05-agent-{uuid.uuid4().hex[:8]}")
                session.add(agent)
                await session.flush()
                version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={}, content_hash="a" * 64)
                session.add(version)
                await session.flush()
                session.add(
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
                session.add(deployment)
                await session.flush()
                execution_binding = (agent.id, deployment.id)
            chain = IdentityChain(
                initiator=None,
                principal_id=str(agent.id),
                workload=WorkloadIdentity(
                    deployment=deployment_ref("hecate", str(deployment.id)), workload_id="hecate:managed"
                ),
                audience="hecate:managed-channel",
            )
            registry = TaskRunRegistry(session)
            task = await registry.create_task(goal="scenario delivery", initiator_ref=chain.to_dict(), workspace_id=WS)
            run = await registry.create_run(
                task_id=task.id,
                workspace_id=WS,
                deployment_id=deployment.id,
                identity_chain=chain,
                backend_run_ref=mk_run_ref("hecate", str(uuid.uuid4())),
            )
            service = ManagedDeliveryService(session)
            delivery = await service.queue_delivery(
                workspace_id=WS,
                enrollment_id=row.id,
                delivery_id=uuid.uuid4(),
                task_ref={**mk_task_ref("hecate", str(task.id)).to_dict(), "kind": "task"},
                run_ref={**mk_run_ref("hecate", str(run.id)).to_dict(), "kind": "run"},
                input_payload=input_payload,
            )
            await session.commit()
            return str(delivery.id)

    channel = ManagedChannel(
        base_url="http://platform.test",
        workspace_id=str(WS),
        trust_root=ROOT_NAME,
        host_id="host-sc05",
        secret=SECRET,
        store=store,
        issuer_domain=ISSUER,
        lease_ttl_seconds=120.0,
        poll_interval_seconds=0.05,
        transport=transport,
    )

    async def accepted_local_task_ref(delivery_row_id: str) -> dict:
        from hecate_durable.contracts.references import BackendRef, RefKind

        return BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_row_id}").to_dict()

    try:
        yield {
            "app": app,
            "channel": channel,
            "admit": admit,
            "opt_in": opt_in,
            "revoke_root": revoke_root,
            "queue_delivery": queue_delivery,
            "accepted_local_task_ref": accepted_local_task_ref,
            "store": store,
            "platform_store": platform_store,
            "session_factory": session_factory,
        }
    finally:
        set_durable_suite(previous_suite)
        store.dispose()
        platform_store.dispose()
        await app_engine.dispose()
        app.dependency_overrides.clear()


def get_or_none_durable_suite() -> Any:
    from hecate.core.composition.durable_platform import _suite

    return _suite


async def serve_over_tcp(app: FastAPI, *, port: int | None = None):
    """Serve the real managed router on a loopback TCP socket."""

    import asyncio
    import socket

    import uvicorn

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port or 0))
    listener.listen(128)
    bound_port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    deadline = asyncio.get_running_loop().time() + 10
    while not server.started and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
    assert server.started, "managed platform HTTP server did not start"
    return server, task, f"http://127.0.0.1:{bound_port}"


async def stop_tcp_server(server, task) -> None:
    """Stop a server started by ``serve_over_tcp`` and await its exit."""

    server.should_exit = True
    await task


async def connect_channel(stack) -> ManagedChannel:
    """The reconnect flow: trust re-verified at registration and every pull."""

    channel: ManagedChannel = stack["channel"]
    await stack["admit"]()  # the operator provisions the trust material first
    assert await channel.register() is True
    await stack["opt_in"]()
    return channel
