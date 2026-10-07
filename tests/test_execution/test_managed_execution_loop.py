"""Managed execution closed loop (step6a): delivery → accept → serial
execution → event/result upload → platform projection.

These tests wire the real runner assembly (durable store + engine + managed
channel + serial scheduler, the same components the CLI assembles) against
the platform's ``/managed`` API over the full wire format. They cover the
iteration-2 acceptance edges: accept≠running, idempotent redelivery after a
lost accept response, restart recovery of the same Task/Run with event
backfill, disconnect buffering with per-run backfill, and identity-drift
reconciliation. Process-level (installed wheel) acceptance stays with
step6f per the evolution plan.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_durable.contracts.references import run_ref as mk_run_ref
from hecate_durable.contracts.references import task_ref as mk_task_ref
from hecate_runner.durable import DurableRuntime
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceStore
from hecate_runner.managed import (
    ManagedChannel,
    ManagedExecutionScheduler,
    ManagedIdentity,
    managed_principal_for,
    managed_run_ref,
    managed_task_ref,
)
from hecate_runner.profile import load_profile
from hecate_runtime.manifest import sha256_hex
from sqlalchemy import select

from hecate.channel.api.managed import router as managed_router
from hecate.core.auth_context import AuthContext
from hecate.core.composition.managed_secrets import clear_managed_secrets, register_managed_secret
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.execution import managed_credentials as mc
from hecate.execution.managed_channel import ManagedDeliveryService
from hecate.execution.trust_roots import TrustRootRegistry
from hecate.models.audit import AuditLogModel  # noqa: F401  (registers audit_logs before schema creation)
from hecate.models.managed_delivery import ManagedDeliveryModel
from hecate.models.organization import OrganizationModel
from hecate.models.run import RunModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from tests.conftest import DEFAULT_WORKSPACE_ID, test_session_factory

WS = DEFAULT_WORKSPACE_ID
ADMIN = uuid.uuid4()
ROOT_NAME = "host-root-loop"
ISSUER = "managed-loop-issuer"
SECRET = b"managed-loop-secret"
ENTRY_NAME = "agents/summary/main.json"
ENTRY_CONTENT = b'{"kind": "entry", "tools": ["query_inventory"]}'
READ_SCHEMA_NAME = "tools/query_inventory.schema.json"
READ_SCHEMA = {
    "type": "object",
    "required": ["domain", "sku"],
    "properties": {"domain": {"type": "string", "minLength": 1}, "sku": {"type": "string", "minLength": 1}},
}
# One managed run with one tool dispatch mirrors exactly seven task facts:
# task_submitted, running task_state, terminal task_state, run_terminal,
# plus the action-ledger trio (action_intent, action_claimed,
# action_outcome) emitted by the durable ledger around the dispatch.
FACTS_PER_RUN = 7


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MANAGED_SECRET", SECRET.decode())
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")
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
    """The platform app (full wire format over ASGI) + operator helpers."""

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

    async def admit() -> None:
        async with test_session_factory() as session:
            await TrustRootRegistry(session).register(
                workspace_id=WS,
                name=ROOT_NAME,
                material_digest=mc.material_digest(SECRET),
                issuer_domain=ISSUER,
                config_fingerprint="cfg-loop",
            )
            await session.commit()

    async def opt_in() -> None:
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

    async def enrollment_id() -> str:
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
            return str(row.id)

    async def queue_delivery(input_payload: dict) -> str:
        """Queue one managed delivery for a real platform Task/Run row.

        The enrollment id is resolved BEFORE the write session opens: the
        test engine is a single shared in-memory SQLite connection, and a
        nested session's close would roll back the outer transaction's
        pending rows.
        """

        from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
        from hecate.contracts.execution.references import deployment_ref
        from hecate.execution.task_run_registry import TaskRunRegistry
        from hecate.models.agent import AgentModel
        from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
        from hecate.models.agent_principal import AgentPrincipalModel
        from hecate.models.agent_version import AgentVersionModel

        enrollment = uuid.UUID(await enrollment_id())
        async with test_session_factory() as db:
            agent = AgentModel(workspace_id=WS, name="managed-loop-agent")
            db.add(agent)
            await db.flush()
            version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={}, content_hash="f" * 64)
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
            chain = IdentityChain(
                initiator=None,
                principal_id=str(agent.id),
                workload=WorkloadIdentity(
                    deployment=deployment_ref("hecate", str(deployment.id)), workload_id="hecate:managed"
                ),
                audience="hecate:managed-channel",
            )
            registry = TaskRunRegistry(db)
            task = await registry.create_task(goal="managed loop", initiator_ref=chain.to_dict(), workspace_id=WS)
            run = await registry.create_run(
                task_id=task.id,
                workspace_id=WS,
                deployment_id=deployment.id,
                identity_chain=chain,
                backend_run_ref=mk_run_ref("hecate", str(uuid.uuid4())),
            )
            service = ManagedDeliveryService(db)
            delivery = await service.queue_delivery(
                workspace_id=WS,
                enrollment_id=enrollment,
                delivery_id=uuid.uuid4(),
                task_ref={**mk_task_ref("hecate", str(task.id)).to_dict(), "kind": "task"},
                run_ref={**mk_run_ref("hecate", str(run.id)).to_dict(), "kind": "run"},
                input_payload=input_payload,
            )
            await db.commit()
            return str(delivery.id)

    yield {"transport": transport, "admit": admit, "opt_in": opt_in, "queue_delivery": queue_delivery}
    app.dependency_overrides.clear()


class _BusinessApi:
    """Records managed tool dispatches (the business side-effect counter)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append({"tool": tool_name, "arguments": dict(arguments), "principal": principal, "domains": domains})
        return {"status": "ok", "result": f"{tool_name}-result"}


