"""SC06 managed slice: central evidence upload faults and backfill.

An installed Runner wheel (clean venv, durable profile) serves managed
deliveries over real loopback HTTP. A fault window sits in front of the
platform's evidence route (``POST /managed/host/events``) only; delivery,
leases, accept and attempt association keep their normal path. Each
delivery runs one named protected business write (``submit_inventory_update``)
plus a read, counted at the stub business API.

- before commit — the route answers 503 without reaching the projection:
  the write runs once, local action results and events stay readable, the
  platform projects nothing and every refused batch restarts at the run's
  first event; after recovery the stream backfills exactly once;
- after commit — the projection commits, then its answer is lost or
  replaced by an incomplete one: the Runner resends the identical batch,
  the platform deduplicates by event id under the original association,
  and only the complete (all-duplicate) confirmation advances;
- process restart — the Runner is killed once the pending facts are
  durable and restarted on the same profile and database; the backlog
  uploads with no loss, no duplicate projection and no repeated write;
- run isolation — one run's confirmations never advance another run's
  position.

The local-gap slice uses an isolated fixture: an in-process channel over the
same real HTTP route and a host store that lost rows inside one run's
observed range, so the durable log reports an explicit gap (a page holding
only the gap marker included).

Every test writes one JSON evidence file (``HECATE_SC06_UPLOAD_EVIDENCE_DIR``,
default: the test's tmp dir): commit, dirty state, source digests, both
database tiers, process identities, fault windows and barriers, batch
attempts, event associations, gap ranges and business call counts — event
ids, sequences and types only, never payloads or credentials.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hecate.models import platform_event as _platform_event  # noqa: F401  (registers platform_events)
from tests.scenarios.tools import runner_harness
from tests.scenarios.tools.managed_platform import (
    ADMIN,
    ISSUER,
    ROOT_NAME,
    SECRET,
    WS,
    build_managed_stack,
    serve_over_tcp,
    stop_tcp_server,
)

requires_uv = pytest.mark.skipif(
    not runner_harness.uv_available(), reason="uv is required for the clean-install harness"
)

REPO = Path(__file__).resolve().parents[2]
UPLOAD_BATCH = 3
EVIDENCE_ROUTE = "/managed/host/events"
BEFORE_COMMIT = "fail_before_commit"
LOSE_ANSWER = "lose_answer_after_commit"
INCOMPLETE_ANSWER = "incomplete_answer_after_commit"
PASS = "pass"

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
MANIFEST_TOOLS = [
    {"name": "submit_inventory_update", "schema_ref": "schemas/write.json", "permission": "write"},
    {"name": "query_inventory", "schema_ref": "schemas/read.json", "permission": "read"},
]
TOOL_ALLOWLIST = ["submit_inventory_update", "query_inventory"]
TOOL_SCHEMAS = {"schemas/read.json": READ_SCHEMA, "schemas/write.json": WRITE_SCHEMA}
SOURCES = (
    "tests/scenarios/test_sc06_managed_evidence_upload.py",
    "tests/scenarios/tools/managed_platform.py",
    "tests/scenarios/tools/runner_harness.py",
    "packages/hecate-runner/src/hecate_runner/managed.py",
    "packages/hecate-runner/src/hecate_runner/__main__.py",
    "packages/hecate-runner/src/hecate_runner/server.py",
    "packages/hecate-durable/src/hecate_durable/storage/eventlog.py",
    "src/hecate/execution/managed_channel.py",
    "src/hecate/channel/api/managed.py",
)


# -- fault window ---------------------------------------------------------------


class UploadWindow:
    """Scripted fault state for the evidence route plus every attempt it saw."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.modes: dict[str, str] = {}  # local task id or "*" -> action
        self.planner: Callable[[dict[str, Any]], str | None] | None = None
        self.attempts: list[dict[str, Any]] = []
        self.windows: list[dict[str, Any]] = []
        self.barriers: list[dict[str, Any]] = []

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 3)

    def open(self, name: str, scope: str, action: str, key: str = "*") -> None:
        self.modes[key] = action
        self.windows.append({"name": name, "scope": scope, "action": action, "opened_at": self.elapsed()})

    def close(self, name: str, key: str = "*") -> None:
        self.modes.pop(key, None)
        for window in self.windows:
            if window["name"] == name and "closed_at" not in window:
                window["closed_at"] = self.elapsed()

    def mark(self, name: str, **facts: Any) -> None:
        self.barriers.append({"name": name, "at": self.elapsed(), **facts})

    def begin(self, payload: dict[str, Any]) -> dict[str, Any]:
        envelopes = payload.get("envelopes") or []
        attempt: dict[str, Any] = {
            "at": self.elapsed(),
            "task": (payload.get("local_task_ref") or {}).get("id"),
            "run": ((envelopes[0].get("run_ref") or {}).get("id")) if envelopes else None,
            "sequences": [envelope.get("source_sequence") for envelope in envelopes],
            "event_ids": [envelope.get("event_id") for envelope in envelopes],
            "event_types": [(envelope.get("payload") or {}).get("event_type") for envelope in envelopes],
        }
        planned = self.planner(attempt) if self.planner is not None else None
        attempt["action"] = planned or self.modes.get(attempt["task"]) or self.modes.get("*") or PASS
        self.attempts.append(attempt)
        return attempt

    def for_task(self, task_id: str) -> list[dict[str, Any]]:
        return [attempt for attempt in self.attempts if attempt["task"] == task_id]


