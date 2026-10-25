"""Run SSE dynamic revocation (step14).

An established ``GET /api/runs/{run_id}/events/stream`` connection keeps
re-verifying the caller's access on a bounded interval and ends once any
fact is revoked — even with no new events flowing. Covered here with
real JWT and API-key credentials and changes committed from independent
sessions:

- Authentication revocations (revoked API key, removed membership,
  downgraded role claim, expired JWT, deactivated user) end the stream
  with the code-only ``event: error`` frame 401; the stream's tenant
  binding survives re-authentication, and a live VIEWER identity keeps
  observing.
- Resource revocations (soft-deleted run, soft-deleted parent task) end
  with 404; a guard-level workspace mismatch or role below viewer maps
  to 403; a failing check ends with 500 and never leaks the failure.
- Timing: idle streams re-check without events; an already-obtained
  batch is dropped instead of relayed when the pre-send check finds the
  revocation (deterministic via a controlled clock); after the terminal
  error frame no further reads or relay projections happen.
- Compatibility: connect-time denials stay normal HTTP 401/403/404, and
  terminal runs, cursor resume, and both event sources (platform ledger
  and entry store) keep their existing semantics.
- Real TCP evidence: uvicorn on a real socket serves the stream, the
  membership is removed mid-stream from an independent transaction, and the
  client receives the 401 frame followed by EOF; an abortive client
  disconnect closes the upstream server-side generator.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import socket
import struct
import threading
import time
import uuid
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI
from hecate_durable.storage import SqlDurableStore
from hecate_runtime.eventstore import InMemoryEventStore
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.channel.api import tasks as tasks_api
from hecate.channel.api.tasks import router, runs_router
from hecate.contracts.execution.events import ActorKind, ActorRef
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.core.auth_context import AuthContext
from hecate.core.composition.durable_platform import DurableSuite
from hecate.core.config import settings
from hecate.core.database import Base
from hecate.core.deps import get_db
from hecate.enterprise.auth.token import create_access_token
from hecate.execution import run_stream_guard
from hecate.execution.entry_events import RunEventMapper
from hecate.execution.governance_events import RUN_TERMINAL, TASK_SUBMITTED, PlatformEventService
from hecate.execution.task_control import TaskControlService
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.api_key import ApiKeyModel, ApiKeyScope
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from tests.conftest import test_session_factory as stream_session_factory

_INTERVAL = 0.05


class _StubRelay:
    """Relay double that records projection attempts only."""

    async def pump_once(self) -> None:
        env_calls.append(("relay", time.monotonic()))


env_calls: list[tuple[str, float]] = []


@pytest.fixture
async def env(db_session: AsyncSession, tmp_path, monkeypatch: pytest.MonkeyPatch):
    """One workspace identity, one live run, real bearer auth, spy reads."""
    user = UserModel(email=f"stream-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    org = OrganizationModel(name="Stream org", slug=f"stream-{uuid.uuid4().hex[:8]}", owner_id=user.id)
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name="Stream ws", slug=f"stream-ws-{uuid.uuid4().hex[:8]}")
    db_session.add(ws)
    await db_session.flush()
    member = WorkspaceMemberModel(user_id=user.id, workspace_id=ws.id, role=WorkspaceRole.EDITOR)
    db_session.add(member)
    await db_session.flush()

    agent = AgentModel(workspace_id=ws.id, name="stream-revocation-agent")
    db_session.add(agent)
    await db_session.flush()
    version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="a" * 64)
    db_session.add(version)
    await db_session.flush()
    principal = AgentPrincipalModel(
        id=agent.id, agent_id=agent.id, workspace_id=ws.id, organization_id=org.id, owner_user_id=user.id
    )
    deployment = AgentDeploymentModel(
        agent_id=agent.id,
        agent_version_id=version.id,
        workspace_id=ws.id,
        backend_type=BackendType.BUILTIN,
        access_mode=AccessMode.IN_PROCESS,
        issuer_domain="hecate",
        capability_snapshot={},
        axes_harness="hecate",
        axes_environment="none",
        axes_tool_execution="hecate_gateway",
        is_default=True,
    )
    db_session.add_all([principal, deployment])
    await db_session.flush()
    chain = IdentityChain(
        initiator=str(user.id),
        principal_id=str(principal.id),
        workload=WorkloadIdentity(
            deployment=BackendRef(RefKind.DEPLOYMENT, "hecate", str(deployment.id)),
            workload_id="hecate:run-stream-revocation",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(goal="revocation stream", initiator_ref=chain.to_dict(), workspace_id=ws.id)
    run = await registry.create_run(
        task_id=task.id,
        workspace_id=ws.id,
        deployment_id=deployment.id,
        identity_chain=chain,
        backend_run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
    )
    events = PlatformEventService(db_session)
    await events.emit(
        task_ref=BackendRef(RefKind.TASK, task.issuer_domain, str(task.id)),
        run_ref=BackendRef(RefKind.RUN, run.issuer_domain, str(run.id)),
        payload_schema_ref=TASK_SUBMITTED,
        payload={"goal": "revocation stream"},
        actor=ActorRef(kind=ActorKind.PLATFORM, id="test-control"),
        workspace_id=ws.id,
    )
    await db_session.commit()

    store = SqlDurableStore(f"sqlite:///{tmp_path / 'run-stream-revocation.db'}", source="platform")
    store.create_schema()
    monkeypatch.setattr(
        tasks_api,
        "get_durable_suite",
        lambda: DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core"),
    )
    monkeypatch.setattr(tasks_api, "_worker_state", SimpleNamespace(durable_outbox_relay=_StubRelay()))

    # The guard must verify against the test database, not the configured
    # one; the interval shrinks so revocations surface within the test.
    monkeypatch.setattr("hecate.core.database.async_session_factory", stream_session_factory)
    monkeypatch.setattr(run_stream_guard, "RECHECK_INTERVAL_SECONDS", _INTERVAL)
    monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-for-ci")

    env_calls.clear()
    for cls, name in (
        (PlatformEventService, "read_run_events"),
        (PlatformEventService, "latest_run_envelope"),
        (RunEventMapper, "read_events"),
        (TaskControlService, "_pump_events"),
    ):
        original = getattr(cls, name)

        async def observed(self, *args, _name=name, _original=original, **kwargs):
            env_calls.append((_name, time.monotonic()))
            return await _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, observed)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(runs_router, prefix="/api")

    async def shared_db():
        yield db_session

    app.dependency_overrides[get_db] = shared_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield {
                "client": client,
                "db": db_session,
                "ws": ws,
                "org": org,
                "user": user,
                "member": member,
                "run": run,
                "task": task,
                "url": f"/api/runs/{run.id}/events/stream",
            }
    finally:
        app.dependency_overrides.clear()
        store.dispose()


def _jwt(env, *, role: str = WorkspaceRole.EDITOR.value, workspace_id: uuid.UUID | None = None) -> str:
    return create_access_token(
        env["user"].id,
        org_id=env["org"].id,
        workspace_id=workspace_id if workspace_id is not None else env["ws"].id,
        role=role,
    )


async def _seed_api_key(env) -> tuple[ApiKeyModel, str]:
    raw = f"hcat_stream_{uuid.uuid4().hex}"
    key = ApiKeyModel(
        name="stream-revocation",
        key_hash=hashlib.sha256(raw.encode()).hexdigest(),
        key_prefix=raw[:8],
        scope=ApiKeyScope.WORKSPACE,
        workspace_id=env["ws"].id,
        created_by=env["user"].id,
        is_active=True,
    )
    env["db"].add(key)
    await env["db"].commit()
    return key, raw


async def _commit_independent(work) -> None:
    """Run one mutation on a short-lived session and commit it.

    Independent of the request/session fixtures so a change lands while a
    stream is being served.
    """
    async with stream_session_factory() as session:
        await work(session)
        await session.commit()


def _parse_sse(text: str) -> list[tuple[str | None, dict[str, Any]]]:
    frames: list[tuple[str | None, dict[str, Any]]] = []
    event: str | None = None
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            frames.append((event, json.loads(line.removeprefix("data: "))))
            event = None
    return frames


def _assert_revoked(response, code: int, *, data_before: int) -> None:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _parse_sse(response.text)
    assert frames, response.text
    assert sum(1 for event, _ in frames if event is None) >= data_before
    assert frames[-1] == ("error", {"code": code}), response.text
    assert frames.count(frames[-1]) == 1 or all(event != "error" for event, _ in frames[:-1]), (
        "only the final frame may be an error"
    )
    # Nothing is relayed after the terminal error frame.
    assert response.text.rstrip().endswith(json.dumps({"code": code}))


async def _drain_and_assert_no_further_reads(env) -> None:
    count = len(env_calls)
    await asyncio.sleep(_INTERVAL * 6)
    assert len(env_calls) == count, f"reads continued after close: {env_calls[count:]}"


# ---------------------------------------------------------------------------
# Authentication revocations
# ---------------------------------------------------------------------------


async def test_api_key_revoked_mid_stream(env) -> None:
    """A key deactivated after the stream is established ends it with 401."""
    _key, raw = await _seed_api_key(env)
    headers = {"Authorization": f"Bearer {raw}"}

    async def revoke() -> None:
        await asyncio.sleep(_INTERVAL * 3)

        async def work(session: AsyncSession) -> None:
            row = (await session.execute(select(ApiKeyModel).where(ApiKeyModel.id == _key.id))).scalar_one()
            row.is_active = False

        await _commit_independent(work)

    revoke_task = asyncio.create_task(revoke())
    response = await env["client"].get(env["url"], headers=headers)
    await revoke_task
    _assert_revoked(response, 401, data_before=1)
    await _drain_and_assert_no_further_reads(env)


async def test_membership_removed_mid_stream(env) -> None:
    """Deleting the workspace membership ends an established JWT stream."""
    token = _jwt(env)
    headers = {"Authorization": f"Bearer {token}"}

    async def revoke() -> None:
        await asyncio.sleep(_INTERVAL * 3)

        async def work(session: AsyncSession) -> None:
            row = (
                await session.execute(select(WorkspaceMemberModel).where(WorkspaceMemberModel.id == env["member"].id))
            ).scalar_one()
            row.deleted = True

        await _commit_independent(work)

    revoke_task = asyncio.create_task(revoke())
    response = await env["client"].get(env["url"], headers=headers)
    await revoke_task
    _assert_revoked(response, 401, data_before=1)
    await _drain_and_assert_no_further_reads(env)


async def test_role_downgrade_ends_stale_claim_but_viewer_keeps_observing(env) -> None:
    """A downgraded editor's old claim dies; a viewer-claim token still streams."""
    token = _jwt(env, role=WorkspaceRole.EDITOR.value)

    async def downgrade() -> None:
        await asyncio.sleep(_INTERVAL * 3)

        async def work(session: AsyncSession) -> None:
            row = (
                await session.execute(select(WorkspaceMemberModel).where(WorkspaceMemberModel.id == env["member"].id))
            ).scalar_one()
            row.role = WorkspaceRole.VIEWER

        await _commit_independent(work)

    task = asyncio.create_task(downgrade())
    response = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {token}"})
    await task
    _assert_revoked(response, 401, data_before=1)

    # The same user with a fresh viewer-claim token keeps observing: the
    # VIEWER floor stays the floor and the downgrade is no extra policy.
    # (Expire just the membership row: the shared request session's
    # identity map still holds the pre-downgrade role.)
    env["db"].expire(env["member"])

    async def finish() -> None:
        await asyncio.sleep(_INTERVAL * 3)

        async def work(session: AsyncSession) -> None:
            events = PlatformEventService(session)
            run = env["run"]
            await events.emit(
                task_ref=BackendRef(RefKind.TASK, run.issuer_domain, str(run.task_id)),
                run_ref=BackendRef(RefKind.RUN, run.issuer_domain, str(run.id)),
                payload_schema_ref=RUN_TERMINAL,
                payload={"status": "succeeded"},
                actor=ActorRef(kind=ActorKind.PLATFORM, id="test-control"),
                workspace_id=run.workspace_id,
            )

        await _commit_independent(work)

    viewer_token = _jwt(env, role=WorkspaceRole.VIEWER.value)
    task = asyncio.create_task(finish())
    viewer = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {viewer_token}"})
    await task
    assert viewer.status_code == 200, viewer.text
    frames = _parse_sse(viewer.text)
    assert all(event is None for event, _ in frames), viewer.text
    assert frames[-1][1]["payload_schema_ref"] == RUN_TERMINAL