def _write_managed_profile(tmp_path: Path, database_url: str) -> Path:
    """A durable + control_plane runner profile mirroring the CLI assembly."""

    profile = tmp_path / "profile"
    (profile / "files" / "agents/summary").mkdir(parents=True, exist_ok=True)
    (profile / "files" / ENTRY_NAME).write_bytes(ENTRY_CONTENT)
    schema_bytes = json.dumps(READ_SCHEMA).encode()
    (profile / "files" / "tools").mkdir(parents=True, exist_ok=True)
    (profile / "files" / READ_SCHEMA_NAME).write_bytes(schema_bytes)
    manifest = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": ENTRY_NAME,
        "files": [
            {"path": ENTRY_NAME, "sha256": sha256_hex(ENTRY_CONTENT), "size": len(ENTRY_CONTENT)},
            {"path": READ_SCHEMA_NAME, "sha256": sha256_hex(schema_bytes), "size": len(schema_bytes)},
        ],
        "tools": [{"name": "query_inventory", "schema_ref": READ_SCHEMA_NAME, "permission": "read"}],
    }
    (profile / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (profile / "runner.json").write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 0,
                "evidence_dir": "evidence",
                "model": {"backend": "stub"},
                "tool_allowlist": ["query_inventory"],
                "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
                "durable": {"database_url": database_url, "workspace": "managed-loop"},
                "control_plane": {
                    "base_url": "http://platform.test",
                    "workspace_id": str(WS),
                    "trust_root": ROOT_NAME,
                    "host_id": "host-loop",
                    "issuer_domain": ISSUER,
                    "secret_ref": "env:MANAGED_SECRET",
                    "data_domains": ["domain_a"],
                },
            }
        ),
        encoding="utf-8",
    )
    (profile / "identity.json").write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "principal": "app-reader",
                        "role": "read_only",
                        "domains": ["domain_a"],
                        "credential": "file:secrets/app-reader-token",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (profile / "secrets").mkdir(exist_ok=True)
    (profile / "secrets/app-reader-token").write_text("reader-secret-token", encoding="utf-8")
    return profile


class _Host:
    """One runner "process": durable store + engine + channel + scheduler."""

    def __init__(self, profile_dir: Path, transport: httpx.ASGITransport, *, api: _BusinessApi) -> None:
        self.profile = load_profile(profile_dir)
        assert self.profile.config.durable is not None
        self.store = _open_store(self.profile.config.durable.database_url)
        self.durable = DurableRuntime(self.store, workspace=self.profile.config.durable.workspace)
        self.evidence = EvidenceStore(self.profile.config.evidence_dir)
        self.api = api
        cp = self.profile.config.control_plane
        assert cp is not None
        self.engine = ExecutionEngine(
            self.profile,
            self.evidence,
            api,
            durable=self.durable,
            managed_identity=ManagedIdentity(principal=managed_principal_for(cp.trust_root), domains=cp.data_domains),
        )
        self.channel = ManagedChannel(
            base_url=cp.base_url,
            workspace_id=cp.workspace_id,
            trust_root=cp.trust_root,
            host_id=cp.host_id,
            secret=SECRET,
            issuer_domain=cp.issuer_domain,
            store=self.store,
            poll_interval_seconds=0.05,
            data_domains=cp.data_domains,
            transport=transport,
            execution_definition=self.profile.execution_definition_digest(),
        )
        self.scheduler = ManagedExecutionScheduler(store=self.store, engine=self.engine, poll_interval_seconds=0.05)

    def close(self) -> None:
        self.store.dispose()


