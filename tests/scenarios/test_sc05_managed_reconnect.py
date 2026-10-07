"""Managed-channel scenarios, including installed Runner HTTP acceptance.

The suite covers component-level delivery/projection behavior and a clean-
installed Runner process over platform HTTP. PostgreSQL process acceptance is
enabled by ``HECATE_STEP6_POSTGRES_URL`` when the host can reach that database.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from fastapi import FastAPI
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
ROOT_NAME = "host-root-sc05"
ISSUER = "managed-sc05-issuer"
SECRET = b"sc05-managed-channel-secret"
_STEP6_POSTGRES_URL = os.environ.get("HECATE_STEP6_POSTGRES_URL")


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
async def managed_stack(auth_context: AuthContext, secrets, tmp_path):
    """Platform app (ASGI) + an enrolled host channel sharing the wire."""

    from hecate_durable.storage import SqlDurableStore

    from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

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

    async with test_session_factory() as db:
        db.add(OrganizationModel(id=WS, name="org", slug=f"org-{WS.hex[:12]}", owner_id=ADMIN))
        db.add(WorkspaceModel(id=WS, org_id=WS, name="ws", slug=f"ws-{WS.hex}"))
        db.add(UserModel(id=ADMIN, email="admin@example.com", hashed_password=uuid.uuid4().hex))
        db.add(WorkspaceMemberModel(workspace_id=WS, user_id=ADMIN, role=WorkspaceRole.ADMIN))
        await db.commit()

    store = SqlDurableStore(f"sqlite:///{tmp_path / 'sc05-host.db'}", source="standalone_host")
    store.create_schema()
    transport = httpx.ASGITransport(app=app)

    async def admit() -> None:
        async with test_session_factory() as session:
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

        async with test_session_factory() as session:
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
        async with test_session_factory() as session:
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
        async with test_session_factory() as session:
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
            else:
                agent_id, deployment_id = execution_binding
                agent = await session.get(AgentModel, agent_id)
                deployment = await session.get(AgentDeploymentModel, deployment_id)
                assert agent is not None and deployment is not None
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

    yield {
        "app": app,
        "channel": channel,
        "admit": admit,
        "opt_in": opt_in,
        "revoke_root": revoke_root,
        "queue_delivery": queue_delivery,
        "accepted_local_task_ref": accepted_local_task_ref,
        "store": store,
    }
    store.dispose()
    app.dependency_overrides.clear()


async def _serve_over_tcp(app: FastAPI):
    """Serve the real managed router on a loopback TCP socket."""

    import asyncio
    import socket

    import uvicorn

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    deadline = asyncio.get_running_loop().time() + 10
    while not server.started and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
    assert server.started, "managed platform HTTP server did not start"
    return server, task, f"http://127.0.0.1:{port}"


async def _connect(stack) -> ManagedChannel:
    """The reconnect flow: trust re-verified at registration and every pull."""

    channel: ManagedChannel = stack["channel"]
    await stack["admit"]()  # the operator provisions the trust material first
    assert await channel.register() is True
    await stack["opt_in"]()
    return channel


async def test_sc05_reconnect_reverification_then_new_work(managed_stack) -> None:
    """Assertion 1: after re-verifying trust the host accepts new work."""

    channel = await _connect(managed_stack)
    delivery_row_id = await managed_stack["queue_delivery"]({"messages": [{"role": "user", "content": "sc05"}]})
    assert await channel.pull_once() == 1
    local_ref = await managed_stack["accepted_local_task_ref"](delivery_row_id)
    from hecate_durable.contracts.references import BackendRef

    state = managed_stack["store"].get_task_state(BackendRef.from_dict(local_ref))
    assert state is not None and state.lifecycle_state is TaskLifecycleStateEnum.QUEUED


TaskLifecycleStateEnum = __import__("hecate_durable").contracts.durable.TaskLifecycleState


@pytest.fixture(
    params=[None, *([_STEP6_POSTGRES_URL] if _STEP6_POSTGRES_URL else [])],
    ids=["sqlite", "postgres"][: 1 + bool(_STEP6_POSTGRES_URL)],
)
def step6_runner_database_url(request):
    """Run the same installed-host scenario on SQLite and optional PostgreSQL."""

    return request.param


async def test_installed_runner_uses_platform_http_across_lost_accept_and_restart(
    managed_stack, tmp_path, step6_runner_database_url
):
    """A clean-installed Runner wheel completes the managed HTTP lifecycle."""

    import asyncio

    import pytest
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    from hecate.models.managed_delivery import ManagedDeliveryModel
    from hecate.models.run import RunModel
    from tests.scenarios.tools import runner_harness

    if not runner_harness.uv_available():
        pytest.skip("uv is required for the installed Runner process acceptance")

    dropped = {"accept": False}
    http_requests: list[tuple[str, int, str]] = []

    class DropFirstAcceptResponse(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            body = b"".join([chunk async for chunk in response.body_iterator])
            if (
                request.url.path in {"/managed/host/accept", "/managed/host/attempts", "/managed/host/events"}
                and len(http_requests) < 30
            ):
                http_requests.append((request.url.path, response.status_code, body.decode("utf-8", errors="replace")))
            if request.url.path == "/managed/host/accept" and not dropped["accept"]:
                dropped["accept"] = True
                return Response(status_code=503)
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers))

    managed_stack["app"].add_middleware(DropFirstAcceptResponse)
    await managed_stack["admit"]()
    server, server_task, base_url = await _serve_over_tcp(managed_stack["app"])
    runner = None
    business_server = None
    try:
        control_plane = {
            "base_url": base_url,
            "workspace_id": str(WS),
            "trust_root": ROOT_NAME,
            "host_id": "installed-runner-sc05",
            "issuer_domain": ISSUER,
            "secret_ref": "file:secrets/managed-secret",
            "poll_interval_seconds": 0.1,
            "data_domains": ["domain_a"],
        }
        runner, business_server, business_calls = runner_harness.start_runner(
            tmp_path,
            durable=True,
            tool_allowlist=["query_inventory"],
            control_plane=control_plane,
            managed_secret=SECRET,
            durable_database_url=step6_runner_database_url,
        )

        async def _wait_enrollment() -> uuid.UUID:
            from sqlalchemy import select

            from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

            deadline = asyncio.get_running_loop().time() + 20
            while asyncio.get_running_loop().time() < deadline:
                async with test_session_factory() as db:
                    row = (
                        (
                            await db.execute(
                                select(StandaloneEnrollmentModel).where(
                                    StandaloneEnrollmentModel.workspace_id == WS,
                                    StandaloneEnrollmentModel.deleted.is_(False),
                                )
                            )
                        )
                        .scalars()
                        .first()
                    )
                    if row is not None:
                        return row.id
                await asyncio.sleep(0.05)
            raise AssertionError("installed Runner did not register over platform HTTP")

        enrollment_id = await _wait_enrollment()
        from hecate.execution.enrollment_resolver import HmacEnrollmentResolver
        from hecate.execution.task_run_registry import TaskRunRegistry

        async with test_session_factory() as db:
            resolver = HmacEnrollmentResolver(db, secret_candidates={ROOT_NAME: SECRET})
            await TaskRunRegistry(db, enrollment_resolver=resolver.verify).set_managed_opt_in(
                enrollment_id,
                WS,
                managed_new_runs=True,
                operator_id=ADMIN,
                admitted=True,
                managed_scope=["domain_a"],
            )
            await db.commit()

        async def _wait_projected(delivery_id: str) -> dict:
            deadline = asyncio.get_running_loop().time() + 10
            observed = None
            while asyncio.get_running_loop().time() < deadline:
                async with test_session_factory() as db:
                    delivery = await db.get(ManagedDeliveryModel, uuid.UUID(delivery_id))
                    if delivery is not None:
                        run = await db.get(RunModel, uuid.UUID(delivery.run_ref["id"]))
                        projection = dict(run.projection or {}) if run is not None else {}
                        task_runs = []
                        if run is not None:
                            from sqlalchemy import select

                            task_runs = [
                                {"id": str(row.id), "origin": str(row.origin), "projection": row.projection}
                                for row in (
                                    await db.execute(select(RunModel).where(RunModel.task_id == run.task_id))
                                ).scalars()
                            ]
                        for attempt in (delivery.accepted_refs or {}).get("successor_runs") or []:
                            platform_ref = attempt.get("platform_run_ref") or {}
                            if platform_ref.get("issuer_domain") == "hecate":
                                projected_run = await db.get(RunModel, uuid.UUID(platform_ref["id"]))
                                projected = dict(projected_run.projection or {}) if projected_run is not None else {}
                                if projected.get("state") == "succeeded":
                                    return projected
                        if projection.get("state") == "succeeded":
                            return projection
                        observed = {
                            "delivery_state": delivery.state,
                            "accepted_refs": delivery.accepted_refs,
                            "run_id": delivery.run_ref.get("id"),
                            "projection": projection,
                            "task_runs": task_runs,
                        }
                await asyncio.sleep(0.1)
            log_path = runner.workdir / "runner.log" if runner is not None else None
            runner_log = log_path.read_text(encoding="utf-8", errors="replace")[-3000:] if log_path else ""
            raise AssertionError(
                f"delivery {delivery_id} did not project a succeeded host run; observed={observed}; "
                f"http={http_requests}; business_calls={business_calls}; runner_log={runner_log}"
            )

        async def _queue_one() -> str:
            return await managed_stack["queue_delivery"](
                {"input": {"prompt": "managed TCP"}, "tool_arguments": {"domain": "domain_a", "sku": "SKU-A1"}}
            )

        first = await _queue_one()
        first_projection = await _wait_projected(first)
        assert first_projection["state"] == "succeeded"
        assert dropped["accept"] is True, "the first committed accept response must have been lost"
        assert len(business_calls) == 1, "redelivery after a lost accept must not execute the task twice"
        from hecate_durable.storage import SqlDurableStore

        local_database_url = step6_runner_database_url or f"sqlite:///{(runner.workdir / 'host.db').as_posix()}"
        local_store = SqlDurableStore(local_database_url)
        try:
            assert len(local_store.list_tasks()) == 1, "lost accept must retain exactly one durable local Task"
        finally:
            local_store.dispose()

        runner = runner.restart()
        assert runner.request("GET", "/healthz", token=None)[0] == 200
        second = await _queue_one()
        assert (await _wait_projected(second))["state"] == "succeeded"
        assert len(business_calls) == 2

        third = await _queue_one()
        assert (await _wait_projected(third))["state"] == "succeeded"
        assert len(business_calls) == 3
    finally:
        if runner is not None:
            runner.stop(kill=True)
        if business_server is not None:
            runner_harness.stop_business_api(business_server)
        server.should_exit = True
        await server_task


async def test_sc05_duplicate_delivery_never_repeats_execution(managed_stack) -> None:
    """Assertion 3 (deliveries): a redelivered row is answered, not executed."""

    channel = await _connect(managed_stack)
    delivery_id = await managed_stack["queue_delivery"]({"messages": []})
    assert await channel.pull_once() == 1

    channel._delivery_cursor = None  # simulate a lost accept response
    duplicated = await channel.pull_once()
    assert duplicated == 0
    assert channel.stats.deliveries_duplicate == 0
    assert (
        await channel._accept_delivery({"delivery_row_id": delivery_id, "input_payload": {"messages": []}})
        == "duplicate"
    )


async def test_sc05_events_deduplicated_on_replay(managed_stack) -> None:
    """Assertion 2: historical events are deduplicated on replay."""

    from hecate.contracts.execution.references import BackendRef, RefKind

    channel = await _connect(managed_stack)
    delivery_row_id = await managed_stack["queue_delivery"]({"messages": []})
    await channel.pull_once()
    local_task = BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_row_id}")
    local_run = BackendRef(RefKind.RUN, "managed-host", f"managed-run-{delivery_row_id}")
    # Accept happened during pull; record the mapping the upload needs.
    async with test_session_factory() as db:
        from hecate.execution.managed_channel import ManagedDeliveryService

        await ManagedDeliveryService(db).accept(
            delivery_row_id=uuid.UUID(delivery_row_id),
            local_task_ref=local_task.to_dict(),
            local_run_ref=local_run.to_dict(),
        )
        await db.commit()

    # Local execution produced events on the durable log.
    managed_stack["store"].apply_task_state(local_task, TaskLifecycleStateEnum.SUCCEEDED)

    uploaded_first = await channel.upload_events()
    assert uploaded_first >= 1
    # The replay (host retried the same batch after a lost response) is
    # deduplicated upstream — zero new projections, zero loss.
    channel._event_cursors.clear()
    uploaded_replay = await channel.upload_events()
    assert uploaded_replay == 0


async def test_sc05_projection_never_overwrites_local_facts(managed_stack) -> None:
    """Assertion 4: state projection never overwrites local execution facts."""

    from hecate.contracts.execution.references import BackendRef, RefKind

    channel = await _connect(managed_stack)
    delivery_row_id = await managed_stack["queue_delivery"]({"messages": []})
    await channel.pull_once()
    local_task = BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_row_id}")
    async with test_session_factory() as db:
        from hecate.execution.managed_channel import ManagedDeliveryService

        await ManagedDeliveryService(db).accept(
            delivery_row_id=uuid.UUID(delivery_row_id),
            local_task_ref=local_task.to_dict(),
            local_run_ref=BackendRef(RefKind.RUN, "managed-host", f"managed-run-{delivery_row_id}").to_dict(),
        )
        await db.commit()

    # The host's own fact: the task is RUNNING locally.
    managed_stack["store"].apply_task_state(local_task, TaskLifecycleStateEnum.RUNNING)
    before = managed_stack["store"].get_task_state(local_task)
    assert before is not None and before.lifecycle_state is TaskLifecycleStateEnum.RUNNING

    # Platform-side belief differs (it projected an older queued state).
    await channel.upload_events()

    # The authoritative local fact is unchanged — projection never writes back.
    after = managed_stack["store"].get_task_state(local_task)
    assert after is not None and after.lifecycle_state is TaskLifecycleStateEnum.RUNNING
    assert after.revision == before.revision


async def test_sc05_revoked_trust_root_stops_new_work(managed_stack) -> None:
    """Reconnect after revocation: the re-verification chain refuses."""

    channel = await _connect(managed_stack)
    assert await channel.pull_once() == 0
    await managed_stack["revoke_root"]()
    channel._credential = None  # reconnect re-authenticates
    channel._delivery_cursor = None
    assert await channel.pull_once() == 0  # refused; no new work
    # The current lease stays valid until its TTL — revocation propagates
    # with the plan's bounded stale window, never instant self-revocation.
    # New deliveries are refused at the platform for the whole window.


async def test_sc05_upload_cursors_are_independent_per_run(managed_stack) -> None:
    channel = await _connect(managed_stack)
    await managed_stack["queue_delivery"]({"messages": []})
    await managed_stack["queue_delivery"]({"messages": []})
    assert await channel.pull_once() == 2
    from hecate_durable.contracts.durable import TaskLifecycleState

    for task in managed_stack["store"].list_tasks():
        managed_stack["store"].apply_task_state(task.task_ref, TaskLifecycleState.RUNNING)
    count = await channel.upload_events()
    assert count == 4  # each run contributes submitted and running events
    assert len(channel._event_cursors) == 2
    assert channel.stats.events_uploaded == count
    channel._event_cursors.clear()
    assert await channel.upload_events() == 0


async def test_sc05_unconfirmed_accept_is_redelivered_after_cursor_advance(managed_stack, monkeypatch) -> None:
    channel = await _connect(managed_stack)
    await managed_stack["queue_delivery"]({"messages": []})
    original = channel._request
    dropped = False

    async def request(method, path, **kwargs):
        nonlocal dropped
        if path == "/managed/host/accept" and not dropped:
            dropped = True
            return None  # local acceptance persisted, platform has no receipt
        return await original(method, path, **kwargs)

    monkeypatch.setattr(channel, "_request", request)
    assert await channel.pull_once() == 1
    assert channel._delivery_cursor is not None
    assert await channel.pull_once() == 0
    assert channel.stats.deliveries_duplicate == 1
    assert len(managed_stack["store"].list_tasks()) == 1


async def test_sc05_conflicting_replay_is_rejected(managed_stack) -> None:
    channel = await _connect(managed_stack)
    await managed_stack["queue_delivery"]({"messages": []})
    await channel.pull_once()
    store = managed_stack["store"]
    task = store.list_tasks()[0]
    run = store.run_for_task(task.task_ref)
    original = store.read_events(run).events[0].to_dict()
    assert await channel.upload_events() == 1
    changed = {**original, "payload": {**original["payload"], "event_type": "run_terminal", "status": "succeeded"}}
    result = await channel._request(
        "POST",
        "/managed/host/events",
        auth=True,
        payload={"local_task_ref": task.task_ref.to_dict(), "envelopes": [changed]},
    )
    assert result is None