async def test_expired_jwt_mid_stream(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A token that expires after the stream starts ends it with 401."""
    from hecate.enterprise.auth import token as token_module

    monkeypatch.setattr(token_module, "_ACCESS_TOKEN_EXPIRE", timedelta(seconds=1))
    token = _jwt(env)

    async def outlive() -> None:
        await asyncio.sleep(1.2)

    task = asyncio.create_task(outlive())
    response = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {token}"})
    await task
    _assert_revoked(response, 401, data_before=1)


async def test_user_deactivated_mid_stream(env) -> None:
    """Deactivating the user ends the stream with 401."""
    token = _jwt(env)

    async def deactivate() -> None:
        await asyncio.sleep(_INTERVAL * 3)

        async def work(session: AsyncSession) -> None:
            row = (await session.execute(select(UserModel).where(UserModel.id == env["user"].id))).scalar_one()
            row.active = False

        await _commit_independent(work)

    task = asyncio.create_task(deactivate())
    response = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {token}"})
    await task
    _assert_revoked(response, 401, data_before=1)


# ---------------------------------------------------------------------------
# Resource revocations and guard-level mappings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["run", "task"])
async def test_resource_deleted_mid_stream(env, target: str) -> None:
    """Soft-deleting the run or its parent task ends the stream with 404."""
    token = _jwt(env)

    async def delete_target() -> None:
        await asyncio.sleep(_INTERVAL * 3)
        model = env["run"].__class__ if target == "run" else env["task"].__class__
        row_id = env["run"].id if target == "run" else env["task"].id

        async def work(session: AsyncSession) -> None:
            row = (await session.execute(select(model).where(model.id == row_id))).scalar_one()
            row.deleted = True

        await _commit_independent(work)

    task = asyncio.create_task(delete_target())
    response = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {token}"})
    await task
    _assert_revoked(response, 404, data_before=1)
    await _drain_and_assert_no_further_reads(env)


async def test_guard_workspace_mismatch_and_role_floor_map_403(env) -> None:
    """Guard-level facts: tenant mismatch and sub-viewer roles end with 403."""
    bound = AuthContext(
        user_id=env["user"].id,
        org_id=env["org"].id,
        workspace_id=env["ws"].id,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )
    other_ws = WorkspaceModel(org_id=env["org"].id, name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    env["db"].add(other_ws)
    await env["db"].commit()

    def _context_for(workspace_id: uuid.UUID | None, role: WorkspaceRole | None) -> AuthContext:
        return AuthContext(
            user_id=env["user"].id,
            org_id=env["org"].id,
            workspace_id=workspace_id if workspace_id is not None else env["ws"].id,
            role=role,
            auth_method="jwt",
            api_key_scope=None,
        )

    async def assert_maps_to(current: AuthContext, expected: int) -> None:
        async def fake_auth(token, db, **kwargs):  # noqa: ANN001 — mirrors authenticate_bearer
            return current

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(run_stream_guard, "authenticate_bearer", fake_auth)
            with pytest.raises(run_stream_guard.RunStreamAccessError) as caught:
                await run_stream_guard.recheck_stream_access(
                    token="token",
                    bound_ctx=bound,
                    workspace_id=env["ws"].id,
                    run_id=env["run"].id,
                    session_factory=stream_session_factory,
                )
        assert caught.value.status_code == expected

    # Re-authentication resolving to a different tenant never re-targets
    # the stream, and a role below the VIEWER floor is denied — both 403.
    await assert_maps_to(_context_for(other_ws.id, WorkspaceRole.EDITOR), 403)
    await assert_maps_to(_context_for(None, None), 403)

    # A VIEWER on the bound workspace stays authorized (no extra policy).
    async def fake_auth_viewer(token, db, **kwargs):  # noqa: ANN001
        return _context_for(None, WorkspaceRole.VIEWER)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(run_stream_guard, "authenticate_bearer", fake_auth_viewer)
        await run_stream_guard.recheck_stream_access(
            token="token",
            bound_ctx=bound,
            workspace_id=env["ws"].id,
            run_id=env["run"].id,
            session_factory=stream_session_factory,
        )

    # A credential that now names a different user maps to 401.
    async def fake_auth_other_user(token, db, **kwargs):  # noqa: ANN001
        return AuthContext(
            user_id=uuid.uuid4(),
            org_id=env["org"].id,
            workspace_id=env["ws"].id,
            role=WorkspaceRole.EDITOR,
            auth_method="jwt",
            api_key_scope=None,
        )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(run_stream_guard, "authenticate_bearer", fake_auth_other_user)
        with pytest.raises(run_stream_guard.RunStreamAccessError) as caught:
            await run_stream_guard.recheck_stream_access(
                token="token",
                bound_ctx=bound,
                workspace_id=env["ws"].id,
                run_id=env["run"].id,
                session_factory=stream_session_factory,
            )
    assert caught.value.status_code == 401


async def test_check_failure_ends_stream_500(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing check refuses to continue reading and ends with 500."""

    async def broken_auth(token, db, **kwargs):  # noqa: ANN001
        raise RuntimeError("driver detail with sql:// and payloads")

    monkeypatch.setattr(run_stream_guard, "authenticate_bearer", broken_auth)
    token = _jwt(env)
    response = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {token}"})
    _assert_revoked(response, 500, data_before=1)
    assert "driver detail" not in response.text
    await _drain_and_assert_no_further_reads(env)


# ---------------------------------------------------------------------------
# Timing: idle re-checks, buffered batches, post-close silence
# ---------------------------------------------------------------------------


async def test_idle_stream_revoked_without_events(env) -> None:
    """A stream with nothing to relay still ends when access is revoked."""
    token = _jwt(env)
    # Consume everything seeded so the stream is purely idle.
    page = await env["client"].get(
        f"/api/runs/{env['run'].id}/events", headers={"Authorization": f"Bearer {token}"}, params={"limit": 100}
    )
    assert page.status_code == 200 and not page.json()["has_more"]

    async def revoke() -> None:
        await asyncio.sleep(_INTERVAL * 3)

        async def work(session: AsyncSession) -> None:
            row = (
                await session.execute(select(WorkspaceMemberModel).where(WorkspaceMemberModel.id == env["member"].id))
            ).scalar_one()
            row.deleted = True

        await _commit_independent(work)

    task = asyncio.create_task(revoke())
    response = await env["client"].get(
        env["url"], headers={"Authorization": f"Bearer {token}"}, params={"cursor": page.json()["next_cursor"]}
    )
    await task
    _assert_revoked(response, 401, data_before=0)
    assert any(name == "relay" for name, _ in env_calls), "idle checkpoint still ran"


class _StubEnvelope:
    def __init__(self, event_id: str) -> None:
        self.event_id = event_id

    def to_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id}


