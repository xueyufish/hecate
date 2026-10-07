"""Managed action-time authorization (step6b): the lease gate at the
protected-dispatch boundary.

These tests drive the real runner assembly (durable store + engine with a
shared lease gate + managed channel + serial scheduler) against the
platform's ``/managed`` API and count actual business-API calls. They pin
the step6b acceptance edges: scoped-lease authorization, disconnect expiry
refusal with zero side effects, stale-lease (nonce replay) refusal,
cross-domain refusal, audience binding, and the empty-gate-after-restart
path. Readonly dispatches continue without the gate throughout.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from hecate_durable.contracts.credentials import CredentialError, lease_claims, verify_lease
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_durable.contracts.references import run_ref as mk_run_ref
from hecate_durable.contracts.references import task_ref as mk_task_ref
from hecate_runner.durable import DurableRuntime
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceStore
from hecate_runner.managed import (
    LeaseGate,
    ManagedChannel,
    ManagedExecutionScheduler,
    ManagedIdentity,
    managed_principal_for,
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
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from tests.conftest import DEFAULT_WORKSPACE_ID, test_session_factory

WS = DEFAULT_WORKSPACE_ID
ADMIN = uuid.uuid4()
ROOT_NAME = "host-root-auth"
ISSUER = "managed-auth-issuer"
SECRET = b"managed-auth-secret"
ENTRY_NAME = "agents/summary/main.json"
ENTRY_CONTENT = b'{"kind": "entry", "tools": ["query_inventory", "submit_inventory_update"]}'
READ_SCHEMA_NAME = "tools/query_inventory.schema.json"
WRITE_SCHEMA_NAME = "tools/submit_inventory_update.schema.json"
READ_SCHEMA = {
    "type": "object",
    "required": ["domain", "sku"],
    "properties": {"domain": {"type": "string", "minLength": 1}, "sku": {"type": "string", "minLength": 1}},
}
WRITE_SCHEMA = {
    "type": "object",
    "required": ["domain", "sku", "quantity"],
    "properties": {
        "domain": {"type": "string", "minLength": 1},
        "sku": {"type": "string", "minLength": 1},
        "quantity": {"type": "integer", "minimum": 1},
    },
}


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
                config_fingerprint="cfg-auth",
            )
            await session.commit()

    async def opt_in(managed_scope: list[str]) -> None:
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
                row.id,
                WS,
                managed_new_runs=True,
                operator_id=ADMIN,
                admitted=True,
                managed_scope=managed_scope,
            )
            await session.commit()

    async def enrollment_scope() -> list[str]:
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
            return list(row.managed_scope or [])

    async def queue_delivery(input_payload: dict) -> str:
        from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
        from hecate.contracts.execution.references import deployment_ref
        from hecate.execution.task_run_registry import TaskRunRegistry
        from hecate.models.agent import AgentModel
        from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
        from hecate.models.agent_principal import AgentPrincipalModel
        from hecate.models.agent_version import AgentVersionModel
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
            enrollment = uuid.UUID(str(row.id))
        # Resolved before the write session opens: the shared in-memory test
        # engine is one connection, a nested close would roll back the
        # outer transaction's pending rows.
        async with test_session_factory() as db:
            agent = AgentModel(workspace_id=WS, name="managed-auth-agent")
            db.add(agent)
            await db.flush()
            version = AgentVersionModel(agent_id=agent.id, version=1, config_snapshot={}, content_hash="a" * 64)
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
            task = await registry.create_task(goal="managed auth", initiator_ref=chain.to_dict(), workspace_id=WS)
            run = await registry.create_run(
                task_id=task.id,
                workspace_id=WS,
                deployment_id=deployment.id,
                identity_chain=chain,
                backend_run_ref=mk_run_ref("hecate", str(uuid.uuid4())),
            )
            delivery = await ManagedDeliveryService(db).queue_delivery(
                workspace_id=WS,
                enrollment_id=enrollment,
                delivery_id=uuid.uuid4(),
                task_ref={**mk_task_ref("hecate", str(task.id)).to_dict(), "kind": "task"},
                run_ref={**mk_run_ref("hecate", str(run.id)).to_dict(), "kind": "run"},
                input_payload=input_payload,
            )
            await db.commit()
            return str(delivery.id)

    yield {
        "transport": transport,
        "admit": admit,
        "opt_in": opt_in,
        "enrollment_scope": enrollment_scope,
        "queue_delivery": queue_delivery,
    }
    app.dependency_overrides.clear()


class _BusinessApi:
    """The business side-effect counter (per-tool dispatch recording)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append({"tool": tool_name, "arguments": dict(arguments), "principal": principal})
        return {"status": "ok", "result": f"{tool_name}-result"}

    def count(self, tool_name: str) -> int:
        return sum(1 for call in self.calls if call["tool"] == tool_name)


