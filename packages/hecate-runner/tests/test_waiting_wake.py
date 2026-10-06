"""Persistent waiting and one-time wake (step6c).

Full-chain tests over the real runner assembly (durable store + engine +
HTTP server): a task parks into waiting_approval / waiting_input with the
approval/input tool's business side effects withheld, an authorized wake
consumes the token, requeues the task on a NEW attempt run, and re-drives
it through the serial slot; expired commands, wrong tokens, and replays
get explicit rejected/idempotent receipts without dispatching anything; a
hard restart keeps the task waiting until a legitimate wake.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest_runner import write_profile
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_runner.durable import DurableRuntime
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceStore
from hecate_runner.profile import load_profile
from hecate_runner.server import RunnerServer

ISSUER = "standalone-host"
READ_ARGS = {"domain": "domain_a", "sku": "S-1"}


@pytest.fixture(autouse=True)
def _shutdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


class _BusinessApi:
    """Records every dispatch; can request input like the contract allows."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append({"tool": tool_name, "arguments": dict(arguments)})
        return {"status": "ok", "result": f"{tool_name}-result"}

    def tools(self) -> list[str]:
        return [call["tool"] for call in self.calls]


class _InputRequiredApi:
    """Proxies the recorder: query_inventory asks for input until woken."""

    def __init__(self, recorder: _BusinessApi) -> None:
        self._recorder = recorder

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        if tool_name == "query_inventory" and "region" not in arguments:
            self._recorder.calls.append({"tool": tool_name, "arguments": dict(arguments)})
            return {"status": "input_required", "contract": {"fields": ["region"]}}
        return await self._recorder(tool_name, arguments, principal, domains)


class _Harness:
    """One runner process: durable store + engine + HTTP server."""

    def __init__(self, profile_dir: Path, *, api: _BusinessApi, database_url: str | None = None) -> None:
        self.profile = load_profile(profile_dir)
        assert self.profile.config.durable is not None
        self.database_url = database_url or self.profile.config.durable.database_url
        self.store = self._open_store(self.database_url)
        self.durable = DurableRuntime(self.store, workspace=self.profile.config.durable.workspace)
        self.evidence = EvidenceStore(self.profile.config.evidence_dir)
        self.api = api
        self.engine = ExecutionEngine(self.profile, self.evidence, api, durable=self.durable)
        self.server = RunnerServer(self.profile, self.engine, self.evidence, durable=self.durable)
        self.httpd = self.server.serve()
        self.port = self.httpd.server_address[1]

    @staticmethod
    def _open_store(url: str):
        from hecate_durable.storage import SqlDurableStore

        store = SqlDurableStore(url)
        store.create_schema()
        return store

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "Bearer reader-secret-token")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def close(self) -> None:
        self.server.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.dispose()


def _profile(tmp_path: Path, database_url: str, *, kind: str) -> Path:
    """Two focused graphs: an approval-wait chain and an input-wait chain."""

    if kind == "approval":
        return write_profile(
            tmp_path,
            allowlist=["query_inventory", "submit_ticket"],
            durable={"database_url": database_url, "workspace": "waiting"},
            with_approval_tool=True,
        )
    return write_profile(
        tmp_path,
        allowlist=["query_inventory"],
        durable={"database_url": database_url, "workspace": "waiting"},
    )


async def _submit(harness: _Harness, tool_arguments: dict) -> str:
    body = {"input": {"prompt": "waiting work"}, "tool_arguments": tool_arguments}
    status, payload = harness.call("POST", "/runs", body)
    assert status == 202, payload
    return payload["run_ref"].rsplit("/", 1)[-1]