async def test_obtained_batch_dropped_after_revocation(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch already read when the revocation surfaces is never relayed."""
    release_second = asyncio.Event()

    async def scripted(self, workspace_id, run_id, *, cursor=None, access_guard=None, **kwargs):  # noqa: ANN001
        if access_guard is not None:
            await access_guard.before_read()
        yield _StubEnvelope("first")
        # The second batch is obtained only when released; the pre-send
        # verification then finds the revocation and the batch is dropped.
        await release_second.wait()
        if access_guard is not None:
            await access_guard.before_send()
        yield _StubEnvelope("must-not-relay")
        while True:
            await asyncio.sleep(10)

    monkeypatch.setattr(TaskControlService, "stream_run_events", scripted)
    monkeypatch.setattr(run_stream_guard, "RECHECK_INTERVAL_SECONDS", _INTERVAL)
    token = _jwt(env)

    async def revoke_then_release() -> None:
        await asyncio.sleep(_INTERVAL * 4)  # the first envelope is relayed meanwhile

        async def work(session: AsyncSession) -> None:
            row = (
                await session.execute(select(WorkspaceMemberModel).where(WorkspaceMemberModel.id == env["member"].id))
            ).scalar_one()
            row.deleted = True

        await _commit_independent(work)
        release_second.set()

    task = asyncio.create_task(revoke_then_release())
    response = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {token}"})
    await task
    assert response.status_code == 200, response.text
    frames = _parse_sse(response.text)
    assert [frame for frame in frames if frame[0] is None] == [(None, {"event_id": "first"})]
    assert frames[-1] == ("error", {"code": 401})
    assert "must-not-relay" not in response.text


@pytest.mark.parametrize("entry", [False, True])
async def test_slow_consumer_rechecks_within_obtained_batch(env, monkeypatch, entry) -> None:
    """Backpressure between envelopes must not extend the access window."""
    if entry:
        event_store = InMemoryEventStore()
        monkeypatch.setattr("hecate.core.composition.entry_assembly.get_shared_event_store", lambda: event_store)
        env["run"].projection = {"entry_name": "chat", "state": "running"}
        await env["db"].commit()
        mapper = RunEventMapper(
            BackendRef(RefKind.TASK, env["task"].issuer_domain, str(env["task"].id)),
            BackendRef(RefKind.RUN, env["run"].issuer_domain, str(env["run"].id)),
            event_store=event_store,
        )
        await mapper.map_stream_event({"type": "message", "content": "first"})
        await mapper.map_stream_event({"type": "message", "content": "must-not-send"})
    else:
        run = env["run"]
        await PlatformEventService(env["db"]).emit(
            task_ref=BackendRef(RefKind.TASK, run.issuer_domain, str(run.task_id)),
            run_ref=BackendRef(RefKind.RUN, run.issuer_domain, str(run.id)),
            payload_schema_ref=RUN_TERMINAL,
            payload={"status": "succeeded"},
            actor=ActorRef(kind=ActorKind.PLATFORM, id="test-control"),
            workspace_id=run.workspace_id,
        )
        await env["db"].commit()

    clock = [0.0]
    monkeypatch.setattr(run_stream_guard, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    revoked = False

    async def recheck() -> None:
        if revoked:
            raise run_stream_guard.RunStreamAccessError(401)

    guard = run_stream_guard.PeriodicAccessGuard(recheck, 5.0)
    service = tasks_api.get_task_control_service(env["db"])
    stream = service.stream_run_events(env["ws"].id, env["run"].id, access_guard=guard)
    try:
        await anext(stream)
        reads = len(env_calls)
        revoked = True
        clock[0] = 6.0
        with pytest.raises(run_stream_guard.RunStreamAccessError) as caught:
            await anext(stream)
        assert caught.value.status_code == 401
        assert len(env_calls) == reads
    finally:
        await stream.aclose()


@pytest.mark.parametrize("changed", [{"org_id": uuid.uuid4()}, {"api_key_scope": "system"}, {"auth_method": "sso"}])
async def test_reauthentication_preserves_full_identity_binding(env, monkeypatch, changed) -> None:
    """A credential must not acquire a different organization or scope."""
    bound = AuthContext(
        user_id=env["user"].id,
        org_id=env["org"].id,
        workspace_id=env["ws"].id,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )

    async def auth(token, db, **kwargs):
        return replace(bound, **changed)

    monkeypatch.setattr(run_stream_guard, "authenticate_bearer", auth)
    with pytest.raises(run_stream_guard.RunStreamAccessError) as caught:
        await run_stream_guard.recheck_stream_access(
            token="token", bound_ctx=bound, workspace_id=env["ws"].id, run_id=env["run"].id
        )
    assert caught.value.status_code == 401


async def test_slow_verification_does_not_extend_fact_freshness(monkeypatch) -> None:
    """A slow query cannot reset the age of facts read before it waited."""
    clock = [0.0]
    checks = []
    monkeypatch.setattr(run_stream_guard, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def recheck() -> None:
        checks.append(clock[0])
        clock[0] += 4.0

    guard = run_stream_guard.PeriodicAccessGuard(recheck, 5.0)
    clock[0] = 5.0
    await guard.before_read()
    clock[0] = 10.0
    await guard.before_send()
    assert checks == [5.0, 10.0]


@pytest.mark.parametrize("failure", ["factory", "timeout"])
async def test_verifier_setup_or_timeout_ends_safely(env, monkeypatch, failure) -> None:
    """Verifier setup and unresponsive authentication both map to safe 500."""
    if failure == "factory":

        def broken_factory():
            raise RuntimeError("private database configuration")

        monkeypatch.setattr(run_stream_guard, "_session_factory", broken_factory)
    else:

        async def blocked_auth(token, db, **kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(run_stream_guard, "authenticate_bearer", blocked_auth)
        monkeypatch.setattr(run_stream_guard, "RECHECK_TIMEOUT_SECONDS", 0.01)
    bound = AuthContext(
        user_id=env["user"].id,
        org_id=env["org"].id,
        workspace_id=env["ws"].id,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )
    with pytest.raises(run_stream_guard.RunStreamAccessError) as caught:
        await asyncio.wait_for(
            run_stream_guard.recheck_stream_access(
                token="token",
                bound_ctx=bound,
                workspace_id=env["ws"].id,
                run_id=env["run"].id,
            ),
            timeout=1.0,
        )
    assert caught.value.status_code == 500


async def test_real_provider_failure_is_not_reported_as_revoked_credentials(env, monkeypatch) -> None:
    """The shared resolver must surface infrastructure faults to the verifier."""
    from hecate.enterprise.auth import resolver
    from hecate.enterprise.auth.api_key_provider import APIKeyAuthProvider
    from hecate.enterprise.auth.jwt_provider import JWTAuthProvider

    async def broken_provider(self, token, db):
        raise RuntimeError("private provider failure")

    monkeypatch.setattr(JWTAuthProvider, "authenticate", broken_provider)
    monkeypatch.setattr(resolver, "_providers", [JWTAuthProvider(), APIKeyAuthProvider()])
    bound = AuthContext(
        user_id=env["user"].id,
        org_id=env["org"].id,
        workspace_id=env["ws"].id,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )
    with pytest.raises(run_stream_guard.RunStreamAccessError) as caught:
        await run_stream_guard.recheck_stream_access(
            token=_jwt(env),
            bound_ctx=bound,
            workspace_id=env["ws"].id,
            run_id=env["run"].id,
        )
    assert caught.value.status_code == 500


# ---------------------------------------------------------------------------
# Compatibility: connect-time denials, terminal runs, cursor, entry source
# ---------------------------------------------------------------------------


async def test_connect_time_denials_stay_http_errors(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-stream denials keep the normal HTTP surfaces (no SSE frames)."""
    # Real chain: no credentials -> 401.
    missing = await env["client"].get(env["url"])
    assert missing.status_code == 401, missing.text
    # Real chain: a JWT without a workspace claim cannot scope the run.
    no_workspace = create_access_token(env["user"].id, org_id=env["org"].id)
    scoped = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {no_workspace}"})
    assert scoped.status_code == 403, scoped.text
    assert scoped.json()["detail"]["error"]["code"] == "NO_WORKSPACE"
    # A membership in a different workspace cannot see this run.
    other = WorkspaceModel(org_id=env["org"].id, name="Denied", slug=f"denied-{uuid.uuid4().hex[:8]}")
    env["db"].add(other)
    await env["db"].flush()
    env["db"].add(WorkspaceMemberModel(user_id=env["user"].id, workspace_id=other.id, role=WorkspaceRole.EDITOR))
    await env["db"].commit()
    foreign = _jwt(env, workspace_id=other.id)
    invisible = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {foreign}"})
    assert invisible.status_code == 404, invisible.text
    assert str(env["run"].id) in invisible.text  # only the caller-supplied id echoes
    assert "text/event-stream" not in invisible.headers.get("content-type", "")
    assert "event: error" not in invisible.text


async def test_terminal_run_and_cursor_resume_unchanged(env) -> None:
    """Terminal runs end on the terminal frame; cursors resume as before."""
    token = _jwt(env)

    async def finish() -> None:
        await asyncio.sleep(_INTERVAL * 3)

        async def work(session: AsyncSession) -> None:
            events = PlatformEventService(session)
            run = env["run"]
            await events.emit(
                task_ref=BackendRef(RefKind.TASK, run.issuer_domain, str(run.task_id)),
                run_ref=BackendRef(RefKind.RUN, run.issuer_domain, str(run.id)),
                payload_schema_ref=RUN_TERMINAL,
                payload={"status": "succeeded"},
                actor=ActorRef(kind=ActorKind.PLATFORM, id="test-control"),
                workspace_id=run.workspace_id,
            )

        await _commit_independent(work)

    task = asyncio.create_task(finish())
    response = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {token}"})
    await task
    assert response.status_code == 200, response.text
    frames = _parse_sse(response.text)
    assert all(event is None for event, _ in frames), response.text
    assert frames[-1][1]["payload_schema_ref"] == RUN_TERMINAL

    page = await env["client"].get(
        f"/api/runs/{env['run'].id}/events", headers={"Authorization": f"Bearer {token}"}, params={"limit": 1}
    )
    resumed = await env["client"].get(
        env["url"], headers={"Authorization": f"Bearer {token}"}, params={"cursor": page.json()["next_cursor"]}
    )
    assert resumed.status_code == 200, resumed.text
    frames = _parse_sse(resumed.text)
    assert frames[-1][1]["payload_schema_ref"] == RUN_TERMINAL


async def test_entry_source_revocation(env, monkeypatch: pytest.MonkeyPatch) -> None:
    """The entry-store event source enforces the same revocation."""
    event_store = InMemoryEventStore()
    monkeypatch.setattr("hecate.core.composition.entry_assembly.get_shared_event_store", lambda: event_store)
    run, task = env["run"], env["task"]
    run.projection = {"entry_name": "chat", "state": "running"}
    await env["db"].commit()
    mapper = RunEventMapper(
        BackendRef(RefKind.TASK, task.issuer_domain, str(task.id)),
        BackendRef(RefKind.RUN, run.issuer_domain, str(run.id)),
        event_store=event_store,
    )
    await mapper.map_stream_event({"type": "message", "content": "visible reply"})
    token = _jwt(env)

    async def revoke() -> None:
        await asyncio.sleep(_INTERVAL * 3)

        async def work(session: AsyncSession) -> None:
            row = (
                await session.execute(select(WorkspaceMemberModel).where(WorkspaceMemberModel.id == env["member"].id))
            ).scalar_one()
            row.deleted = True

        await _commit_independent(work)

    task_handle = asyncio.create_task(revoke())
    response = await env["client"].get(env["url"], headers={"Authorization": f"Bearer {token}"})
    await task_handle
    _assert_revoked(response, 401, data_before=1)
    assert "read_events" in [name for name, _ in env_calls]


# ---------------------------------------------------------------------------
# Real-TCP evidence
# ---------------------------------------------------------------------------


class _SseSocket:
    """Raw-socket SSE reader over a real TCP connection."""

    def __init__(self, base_url: str, path: str, token: str) -> None:
        host, _, port = base_url.removeprefix("http://").partition(":")
        self._sock = socket.create_connection((host, int(port)), timeout=30)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Authorization: Bearer {token}\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: close\r\n\r\n"
        )
        self._sock.sendall(request.encode())
        self._buffer = b""
        self.eof = False

    def _fill(self, deadline: float) -> bool:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        self._sock.settimeout(remaining)
        try:
            chunk = self._sock.recv(4096)
        except TimeoutError:
            return False
        if not chunk:
            self.eof = True
            return False
        self._buffer += chunk
        return True

    def read_headers(self) -> str:
        deadline = time.monotonic() + 20.0
        while b"\r\n\r\n" not in self._buffer:
            if not self._fill(deadline):
                raise AssertionError(f"no response headers: {self._buffer[:200]!r}")
        head, _, self._buffer = self._buffer.partition(b"\r\n\r\n")
        return head.decode()

    def read_frame(self, timeout: float) -> tuple[str | None, dict[str, Any]] | None:
        deadline = time.monotonic() + timeout
        while True:
            for sep in (b"\n\n", b"\r\n\r\n"):
                if sep in self._buffer:
                    block, _, self._buffer = self._buffer.partition(sep)
                    event: str | None = None
                    data: dict[str, Any] | None = None
                    for line in block.decode().splitlines():
                        if line.startswith("event: "):
                            event = line.removeprefix("event: ")
                        elif line.startswith("data: "):
                            data = json.loads(line.removeprefix("data: "))
                    if data is not None:
                        return event, data
                    continue  # a comment/keepalive block
            if not self._fill(deadline):
                return None

    def rst_close(self) -> None:
        """Abortive close (SO_LINGER 0 -> RST), like leaving an SSE page."""
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        self._sock.close()

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()


