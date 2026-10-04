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
#   - platform-task-control-api: the platform adapter over this seam (worktree B)
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


def _platform_adapter_suite() -> DurableSuite:
    """Worktree-B platform adapters over an isolated in-memory database.

    The adapters are dialect-agnostic SQLAlchemy; the suite exercises them
    on in-memory SQLite exactly like the rest of the CI matrix, while the
    production binding maps the same code onto PostgreSQL. The ledger slot
    stays on the InMemory stub until durable-execution-core lands its own.
    """

    from hecate.execution.platform_durable import PlatformDurableFactory

    factory = PlatformDurableFactory("sqlite://")
    factory.create_all()
    return (
        factory.task_store(),
        factory.command_recorder(),
        InMemoryActionLedger(),
    )


DURABLE_IMPLEMENTATIONS: dict[str, DurableImplementationFactory] = {
    "inmemory-stub": lambda: (
        InMemoryDurableTaskStore(),
        InMemoryControlCommandRecorder(),
        InMemoryActionLedger(),
    ),
    "sql-sqlite": _sql_sqlite_suite,
    "platform-postgres": _platform_adapter_suite,
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
