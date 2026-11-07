"""Signed release lifecycle over one offline wheelhouse: A → B → rollback A.

The full offline deployment acceptance from the guide, executed as one
repeatable drill on the standalone single-host Runner profile: prepare a
real wheelhouse delivery, install it into a clean venv, install and
activate signed artifacts A and B, then cold start A → graceful stop →
switch to B → refuse A's stale composition (pointer unchanged, host lock
released) → cold start B → graceful stop → roll back to A per the current
generation → cold start A and re-serve the HISTORICAL Task/Run terminal
records over HTTP. The business stub's call log is the replay witness:
startup, the release switch and the historical queries must make zero
business calls; only the new acceptance reads advance the log — proving
the switch and the rollback replayed no completed action.

The durable SQLite ledger under the host profile is the queryable
terminal-record authority: each phase verifies its run through the store
while the runner is stopped, and the phase-3 historical queries verify
the phase-1 record is unchanged after two releases and a rollback.

Network isolation is a deployment property, not a test property: when the
drill runs inside a loopback-only namespace (CI's runtime-wheel-clean-install
job wires `unshare --net` with `lo` up and sets
``HECATE_LIFECYCLE_ISOLATION=loopback-only``), the evidence records and
asserts that boundary (Linux exposes only the loopback interface there).
``HECATE_LIFECYCLE_DELIVERY`` points the drill at a delivery prepared
OUTSIDE the isolated phase so the namespace never resolves or downloads
dependencies — the isolated phase installs from the locked wheelhouse
with the standalone installer and runs everything on loopback.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage import SqlDurableStore

from tests.scenarios.test_deployment_manifest import _assert_refusal, _DeploymentStack, _script
from tests.scenarios.tools import runner_harness


def _isolation_evidence() -> dict:
    """The network boundary the drill actually ran under."""
    mode = os.environ.get("HECATE_LIFECYCLE_ISOLATION", "host-namespace")
    interfaces = (
        sorted(entry.name for entry in Path("/sys/class/net").iterdir()) if sys.platform.startswith("linux") else []
    )
    return {"mode": mode, "interfaces": interfaces}


@pytest.fixture(scope="module")
def offline_env(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """A real offline-installed environment and its delivery directory.

    Under ``HECATE_LIFECYCLE_DELIVERY`` the wheelhouse was prepared before
    the isolated phase started; the drill then only installs from the
    locked delivery — never re-resolving or downloading dependencies.
    """

    root = tmp_path_factory.mktemp("runner-lifecycle")
    prepared = os.environ.get("HECATE_LIFECYCLE_DELIVERY")
    if prepared:
        delivery = Path(prepared)
    else:
        assert os.environ.get("HECATE_LIFECYCLE_ISOLATION") != "loopback-only", (
            "prepare HECATE_LIFECYCLE_DELIVERY before entering the isolated phase"
        )
        if not runner_harness.uv_available():
            pytest.skip("uv is required to prepare a wheelhouse when no delivery is supplied")
        delivery = root / "delivery"
        result = _script("runner_env_prepare.py", "--target", str(delivery))
        assert result.returncode == 0, result.stderr[-3000:]
    digest = json.loads((delivery / "environment.json").read_text(encoding="utf-8"))["manifest_sha256"]
    venv = root / "venv"
    result = _script(
        "runner_env_install.py",
        "--delivery",
        str(delivery),
        "--expected-manifest-sha256",
        digest,
        "--target",
        str(venv),
    )
    assert result.returncode == 0, result.stderr[-3000:]
    python_exe = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return python_exe, delivery


@pytest.fixture(scope="module")
def lifecycle(tmp_path_factory: pytest.TempPathFactory, offline_env: tuple[Path, Path]):
    """The durable-wired stack plus the business stub's call log."""
    offline_python, delivery = offline_env
    business, calls = runner_harness._start_business_api()
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("RUNNER_SHUTDOWN_TOKEN", "sc-shutdown-token")
            root = tmp_path_factory.mktemp("lifecycle-host")
            durable = {
                "database_url": f"sqlite:///{(root / 'host.db').as_posix()}",
                "workspace": "offline-lifecycle",
            }
            # The release CLI verifies the host's durable state on every
            # install/activate/rollback; the ledger must already exist and
            # be initialized, exactly as the runner would leave it.
            bootstrap = SqlDurableStore(durable["database_url"])
            bootstrap.create_schema()
            bootstrap.dispose()
            stack = _DeploymentStack(root, offline_python, delivery, business.server_address[1], durable=durable)
            yield stack, calls, durable["database_url"]
    finally:
        runner_harness.stop_business_api(business)