class EvidenceRouteFaults:
    """Pure ASGI middleware applying the window to the evidence route only."""

    def __init__(self, app: Any, window: UploadWindow) -> None:
        self.app = app
        self.window = window

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or scope["path"] != EVIDENCE_ROUTE or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        attempt = self.window.begin(json.loads(body or b"{}"))
        if attempt["action"] == BEFORE_COMMIT:
            attempt["status"] = 503
            await _send_json(send, 503, {"detail": "evidence route unavailable"})
            return

        replayed = False

        async def replay() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        start: dict[str, Any] = {}
        chunks: list[bytes] = []

        async def capture(message: dict) -> None:
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, replay, capture)
        answer = b"".join(chunks)
        attempt["platform_status"] = start.get("status")
        attempt["platform_answer"] = _counts_of(answer)
        if attempt["action"] == LOSE_ANSWER and start.get("status") == 200:
            attempt["status"] = 503
            await _send_json(send, 503, {"detail": "answer lost after commit"})
        elif attempt["action"] == INCOMPLETE_ANSWER and start.get("status") == 200:
            attempt["status"] = 200
            await _send_json(send, 200, {"projected": (attempt["platform_answer"] or {}).get("projected")})
        else:
            attempt["status"] = start.get("status")
            await send({"type": "http.response.start", "status": start["status"], "headers": start.get("headers", [])})
            await send({"type": "http.response.body", "body": answer})


async def _send_json(send: Callable, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _counts_of(answer: bytes) -> dict[str, Any] | None:
    try:
        decoded = json.loads(answer)
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    return {key: decoded.get(key) for key in ("projected", "skipped_duplicates")}


# -- fixtures and environment -----------------------------------------------------


@pytest.fixture(scope="module")
def wheel_dist(tmp_path_factory) -> Path:
    dist = Path(tmp_path_factory.mktemp("sc06-upload-wheels"))
    runner_harness.ensure_wheels(dist)
    return dist


@pytest.fixture
def auth_context():
    from hecate.core.auth_context import AuthContext
    from hecate.models.workspace_member import WorkspaceRole

    return AuthContext(
        user_id=ADMIN, org_id=WS, workspace_id=WS, role=WorkspaceRole.ADMIN, auth_method="jwt", api_key_scope=None
    )


@pytest.fixture
async def upload_stack(auth_context, managed_secrets, tmp_path, step6_runner_database_url):
    # The stack's own host store only serves the isolated gap fixture; the
    # installed Runner keeps its database at the same tier.
    async with build_managed_stack(auth_context, tmp_path, host_database_url=step6_runner_database_url) as stack:
        yield stack


@asynccontextmanager
async def _installed_host(
    stack, tmp_path: Path, wheel_dist: Path, database_url: str | None, window: UploadWindow
) -> AsyncGenerator[SimpleNamespace, None]:
    """Admitted platform over TCP (window in front of the evidence route) + installed Runner."""

    await stack["admit"]()
    stack["app"].add_middleware(EvidenceRouteFaults, window=window)
    server, server_task, base_url = await serve_over_tcp(stack["app"])
    env = SimpleNamespace(runner=None, business_server=None, calls=[], pids=[], database_url=database_url)
    try:
        runner, business_server, calls = await asyncio.to_thread(
            runner_harness.start_runner,
            tmp_path,
            manifest_tools=MANIFEST_TOOLS,
            durable=True,
            tool_allowlist=TOOL_ALLOWLIST,
            tool_schemas=TOOL_SCHEMAS,
            control_plane={
                "base_url": base_url,
                "workspace_id": str(WS),
                "trust_root": ROOT_NAME,
                "host_id": "installed-runner-sc06-upload",
                "issuer_domain": ISSUER,
                "secret_ref": "file:secrets/managed-secret",
                "poll_interval_seconds": 0.05,
                "upload_batch": UPLOAD_BATCH,
                "data_domains": ["domain_a"],
            },
            managed_secret=SECRET,
            durable_database_url=database_url,
            dist_dir=wheel_dist,
        )
        env.runner, env.business_server, env.calls = runner, business_server, calls
        env.pids.append(await asyncio.to_thread(_runner_pid, runner))
        await _opt_in(stack["session_factory"], await _wait_enrollment(stack["session_factory"]))
        yield env
    finally:
        if env.runner is not None:
            env.runner.stop(kill=True)
        if env.business_server is not None:
            runner_harness.stop_business_api(env.business_server)
        await stop_tcp_server(server, server_task)


def _runner_pid(runner: runner_harness.RunnerInstance) -> int:
    """The Runner interpreter's process id for the evidence record.

    The harness launches the uv venv ``python.exe`` directly and it
    executes in place (no child interpreter), so the Popen pid IS the
    interpreter pid.
    """

    return runner.process.pid


def _stop_for_restart(runner: runner_harness.RunnerInstance) -> None:
    """Hard-stop the owned Runner and verify its local surface is closed.

    What the replacement must not race is the Runner's HTTP port (and the
    durable database behind it), so the gate probes the PORT, not the
    system process table: a terminated process can still be listed there
    while our own handle stays open, and WMI parent matching misfires on
    machines with aggressive pid reuse.
    """

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(runner.process.pid)], check=True, capture_output=True, timeout=10
        )
    runner.stop(kill=True)
    runner.process.wait(timeout=10)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.3)
            try:
                probe.connect(("127.0.0.1", runner.port))
            except OSError:
                return  # refused: nothing of the old Runner serves anymore
        time.sleep(0.1)
    raise AssertionError("the old Runner port still accepts connections")


