"""Shared helpers for execution-contract tests.

Loads the authoritative schema files into a ``referencing`` Registry so
cross-file ``$ref`` (references.schema.json#/$defs/...) resolve by ``$id``,
and maps each standard sample to the schema (and fragment) that governs it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "src" / "hecate" / "contracts" / "schemas"
SAMPLES_DIR = Path(__file__).resolve().parent / "samples"
EXECUTION_BASE = "https://hecate.dev/contracts/execution/0.1"
SANDBOX_BASE = "https://hecate.dev/contracts/sandbox/0.1"


@lru_cache(maxsize=1)
def load_registry() -> Registry:
    """Registry holding every authoritative schema file under its ``$id``."""

    resources: list[tuple[str, Resource]] = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append((contents["$id"], Resource.from_contents(contents)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=1)
def schema_ids() -> set[str]:
    return {path.stem for path in SCHEMA_DIR.glob("*.schema.json")}


def schema_uri(name: str) -> str:
    """Full ``$id`` for one schema file stem (e.g. ``execution-request``).

    v0.2 files (errors, capabilities) carry their own version after the
    execution-backend-bindings change bumped them independently.
    """

    theme = "sandbox" if name == "sandbox" else "execution"
    version = "0.2" if name in {"errors", "capabilities"} else "0.1"
    return f"https://hecate.dev/contracts/{theme}/{version}/{name}.schema.json"


def validate_against_schema(instance: Any, schema_name: str, fragment: str = "") -> None:
    """Validate ``instance`` against an authoritative schema (or one of its defs)."""

    uri = schema_uri(schema_name)
    target: dict[str, Any] = (
        {"$ref": uri + fragment}
        if fragment
        else json.loads((SCHEMA_DIR / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
    )
    validator = jsonschema.Draft202012Validator(target, registry=load_registry())
    validator.validate(instance)


def sample_schema_ref(path: Path) -> tuple[str, str] | None:
    """Map one standard sample to its governing schema name and fragment.

    ``None`` means the sample is not governed by a single schema file
    (currently: HTTP request/response pairs, validated by the binding tests).
    """

    theme = path.parent.name
    name = path.name
    if theme == "http":
        return None
    if theme == "builtin":
        builtin_map = {
            "submit-receipt.json": ("submit-receipt", ""),
            "run-status.json": ("run-status", ""),
            "cancel-receipt.json": ("cancel-receipt", ""),
            "event-envelope.json": ("event-envelope", ""),
        }
        if name in builtin_map:
            return builtin_map[name]
        raise AssertionError(f"unknown builtin sample: {name}; extend the mapping")
    if theme == "references":
        return "references", "#/$defs/ref"
    if theme == "requests":
        return "execution-request", ""
    if theme == "events":
        return "event-envelope", ""
    if theme == "errors":
        return "errors", ""
    if theme == "capabilities":
        return "capabilities", ""
    if theme == "manifest":
        return "artifact-manifest", ""
    if theme == "security-claims":
        return "security-claims", ""
    if theme == "tools":
        return "tool", ""
    if theme == "durable":
        durable_map = {
            "task-state-record.json": ("durable-task-state", ""),
            "negative-task-state-unknown-state.json": ("durable-task-state", ""),
            "command-record.json": ("durable-command-record", ""),
            "command-record-expired.json": ("durable-command-record", ""),
            "negative-command-payload-without-schema.json": ("durable-command-record", ""),
            "action-intent.json": ("durable-action-ledger", "#/$defs/actionIntent"),
            "action-recovery.json": ("durable-action-ledger", "#/$defs/actionRecovery"),
            "claim-receipt.json": ("durable-action-ledger", "#/$defs/claimReceipt"),
            "governance-event.json": ("event-envelope", ""),
        }
        if name in durable_map:
            return durable_map[name]
        raise AssertionError(f"unknown durable sample: {name}; extend the mapping")
    if theme == "sandbox":
        if "create-environment" in name:
            return "sandbox", "#/$defs/createEnvironmentRequest"
        if "sandbox-info" in name:
            return "sandbox", "#/$defs/sandboxInfo"
        return "sandbox", "#/$defs/commandRecord"
    raise AssertionError(f"unknown sample theme directory: {theme}")


def all_samples() -> list[Path]:
    return sorted(p for p in SAMPLES_DIR.rglob("*.json"))


def load_sample(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# --- live pilot interop (change execution-backend-nonpython-pilot) -----------

import os  # noqa: E402
import queue  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

from tests.test_execution.live_http import LiveHttpBackend  # noqa: E402
from tests.test_execution.tool_callback import ToolCallbackServer  # noqa: E402

_PILOT_DIR = SCHEMA_DIR.parents[3] / "pilots" / "execution-backend-ts"
_TSC_LOCK_DIR = _PILOT_DIR / ".tsc-build.lock"
_TSC_LOCK_STALE_SECONDS = 300


def _build_pilot(node: str, tsc: Path) -> None:
    """Compile the pilot, serializing the build across pytest-xdist workers.

    Every worker session rebuilds (the pilot spec forbids reusing a stale
    dist), and all workers share the same outDir — without the lock a worker
    can spawn dist/server.js while another worker's tsc is still rewriting it.
    """
    deadline = time.monotonic() + _TSC_LOCK_STALE_SECONDS
    while True:
        try:
            _TSC_LOCK_DIR.mkdir()
            break
        except FileExistsError:
            try:
                stale = time.time() - _TSC_LOCK_DIR.stat().st_mtime > _TSC_LOCK_STALE_SECONDS
            except FileNotFoundError:
                stale = False
            if stale:
                shutil.rmtree(_TSC_LOCK_DIR, ignore_errors=True)
            elif time.monotonic() > deadline:
                pytest.fail("pilot build lock held too long")
            else:
                time.sleep(0.1)
    try:
        subprocess.run(  # noqa: S603 - node and args resolved from repo layout
            [
                node,
                str(tsc),
                "-p",
                str(_PILOT_DIR / "tsconfig.json"),
            ],
            check=True,
            cwd=_PILOT_DIR,
        )
    finally:
        shutil.rmtree(_TSC_LOCK_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def live_backend() -> LiveHttpBackend:
    """Run the TypeScript pilot on an ephemeral loopback port.

    Skips with a stated reason when node is unavailable - the A-side vitest
    suite is the standing proof; this fixture adds cross-language interop
    whenever a node runtime exists. It is a conditional skip, not a
    permanent one.
    """
    node = shutil.which("node")
    if node is None:
        if os.environ.get("HECATE_REQUIRE_LIVE_PILOT") == "1":
            pytest.fail("live pilot is required but node is unavailable")
        pytest.skip("node runtime not available; live pilot interop requires node")
    dist_server = _PILOT_DIR / "dist" / "server.js"
    tsc = _PILOT_DIR / "node_modules" / "typescript" / "bin" / "tsc"
    if not tsc.exists():
        if os.environ.get("HECATE_REQUIRE_LIVE_PILOT") == "1":
            pytest.fail("live pilot is required but npm dependencies are unavailable")
        pytest.skip("pilot JS dependencies not installed; run npm ci in pilots/execution-backend-ts")
    else:
        try:
            _build_pilot(node, tsc)
        except (subprocess.CalledProcessError, OSError) as error:
            pytest.fail(f"pilot build failed: {error}")
    callback = ToolCallbackServer()
    proc = subprocess.Popen(  # noqa: S603 - node and args resolved from repo layout
        [node, str(dist_server), "--port", "0", "--tool-callback-url", callback.url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        port = None
        assert proc.stdout is not None
        lines: queue.Queue[str] = queue.Queue()

        def collect_lines() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
            lines.put("")

        threading.Thread(target=collect_lines, daemon=True).start()
        deadline = time.monotonic() + 20
        while port is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                pytest.fail("pilot server did not report readiness in time")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                pytest.fail("pilot server did not report readiness in time")
            if not line:
                if port is None:
                    pytest.fail("pilot server exited before reporting readiness")
                break
            if "PILOT_LISTENING" in line:
                port = int(line.strip().rsplit("=", 1)[1])
        backend = LiveHttpBackend(f"http://127.0.0.1:{port}")
        backend.callback_calls = callback.calls
        yield backend
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        if proc.stdout is not None:
            proc.stdout.close()
        callback.close()


# --- durable-execution seams (change durable-execution-contracts) ----------

from collections.abc import Callable  # noqa: E402

from hecate.contracts.execution.durable import (  # noqa: E402
    ActionIntent,
    ActionOutcomeRecord,
    ActionRecovery,
    ClaimReceipt,
    CommandState,
    ControlCommandRecord,
    IdempotencyKey,
    SubmissionAssociation,
    TaskLifecycleState,
    TaskStateRecord,
)
from hecate.contracts.execution.references import BackendRef  # noqa: E402
from hecate.execution.durable import (  # noqa: E402
    ActionLedger,
    ControlCommandRecorder,
    DurableTaskStore,
)
from hecate.execution.stub_durable import (  # noqa: E402
    InMemoryActionLedger,
    InMemoryControlCommandRecorder,
    InMemoryDurableTaskStore,
)

DurableSuite = tuple[DurableTaskStore, ControlCommandRecorder, ActionLedger]
DurableImplementationFactory = Callable[[], DurableSuite]


# Registration point for durable-seam implementations. The contract suite in
# test_durable_contract.py runs against every entry unchanged; production
# implementations register here when their changes land:
#   - durable-execution-core: the SQL store core (worktree A) — registered
#     below over a per-test SQLite file (the portable dialect; the same
#     implementation runs its fault-injection suite against PostgreSQL via
#     DURABLE_TEST_POSTGRES_URL in packages/hecate-durable/tests/)
def _sql_sqlite_suite() -> DurableSuite:
    import os
    import tempfile

    from hecate_durable.storage import SqlDurableStore

    handle, path = tempfile.mkstemp(suffix=".durable-suite.db")
    os.close(handle)
    os.unlink(path)
    store = SqlDurableStore(f"sqlite:///{path}")
    store.create_schema()
    return (store, store, store)


DURABLE_IMPLEMENTATIONS: dict[str, DurableImplementationFactory] = {
    "inmemory-stub": lambda: (
        InMemoryDurableTaskStore(),
        InMemoryControlCommandRecorder(),
        InMemoryActionLedger(),
    ),
    "sql-sqlite": _sql_sqlite_suite,
}

__all__ = [
    "ActionIntent",
    "ActionOutcomeRecord",
    "ActionRecovery",
    "BackendRef",
    "ClaimReceipt",
    "CommandState",
    "ControlCommandRecord",
    "DURABLE_IMPLEMENTATIONS",
    "DurableImplementationFactory",
    "DurableSuite",
    "IdempotencyKey",
    "SubmissionAssociation",
    "TaskLifecycleState",
    "TaskStateRecord",
    "all_samples",
    "load_registry",
    "load_sample",
    "sample_schema_ref",
    "schema_ids",
    "schema_uri",
    "validate_against_schema",
]


# --- durable-dispatch harness (step6 continuation acceptance) ----------------

import asyncio as _asyncio  # noqa: E402
import uuid as _uuid_mod  # noqa: E402

import pytest_asyncio as _pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine  # noqa: E402

from tests.conftest import Base as _Base  # noqa: E402

HARNESS_WS = _uuid_mod.UUID("00000000-0000-0000-0000-0000000000a0")
HARNESS_ORG = _uuid_mod.UUID("00000000-0000-0000-0000-0000000000a1")
HARNESS_OWNER = _uuid_mod.UUID("00000000-0000-0000-0000-0000000000a2")

# PostgreSQL for the concurrency-tier acceptance (hang/resume, receipt loss).
# SQLite stays the default local tier; the concurrent dispatch scenarios need
# a real multi-connection database (SQLite's single-writer semantics
# serialize them away). One schema per test keeps PG runs isolated.
HARNESS_POSTGRES_URL = os.environ.get("HECATE_STEP6_POSTGRES_URL")


def _harness_schema_name(tmp_path) -> str:
    import re as _re

    return "s_" + _re.sub(r"[^0-9a-zA-Z_]", "_", tmp_path.name)[:40] + "_" + _uuid_mod.uuid4().hex[:8]


def _pg_url_with_schema(url: str, schema: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}options=-csearch_path%3D{schema},public"


@_pytest_asyncio.fixture
async def harness(tmp_path):
    """Durable-dispatch harness on SQLite (default) or PostgreSQL.

    ``HECATE_STEP6_POSTGRES_URL`` switches both engines (platform ORM and
    durable store) to PostgreSQL, one schema per test for isolation.
    """

    from datetime import UTC, datetime, timedelta

    from hecate_durable.storage import SqlDurableStore

    from hecate.models.agent import AgentModel
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.agent_principal import AgentPrincipalModel
    from hecate.models.agent_version import AgentVersionModel
    from hecate.models.organization import OrganizationModel
    from hecate.models.user import UserModel
    from hecate.models.workspace import WorkspaceModel

    pg_url = HARNESS_POSTGRES_URL
    schema = None
    if pg_url:
        # asyncpg for EVERYTHING on the event loop: psycopg3 SYNC connect
        # hangs forever when its select() wait runs on the loop thread
        # (Windows + Docker port proxy); in worker threads (to_thread) the
        # same call is fine, so the sync-psycopg store stays.
        from sqlalchemy import text as _text

        schema = _harness_schema_name(tmp_path)
        async_url = pg_url.replace("+psycopg", "+asyncpg")
        async_admin = _create_async_engine(async_url)
        async with async_admin.begin() as conn:
            await conn.execute(_text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        async_engine = _create_async_engine(
            async_url,
            connect_args={"server_settings": {"search_path": f"{schema},public"}},
        )
        store = SqlDurableStore(_pg_url_with_schema(pg_url, schema), source="platform")
    else:
        from sqlalchemy import event as sa_event

        db_file = tmp_path / "orchestration.db"
        async_engine = _create_async_engine(f"sqlite+aiosqlite:///{db_file}", connect_args={"timeout": 30})

        @sa_event.listens_for(async_engine.sync_engine, "connect")
        def _wal_async(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")

        store = SqlDurableStore(f"sqlite:///{db_file}", source="platform")

        @sa_event.listens_for(store.engine, "connect")
        def _wal_sync(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")

    if pg_url is None:
        store.create_schema()
    else:
        # Sync-psycopg connects must run OFF the event loop thread (see the
        # PG-branch comment above); create_schema is a blocking connect.
        await _asyncio.to_thread(store.create_schema)
    async with async_engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    session_factory = _async_sessionmaker(async_engine, class_=_AsyncSession, expire_on_commit=False)

    class _Clock:
        now = datetime(2026, 1, 1, tzinfo=UTC)

        def advance(self, seconds: float) -> None:
            self.now = self.now + timedelta(seconds=seconds)

    clock = _Clock()
    import hecate_durable.storage.lease as lease_module

    original_default = lease_module._default_clock
    # The store builds its LeaseManager at __init__ with the real clock;
    # rebuild it with the injectable test clock on BOTH dialects so the
    # tests can expire the dead dispatch's lease without sleeping.
    store.leases = lease_module.LeaseManager(store.session_factory, clock=lambda: clock.now)

    async with session_factory() as db:
        db.add(
            OrganizationModel(id=HARNESS_ORG, name="org", slug=f"org-{HARNESS_ORG.hex[:12]}", owner_id=HARNESS_OWNER)
        )
        db.add(WorkspaceModel(id=HARNESS_WS, org_id=HARNESS_ORG, name="ws", slug=f"ws-{HARNESS_WS.hex}"))
        db.add(UserModel(id=HARNESS_OWNER, email="owner@example.com", hashed_password=_uuid_mod.uuid4().hex))
        agent = AgentModel(workspace_id=HARNESS_WS, name="orch-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="c" * 64
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id,
                agent_id=agent.id,
                workspace_id=HARNESS_WS,
                organization_id=HARNESS_ORG,
                owner_user_id=HARNESS_OWNER,
            )
        )
        db.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=HARNESS_WS,
                backend_type=BackendType.BUILTIN,
                access_mode=AccessMode.IN_PROCESS,
                issuer_domain="hecate",
                capability_snapshot={},
                axes_harness="hecate",
                axes_environment="none",
                axes_tool_execution="hecate_gateway",
                is_default=True,
            )
        )
        await db.commit()
        agent_id = agent.id

    yield {
        "session_factory": session_factory,
        "store": store,
        "agent_id": agent_id,
        "clock": clock,
        "pg": pg_url is not None,
    }

    lease_module._default_clock = original_default
    await async_engine.dispose()
    store.dispose()
    if schema is not None:
        from sqlalchemy import text as _text

        async_url = pg_url.replace("+psycopg", "+asyncpg")
        async_admin = _create_async_engine(async_url)
        async with async_admin.begin() as conn:
            await conn.execute(_text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await async_admin.dispose()
