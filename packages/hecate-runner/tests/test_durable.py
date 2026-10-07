"""Durable profile: persistent tasks, idempotent submission, restart
recovery, control-command receipts, and the local evidence gate.

These are the step6 halves of the standalone-consumption scenarios:

- the SC03 persistence/restart half — a write whose outcome was recorded
  recovers its REAL result after a restart (no duplicate business write);
  a write claimed but unresolved at crash time safely stops the task into
  ``reconciliation_required`` (the approval-binding half stays with step7);
- the SC06 local half — an unwritable local evidence store stops new
  protected actions while readonly traffic continues (central-upload
  buffering stays with step10).

Crashes are simulated at the durable-state level: the ledger rows a crashed
process leaves behind are exactly what these tests construct, and recovery
runs through the real engine + ledger gate over the same database file.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest_runner import READER_TOKEN, write_profile
from hecate_durable.contracts.durable import (
    ActionIntent,
    ActionOutcome,
    ActionOutcomeRecord,
    TaskLifecycleState,
)
from hecate_durable.contracts.references import BackendRef
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlDurableStore
from hecate_runner.durable import DurableRuntime, action_key_for
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceCapacityError, EvidenceStore
from hecate_runner.profile import load_profile
from hecate_runner.server import RunnerServer
from hecate_runner.tools import BusinessApiToolDispatcher

ISSUER = "standalone-host"


class _WriteAwareBusinessApi:
    """In-process inventory API stub with a recorded, blockable write path."""

    def __init__(self) -> None:
        self.write_calls: list[dict] = []
        self.block_write = threading.Event()
        self.block_write.set()
        calls = self.write_calls

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args) -> None:
                pass

            def _send(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                request = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/inventory/write":
                    calls.append(request)
                    # Hold the write until the test releases it so the run
                    # stays in flight across the "crash" construction.
                    import time

                    deadline = time.monotonic() + 10.0
                    while not self.server.block_write.is_set() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self._send(200, {"result": {"written": True, "sku": request["arguments"]["sku"]}})
                    return
                asked = (request.get("arguments") or {}).get("domain")
                if asked not in request.get("domains", []):
                    self._send(403, {"detail": "cross-domain denied"})
                    return
                self._send(200, {"result": {"sku": "SKU-A1", "quantity": 100}, "outcome": "ok"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.block_write = self.block_write
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.block_write.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def database_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'runner-state.db'}"


@pytest.fixture()
def durable_profile_dir(tmp_path: Path, database_url: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")
    return write_profile(
        tmp_path,
        allowlist=["query_inventory", "submit_inventory_update"],
        durable={"database_url": database_url, "workspace": "standalone"},
        with_write_tool=True,
    )


@pytest.fixture()
def api() -> _WriteAwareBusinessApi:
    stub = _WriteAwareBusinessApi()
    yield stub
    stub.close()


class _Harness:
    """One runner process: durable store + engine + HTTP server."""

    def __init__(self, profile_dir: Path, api_url: str, *, database_url: str) -> None:
        self.profile = load_profile(profile_dir)
        self.store = SqlDurableStore(database_url)
        self.store.create_schema()
        self.durable = DurableRuntime(self.store, workspace=self.profile.config.durable.workspace)
        self.evidence = EvidenceStore(self.profile.config.evidence_dir)
        dispatcher = BusinessApiToolDispatcher(api_url)

        async def dispatch(tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
            return await dispatcher(tool_name, arguments, principal, domains)

        self.engine = ExecutionEngine(self.profile, self.evidence, dispatch, durable=self.durable)
        self.server = RunnerServer(self.profile, self.engine, self.evidence, durable=self.durable)
        self.httpd = self.server.serve()
        self.port = self.httpd.server_address[1]

    def call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        token: str | None = READER_TOKEN,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def resume(self, task_ref: BackendRef, run_ref: BackendRef, run_input: dict) -> object:
        return self.server._run_coro(_resume_coro(self.engine, task_ref, run_ref, run_input))

    def close(self, *, abandon: bool = False) -> None:
        """``abandon`` simulates a crash: no graceful engine shutdown."""

        if not abandon:
            self.server._run_coro(self.engine.close())
        self.server.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.dispose()


def _resume_coro(engine: ExecutionEngine, task_ref: BackendRef, run_ref: BackendRef, run_input: dict):
    async def _inner():
        return engine.resume(task_ref, run_ref, run_input)

    return _inner()


@pytest.fixture()
def harness(durable_profile_dir: Path, api: _WriteAwareBusinessApi, database_url: str):
    one = _Harness(durable_profile_dir, api.url, database_url=database_url)
    yield one
    one.close(abandon=True)


def _wait_task_terminal(harness: _Harness, run_id: str, timeout: float = 10.0) -> dict:
    """Poll /tasks until the run's task reaches a terminal/reconciled state.

    The engine publishes the in-memory run status slightly before the
    durable finish commit; the task store is the authority to wait on.
    """

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tasks = harness.call("GET", "/tasks")[1]["tasks"]
        match = next((t for t in tasks if t["run_ref"] == f"runs/{run_id}"), None)
        if match is not None and match["state"] not in ("queued", "running"):
            return match
        time.sleep(0.05)
    raise AssertionError(f"task for run {run_id} did not reach a terminal state")


def _write_request(quantity: int = 3) -> dict:
    return {
        "input": {"prompt": "restock"},
        "tool_arguments": {
            "query_inventory": {"domain": "domain_a", "sku": "SKU-A1"},
            "submit_inventory_update": {"domain": "domain_a", "sku": "SKU-A1", "quantity": quantity},
        },
    }


# --- submission idempotency + persistence -----------------------------------


def test_durable_submit_persists_task_and_idempotent_replay(harness: _Harness) -> None:
    request = _write_request()
    status, first = harness.call("POST", "/runs", request, headers={"Idempotency-Key": "order-1"})
    assert status == 202, first
    run_id = first["run_ref"].split("/")[1]
    harness.engine.wait_for_sync(run_id)

    status, tasks = harness.call("GET", "/tasks")
    assert status == 200
    assert any(t["state"] in ("succeeded", "reconciliation_required") for t in tasks["tasks"])

    # Same key + same body → the SAME run, never a second execution.
    status, replay = harness.call("POST", "/runs", request, headers={"Idempotency-Key": "order-1"})
    assert status == 202
    assert replay["run_ref"] == first["run_ref"]
    assert replay.get("replayed") is True
    assert len(harness.call("GET", "/tasks")[1]["tasks"]) == 1

    # Same key + different body → explicit conflict.
    status, conflict = harness.call("POST", "/runs", _write_request(quantity=9), headers={"Idempotency-Key": "order-1"})
    assert status == 409
    assert conflict["type"].endswith("idempotency-conflict")


def test_write_tool_executes_once_through_the_ledger(harness: _Harness, api: _WriteAwareBusinessApi) -> None:
    status, submitted = harness.call("POST", "/runs", _write_request())
    assert status == 202
    run_id = submitted["run_ref"].split("/")[1]
    harness.engine.wait_for_sync(run_id)
    assert len(api.write_calls) == 1
    record = _wait_task_terminal(harness, run_id)
    assert record["state"] == "succeeded"


# --- SC03 persistence/restart half ---------------------------------------------


def test_restart_recovers_real_result_after_recorded_outcome(
    harness: _Harness, api: _WriteAwareBusinessApi, durable_profile_dir: Path, database_url: str
) -> None:
    """Write outcome recorded, task still running at crash: recovery
    backfills the ledger's REAL result; the business write never re-executes."""
    request = _write_request()
    task_ref, run_ref = _crash_artifacts(harness.durable, request, outcome_recorded=True)
    task_id = task_ref.id
    run_id = run_ref.id
    harness.close(abandon=True)
    api.close()

    # New process over the same database: replay through the ledger gate.
    restarted_api = _WriteAwareBusinessApi()
    try:
        two = _Harness(durable_profile_dir, restarted_api.url, database_url=database_url)
        try:
            two.resume(task_ref, run_ref, request)
            two.engine.wait_for_sync(run_id)
            assert restarted_api.write_calls == [], "recorded outcome must not re-execute the write"
            actions = two.call("GET", f"/tasks/{task_id}/actions")[1]["actions"]
            write_action = next(a for a in actions if a["intent"]["action_name"] == "submit_inventory_update")
            assert write_action["last_outcome"]["outcome"] == "succeeded"
            assert write_action["result_payload"] == {"written": True, "sku": "SKU-A1"}, (
                "recovery must surface the real recorded result"
            )
            record = two.durable.task_state(task_id)
            assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
        finally:
            two.close(abandon=True)
    finally:
        restarted_api.close()