async def _wait_enrollment(sf) -> uuid.UUID:
    from sqlalchemy import select

    from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

    async def enrolled() -> uuid.UUID | None:
        async with sf() as db:
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
            return row.id if row is not None else None

    return await _until(enrolled, what="the installed Runner registering over platform HTTP", timeout=60)


async def _opt_in(sf, enrollment_id: uuid.UUID) -> None:
    from hecate.execution.enrollment_resolver import HmacEnrollmentResolver
    from hecate.execution.task_run_registry import TaskRunRegistry

    async with sf() as db:
        resolver = HmacEnrollmentResolver(db, secret_candidates={ROOT_NAME: SECRET})
        await TaskRunRegistry(db, enrollment_resolver=resolver.verify).set_managed_opt_in(
            enrollment_id, WS, managed_new_runs=True, operator_id=ADMIN, admitted=True, managed_scope=["domain_a"]
        )
        await db.commit()


async def _until(probe: Callable[[], Awaitable[Any]], *, what: str, timeout: float = 60.0) -> Any:
    """Poll an explicit barrier condition; the satisfied probe value is returned."""

    deadline = asyncio.get_running_loop().time() + timeout
    last: Any = None
    while asyncio.get_running_loop().time() < deadline:
        last = await probe()
        if last:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}; last observation: {last!r}")


def _delivery_input(quantity: int) -> dict[str, Any]:
    return {
        "input": {"prompt": "sc06 managed upload"},
        "tool_arguments": {
            "submit_inventory_update": {"domain": "domain_a", "sku": "SKU-A1", "quantity": quantity},
            "query_inventory": {"domain": "domain_a", "sku": "SKU-A1"},
        },
    }


def _writes(calls: list[dict], quantity: int) -> int:
    return sum(1 for call in calls if (call.get("arguments") or {}).get("quantity") == quantity)


def _reads(calls: list[dict]) -> int:
    return sum(1 for call in calls if (call.get("arguments") or {}).get("quantity") is None)


# -- local and central facts --------------------------------------------------------


def _local_url(env: SimpleNamespace) -> str:
    return env.database_url or f"sqlite:///{(env.runner.workdir / 'host.db').as_posix()}"


def _read_local(url: str, delivery_id: str) -> dict[str, Any]:
    from hecate_durable.contracts.references import BackendRef, RefKind
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(url)
    try:
        task_ref = BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_id}")
        record = store.get_task_state(task_ref)
        run_ref = store.run_for_task(task_ref)
        events: list[Any] = []
        cursor = 0
        while run_ref is not None:
            page = store.read_events(run_ref, cursor=cursor, limit=200)
            events.extend(page.events)
            if not page.has_more:
                break
            cursor = page.next_cursor
        actions = store.list_run_actions(run_ref) if run_ref is not None else []
        return {
            "state": record.lifecycle_state.value if record is not None else None,
            "task_ref": task_ref.to_dict(),
            "run_ref": run_ref.to_dict() if run_ref is not None else None,
            "events": [
                {
                    "sequence": event.source_sequence,
                    "event_id": event.event_id,
                    "kind": event.kind.value,
                    "type": (event.payload or {}).get("event_type"),
                }
                for event in events
            ],
            "actions": [
                {
                    "tool": next((name for name in TOOL_ALLOWLIST if action["action_key"].endswith(name)), None),
                    "outcome": (action.get("last_outcome") or {}).get("outcome"),
                    "has_result": action["result_payload"] is not None,
                    "pending_reconciliation": action["pending_reconciliation"],
                }
                for action in actions
            ],
            "task_count": len(store.list_tasks()),
        }
    finally:
        store.dispose()


async def _local(env: SimpleNamespace, delivery_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_read_local, _local_url(env), delivery_id)


async def _center(stack, delivery_id: str) -> dict[str, Any]:
    from sqlalchemy import select

    from hecate.models.managed_delivery import ManagedDeliveryModel
    from hecate.models.platform_event import PlatformEventModel
    from hecate.models.run import RunModel

    async with stack["session_factory"]() as db:
        delivery = await db.get(ManagedDeliveryModel, uuid.UUID(delivery_id))
        assert delivery is not None
        run_id = str(delivery.run_ref["id"])
        run = await db.get(RunModel, uuid.UUID(run_id))
        rows = (
            (
                await db.execute(
                    select(PlatformEventModel)
                    .where(PlatformEventModel.run_issuer_domain == "hecate", PlatformEventModel.run_ref_id == run_id)
                    .order_by(PlatformEventModel.source_sequence)
                )
            )
            .scalars()
            .all()
        )
        return {
            "platform_run_id": run_id,
            "projection_state": (run.projection or {}).get("state") if run is not None else None,
            "accepted_refs": dict(delivery.accepted_refs or {}),
            "events": [
                {
                    "event_id": row.event_id,
                    "host_sequence": ((row.envelope.get("detail_ns") or {}).get("host_origin") or {}).get(
                        "source_sequence"
                    ),
                }
                for row in rows
            ],
        }


