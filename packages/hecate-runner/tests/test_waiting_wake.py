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

    def call(
        self, method: str, path: str, body: dict | None = None, *, token: str = "reader-secret-token"
    ) -> tuple[int, dict]:
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
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
        assert not ticket_actions, "waiting must not create a claimed-but-unknown business action"

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
        assert api.tools() == ["query_inventory", "submit_ticket"]

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
        assert api.tools() == ["query_inventory", "submit_ticket"]
    finally:
        harness.close()


async def test_wake_does_not_repeat_write_before_approval(tmp_path: Path):
    """A new attempt must retain the completed predecessor action identity."""
    api = _BusinessApi()
    profile = write_profile(
        tmp_path,
        allowlist=["submit_inventory_update", "submit_ticket"],
        durable={"database_url": f"sqlite:///{tmp_path / 'host.db'}", "workspace": "waiting"},
        with_write_tool=True,
        with_approval_tool=True,
    )
    harness = _Harness(profile, api=api)
    try:
        run_id = await _submit(
            harness,
            {
                "submit_inventory_update": {"domain": "domain_a", "sku": "S-1", "quantity": 2},
                "submit_ticket": {"domain": "domain_a", "ticket": "T-1"},
            },
        )
        task = harness.store.task_for_run(BackendRef(RefKind.RUN, ISSUER, run_id))
        await _wait_task_state(harness, task, "waiting_approval")
        token = harness.durable.wait_of(task)["wait_token"]
        status, response = harness.call(
            "POST",
            f"/runs/{run_id}/resume",
            {
                "command_id": "approve-once",
                "wait_token": token,
            },
        )
        assert status == 202 and response["state"] == "applied"
        await _wait_task_state(harness, task, "succeeded")
        assert api.tools() == ["submit_inventory_update", "submit_ticket"]
    finally:
        harness.close()