def _instance(stack: _DeploymentStack, process, port: int) -> runner_harness.RunnerInstance:
    return runner_harness.RunnerInstance(stack.root, stack.host, stack.offline_python, process, port)


def _port(stack: _DeploymentStack, deployment_path: Path) -> int:
    return int((stack.root / f"port-{deployment_path.stem}.txt").read_text(encoding="utf-8"))


def _graceful_stop(stack: _DeploymentStack, process, port: int) -> None:
    """The acceptance's normal shutdown: the token endpoint, exit code 0."""
    status, _ = _instance(stack, process, port).request(
        "POST", "/admin/shutdown", {"token": "sc-shutdown-token"}, token=None
    )
    assert status in (200, 202, 204)
    assert process.wait(timeout=30) == 0


def _authorized_read(stack: _DeploymentStack, port: int) -> tuple[str, dict]:
    """One real authorized inventory read; returns (run_id, run body).

    The authorization is server-verified (the bearer identity scopes the
    run), the business stub's call log advances by exactly this read —
    the live execution evidence — and the delivered result itself is
    verified against the durable SQLite record at the phase boundary
    (:func:`_terminal_record`), which is the acceptance's authoritative
    by-identity terminal query.
    """
    runner = _instance(stack, None, port)
    status, submit = runner.request(
        "POST",
        "/runs",
        {"input": {"prompt": "check"}, "tool_arguments": {"query_inventory": {"domain": "domain_a", "sku": "SKU-A1"}}},
    )
    assert status == 202, submit
    run_id = submit["run_ref"].split("/")[1]
    status, run = runner.wait_run(run_id)
    assert status == 200 and run["status"] == "succeeded", run
    status, delivered = runner.request("GET", f"/runs/{run_id}")
    assert status == 200 and delivered["status"] == "succeeded", delivered
    assert delivered.get("result_ref") == f"runs/{run_id}/artifacts", delivered
    return run_id, delivered


def _terminal_record(database_url: str, run_id: str) -> tuple:
    """The durable terminal facts of one run, resolved by its Run identity.

    The stub model backend commits no delivery content on the task row;
    the honest terminal facts are the lifecycle state, the ``run_terminal``
    event and the run's action-ledger verdicts (tool, outcome, result
    digest) — all resolvable by the original Task/Run identity after any
    number of releases and restarts.
    """
    from hecate_runner.contract import ISSUER_DOMAIN

    store = SqlDurableStore(database_url)
    try:
        run_ref = BackendRef(RefKind.RUN, ISSUER_DOMAIN, run_id)
        task_ref = store.task_for_run(run_ref)
        assert task_ref is not None, f"no durable task for run {run_id}"
        record = store.get_task_state(task_ref)
        assert record is not None and record.lifecycle_state is TaskLifecycleState.SUCCEEDED
        terminal = [
            event.payload
            for event in store.read_events(run_ref).events
            if event.payload.get("event_type") == "run_terminal"
        ]
        assert terminal and terminal[-1].get("status") == "succeeded", terminal
        actions = store.list_run_actions(run_ref)
        assert len(actions) == 1, actions
        action = actions[0]
        assert action["intent"]["action_name"] == "query_inventory", action
        assert action["last_outcome"]["outcome"] == "succeeded", action
        assert action["last_outcome"]["result_digest"], action
        assert action["result_payload"]["quantity"] == 100, action
        snapshot = {"task": record.to_dict(), "terminal": terminal, "actions": actions}
        return task_ref, snapshot
    finally:
        store.dispose()