def _event_ids(local: dict[str, Any]) -> list[str]:
    return [event["event_id"] for event in local["events"] if event["kind"] == "event"]


def _mirror(local: dict[str, Any], center: dict[str, Any]) -> dict[str, list[str]]:
    """Event-id association of one run on both sides (evidence form)."""

    return {"local": _event_ids(local), "platform": [event["event_id"] for event in center["events"]]}


def _assert_mirrored_once(local: dict[str, Any], center: dict[str, Any]) -> None:
    """Every local event reached the platform exactly once, under its host sequence."""

    local_sequences = {event["event_id"]: event["sequence"] for event in local["events"] if event["kind"] == "event"}
    central_ids = [event["event_id"] for event in center["events"]]
    assert len(central_ids) == len(set(central_ids)), "duplicate projection"
    assert set(central_ids) == set(local_sequences), {
        "missing": sorted(set(local_sequences) - set(central_ids)),
        "extra": sorted(set(central_ids) - set(local_sequences)),
    }
    assert all(event["host_sequence"] == local_sequences[event["event_id"]] for event in center["events"])


async def _health(env: SimpleNamespace) -> dict[str, Any]:
    status, body = await asyncio.to_thread(env.runner.request, "GET", "/healthz", None, None)
    assert status == 200, body
    return body


def _contiguous(attempts: list[dict[str, Any]]) -> list[int]:
    return [sequence for attempt in attempts for sequence in attempt["sequences"]]


# -- evidence ------------------------------------------------------------------------


class _Evidence:
    """One JSON evidence file per test; verification fields only."""

    def __init__(self, request, scenario: str, database_url: str | None, tmp_path: Path) -> None:
        self.scenario = scenario
        self.tmp_path = tmp_path
        self.database_url = database_url
        self.payload: dict[str, Any] = {
            "schema_version": 1,
            "scenario": scenario,
            "test_id": request.node.nodeid,
            "code_version": _git("rev-parse", "HEAD"),
            "code_dirty": bool(_git("status", "--porcelain")),
            "source_sha256": {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in SOURCES},
            "databases": {
                "runner": "postgresql" if database_url else "sqlite",
                "platform": "sqlite",
            },
            "processes": {"platform_pid": os.getpid(), "runner_pids": []},
            "upload_batch": UPLOAD_BATCH,
            "outcome": "incomplete",
        }

    def write(self, window: UploadWindow, env: SimpleNamespace | None) -> None:
        self.payload["fault_windows"] = window.windows
        self.payload["barriers"] = window.barriers
        self.payload["attempts"] = [
            {key: attempt.get(key) for key in ("at", "task", "run", "sequences", "event_ids", "event_types", "action")}
            | {
                "status": attempt.get("status"),
                "platform_status": attempt.get("platform_status"),
                "platform_answer": attempt.get("platform_answer"),
            }
            for attempt in window.attempts
        ]
        actions = [attempt["action"] for attempt in window.attempts]
        self.payload["batch_counts"] = {
            "attempted": len(actions),
            BEFORE_COMMIT: actions.count(BEFORE_COMMIT),
            LOSE_ANSWER: actions.count(LOSE_ANSWER),
            INCOMPLETE_ANSWER: actions.count(INCOMPLETE_ANSWER),
            "passed": actions.count(PASS),
        }
        if env is not None:
            self.payload["processes"]["runner_pids"] = list(env.pids)
            self.payload["business_calls"] = {
                "writes": sum(1 for call in env.calls if (call.get("arguments") or {}).get("quantity") is not None),
                "reads": _reads(env.calls),
            }
        directory = Path(os.environ.get("HECATE_SC06_UPLOAD_EVIDENCE_DIR", str(self.tmp_path)))
        directory.mkdir(parents=True, exist_ok=True)
        tier = self.payload["databases"]["runner"]
        path = directory / f"sc06-upload-{self.scenario}-{tier}-{uuid.uuid4().hex[:8]}.json"
        text = json.dumps(self.payload, indent=2, sort_keys=True, default=str)
        assert SECRET.decode() not in text and "ManagedBearer" not in text
        if self.database_url and "@" in self.database_url:
            assert self.database_url.split("@", 1)[0] not in text
        path.write_text(text, encoding="utf-8")


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=REPO, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


# -- installed Runner scenarios -------------------------------------------------------