def test_restart_safely_stops_claimed_write(
    harness: _Harness, api: _WriteAwareBusinessApi, durable_profile_dir: Path, database_url: str
) -> None:
    """Write claimed, outcome missing at crash: the resumed dispatch is
    withheld, the task lands in reconciliation_required, no second write."""
    request = _write_request()
    task_ref, run_ref = _crash_artifacts(harness.durable, request, outcome_recorded=False)
    task_id = task_ref.id
    run_id = run_ref.id
    harness.close(abandon=True)
    api.close()

    restarted_api = _WriteAwareBusinessApi()
    try:
        two = _Harness(durable_profile_dir, restarted_api.url, database_url=database_url)
        try:
            two.resume(task_ref, run_ref, request)
            two.engine.wait_for_sync(run_id)
            assert restarted_api.write_calls == [], "a claimed write must never auto-replay"
            actions = two.call("GET", f"/tasks/{task_id}/actions")[1]["actions"]
            write_action = next(a for a in actions if a["intent"]["action_name"] == "submit_inventory_update")
            assert write_action["state"] == "claimed"
            assert write_action["pending_reconciliation"] is True
            record = two.durable.task_state(task_id)
            assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
        finally:
            two.close(abandon=True)
    finally:
        restarted_api.close()


def _crash_artifacts(durable: DurableRuntime, request: dict, *, outcome_recorded: bool):
    """Build exactly the durable rows a crashed process leaves behind.

    The submission is registered and the task moved to running — then the
    write action is either claimed-with-outcome (crash after the outcome
    commit, before the task converged) or claimed-without-outcome (crash in
    the dispatch window). No engine execution happens here; the resumed
    process replays against these rows.
    """

    from hecate_runtime.action_ledger import tool_arguments_digest

    submission = durable.submit(principal="app-reader", run_input=request, idempotency_key="crash-window")
    task_ref = submission.association.task_ref
    run_ref = submission.association.run_ref
    durable.begin_run(task_ref)
    action_key = action_key_for(run_ref.id, "submit_inventory_update")
    write_args = request["tool_arguments"]["submit_inventory_update"]
    durable.store.record_intent_ex(
        ActionIntent(
            action_key=action_key,
            action_name="submit_inventory_update",
            arguments_digest=tool_arguments_digest(write_args),
            side_effect_class=ToolSideEffectClass.NON_IDEMPOTENT_WRITE,
        ),
        task_ref=task_ref,
        run_ref=run_ref,
        session_id=run_ref.id,
        execution_id=action_key,
        tool_call_id=action_key,
    )
    _receipt, token = durable.store.claim_ex(action_key, holder="crashed-worker")
    if outcome_recorded:
        durable.store.record_outcome_ex(
            ActionOutcomeRecord(
                action_key=action_key,
                outcome=ActionOutcome.SUCCEEDED,
                result_digest=tool_arguments_digest({"written": True, "sku": write_args["sku"]}),
            ),
            claim_token=token,
            result_payload={"written": True, "sku": write_args["sku"]},
        )
    return task_ref, run_ref