async def _wait_task_state(harness: _Harness, task_ref: BackendRef, *states: str, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = harness.store.get_task_state(task_ref)
        if record is not None and record.lifecycle_state.value in states:
            return record
        await asyncio.sleep(0.05)
    current = harness.store.get_task_state(task_ref)
    raise AssertionError(f"task {task_ref.id} at {current.lifecycle_state.value if current else None}, wanted {states}")


async def test_approval_tool_parks_and_resume_executes_once(tmp_path: Path):
    api = _BusinessApi()
    harness = _Harness(_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval"), api=api)
    try:
        run_id = await _submit(
            harness, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
        )
        run_ref = BackendRef(RefKind.RUN, ISSUER, run_id)
        task_ref = harness.durable.store.task_for_run(run_ref)
        assert task_ref is not None
        await _wait_task_state(harness, task_ref, "waiting_approval")

        # The approval tool itself never dispatched: the read node in the
        # fixed linear graph already ran, but the claimed action stayed
        # claimed and its business call waits for the resume.
        assert api.tools() == ["query_inventory"]
        actions = harness.durable.run_actions(run_ref)
        ticket_actions = [a for a in actions if (a.get("intent") or {}).get("action_name") == "submit_ticket"]
        assert ticket_actions, "approval intent must be recorded before parking"
        claim = ticket_actions[0].get("active_claim") or ticket_actions[0].get("claim")
        assert claim, "the claimed action must stay claimed, never executed"

        # Authorized wait view carries the one-time token for the owner.
        _status, view = harness.call("GET", f"/runs/{run_id}")
        assert view["status"] == "waiting_approval"
        assert view["wait"]["wake_kind"] == "resume"
        token = view["wait"]["wait_token"]

        # Wake: applied receipt, token consumed, new attempt run executes.
        command_id = f"resume-{uuid.uuid4()}"
        status, payload = harness.call(
            "POST", f"/runs/{run_id}/resume", {"command_id": command_id, "wait_token": token}
        )
        assert status == 202 and payload["state"] == "applied", payload
        await _wait_task_state(harness, task_ref, "succeeded")
        # The wake attempt re-runs the read node, then dispatches the
        # approval tool under the consumed grant.
        assert api.tools() == ["query_inventory", "query_inventory", "submit_ticket"]

        # The wake rebind moved the task onto a NEW attempt run.
        new_run = harness.durable.store.run_for_task(task_ref)
        assert new_run is not None and new_run.id != run_id

        # Replay of the same command returns the applied receipt, no second
        # execution.
        status, payload = harness.call(
            "POST", f"/runs/{run_id}/resume", {"command_id": command_id, "wait_token": token}
        )
        assert status == 202 and payload["state"] == "applied"
        await asyncio.sleep(0.3)
        assert api.tools() == ["query_inventory", "query_inventory", "submit_ticket"]
    finally:
        harness.close()


async def test_wrong_token_and_missing_fields_rejected_without_dispatch(tmp_path: Path):
    api = _BusinessApi()
    harness = _Harness(_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval"), api=api)
    try:
        run_id = await _submit(
            harness, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
        )
        run_ref = BackendRef(RefKind.RUN, ISSUER, run_id)
        task_ref = harness.durable.store.task_for_run(run_ref)
        await _wait_task_state(harness, task_ref, "waiting_approval")

        status, payload = harness.call(
            "POST", f"/runs/{run_id}/resume", {"command_id": f"resume-{uuid.uuid4()}", "wait_token": "wrong"}
        )
        assert status == 202 and payload["state"] == "rejected" and "mismatch" in payload["reason"]
        wait = harness.durable.wait_of(task_ref)
        assert wait is not None and wait["consumed"] is False
        record = await _wait_task_state(harness, task_ref, "waiting_approval")
        assert record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
        assert api.tools() == ["query_inventory"]

        # Missing fields are a 422 before any command is recorded.
        status, _payload = harness.call("POST", f"/runs/{run_id}/resume", {"wait_token": "x"})
        assert status == 422
        assert api.tools() == ["query_inventory"]
    finally:
        harness.close()


async def test_expired_command_rejected(tmp_path: Path):
    from hecate_durable.contracts.durable import CommandState, ControlCommandKind, ControlCommandRecord

    api = _BusinessApi()
    harness = _Harness(_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval"), api=api)
    try:
        run_id = await _submit(
            harness, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
        )
        run_ref = BackendRef(RefKind.RUN, ISSUER, run_id)
        task_ref = harness.durable.store.task_for_run(run_ref)
        await _wait_task_state(harness, task_ref, "waiting_approval")
        wait = harness.durable.wait_of(task_ref)
        assert wait is not None
        token = wait["wait_token"]

        # Record the command with an already-passed expiry (the wake route
        # never sets expiries itself; the store validates inside apply).
        command_id = f"resume-{uuid.uuid4()}"
        harness.durable.store.record(
            ControlCommandRecord(
                command_id=command_id,
                kind=ControlCommandKind.RESUME,
                issuer="app-reader",
                task_ref=task_ref,
                issued_at=datetime.now(UTC).isoformat(),
                state=CommandState.REQUESTED,
                expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            )
        )
        receipt, reason = harness.durable.apply_wake(
            command_id=command_id,
            kind="resume",
            issuer="app-reader",
            task_ref=task_ref,
            wait_token=token,
        )
        assert receipt.state.value == "rejected"
        assert reason is not None and "expired" in reason.lower()
        await asyncio.sleep(0.2)
        assert api.tools() == ["query_inventory"]
        wait = harness.durable.wait_of(task_ref)
        assert wait is not None and wait["consumed"] is False
    finally:
        harness.close()


async def test_input_required_parks_and_provide_input_merges(tmp_path: Path):
    recorder = _BusinessApi()
    api = _InputRequiredApi(recorder)
    harness = _Harness(_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="input"), api=api)
    recorder = recorder  # noqa: PLW0127 - named for assertion clarity below
    try:
        run_id = await _submit(harness, {"query_inventory": READ_ARGS})
        run_ref = BackendRef(RefKind.RUN, ISSUER, run_id)
        task_ref = harness.durable.store.task_for_run(run_ref)
        await _wait_task_state(harness, task_ref, "waiting_input")
        wait = harness.durable.wait_of(task_ref)
        assert wait is not None and wait["wake_kind"] == "provide_input"
        # Only the input-requiring call happened; no second dispatch.
        assert recorder.tools() == ["query_inventory"]

        wait = harness.durable.wait_of(task_ref)
        assert wait is not None
        token = wait["wait_token"]
        status, payload = harness.call(
            "POST",
            f"/runs/{run_id}/provide-input",
            {"command_id": f"input-{uuid.uuid4()}", "wait_token": token, "input": {"region": "cn-north"}},
        )
        assert status == 202 and payload["state"] == "applied", payload
        await _wait_task_state(harness, task_ref, "succeeded")
        # The new attempt dispatched with the merged provided input present.
        assert recorder.tools() == ["query_inventory", "query_inventory"]
        assert recorder.calls[1]["arguments"].get("region") == "cn-north"
    finally:
        harness.close()


async def test_hard_restart_keeps_waiting_then_wakes(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'host.db'}"
    profile_dir = _profile(tmp_path, database_url, kind="approval")
    api = _BusinessApi()
    crashed = _Harness(profile_dir, api=api, database_url=database_url)
    run_id = await _submit(
        crashed, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
    )
    run_ref = BackendRef(RefKind.RUN, ISSUER, run_id)
    task_ref = crashed.durable.store.task_for_run(run_ref)
    await _wait_task_state(crashed, task_ref, "waiting_approval")
    crashed.store.dispose()  # process death: the in-memory gate dies with it

    # A fresh process over the same database: the task stays parked and is
    # NOT re-driven by startup reconcile (waiting is filtered out).
    restarted = _Harness(profile_dir, api=api, database_url=database_url)
    try:
        record = restarted.store.get_task_state(task_ref)
        assert record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
        await asyncio.sleep(0.4)
        assert api.tools() == ["query_inventory"]
        assert restarted.store.get_task_state(task_ref).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL

        # Legitimate wake on the new process executes the task once: the
        # read node re-runs (new attempt), the approval tool dispatches.
        wait = restarted.durable.wait_of(task_ref)
        assert wait is not None
        token = wait["wait_token"]
        status, payload = restarted.call(
            "POST", f"/runs/{run_id}/resume", {"command_id": f"resume-{uuid.uuid4()}", "wait_token": token}
        )
        assert status == 202 and payload["state"] == "applied", payload
        await _wait_task_state(restarted, task_ref, "succeeded")
        assert api.tools() == ["query_inventory", "query_inventory", "submit_ticket"]
    finally:
        restarted.close()


async def test_preview_profile_still_refuses_approval_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from hecate_runner.profile import ProfileError, load_profile

    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "tok")
    profile_dir = write_profile(
        tmp_path,
        allowlist=["query_inventory", "submit_ticket"],
        with_approval_tool=True,
    )
    with pytest.raises(ProfileError, match="approval_required"):
        load_profile(profile_dir)