@requires_uv
async def test_sc06_upload_fault_before_commit_keeps_position_until_recovery(
    upload_stack, tmp_path, wheel_dist, step6_runner_database_url, request
) -> None:
    window = UploadWindow()
    evidence = _Evidence(request, "before-commit", step6_runner_database_url, tmp_path)
    env: SimpleNamespace | None = None
    try:
        window.open("evidence-route-refused", "all managed runs", BEFORE_COMMIT)
        async with _installed_host(upload_stack, tmp_path, wheel_dist, step6_runner_database_url, window) as env:
            delivery = await upload_stack["queue_delivery"](_delivery_input(3))
            task_id = f"managed-{delivery}"

            async def finished() -> dict[str, Any] | None:
                local = await _local(env, delivery)
                return local if local["state"] == "succeeded" else None

            local = await _until(finished, what="the protected run finishing locally")
            terminal_at = window.elapsed()
            window.mark("local-terminal-persisted", events=len(_event_ids(local)))

            async def refused_after_terminal() -> dict[str, Any] | None:
                attempts = window.for_task(task_id)
                retries = [attempt for attempt in attempts if attempt["at"] > terminal_at and attempt["status"] == 503]
                return attempts[0] if len(retries) >= 2 else None

            first = await _until(refused_after_terminal, what="the original first batch being retried after completion")
            first_batch = first["sequences"]
            assert 1 <= len(first_batch) <= UPLOAD_BATCH
            assert first_batch == [event["sequence"] for event in local["events"]][: len(first_batch)]
            window.mark("first-batch-refused-twice")

            # The write ran once and its local facts are readable.
            assert _writes(env.calls, 3) == 1 and _reads(env.calls) == 1
            assert sorted(
                (action["tool"], action["outcome"], action["has_result"], action["pending_reconciliation"])
                for action in local["actions"]
            ) == [("query_inventory", "succeeded", True, False), ("submit_inventory_update", "succeeded", True, False)]
            assert [event["kind"] for event in local["events"]] == ["event"] * len(local["events"])
            assert "run_terminal" in [event["type"] for event in local["events"]]
            # Nothing reached the projection, and no refused batch moved the position.
            center = await _center(upload_stack, delivery)
            assert center["events"] == [] and center["projection_state"] is None
            refused = window.for_task(task_id)
            assert refused and all(a["action"] == BEFORE_COMMIT for a in refused)
            assert all(a["sequences"] == first_batch and a["event_ids"] == first["event_ids"] for a in refused)

            async def outage_reported() -> dict[str, Any] | None:
                health = (await _health(env))["managed_upload"]
                return health if health["batches_failed"] >= 2 else None

            health = await _until(outage_reported, what="the health summary counting the refused batches")
            assert health["streams_behind"] == 1 and health["streams_with_gap"] == 0
            assert health["batches_acknowledged"] == 0

            window.close("evidence-route-refused")

            async def mirrored() -> dict[str, Any] | None:
                center = await _center(upload_stack, delivery)
                if len(center["events"]) == len(_event_ids(local)) and center["projection_state"] == "succeeded":
                    return center
                return None

            center = await _until(mirrored, what="the backlog reaching the platform")
            window.mark("backlog-mirrored")
            local = await _local(env, delivery)
            _assert_mirrored_once(local, center)
            passed = [a for a in window.for_task(task_id) if a["action"] == PASS]
            assert passed[0]["sequences"] == first_batch
            assert _contiguous(passed) == [event["sequence"] for event in local["events"]]
            assert all(len(a["sequences"]) <= UPLOAD_BATCH for a in passed)
            assert _writes(env.calls, 3) == 1

            async def caught_up() -> dict[str, Any] | None:
                health = (await _health(env))["managed_upload"]
                return health if health["streams_behind"] == 0 else None

            health = await _until(caught_up, what="the health summary reporting no backlog")
            assert health["batches_acknowledged"] == len(passed)
            evidence.payload["association"] = {"local": local["task_ref"], "platform_run_id": center["platform_run_id"]}
            evidence.payload["events"] = _mirror(local, center)
            evidence.payload["health"] = health
            evidence.payload["outcome"] = "passed"
    finally:
        evidence.write(window, env)