async def test_submission_cannot_inject_internal_approval_grant(tmp_path: Path):
    """Caller-controlled input cannot skip the persistent approval wait."""
    from hecate_runtime.action_ledger import tool_arguments_digest

    api = _BusinessApi()
    harness = _Harness(_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval"), api=api)
    try:
        args = {"domain": "domain_a", "ticket": "T-1"}
        status, _ = harness.call(
            "POST",
            "/runs",
            {
                "tool_arguments": {"query_inventory": READ_ARGS, "submit_ticket": args},
                "_wake_grant": {"tool": "submit_ticket", "arguments_digest": tool_arguments_digest(args)},
            },
        )
        assert status == 422
        assert api.calls == []
    finally:
        harness.close()


async def test_wake_replay_rejects_changed_token(tmp_path: Path):
    """Idempotency binds the complete wake request, not only its kind."""
    api = _BusinessApi()
    harness = _Harness(_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval"), api=api)
    try:
        run_id = await _submit(
            harness, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
        )
        task = harness.store.task_for_run(BackendRef(RefKind.RUN, ISSUER, run_id))
        await _wait_task_state(harness, task, "waiting_approval")
        token = harness.durable.wait_of(task)["wait_token"]
        assert (
            harness.call("POST", f"/runs/{run_id}/resume", {"command_id": "bound-wake", "wait_token": token})[1][
                "state"
            ]
            == "applied"
        )
        status, _ = harness.call(
            "POST", f"/runs/{run_id}/resume", {"command_id": "bound-wake", "wait_token": "different"}
        )
        assert status == 409
    finally:
        harness.close()


async def test_unknown_write_stops_later_business_tools(tmp_path: Path):
    """An indeterminate write cannot be followed by more external actions."""
    api = _BusinessApi()
    profile = write_profile(
        tmp_path,
        allowlist=["submit_inventory_update", "query_inventory"],
        durable={"database_url": f"sqlite:///{tmp_path / 'unknown.db'}", "workspace": "waiting"},
        with_write_tool=True,
    )

    async def dispatch(name, arguments, principal, domains):
        await api(name, arguments, principal, domains)
        return {"status": "unknown", "detail": "business write receipt lost"}

    harness = _Harness(profile, api=dispatch)
    try:
        run_id = await _submit(
            harness,
            {
                "submit_inventory_update": {"domain": "domain_a", "sku": "S-1", "quantity": 2},
                "query_inventory": READ_ARGS,
            },
        )
        task = harness.store.task_for_run(BackendRef(RefKind.RUN, ISSUER, run_id))
        await _wait_task_state(harness, task, "reconciliation_required")
        assert api.tools() == ["submit_inventory_update"]
    finally:
        harness.close()


async def test_get_cannot_apply_wake(tmp_path: Path):
    """Read requests cannot mutate a persistent wait."""
    api = _BusinessApi()
    harness = _Harness(_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval"), api=api)
    try:
        run = await _submit(
            harness, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
        )
        task = harness.store.task_for_run(BackendRef(RefKind.RUN, ISSUER, run))
        await _wait_task_state(harness, task, "waiting_approval")
        token = harness.durable.wait_of(task)["wait_token"]
        assert harness.call("GET", f"/runs/{run}/resume", {"command_id": "unsafe-get", "wait_token": token})[0] == 405
        assert harness.store.get_task_state(task).lifecycle_state is TaskLifecycleState.WAITING_APPROVAL
        assert harness.store.get("unsafe-get") is None
    finally:
        harness.close()


async def test_wake_commit_survives_restart_without_loading_old_checkpoint(tmp_path, monkeypatch):
    """A crash after wake commit must not restore the parked attempt's checkpoint."""
    api = _BusinessApi()
    profile_dir = _profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval")
    first = _Harness(profile_dir, api=api)
    try:
        old_run = await _submit(
            first, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
        )
        task = first.store.task_for_run(BackendRef(RefKind.RUN, ISSUER, old_run))
        await _wait_task_state(first, task, "waiting_approval")
        token = first.durable.wait_of(task)["wait_token"]
        monkeypatch.setattr(first.server, "_schedule_redrive", lambda *args: None)
        status, receipt = first.call(
            "POST", f"/runs/{old_run}/resume", {"command_id": "commit-before-crash", "wait_token": token}
        )
        assert status == 202 and receipt["state"] == "applied"
        new_run = first.store.run_for_task(task)
        assert new_run.id != old_run
    finally:
        first.close()
    restarted = _Harness(profile_dir, api=api)
    try:
        # The CLI owns startup recovery; this in-process server harness
        # drives that same redelivery entry explicitly.
        restarted.server._schedule_redrive(task)
        await _wait_task_state(restarted, task, "succeeded")
        assert api.tools() == ["query_inventory", "submit_ticket"]
        assert restarted.call("GET", f"/runs/{new_run.id}")[0] == 200
    finally:
        restarted.close()
    final = _Harness(profile_dir, api=api)
    try:
        status, run = final.call("GET", f"/runs/{new_run.id}")
        assert status == 200 and run["status"] == "succeeded"
        assert api.tools() == ["query_inventory", "submit_ticket"]
    finally:
        final.close()


@pytest.mark.parametrize("same_id", [False, True])
async def test_concurrent_wakes_consume_token_once(tmp_path, same_id):
    """Different command IDs cannot both apply the same wait revision."""
    api = _BusinessApi()
    harness = _Harness(_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval"), api=api)
    try:
        run = await _submit(
            harness, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
        )
        ref = BackendRef(RefKind.RUN, ISSUER, run)
        task = harness.store.task_for_run(ref)
        await _wait_task_state(harness, task, "waiting_approval")
        token = harness.durable.wait_of(task)["wait_token"]
        receipts = await asyncio.gather(
            *[
                asyncio.to_thread(
                    harness.durable.apply_wake,
                    command_id="racing-one" if same_id else f"racing-{i}",
                    kind="resume",
                    issuer="app-reader",
                    task_ref=task,
                    run_ref=ref,
                    wait_token=token,
                )
                for i in range(2)
            ]
        )
        assert sorted(receipt.state.value for receipt, _ in receipts) == (
            ["applied", "applied"] if same_id else ["applied", "rejected"]
        )
        assert api.tools() == ["query_inventory"]
    finally:
        harness.close()


async def test_durable_task_reads_are_scoped_to_owner(tmp_path):
    """Task metadata, actions and governance events cannot cross caller identities."""
    profile_dir = _profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval")
    identity_path = profile_dir / "identity.json"
    config = json.loads(identity_path.read_text(encoding="utf-8"))
    config["identities"].append(
        {
            "principal": "other-reader",
            "role": "read_only",
            "domains": ["domain_a"],
            "credential": "file:secrets/other-reader-token",
        }
    )
    (profile_dir / "secrets/other-reader-token").write_text("other-token", encoding="utf-8")
    identity_path.write_text(json.dumps(config), encoding="utf-8")
    harness = _Harness(profile_dir, api=_BusinessApi())
    try:
        run = await _submit(
            harness, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
        )
        task = harness.store.task_for_run(BackendRef(RefKind.RUN, ISSUER, run))
        await _wait_task_state(harness, task, "waiting_approval")
        other_token = (profile_dir / "secrets/other-reader-token").read_text(encoding="utf-8")
        assert harness.call("GET", "/tasks", token=other_token)[1]["tasks"] == []
        for suffix in ("", "/events", "/actions"):
            assert harness.call("GET", f"/tasks/{task.id}{suffix}", token=other_token)[0] == 403
        assert harness.call("GET", f"/tasks/{task.id}")[0] == 200
    finally:
        harness.close()


async def test_changed_definition_rejects_wake_before_consuming_token(tmp_path):
    """An upgrade cannot grant a waiting action under a replacement tool graph."""
    from dataclasses import replace

    api = _BusinessApi()
    harness = _Harness(_profile(tmp_path, f"sqlite:///{tmp_path / 'host.db'}", kind="approval"), api=api)
    try:
        run = await _submit(
            harness, {"query_inventory": READ_ARGS, "submit_ticket": {"domain": "domain_a", "ticket": "T-1"}}
        )
        task = harness.store.task_for_run(BackendRef(RefKind.RUN, ISSUER, run))
        await _wait_task_state(harness, task, "waiting_approval")
        token = harness.durable.wait_of(task)["wait_token"]
        changed = replace(harness.profile, config=replace(harness.profile.config, tool_allowlist=("submit_ticket",)))
        harness.server._engine = ExecutionEngine(changed, harness.evidence, api, durable=harness.durable)
        status, _ = harness.call(
            "POST", f"/runs/{run}/resume", {"command_id": "changed-definition", "wait_token": token}
        )
        assert status == 409
        assert harness.durable.wait_of(task)["consumed"] is False
        assert harness.store.get("changed-definition") is None
        assert api.tools() == ["query_inventory"]
    finally:
        harness.server._engine = harness.engine
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
                run_ref=run_ref,
                expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                detail_ns={"wait_token": token},
            )
        )
        receipt, reason = harness.durable.apply_wake(
            command_id=command_id,
            kind="resume",
            issuer="app-reader",
            task_ref=task_ref,
            run_ref=run_ref,
            wait_token=token,
            expires_at=harness.store.get(command_id).expires_at,
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
        assert api.tools() == ["query_inventory", "submit_ticket"]
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