@pytest.fixture
async def tcp_stack(tmp_path, monkeypatch: pytest.MonkeyPatch, governance_session_factory):
    """uvicorn on a real socket over a file DB with real bearer auth."""
    from hecate.main import app

    server_factory = governance_session_factory
    async with server_factory.kw["bind"].begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with server_factory() as session:
        user = UserModel(email=f"tcp-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
        session.add(user)
        await session.flush()
        org = OrganizationModel(name="TCP org", slug=f"tcp-{uuid.uuid4().hex[:8]}", owner_id=user.id)
        session.add(org)
        await session.flush()
        ws = WorkspaceModel(org_id=org.id, name="TCP ws", slug=f"tcp-ws-{uuid.uuid4().hex[:8]}")
        session.add(ws)
        await session.flush()
        member = WorkspaceMemberModel(user_id=user.id, workspace_id=ws.id, role=WorkspaceRole.EDITOR)
        session.add(member)
        await session.flush()
        agent = AgentModel(workspace_id=ws.id, name="tcp-agent")
        session.add(agent)
        await session.flush()
        version = AgentVersionModel(
            agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="b" * 64
        )
        session.add(version)
        await session.flush()
        principal = AgentPrincipalModel(
            id=agent.id, agent_id=agent.id, workspace_id=ws.id, organization_id=org.id, owner_user_id=user.id
        )
        deployment = AgentDeploymentModel(
            agent_id=agent.id,
            agent_version_id=version.id,
            workspace_id=ws.id,
            backend_type=BackendType.BUILTIN,
            access_mode=AccessMode.IN_PROCESS,
            issuer_domain="hecate",
            capability_snapshot={},
            axes_harness="hecate",
            axes_environment="none",
            axes_tool_execution="hecate_gateway",
            is_default=True,
        )
        session.add_all([principal, deployment])
        await session.flush()
        chain = IdentityChain(
            initiator=str(user.id),
            principal_id=str(principal.id),
            workload=WorkloadIdentity(
                deployment=BackendRef(RefKind.DEPLOYMENT, "hecate", str(deployment.id)),
                workload_id="hecate:run-stream-tcp",
            ),
            audience="hecate:test",
        )
        registry = TaskRunRegistry(session)
        task = await registry.create_task(
            goal="tcp revocation stream", initiator_ref=chain.to_dict(), workspace_id=ws.id
        )
        run = await registry.create_run(
            task_id=task.id,
            workspace_id=ws.id,
            deployment_id=deployment.id,
            identity_chain=chain,
            backend_run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
        )
        await PlatformEventService(session).emit(
            task_ref=BackendRef(RefKind.TASK, task.issuer_domain, str(task.id)),
            run_ref=BackendRef(RefKind.RUN, run.issuer_domain, str(run.id)),
            payload_schema_ref=TASK_SUBMITTED,
            payload={"goal": "tcp revocation stream"},
            actor=ActorRef(kind=ActorKind.PLATFORM, id="test-control"),
            workspace_id=ws.id,
        )
        raw_key = f"hcat_tcp_{uuid.uuid4().hex}"
        key = ApiKeyModel(
            name="tcp-stream",
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            key_prefix=raw_key[:8],
            scope=ApiKeyScope.WORKSPACE,
            workspace_id=ws.id,
            created_by=user.id,
            is_active=True,
        )
        session.add(key)
        await session.commit()

    store = SqlDurableStore(f"sqlite:///{tmp_path / 'run-stream-tcp-durable.db'}", source="platform")
    store.create_schema()
    monkeypatch.setattr(
        tasks_api,
        "get_durable_suite",
        lambda: DurableSuite(store=store, recorder=store, ledger=store, backend="postgres", ledger_source="core"),
    )
    monkeypatch.setattr(tasks_api, "_worker_state", SimpleNamespace(durable_outbox_relay=_StubRelay()))
    monkeypatch.setattr("hecate.core.database.async_session_factory", server_factory)
    monkeypatch.setattr(run_stream_guard, "RECHECK_INTERVAL_SECONDS", 0.1)
    monkeypatch.setattr(settings, "JWT_SECRET", "test-jwt-secret-for-ci")

    async def override_get_db():
        async with server_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setitem(app.dependency_overrides, get_db, override_get_db)

    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, loop="asyncio", lifespan="off", log_level="warning", access_log=False
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if server.started and server.servers:
            break
        if server_task.done():
            await server_task
            raise RuntimeError("uvicorn stopped during startup")
        await asyncio.sleep(0.05)
    assert server.started and server.servers, "uvicorn startup timed out"
    port = server.servers[0].sockets[0].getsockname()[1]

    token = create_access_token(user.id, org_id=org.id, workspace_id=ws.id, role=WorkspaceRole.EDITOR.value)
    try:
        yield {
            "base_url": f"http://127.0.0.1:{port}",
            "path": f"/api/runs/{run.id}/events/stream",
            "run_id": run.id,
            "run_path": f"/api/runs/{run.id}",
            "member_id": member.id,
            "key_id": key.id,
            "raw_key": raw_key,
            "token": token,
            "reader_factory": server_factory,
        }
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, 10)
        store.dispose()