@requires_uv
async def test_sc06_lost_or_incomplete_answer_after_commit_resends_same_batch(
    upload_stack, tmp_path, wheel_dist, step6_runner_database_url, request
) -> None:
    window = UploadWindow()
    evidence = _Evidence(request, "after-commit", step6_runner_database_url, tmp_path)
    env: SimpleNamespace | None = None
    try:
        # Hold uploads until the run is complete locally, so every scripted
        # attempt below carries a fixed batch.
        window.open("hold-until-local-terminal", "all managed runs", BEFORE_COMMIT)
        async with _installed_host(upload_stack, tmp_path, wheel_dist, step6_runner_database_url, window) as env:
            delivery = await upload_stack["queue_delivery"](_delivery_input(5))
            task_id = f"managed-{delivery}"

            async def finished() -> dict[str, Any] | None:
                local = await _local(env, delivery)
                return local if local["state"] == "succeeded" else None

            local = await _until(finished, what="the protected run finishing locally")
            sequences = [event["sequence"] for event in local["events"]]
            assert len(sequences) > 2 * UPLOAD_BATCH, "the scripted answers need at least three batches"
            window.mark("local-terminal-persisted", events=len(sequences))

            starts: list[int] = []

            def answers(attempt: dict[str, Any]) -> str | None:
                if attempt["task"] != task_id:
                    return None
                first = attempt["sequences"][0]
                if first in starts:
                    return PASS
                starts.append(first)
                if len(starts) == 1:
                    return LOSE_ANSWER
                if len(starts) == 2:
                    return INCOMPLETE_ANSWER
                return LOSE_ANSWER if "run_terminal" in attempt["event_types"] else PASS

            scripted_from = len(window.for_task(task_id))
            window.planner = answers
            window.close("hold-until-local-terminal")
            window.open("answers-lost-or-incomplete", "first, second and terminal batch", "scripted")

            async def mirrored() -> dict[str, Any] | None:
                center = await _center(upload_stack, delivery)
                if len(center["events"]) == len(_event_ids(local)) and center["projection_state"] == "succeeded":
                    return center
                return None

            center = await _until(mirrored, what="the platform projecting the completed run")

            async def settled() -> bool:
                scripted = window.for_task(task_id)[scripted_from:]
                if not scripted:
                    return False
                last = scripted[-1]
                # The answer must have landed too: the attempt is recorded
                # before the platform's response is forwarded.
                return (
                    last["action"] == PASS
                    and last.get("platform_status") is not None
                    and last["sequences"][-1] == sequences[-1]
                )

            await _until(settled, what="the terminal batch's resend being confirmed")
            window.close("answers-lost-or-incomplete")
            window.mark("all-batches-confirmed")
            _assert_mirrored_once(local, center)

            scripted = window.for_task(task_id)[scripted_from:]
            faulted = [index for index, a in enumerate(scripted) if a["action"] in (LOSE_ANSWER, INCOMPLETE_ANSWER)]
            assert [scripted[index]["action"] for index in faulted] == [LOSE_ANSWER, INCOMPLETE_ANSWER, LOSE_ANSWER]
            for index in faulted:
                committed, resent = scripted[index], scripted[index + 1]
                # The platform committed the batch; its answer never confirmed it.
                assert committed["platform_status"] == 200
                assert committed["platform_answer"] == {
                    "projected": len(committed["event_ids"]),
                    "skipped_duplicates": 0,
                }
                # The Runner resent the identical batch and the platform deduplicated it.
                assert resent["event_ids"] == committed["event_ids"] and resent["action"] == PASS
                assert resent["platform_answer"] == {"projected": 0, "skipped_duplicates": len(resent["event_ids"])}
                # Only that complete (all-duplicate) confirmation moved the position.
                if index + 2 < len(scripted):
                    assert scripted[index + 2]["sequences"][0] == resent["sequences"][-1] + 1
            confirmed = [a for a in scripted if a["action"] == PASS and a["platform_answer"]]
            assert sorted(set(_contiguous(confirmed))) == sequences
            assert _writes(env.calls, 5) == 1

            async def caught_up() -> dict[str, Any] | None:
                health = (await _health(env))["managed_upload"]
                return health if health["streams_behind"] == 0 else None

            health = await _until(caught_up, what="the health summary reporting no backlog")
            assert health["batches_unconfirmed"] == 1
            assert health["batches_failed"] >= 2
            evidence.payload["events"] = _mirror(local, center)
            evidence.payload["association"] = {"local": local["task_ref"], "platform_run_id": center["platform_run_id"]}
            evidence.payload["health"] = health
            evidence.payload["outcome"] = "passed"
    finally:
        evidence.write(window, env)


@requires_uv
async def test_sc06_runner_restart_backfills_pending_evidence_without_repeat(
    upload_stack, tmp_path, wheel_dist, step6_runner_database_url, request
) -> None:
    window = UploadWindow()
    evidence = _Evidence(request, "restart", step6_runner_database_url, tmp_path)
    env: SimpleNamespace | None = None
    try:
        window.open("evidence-route-refused", "all managed runs", BEFORE_COMMIT)
        async with _installed_host(upload_stack, tmp_path, wheel_dist, step6_runner_database_url, window) as env:
            delivery = await upload_stack["queue_delivery"](_delivery_input(7))
            task_id = f"managed-{delivery}"

            async def finished() -> dict[str, Any] | None:
                local = await _local(env, delivery)
                return local if local["state"] == "succeeded" else None

            before = await _until(finished, what="the protected run finishing locally")
            terminal_at = window.elapsed()
            window.mark("local-terminal-persisted", events=len(_event_ids(before)))

            async def refused_after(moment: float) -> bool:
                return any(a["at"] > moment and a["sequences"][0] == 1 for a in window.for_task(task_id))

            await _until(lambda: refused_after(terminal_at), what="a refused upload after the facts were durable")
            assert (await _center(upload_stack, delivery))["events"] == []
            previous = env.runner
            window.mark("killing-runner", pid=env.pids[-1], launcher_pid=previous.process.pid)
            await asyncio.to_thread(_stop_for_restart, previous)
            window.mark("old-runner-exited", pid=env.pids[-1])
            env.runner = await asyncio.to_thread(previous.restart, kill_previous=True)
            restarted_at = window.elapsed()
            env.pids.append(await asyncio.to_thread(_runner_pid, env.runner))
            window.mark("runner-restarted", pid=env.pids[-1], launcher_pid=env.runner.process.pid)
            assert env.pids[0] != env.pids[1]

            # The new process starts again from the run's first event.
            await _until(lambda: refused_after(restarted_at), what="the restarted Runner retrying its backlog")
            window.close("evidence-route-refused")

            async def mirrored() -> dict[str, Any] | None:
                center = await _center(upload_stack, delivery)
                if len(center["events"]) == len(_event_ids(before)) and center["projection_state"] == "succeeded":
                    return center
                return None

            center = await _until(mirrored, what="the backlog reaching the platform after restart")
            window.mark("backlog-mirrored")
            after = await _local(env, delivery)
            assert after["events"] == before["events"], "restart must not alter the retained local events"
            _assert_mirrored_once(after, center)
            assert _writes(env.calls, 7) == 1 and _reads(env.calls) == 1
            # The Task/Run association is the original one.
            assert after["task_count"] == 1
            assert after["run_ref"]["id"] == f"managed-run-{delivery}"
            assert center["accepted_refs"]["task_ref"] == after["task_ref"]
            assert center["accepted_refs"]["run_ref"] == after["run_ref"]
            assert not center["accepted_refs"].get("successor_runs")
            passed = [a for a in window.for_task(task_id) if a["action"] == PASS]
            assert all(a["at"] > restarted_at for a in passed)
            assert _contiguous(passed) == [event["sequence"] for event in after["events"]]

            async def caught_up() -> dict[str, Any] | None:
                health = (await _health(env))["managed_upload"]
                return health if health["streams_behind"] == 0 else None

            evidence.payload["health"] = await _until(caught_up, what="the restarted Runner reporting no backlog")
            evidence.payload["association"] = {
                "local": after["task_ref"],
                "local_run": after["run_ref"],
                "platform_run_id": center["platform_run_id"],
            }
            evidence.payload["events"] = _mirror(after, center)
            evidence.payload["outcome"] = "passed"
    finally:
        evidence.write(window, env)