# --- events cursor + cancel receipts -------------------------------------------


def test_events_cursor_reads_persisted_log(harness: _Harness) -> None:
    status, submitted = harness.call("POST", "/runs", _write_request())
    run_id = submitted["run_ref"].split("/")[1]
    harness.engine.wait_for_sync(run_id)
    status, page = harness.call("GET", f"/runs/{run_id}/events")
    assert status == 200
    types = [e["payload"]["event_type"] for e in page["events"] if e["kind"] == "event"]
    assert "task_submitted" in types and "action_outcome" in types
    # Cursor resume: the second page after the returned cursor is empty at
    # the tail and keeps the cursor stable.
    status, tail = harness.call("GET", f"/runs/{run_id}/events?cursor={page['cursor']}")
    assert status == 200
    assert tail["events"] == [] and tail["cursor"] == page["cursor"]


def test_cancel_records_command_and_converges_on_effect(harness: _Harness, api: _WriteAwareBusinessApi) -> None:
    api.block_write.clear()  # hold the write so the run stays in flight
    status, submitted = harness.call("POST", "/runs", _write_request())
    run_id = submitted["run_ref"].split("/")[1]

    async def _wait_write_in_flight() -> None:
        for _ in range(400):
            if len(api.write_calls) == 1:
                return
            await asyncio.sleep(0.05)

    harness.server._run_coro(_wait_write_in_flight())
    assert len(api.write_calls) == 1
    status, cancel = harness.call("POST", f"/runs/{run_id}/cancel")
    assert status == 202 and cancel["command_id"]
    api.block_write.set()
    harness.engine.wait_for_sync(run_id)

    record = _wait_task_terminal(harness, run_id)
    assert record["state"] == "cancelled", record
    command = harness.durable.store.get(cancel["command_id"])
    assert command.state.value == "applied", "applied only after the cooperative boundary took hold"


# --- SC06 local half --------------------------------------------------------------