async def test_real_tcp_membership_removal_closes_stream(tcp_stack) -> None:
    """Real TCP: an established stream ends with 401 after membership removal."""
    client = await asyncio.to_thread(_SseSocket, tcp_stack["base_url"], tcp_stack["path"], tcp_stack["token"])
    try:
        headers = await asyncio.to_thread(client.read_headers)
        assert headers.startswith("HTTP/1.1 200"), headers
        assert "text/event-stream" in headers
        first = await asyncio.to_thread(client.read_frame, timeout=10.0)
        assert first is not None and first[0] is None, "expected a first data frame"

        async with tcp_stack["reader_factory"]() as session:
            row = (
                await session.execute(
                    select(WorkspaceMemberModel).where(WorkspaceMemberModel.id == tcp_stack["member_id"])
                )
            ).scalar_one()
            row.deleted = True
            await session.commit()

        frames = []
        closed = False
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            frame = await asyncio.to_thread(client.read_frame, timeout=max(0.1, deadline - time.monotonic()))
            if frame is None:
                closed = True
                break
            frames.append(frame)
        assert closed, f"stream did not close: frames={frames}"
        assert client.eof, "read timeout is not proof that the stream closed"
        assert frames and frames[-1] == ("error", {"code": 401}), frames
        assert all(event is None for event, _ in frames[:-1]), frames

        # The observation never cancelled or woke the run.
        async with tcp_stack["reader_factory"]() as session:
            from hecate.models.run import RunModel

            run = (await session.execute(select(RunModel).where(RunModel.id == tcp_stack["run_id"]))).scalar_one()
            assert not run.deleted
    finally:
        client.close()