@requires_uv
async def test_sc06_confirmation_of_one_run_never_advances_another(
    upload_stack, tmp_path, wheel_dist, step6_runner_database_url, request
) -> None:
    window = UploadWindow()
    evidence = _Evidence(request, "run-isolation", step6_runner_database_url, tmp_path)
    env: SimpleNamespace | None = None
    try:
        window.open("all-runs-held", "all managed runs", BEFORE_COMMIT)
        async with _installed_host(upload_stack, tmp_path, wheel_dist, step6_runner_database_url, window) as env:
            held = await upload_stack["queue_delivery"](_delivery_input(3))
            flowing = await upload_stack["queue_delivery"](_delivery_input(7))
            held_task = f"managed-{held}"
            window.open("one-run-refused", held_task, BEFORE_COMMIT, key=held_task)
            window.close("all-runs-held")

            async def finished(delivery: str) -> dict[str, Any] | None:
                local = await _local(env, delivery)
                return local if local["state"] == "succeeded" else None

            held_local = await _until(lambda: finished(held), what="the held run finishing locally")
            flowing_local = await _until(lambda: finished(flowing), what="the flowing run finishing locally")

            async def flowing_mirrored() -> dict[str, Any] | None:
                center = await _center(upload_stack, flowing)
                return center if len(center["events"]) == len(_event_ids(flowing_local)) else None

            flowing_center = await _until(flowing_mirrored, what="the flowing run reaching the platform")
            window.mark("flowing-run-mirrored")
            _assert_mirrored_once(flowing_local, flowing_center)
            assert (await _center(upload_stack, held))["events"] == []
            held_attempts = window.for_task(held_task)
            assert held_attempts and all(a["action"] == BEFORE_COMMIT for a in held_attempts)
            assert all(a["sequences"][0] == 1 for a in held_attempts)

            async def only_held_behind() -> dict[str, Any] | None:
                health = (await _health(env))["managed_upload"]
                return health if health["streams_behind"] == 1 and health["streams"] == 2 else None

            health = await _until(only_held_behind, what="the health summary showing only the held run behind")
            assert health["streams_with_gap"] == 0

            window.close("one-run-refused", key=held_task)

            async def held_mirrored() -> dict[str, Any] | None:
                center = await _center(upload_stack, held)
                return center if len(center["events"]) == len(_event_ids(held_local)) else None

            held_center = await _until(held_mirrored, what="the held run backfilling")
            window.mark("held-run-mirrored")
            _assert_mirrored_once(held_local, held_center)
            # The other run's confirmations never moved this run's position.
            first_passed = next(a for a in window.for_task(held_task) if a["action"] == PASS)
            assert first_passed["sequences"][0] == 1
            assert _writes(env.calls, 3) == 1 and _writes(env.calls, 7) == 1
            evidence.payload["events"] = {
                "held": _mirror(held_local, held_center),
                "flowing": _mirror(flowing_local, flowing_center),
            }
            evidence.payload["outcome"] = "passed"
    finally:
        evidence.write(window, env)


# -- isolated local-gap fixture -----------------------------------------------------------


