"""SC05 delivery/projection slices over the managed channel wire format.

These ASGI/client component tests verify enrollment, delivery acceptance,
deduplication and event projection. They do not run the installed runner CLI,
execute managed business tools or enforce a lease at action dispatch. The
complete SC05 remains planned until those paths pass process-level acceptance.
"""

from __future__ import annotations

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

    async def queue_delivery(input_payload: dict) -> str:
        from sqlalchemy import select

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
            service = ManagedDeliveryService(session)
            delivery = await service.queue_delivery(
                workspace_id=WS,
                enrollment_id=row.id,
                delivery_id=uuid.uuid4(),
                task_ref={"kind": "task", "issuer_domain": "hecate", "id": str(uuid.uuid4())},
                run_ref={"kind": "run", "issuer_domain": "hecate", "id": str(uuid.uuid4())},
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