def _open_store(database_url: str):
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(database_url)
    store.create_schema()
    return store


async def _wait_terminal(store, task_ref, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = store.get_task_state(task_ref)
        if record is not None and record.lifecycle_state not in {
            TaskLifecycleState.QUEUED,
            TaskLifecycleState.RUNNING,
        }:
            return record
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {task_ref.id} did not reach a terminal state")


async def _platform_query(factory):
    async with test_session_factory() as db:
        return await factory(db)


async def _delivery_state(delivery_row_id: str) -> tuple[str, dict | None]:
    async def _query(db):
        row = (
            await db.execute(select(ManagedDeliveryModel).where(ManagedDeliveryModel.id == uuid.UUID(delivery_row_id)))
        ).scalar_one()
        return row.state, row.accepted_refs

    return await _platform_query(_query)


async def _platform_run_id_of(delivery_row_id: str) -> str:
    async def _query(db):
        row = (
            await db.execute(select(ManagedDeliveryModel).where(ManagedDeliveryModel.id == uuid.UUID(delivery_row_id)))
        ).scalar_one()
        return str(row.run_ref["id"])

    return await _platform_query(_query)


async def _run_projection(platform_run_id: str) -> dict:
    async def _query(db):
        run = (await db.execute(select(RunModel).where(RunModel.id == uuid.UUID(platform_run_id)))).scalar_one()
        return dict(run.projection or {})

    return await _platform_query(_query)


async def _drain_all(host: _Host, row_ids: tuple[str, ...], timeout: float = 30.0) -> None:
    """Drive the scheduler until every accepted task reaches a terminal state.

    The pick order within one pull batch is not a FIFO contract (equal
    created_at timestamps order arbitrarily), so completion — serially,
    nothing skipped — is the assertion, not the sequence.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await host.scheduler.drain_once():
            continue
        states = [host.store.get_task_state(managed_task_ref(row_id)).lifecycle_state for row_id in row_ids]
        if all(state not in (TaskLifecycleState.QUEUED, TaskLifecycleState.RUNNING) for state in states):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("scheduler did not drain all accepted tasks")


def _delivery_input(sequence: int) -> dict:
    return {
        "prompt": f"managed work {sequence}",
        "tool_arguments": {"query_inventory": {"domain": "domain_a", "sku": f"S-{sequence}"}},
    }


async def test_accept_is_queued_and_projection_starts_empty(platform, tmp_path: Path):
    await platform["admit"]()
    profile_dir = _write_managed_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}")
    host = _Host(profile_dir, platform["transport"], api=_BusinessApi())
    try:
        assert await host.channel.register() is True
        await platform["opt_in"]()
        delivery_row_id = await platform["queue_delivery"](_delivery_input(1))

        assert await host.channel.pull_once() == 1
        state, accepted = await _delivery_state(delivery_row_id)
        assert state == "delivered" and accepted is not None

        # Accept means accepted: the local queue is QUEUED and the platform
        # Run projection carries no execution state until host events arrive.
        record = host.store.get_task_state(managed_task_ref(delivery_row_id))
        assert record is not None and record.lifecycle_state is TaskLifecycleState.QUEUED
        stamped = host.store.get_task_input(managed_task_ref(delivery_row_id))
        assert stamped is not None
        assert stamped["_host_identity"]["principal"] == managed_principal_for(ROOT_NAME)
        platform_run_id = await _platform_run_id_of(delivery_row_id)
        assert await _run_projection(platform_run_id) == {}
    finally:
        host.close()


async def test_normal_path_executes_serially_and_projects_terminal(platform, tmp_path: Path):
    await platform["admit"]()
    profile_dir = _write_managed_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}")
    api = _BusinessApi()
    host = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await host.channel.register() is True
        await platform["opt_in"]()
        first = await platform["queue_delivery"](_delivery_input(1))
        second = await platform["queue_delivery"](_delivery_input(2))

        assert await host.channel.pull_once() == 2
        # Serial scheduling: one task per drain tick; the slot refuses
        # overlap while a run holds it (the pick order across the batch is
        # not a FIFO contract — completion of every accepted task is).
        assert await host.scheduler.drain_once() is True
        assert await host.scheduler.drain_once() is False
        await _drain_all(host, (first, second))
        assert await host.scheduler.drain_once() is False  # queue drained

        # Exactly one business dispatch per run, under the stamped identity.
        assert len(api.calls) == 2
        assert {call["principal"] for call in api.calls} == {managed_principal_for(ROOT_NAME)}
        assert all(call["domains"] == ["domain_a"] for call in api.calls)

        # Terminal facts upload and the platform folds the real terminal.
        assert await host.channel.upload_events() == FACTS_PER_RUN * 2
        for row_id in (first, second):
            platform_run_id = await _platform_run_id_of(row_id)
            projection = await _run_projection(platform_run_id)
            assert projection["state"] == "succeeded"
            assert projection["source_cursors"], "per-source cursors must be recorded"
        # The replay (lost upload response) is fully deduplicated upstream.
        assert await host.channel.upload_events() == 0
    finally:
        host.close()


async def test_lost_accept_response_redelivers_without_reexecution(platform, tmp_path: Path, monkeypatch):
    await platform["admit"]()
    profile_dir = _write_managed_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}")
    api = _BusinessApi()
    host = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await host.channel.register() is True
        await platform["opt_in"]()
        delivery_row_id = await platform["queue_delivery"](_delivery_input(1))

        original = host.channel._request
        dropped = False

        async def request(method, path, **kwargs):
            nonlocal dropped
            if path == "/managed/host/accept" and not dropped:
                dropped = True
                return None  # local accept persisted, platform receipt lost
            return await original(method, path, **kwargs)

        monkeypatch.setattr(host.channel, "_request", request)
        assert await host.channel.pull_once() == 1
        state, _ = await _delivery_state(delivery_row_id)
        assert state == "pending"  # no receipt reached the platform

        # Redelivery (the row is still pending upstream) is answered from
        # the idempotent accept record: no second task, no execution.
        monkeypatch.setattr(host.channel, "_request", original)
        assert await host.channel.pull_once() == 0
        assert host.channel.stats.deliveries_duplicate == 1
        assert len(host.store.list_tasks()) == 1
        assert api.calls == []

        state, accepted = await _delivery_state(delivery_row_id)
        assert state == "delivered" and accepted is not None

        # Execution happens exactly once after the reconciliation.
        assert await host.scheduler.drain_once() is True
        await _wait_terminal(host.store, managed_task_ref(delivery_row_id))
        assert len(api.calls) == 1
    finally:
        host.close()


async def test_restart_recovers_same_task_run_and_backfills_events(platform, tmp_path: Path):
    await platform["admit"]()
    database_url = f"sqlite:///{tmp_path / 'host.db'}"
    profile_dir = _write_managed_profile(tmp_path, database_url)
    api = _BusinessApi()
    crashed = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await crashed.channel.register() is True
        await platform["opt_in"]()
        delivery_row_id = await platform["queue_delivery"](_delivery_input(1))
        assert await crashed.channel.pull_once() == 1
    finally:
        crashed.store.dispose()  # "process death": the DB file outlives it

    # A fresh host process over the same durable database and profile.
    restarted = _Host(profile_dir, platform["transport"], api=api)
    try:
        task_ref = managed_task_ref(delivery_row_id)
        record = restarted.store.get_task_state(task_ref)
        assert record is not None and record.lifecycle_state is TaskLifecycleState.QUEUED
        # Standalone replay must not touch the managed task...
        assert restarted.engine.resume(task_ref, managed_run_ref(delivery_row_id), {}) is None
        assert restarted.store.get_task_state(task_ref).lifecycle_state is TaskLifecycleState.QUEUED

        # Reconnect: re-registration (duplicate tolerated) restores the
        # channel credential before anything uploads.
        assert await restarted.channel.register() is True
        # ...the managed scheduler recovers the SAME Task/Run and executes.
        assert await restarted.scheduler.drain_once() is True
        await _wait_terminal(restarted.store, task_ref)
        assert len(api.calls) == 1

        # Events never uploaded by the crashed process backfill on reconnect
        # and a replayed upload is deduplicated by the platform.
        assert await restarted.channel.upload_events() == FACTS_PER_RUN
        assert await restarted.channel.upload_events() == 0
        platform_run_id = await _platform_run_id_of(delivery_row_id)
        assert (await _run_projection(platform_run_id))["state"] == "succeeded"
    finally:
        restarted.close()


async def test_disconnect_buffers_then_backfills_both_runs_without_skip(platform, tmp_path: Path, monkeypatch):
    await platform["admit"]()
    profile_dir = _write_managed_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}")
    api = _BusinessApi()
    host = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await host.channel.register() is True
        await platform["opt_in"]()
        first = await platform["queue_delivery"](_delivery_input(1))
        second = await platform["queue_delivery"](_delivery_input(2))
        assert await host.channel.pull_once() == 2

        await _drain_all(host, (first, second))

        # Disconnect: uploads fail while local execution continued as usual.
        original = host.channel._request

        async def failing_request(method, path, **kwargs):
            return None  # every platform call degrades

        monkeypatch.setattr(host.channel, "_request", failing_request)
        assert await host.channel.upload_events() == 0
        monkeypatch.setattr(host.channel, "_request", original)

        # Reconnect: both runs' full streams backfill, nothing skipped.
        assert await host.channel.upload_events() == FACTS_PER_RUN * 2
        for row_id in (first, second):
            platform_run_id = await _platform_run_id_of(row_id)
            projection = await _run_projection(platform_run_id)
            assert projection["state"] == "succeeded"
            run_ref = host.store.run_for_task(managed_task_ref(row_id))
            page = host.store.read_events(run_ref)
            max_sequence = max(e.source_sequence for e in page.events if e.kind.value == "event")
            cursors = projection["source_cursors"]
            assert all(cursors.get(source, 0) >= max_sequence for source in cursors)
        assert await host.channel.upload_events() == 0
    finally:
        host.close()


async def test_identity_drift_stops_task_into_reconciliation(platform, tmp_path: Path):
    await platform["admit"]()
    profile_dir = _write_managed_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}")
    api = _BusinessApi()
    host = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await host.channel.register() is True
        await platform["opt_in"]()
        delivery_row_id = await platform["queue_delivery"](_delivery_input(1))
        assert await host.channel.pull_once() == 1
    finally:
        host.close()

    # Restart with a different managed scope: the accept-time stamp no
    # longer matches, so the task stops into reconciliation instead of
    # executing under a re-homed identity.
    drifted = _Host(profile_dir, platform["transport"], api=api)
    try:
        drift_engine = ExecutionEngine(
            drifted.profile,
            drifted.evidence,
            api,
            durable=drifted.durable,
            managed_identity=ManagedIdentity(principal=managed_principal_for(ROOT_NAME), domains=("domain_b",)),
        )
        scheduler = ManagedExecutionScheduler(store=drifted.store, engine=drift_engine, poll_interval_seconds=0.05)
        task_ref = managed_task_ref(delivery_row_id)
        assert await scheduler.drain_once() is False
        record = drifted.store.get_task_state(task_ref)
        assert record is not None and record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
        assert api.calls == []
        # The original stamp is preserved for operator reconciliation.
        preserved = drifted.store.get_task_input(task_ref)
        assert preserved is not None
        assert preserved["_host_identity"]["domains"] == ["domain_a"]
    finally:
        drifted.close()


async def test_unprojectable_stream_does_not_starve_other_runs_or_upload_local_tasks(platform, tmp_path, monkeypatch):
    """The upload loop isolates both event streams and independent task origins."""
    await platform["admit"]()
    host = _Host(
        _write_managed_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}"),
        platform["transport"],
        api=_BusinessApi(),
    )
    try:
        assert await host.channel.register()
        await platform["opt_in"]()
        first = await platform["queue_delivery"](_delivery_input(1))
        second = await platform["queue_delivery"](_delivery_input(2))
        assert await host.channel.pull_once() == 2
        await _drain_all(host, (first, second))
        host.durable.submit(
            principal="local-reader",
            domains=("domain_a",),
            run_input=_delivery_input(3),
            idempotency_key="local-task",
        )
        original = host.channel._request
        attempts = []

        async def rejecting_first(method, path, **kwargs):
            if path == "/managed/host/events":
                ref = kwargs["payload"]["local_task_ref"]
                attempts.append(ref)
                if ref["id"] == managed_task_ref(first).id:
                    return None
            return await original(method, path, **kwargs)

        monkeypatch.setattr(host.channel, "_request", rejecting_first)
        assert await host.channel.upload_events() == FACTS_PER_RUN
        assert all(ref["issuer_domain"] == "managed-host" for ref in attempts)
        assert (await _run_projection(await _platform_run_id_of(first))) == {}
        assert (await _run_projection(await _platform_run_id_of(second)))["state"] == "succeeded"
        monkeypatch.setattr(host.channel, "_request", original)
        assert await host.channel.upload_events() == FACTS_PER_RUN
    finally:
        host.close()