async def test_real_tcp_api_key_recheck_does_not_wait_for_request_transaction(tcp_stack, monkeypatch) -> None:
    """Initial key bookkeeping must not lock out subsequent rechecks."""
    checked = asyncio.Event()
    original = run_stream_guard.authenticate_bearer

    async def observe_auth(token, db, **kwargs):
        context = await original(token, db, **kwargs)
        if context is not None:
            checked.set()
        return context

    monkeypatch.setattr(run_stream_guard, "authenticate_bearer", observe_auth)
    client = await asyncio.to_thread(_SseSocket, tcp_stack["base_url"], tcp_stack["path"], tcp_stack["raw_key"])
    try:
        headers = await asyncio.to_thread(client.read_headers)
        assert headers.startswith("HTTP/1.1 200"), headers
        assert await asyncio.to_thread(client.read_frame, timeout=2.0) is not None
        await asyncio.wait_for(checked.wait(), timeout=1.0)
        async with tcp_stack["reader_factory"]() as session:
            key = await session.get(ApiKeyModel, tcp_stack["key_id"])
            assert key is not None
            key.is_active = False
            await asyncio.wait_for(session.commit(), timeout=1.0)
        assert await asyncio.to_thread(client.read_frame, timeout=2.0) == ("error", {"code": 401})
        assert await asyncio.to_thread(client.read_frame, timeout=2.0) is None
        assert client.eof
    finally:
        client.close()