def test_signed_release_lifecycle_switch_and_rollback_with_history(lifecycle, tmp_path: Path) -> None:
    stack, calls, database_url = lifecycle
    isolation = _isolation_evidence()
    if isolation["mode"] == "loopback-only":
        # The namespace boundary is part of the evidence: loopback only.
        assert isolation["interfaces"] == ["lo"], isolation

    # -- Phase 1: cold start A, serve the real acceptance surface, stop. ---
    stack.activate(stack.sha_a)
    assert json.loads(stack.pointer())["manifest_sha256"] == stack.sha_a
    deployment_a, digest_a = stack.compose(stack.sha_a, tmp_path)
    process, serving = stack.start(deployment_a, digest_a)
    try:
        assert "serving on" in serving, serving[-800:]
        port = _port(stack, deployment_a)
        runner = _instance(stack, process, port)
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200 and health["ready"] is True
        assert len(calls) == 0
        run_a1, body_a1 = _authorized_read(stack, port)
        calls_after_a1 = len(calls)
        assert calls_after_a1 == 1
        _graceful_stop(stack, process, port)
    finally:
        stack.stop(process)
    task_a1, snapshot_a1 = _terminal_record(database_url, run_a1)
    assert snapshot_a1["actions"]

    # -- Phase 2: switch to B; A's stale composition refuses cold. ---------
    stack.activate(stack.sha_b)
    assert json.loads(stack.pointer())["manifest_sha256"] == stack.sha_b
    _assert_refusal(stack, deployment_a, digest_a, "activate the composed release")
    deployment_b, digest_b = stack.compose(stack.sha_b, tmp_path)
    process, serving = stack.start(deployment_b, digest_b)
    try:
        assert "serving on" in serving, serving[-800:]
        # Startup and the failed switch made zero business calls: the stub
        # log separates lifecycle traffic from acceptance reads.
        assert len(calls) == calls_after_a1
        port = _port(stack, deployment_b)
        runner = _instance(stack, process, port)
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200 and health["ready"] is True
        run_b, body_b = _authorized_read(stack, port)
        calls_after_b = len(calls)
        assert calls_after_b == calls_after_a1 + 1
        _graceful_stop(stack, process, port)
    finally:
        stack.stop(process)
    task_b, snapshot_b = _terminal_record(database_url, run_b)
    assert task_b != task_a1 and snapshot_b["actions"]

    # -- Phase 3: roll back to A; history serves, nothing replays. ---------
    stack.rollback()
    assert json.loads(stack.pointer())["manifest_sha256"] == stack.sha_a
    process, serving = stack.start(deployment_a, digest_a)
    try:
        assert "serving on" in serving, serving[-800:]
        port = _port(stack, deployment_a)
        runner = _instance(stack, process, port)
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200 and health["ready"] is True
        # The restart, the switch and the rollback replayed no completed
        # action: the business stub saw nothing since the last acceptance.
        assert len(calls) == calls_after_b
        # The original Task/Run identities still resolve under the
        # rolled-back A — including the run that B served — with their
        # original delivered results, restored from the SQLite ledger.
        status, history_a1 = runner.request("GET", f"/runs/{run_a1}")
        assert status == 200 and history_a1 == body_a1, history_a1
        status, history_b = runner.request("GET", f"/runs/{run_b}")
        assert status == 200 and history_b == body_b, history_b
        status, actions_a1 = runner.request("GET", f"/tasks/{task_a1.id}/actions")
        assert status == 200 and actions_a1["actions"] == snapshot_a1["actions"], actions_a1
        status, actions_b = runner.request("GET", f"/tasks/{task_b.id}/actions")
        assert status == 200 and actions_b["actions"] == snapshot_b["actions"], actions_b
        # History cost the business API nothing; a NEW acceptance read does.
        assert len(calls) == calls_after_b
        run_a2, _body_a2 = _authorized_read(stack, port)
        assert run_a2 not in {run_a1, run_b}
        calls_after_a2 = len(calls)
        assert calls_after_a2 == calls_after_b + 1
        _graceful_stop(stack, process, port)
    finally:
        stack.stop(process)

    # The phase-1 durable record is untouched by two releases and a rollback.
    task_a1_after, snapshot_a1_after = _terminal_record(database_url, run_a1)
    assert task_a1_after == task_a1 and snapshot_a1_after == snapshot_a1
    task_b_after, snapshot_b_after = _terminal_record(database_url, run_b)
    assert task_b_after == task_b and snapshot_b_after == snapshot_b

    evidence = {
        "isolation": isolation,
        "delivery_reused_from_env": bool(os.environ.get("HECATE_LIFECYCLE_DELIVERY")),
        "phases": {
            "a_cold_start": {"run_id": run_a1, "business_calls": calls_after_a1},
            "b_switch": {"run_id": run_b, "business_calls": calls_after_b},
            "rollback_a": {"run_id": run_a2, "business_calls": calls_after_a2},
        },
        "business_calls_total": len(calls),
    }
    evidence_path = tmp_path / "lifecycle-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nlifecycle evidence: {evidence_path}\n{evidence_path.read_text(encoding='utf-8')}")