def test_evidence_probe_fails_closed(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(OSError):
        EvidenceStore(blocker).probe()


def test_unwritable_evidence_stops_new_protected_runs(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    api: _WriteAwareBusinessApi,
    durable_profile_dir: Path,
    database_url: str,
) -> None:
    def _broken_probe(_self) -> None:
        raise OSError("evidence device full")

    monkeypatch.setattr(EvidenceStore, "probe", _broken_probe)
    status, protected = harness.call("POST", "/runs", _write_request())
    assert status == 503
    assert protected["type"].endswith("evidence-unavailable")
    assert api.write_calls == []
    # Readonly traffic is not gated by the protected-action rule: a
    # read-only durable profile admits runs while the evidence store is
    # unwritable (no protected tools in flight).
    readonly_dir = write_profile(
        Path(str(durable_profile_dir).replace("profile", "readonly-profile")),
        durable={"database_url": database_url + "-readonly", "workspace": "standalone"},
    )
    readonly_api = _WriteAwareBusinessApi()
    try:
        ro = _Harness(readonly_dir, readonly_api.url, database_url=database_url + "-readonly")
        try:
            status, readonly = ro.call(
                "POST",
                "/runs",
                {
                    "input": {"prompt": "check"},
                    "tool_arguments": {"query_inventory": {"domain": "domain_a", "sku": "SKU-A1"}},
                },
            )
            assert status == 202, readonly
        finally:
            ro.close(abandon=True)
    finally:
        readonly_api.close()


def test_capacity_gate_records_failure_category(
    harness: _Harness,
    api: _WriteAwareBusinessApi,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Over-capacity is a policy refusal, not a disk fault: the category travels."""

    def _over_capacity(_self) -> None:
        raise EvidenceCapacityError("evidence over capacity after retention cleanup", usage=10, limit=9, unit="bytes")

    monkeypatch.setattr(EvidenceStore, "probe", _over_capacity)
    status, protected = harness.call("POST", "/runs", _write_request())
    assert status == 503
    assert protected["type"].endswith("evidence-unavailable")
    assert api.write_calls == []
    denials = harness.evidence.query(kind="evidence_gate", outcome="denied")
    assert len(denials) == 1
    assert denials[0].detail["category"] == "capacity"


@pytest.mark.parametrize("failed_tool", ["query_inventory", "submit_inventory_update"])
def test_outcome_persist_failure_keeps_task_pending_reconciliation(
    harness: _Harness, api: _WriteAwareBusinessApi, monkeypatch: pytest.MonkeyPatch, failed_tool: str
) -> None:
    real = SqlDurableStore.record_outcome_ex

    def _failing_outcome(self, outcome, **kwargs):  # noqa: ANN001
        if outcome.action_key.endswith(f":{failed_tool}"):
            raise RuntimeError("ledger write failed after execution")
        return real(self, outcome, **kwargs)

    monkeypatch.setattr(SqlDurableStore, "record_outcome_ex", _failing_outcome)
    status, submitted = harness.call("POST", "/runs", _write_request())
    assert status == 202
    run_id = submitted["run_ref"].split("/")[1]
    harness.engine.wait_for_sync(run_id)
    assert len(api.write_calls) == (1 if failed_tool == "submit_inventory_update" else 0)
    task_id = _wait_task_terminal(harness, run_id)["task_ref"]
    record = harness.durable.task_state(task_id)
    assert record.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED, (
        "an unrecordable outcome must stay pending reconciliation, never silently succeed"
    )


def test_durable_capabilities_override_preview_defaults(harness) -> None:
    capabilities = harness.engine.capabilities()
    for name in ("durable_tasks", "long_task_recovery", "write_tools"):
        assert capabilities[name].startswith("supported:")
    assert capabilities["pause"].startswith("unsupported:")


def test_restart_revalidates_persisted_identity(harness) -> None:
    from dataclasses import replace

    request = _write_request()
    submission = harness.durable.submit(
        principal="app-reader",
        domains=("inventory",),
        run_input=request,
        idempotency_key="revoked",
    )
    harness.durable.begin_run(submission.association.task_ref)
    harness.engine._profile = replace(harness.profile, identities=())
    assert (
        harness.engine.resume(
            submission.association.task_ref,
            submission.association.run_ref,
            {**request, "_host_identity": {"principal": "attacker", "domains": ["all"]}},
        )
        is None
    )
    state = harness.store.get_task_state(submission.association.task_ref)
    assert state.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED


def test_cancel_command_retry_preserves_applied_receipt(harness) -> None:
    from hecate_durable.contracts.durable import CommandState

    submission = harness.durable.submit(
        principal="app-reader", run_input=_write_request(), idempotency_key="cancel-replay"
    )
    arguments = dict(
        command_id="cancel-once",
        issuer="app-reader",
        task_ref=submission.association.task_ref,
        run_ref=submission.association.run_ref,
    )
    original = harness.durable.record_cancel(**arguments)
    harness.durable.cancel_applied(original.command_id)
    replay = harness.durable.record_cancel(**arguments)
    assert replay.state is CommandState.APPLIED
    assert replay.issued_at == original.issued_at
    with pytest.raises(ValueError, match="another cancellation"):
        harness.durable.record_cancel(**{**arguments, "issuer": "other-principal"})