def _write_auth_profile(tmp_path: Path, database_url: str, *, data_domains: tuple[str, ...]) -> Path:
    """Durable + control_plane profile declaring one read and one write tool."""

    profile = tmp_path / "profile"
    (profile / "files" / "agents/summary").mkdir(parents=True, exist_ok=True)
    (profile / "files" / "tools").mkdir(parents=True, exist_ok=True)
    (profile / "files" / ENTRY_NAME).write_bytes(ENTRY_CONTENT)
    read_bytes = json.dumps(READ_SCHEMA).encode()
    write_bytes = json.dumps(WRITE_SCHEMA).encode()
    (profile / "files" / READ_SCHEMA_NAME).write_bytes(read_bytes)
    (profile / "files" / WRITE_SCHEMA_NAME).write_bytes(write_bytes)
    manifest = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": ENTRY_NAME,
        "files": [
            {"path": ENTRY_NAME, "sha256": sha256_hex(ENTRY_CONTENT), "size": len(ENTRY_CONTENT)},
            {"path": READ_SCHEMA_NAME, "sha256": sha256_hex(read_bytes), "size": len(read_bytes)},
            {"path": WRITE_SCHEMA_NAME, "sha256": sha256_hex(write_bytes), "size": len(write_bytes)},
        ],
        "tools": [
            {"name": "query_inventory", "schema_ref": READ_SCHEMA_NAME, "permission": "read"},
            {"name": "submit_inventory_update", "schema_ref": WRITE_SCHEMA_NAME, "permission": "write"},
        ],
    }
    (profile / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (profile / "runner.json").write_text(
        json.dumps(
            {
                "host": "127.0.0.1",
                "port": 0,
                "evidence_dir": "evidence",
                "model": {"backend": "stub"},
                "tool_allowlist": ["query_inventory", "submit_inventory_update"],
                "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
                "durable": {"database_url": database_url, "workspace": "managed-auth"},
                "control_plane": {
                    "base_url": "http://platform.test",
                    "workspace_id": str(WS),
                    "trust_root": ROOT_NAME,
                    "host_id": "host-auth",
                    "issuer_domain": ISSUER,
                    "secret_ref": "env:MANAGED_SECRET",
                    "data_domains": list(data_domains),
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
                        "domains": ["domain_a", "domain_b"],
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


def _open_store(database_url: str):
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(database_url)
    store.create_schema()
    return store


class _Host:
    """One runner "process" with a shared lease gate and a mutable clock."""

    def __init__(
        self,
        profile_dir: Path,
        transport: httpx.ASGITransport,
        *,
        api: _BusinessApi,
        clock=None,
    ) -> None:
        self.profile = load_profile(profile_dir)
        assert self.profile.config.durable is not None
        self.store = _open_store(self.profile.config.durable.database_url)
        self.durable = DurableRuntime(self.store, workspace=self.profile.config.durable.workspace)
        self.evidence = EvidenceStore(self.profile.config.evidence_dir)
        self.api = api
        cp = self.profile.config.control_plane
        assert cp is not None
        self.gate = LeaseGate(
            SECRET,
            deployment_domain=cp.trust_root,
            clock=clock,
            issuer_domain=cp.issuer_domain,
            host_id=cp.host_id,
            workspace_id=cp.workspace_id,
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
            lease_gate=self.gate,
            execution_definition=self.profile.execution_definition_digest(),
            transport=transport,
            clock=clock,
        )
        self.engine = ExecutionEngine(
            self.profile,
            self.evidence,
            api,
            durable=self.durable,
            managed_identity=ManagedIdentity(principal=managed_principal_for(cp.trust_root), domains=cp.data_domains),
            lease_gate=self.gate,
        )
        self.scheduler = ManagedExecutionScheduler(store=self.store, engine=self.engine, poll_interval_seconds=0.05)

    def close(self) -> None:
        self.store.dispose()


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


async def _drain_all(host: _Host, row_ids: tuple[str, ...], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await host.scheduler.drain_once():
            continue
        states = [host.store.get_task_state(managed_task_ref(row_id)).lifecycle_state for row_id in row_ids]
        if all(state not in (TaskLifecycleState.QUEUED, TaskLifecycleState.RUNNING) for state in states):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("scheduler did not drain all accepted tasks")


def _input(read_domain: str = "domain_a", write_domain: str = "domain_a") -> dict:
    return {
        "prompt": "managed auth work",
        "tool_arguments": {
            "query_inventory": {"domain": read_domain, "sku": "S-1"},
            "submit_inventory_update": {"domain": write_domain, "sku": "S-1", "quantity": 2},
        },
    }


async def _accept(platform, host: _Host, input_payload: dict) -> str:
    delivery_row_id = await platform["queue_delivery"](input_payload)
    assert await host.channel.pull_once() == 1
    return delivery_row_id


async def test_scoped_lease_authorizes_protected_dispatch(platform, tmp_path: Path):
    await platform["admit"]()
    profile_dir = _write_auth_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", data_domains=("domain_a",))
    api = _BusinessApi()
    host = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await host.channel.register() is True
        await platform["opt_in"](managed_scope=["domain_a"])
        assert await platform["enrollment_scope"]() == ["domain_a"]
        delivery_row_id = await _accept(platform, host, _input())
        assert await host.scheduler.drain_once() is True
        await _wait_terminal(host.store, managed_task_ref(delivery_row_id))
        # The protected dispatch ran exactly once under the scoped lease.
        assert api.count("submit_inventory_update") == 1
        assert api.count("query_inventory") == 1
        assert all(call["principal"] == managed_principal_for(ROOT_NAME) for call in api.calls)
    finally:
        host.close()


async def test_expiry_refuses_protected_dispatch_with_zero_side_effects(platform, tmp_path: Path):
    await platform["admit"]()
    profile_dir = _write_auth_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", data_domains=("domain_a",))
    api = _BusinessApi()
    now = {"t": datetime.now(UTC)}
    host = _Host(profile_dir, platform["transport"], api=api, clock=lambda: now["t"])
    try:
        assert await host.channel.register() is True
        await platform["opt_in"](managed_scope=["domain_a"])
        first = await _accept(platform, host, _input())
        assert await host.scheduler.drain_once() is True
        await _wait_terminal(host.store, managed_task_ref(first))
        assert api.count("submit_inventory_update") == 1

        # Disconnect: the host clock passes the lease TTL. The next pull
        # still succeeds (the channel credential is verified server-side
        # against real time) but its lease is already expired on the host
        # clock — the protected dispatch is refused with zero business
        # calls while the readonly dispatch of the same run continues.
        now["t"] = now["t"] + timedelta(seconds=7200)
        second = await _accept(platform, host, _input())
        assert await host.scheduler.drain_once() is True
        await _wait_terminal(host.store, managed_task_ref(second))
        writes_after_expiry = api.count("submit_inventory_update")
        reads_after_expiry = api.count("query_inventory")
        assert writes_after_expiry == 1  # unchanged: the write was refused
        assert reads_after_expiry == 2  # the readonly dispatch still ran
        run_state = host.engine.get_state(f"managed-run-{second}")
        refusal = next(
            event
            for event in reversed(run_state.events)
            if event.get("type") == "tool_result" and event.get("tool") == "submit_inventory_update"
        )
        assert refusal["outcome"]["status"] == "authorization"
        assert "expired" in refusal["outcome"]["detail"]
    finally:
        host.close()


async def test_stale_lease_nonce_replay_refuses_second_dispatch(platform, tmp_path: Path):
    await platform["admit"]()
    profile_dir = _write_auth_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", data_domains=("domain_a",))
    api = _BusinessApi()
    host = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await host.channel.register() is True
        await platform["opt_in"](managed_scope=["domain_a"])
        # One lease authorizes exactly one protected action: the first run
        # consumes the nonce, the second run's protected dispatch is a
        # replay refusal until the next pull delivers a fresh lease.
        first = await _accept(platform, host, _input())
        second = await _accept(platform, host, _input())
        assert await host.scheduler.drain_once() is True
        await _wait_terminal(host.store, managed_task_ref(first))
        assert api.count("submit_inventory_update") == 1
        assert await host.scheduler.drain_once() is True
        await _wait_terminal(host.store, managed_task_ref(second))
        assert api.count("submit_inventory_update") == 1  # replay refused
        run_state = host.engine.get_state(f"managed-run-{second}")
        refusal = next(
            event
            for event in reversed(run_state.events)
            if event.get("type") == "tool_result" and event.get("tool") == "submit_inventory_update"
        )
        assert "nonce" in refusal["outcome"]["detail"]

        # The next pull carries a fresh scoped lease: new work executes.
        third = await _accept(platform, host, _input())
        assert await host.scheduler.drain_once() is True
        await _wait_terminal(host.store, managed_task_ref(third))
        assert api.count("submit_inventory_update") == 2
    finally:
        host.close()


async def test_cross_domain_dispatch_refused_by_lease_scope(platform, tmp_path: Path):
    await platform["admit"]()
    # The host-side stamp admits both domains: only the LEASE scope stops
    # domain_b, pinning the lease as the runtime authority (step6b).
    profile_dir = _write_auth_profile(
        tmp_path, f"sqlite:///{tmp_path / 'host.db'}", data_domains=("domain_a", "domain_b")
    )
    api = _BusinessApi()
    host = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await host.channel.register() is True
        await platform["opt_in"](managed_scope=["domain_a"])
        delivery_row_id = await _accept(platform, host, _input(write_domain="domain_b"))
        assert await host.scheduler.drain_once() is True
        await _wait_terminal(host.store, managed_task_ref(delivery_row_id))
        assert api.count("submit_inventory_update") == 0
        run_state = host.engine.get_state(f"managed-run-{delivery_row_id}")
        refusal = next(
            event
            for event in reversed(run_state.events)
            if event.get("type") == "tool_result" and event.get("tool") == "submit_inventory_update"
        )
        assert refusal["outcome"]["status"] == "authorization"
        assert "outside the current lease scope" in refusal["outcome"]["detail"]
        # The readonly read of the same run still executed under the stamp.
        assert api.count("query_inventory") == 1
    finally:
        host.close()


async def test_foreign_deployment_lease_refuses(platform, tmp_path: Path):
    await platform["admit"]()
    profile_dir = _write_auth_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", data_domains=("domain_a",))
    api = _BusinessApi()
    host = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await host.channel.register() is True
        await platform["opt_in"](managed_scope=["domain_a"])
        # A lease bound to another deployment domain fails the gate's
        # audience check: it must not drive this host's protected actions.
        # (Injected after the pull, which installed the platform lease.)
        delivery_row_id = await _accept(platform, host, _input())
        foreign = lease_claims(
            SECRET,
            iss=ISSUER,
            deployment_domain="other-host-root",
            sub="host-auth",
            ttl_seconds=3600,
            scope=["domain_a"],
        )[0]
        host.gate.update(foreign)
        assert await host.scheduler.drain_once() is True
        await _wait_terminal(host.store, managed_task_ref(delivery_row_id))
        assert api.count("submit_inventory_update") == 0
        run_state = host.engine.get_state(f"managed-run-{delivery_row_id}")
        refusal = next(
            event
            for event in reversed(run_state.events)
            if event.get("type") == "tool_result" and event.get("tool") == "submit_inventory_update"
        )
        assert refusal["outcome"]["status"] == "authorization"
        assert "does not bind deployment" in refusal["outcome"]["detail"]
    finally:
        host.close()


@pytest.mark.parametrize("field", ["iss", "sub", "tenant"])
async def test_foreign_signed_lease_identity_withholds_business_write(platform, tmp_path, field):
    """Dispatch verifies identity binding even when the HMAC is valid."""
    await platform["admit"]()
    profile_dir = _write_auth_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", data_domains=("domain_a",))
    api = _BusinessApi()
    host = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await host.channel.register()
        await platform["opt_in"](managed_scope=["domain_a"])
        delivery = await _accept(platform, host, _input())
        cp = host.profile.config.control_plane
        claims = {"iss": cp.issuer_domain, "sub": cp.host_id, "tenant": cp.workspace_id}
        claims[field] = "foreign"
        token, _ = lease_claims(
            SECRET,
            deployment_domain=cp.trust_root,
            ttl_seconds=60,
            scope=["domain_a"],
            **claims,
        )
        host.gate.update(token)
        assert await host.scheduler.drain_once()
        await _wait_terminal(host.store, managed_task_ref(delivery))
        assert api.count("submit_inventory_update") == 0
        assert host.engine.get_state(f"managed-run-{delivery}").status == "failed"
    finally:
        host.close()


async def test_restart_empty_gate_refuses_until_first_pull(platform, tmp_path: Path):
    await platform["admit"]()
    database_url = f"sqlite:///{tmp_path / 'host.db'}"
    profile_dir = _write_auth_profile(tmp_path, database_url, data_domains=("domain_a",))
    api = _BusinessApi()
    crashed = _Host(profile_dir, platform["transport"], api=api)
    try:
        assert await crashed.channel.register() is True
        await platform["opt_in"](managed_scope=["domain_a"])
        delivery_row_id = await _accept(platform, crashed, _input())
    finally:
        crashed.store.dispose()  # "process death": the in-memory gate dies too

    restarted = _Host(profile_dir, platform["transport"], api=api)
    try:
        # The gate is empty after restart: the protected dispatch of the
        # recovered task is refused before the first successful pull.
        assert await restarted.scheduler.drain_once() is True
        await _wait_terminal(restarted.store, managed_task_ref(delivery_row_id))
        assert api.count("submit_inventory_update") == 0

        # Reconnect: a fresh scoped lease re-arms new protected actions.
        assert await restarted.channel.register() is True
        next_delivery = await _accept(platform, restarted, _input())
        assert await restarted.scheduler.drain_once() is True
        await _wait_terminal(restarted.store, managed_task_ref(next_delivery))
        assert api.count("submit_inventory_update") == 1
    finally:
        restarted.close()


async def test_expired_scope_lease_round_trip_and_tampering():
    """Contract edge: scope rides the HMAC digest; tampering breaks it."""

    token, _nonce = lease_claims(
        SECRET, iss=ISSUER, deployment_domain=ROOT_NAME, sub="host-auth", ttl_seconds=3600, scope=["domain_a"]
    )
    claims = verify_lease(token, SECRET, deployment_domain=ROOT_NAME)
    assert claims.scope == ["domain_a"]

    tampered = {**token, "scope": ["domain_b"]}
    with pytest.raises(CredentialError):
        verify_lease(tampered, SECRET, deployment_domain=ROOT_NAME)
    without_scope = {key: value for key, value in token.items() if key != "scope"}
    with pytest.raises(CredentialError):
        verify_lease(without_scope, SECRET, deployment_domain=ROOT_NAME)