async def test_real_tcp_disconnect_during_verification_releases_sessions(tcp_stack, monkeypatch) -> None:
    """Disconnect cancellation closes both the request and verifier sessions."""
    checking = asyncio.Event()
    original = run_stream_guard.authenticate_bearer

    async def block_auth(token, db, **kwargs):
        await original(token, db, **kwargs)
        checking.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(run_stream_guard, "authenticate_bearer", block_auth)
    client = await asyncio.to_thread(_SseSocket, tcp_stack["base_url"], tcp_stack["path"], tcp_stack["token"])
    try:
        assert (await asyncio.to_thread(client.read_headers)).startswith("HTTP/1.1 200")
        assert await asyncio.to_thread(client.read_frame, timeout=2.0) is not None
        await asyncio.wait_for(checking.wait(), timeout=1.0)
        client.rst_close()
        pool = tcp_stack["reader_factory"].kw["bind"].pool
        async with asyncio.timeout(2.0):
            while pool.checkedout():
                await asyncio.sleep(0.01)
    finally:
        client.close()


async def test_real_tcp_disconnect_closes_upstream_generator(tcp_stack, monkeypatch) -> None:
    """Real TCP: an abortive client disconnect tears down the server stream."""
    closed = threading.Event()

    original = TaskControlService.stream_run_events

    def tracking(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        async def generator():
            try:
                async for envelope in original(self, *args, **kwargs):
                    yield envelope
            finally:
                closed.set()

        return generator()

    monkeypatch.setattr(TaskControlService, "stream_run_events", tracking)

    client = await asyncio.to_thread(_SseSocket, tcp_stack["base_url"], tcp_stack["path"], tcp_stack["token"])
    try:
        headers = await asyncio.to_thread(client.read_headers)
        assert headers.startswith("HTTP/1.1 200"), headers
        first = await asyncio.to_thread(client.read_frame, timeout=10.0)
        assert first is not None
        client.rst_close()
    finally:
        client.close()

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not closed.is_set():
        await asyncio.sleep(0.05)
    assert closed.is_set(), "upstream generator was not closed on disconnect"

    # The server stays healthy after the teardown.
    import httpx

    async with httpx.AsyncClient(base_url=tcp_stack["base_url"]) as http:
        response = await http.get(tcp_stack["run_path"], headers={"Authorization": f"Bearer {tcp_stack['token']}"})
    assert response.status_code == 200, response.text