async def test_sc06_local_gap_holds_stream_apart_from_platform_lag(
    upload_stack, tmp_path, step6_runner_database_url, request
) -> None:
    """A run that lost local rows is held before its gap and reported with the
    missing range; a healthy run whose platform is temporarily behind is
    reported separately and catches up; nothing past the gap is uploaded."""

    from hecate_durable.contracts.durable import TaskLifecycleState
    from hecate_durable.storage.models import EventRow
    from hecate_runner.managed import ManagedChannel, managed_run_ref, managed_task_ref
    from sqlalchemy import delete

    store = upload_stack["store"]
    window = UploadWindow()
    evidence = _Evidence(request, "local-gap", step6_runner_database_url, tmp_path)
    recorded: list[dict[str, Any]] = []
    await upload_stack["admit"]()
    upload_stack["app"].add_middleware(EvidenceRouteFaults, window=window)
    server, server_task, base_url = await serve_over_tcp(upload_stack["app"])

    def channel_process() -> ManagedChannel:
        return ManagedChannel(
            base_url=base_url,
            workspace_id=str(WS),
            trust_root=ROOT_NAME,
            host_id="host-sc06-gap",
            secret=SECRET,
            store=store,
            issuer_domain=ISSUER,
            upload_batch=1,
            gap_recorder=recorded.append,
        )

    try:
        channel = channel_process()
        assert await channel.register() is True
        await upload_stack["opt_in"]()
        gapped_delivery = await upload_stack["queue_delivery"]({"messages": []})
        healthy_delivery = await upload_stack["queue_delivery"]({"messages": []})
        assert await channel.pull_once() == 2
        for delivery in (gapped_delivery, healthy_delivery):
            task_ref, run_ref = managed_task_ref(delivery), managed_run_ref(delivery)
            store.apply_task_state(task_ref, TaskLifecycleState.RUNNING)
            store.emit_event(task_ref, run_ref, event_type="progress_note", payload={"step": 1})
            store.apply_task_state(task_ref, TaskLifecycleState.SUCCEEDED)
        gapped_run, healthy_run = managed_run_ref(gapped_delivery), managed_run_ref(healthy_delivery)
        with store.session_factory() as session, session.begin():
            # Isolated fault: the gapped run loses its second local row.
            session.execute(delete(EventRow).where(EventRow.run_id == gapped_run.id, EventRow.source_sequence == 2))
        window.mark("local-row-lost", run=gapped_run.id, sequence=2)
        # With one envelope per batch, the page after sequence 1 is the gap marker alone.
        assert [event.kind.value for event in store.read_events(gapped_run, cursor=1, limit=1).events] == ["gap"]
        window.open("platform-behind", f"managed-{healthy_delivery}", BEFORE_COMMIT, key=f"managed-{healthy_delivery}")

        for _ in range(4):
            await channel.upload_events()
        report = channel.upload_report()
        streams = {item["run_ref"]["id"]: item for item in report["streams"]}
        assert report["streams_behind"] == 1 and report["streams_with_gap"] == 1
        assert streams[healthy_run.id]["state"] == "behind" and streams[healthy_run.id]["gap"] is None
        assert streams[gapped_run.id] == {
            "task_ref": managed_task_ref(gapped_delivery).to_dict(),
            "run_ref": gapped_run.to_dict(),
            "state": "reconciliation_required",
            "acknowledged_sequence": 1,
            "behind": False,
            "gap": {"from_sequence": 2, "to_sequence": 2},
        }
        evidence.payload["report_during_outage"] = report

        window.close("platform-behind", key=f"managed-{healthy_delivery}")
        for _ in range(8):
            await channel.upload_events()
        report = channel.upload_report()
        assert report["streams_behind"] == 0 and report["streams_with_gap"] == 1

        healthy_local = await asyncio.to_thread(_read_local, store.engine.url.render_as_string(False), healthy_delivery)
        healthy_center = await _center(upload_stack, healthy_delivery)
        _assert_mirrored_once(healthy_local, healthy_center)
        gapped_local = await asyncio.to_thread(_read_local, store.engine.url.render_as_string(False), gapped_delivery)
        gapped_center = await _center(upload_stack, gapped_delivery)
        # Only the prefix before the gap reached the platform; the stream is
        # never presented as complete and the later local events stay retained.
        assert [event["event_id"] for event in gapped_center["events"]] == [gapped_local["events"][0]["event_id"]]
        assert gapped_center["projection_state"] is None
        assert [(event["kind"], event["sequence"]) for event in gapped_local["events"]] == [
            ("event", 1),
            ("gap", 2),
            ("event", 3),
            ("event", 4),
        ]
        assert {attempt["sequences"][0] for attempt in window.attempts if attempt["run"] == gapped_run.id} == {1}
        gap_record = {
            "task_ref": managed_task_ref(gapped_delivery).to_dict(),
            "run_ref": gapped_run.to_dict(),
            "from_sequence": 2,
            "to_sequence": 2,
        }
        assert recorded == [gap_record]

        # A restarted process re-detects the same gap from the retained log,
        # re-confirms the prefix as duplicates and stays held.
        restarted = channel_process()
        assert await restarted.register() is True
        for _ in range(3):
            await restarted.upload_events()
        assert restarted.upload_report()["streams_with_gap"] == 1
        assert recorded == [gap_record, gap_record]
        assert len((await _center(upload_stack, gapped_delivery))["events"]) == 1
        assert restarted.stats.events_acknowledged_duplicate >= 1
        evidence.payload["gap"] = {"run": gapped_run.id, **{k: gap_record[k] for k in ("from_sequence", "to_sequence")}}
        evidence.payload["report_after_recovery"] = report
        evidence.payload["events"] = {
            "gapped": _mirror(gapped_local, gapped_center),
            "healthy": _mirror(healthy_local, healthy_center),
        }
        evidence.payload["outcome"] = "passed"
    finally:
        evidence.write(window, None)
        await stop_tcp_server(server, server_task)
