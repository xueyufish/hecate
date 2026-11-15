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

Two fault windows extend the drill beyond the happy path, after release
A has completed one business run and stopped: activating a tampered
INSTALLED candidate must refuse through the real release CLI without
moving the pointer (bytes and generation preserved), and a switch
subprocess interrupted before the pointer commit must preserve the
previous selection with the host lock immediately re-acquirable. In both
windows release A cold starts again, the completed run's Task/Run and
action ledger still resolve over HTTP and from the durable store, the
fault itself makes zero business calls, and the interrupted switch's
retried candidate commits cleanly. The verified boundary is process
interruption at the activation seams — not host-wide power loss or disk
corruption (see the install guide's verified scope).
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage import SqlDurableStore

from tests.scenarios.test_deployment_manifest import _assert_refusal, _DeploymentStack, _script
from tests.scenarios.tools import runner_harness


def _isolation_evidence() -> dict:
    """The network boundary the drill actually ran under.

    The interface list comes from ``socket.if_nameindex`` — a netlink
    query scoped to the calling process's network namespace, exactly what
    ``ip link`` reports. ``/sys/class/net`` is NOT namespace-scoped unless
    sysfs is remounted inside the namespace, so it would wrongly show the
    host's interfaces.
    """
    mode = os.environ.get("HECATE_LIFECYCLE_ISOLATION", "host-namespace")
    interfaces = sorted(name for _index, name in socket.if_nameindex()) if sys.platform.startswith("linux") else []
    isolation = {"mode": mode, "interfaces": interfaces}
    if mode == "loopback-only":
        assert interfaces == ["lo"], isolation
    return isolation


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


@pytest.fixture()
def fault_stack(tmp_path: Path, offline_env: tuple[Path, Path]):
    """A fresh release-managed host for one fault window.

    The offline venv and delivery are shared with the module; the host
    directory, durable ledger, trust keys and the business stub are per
    test, so each fault scenario controls the pointer generation and the
    business call log exactly.
    """
    offline_python, delivery = offline_env
    business, calls = runner_harness._start_business_api()
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("RUNNER_SHUTDOWN_TOKEN", "sc-shutdown-token")
            root = tmp_path / "fault-host"
            root.mkdir()  # the SQLite ledger bootstrap cannot create parents
            durable = {
                "database_url": f"sqlite:///{(root / 'host.db').as_posix()}",
                "workspace": "offline-lifecycle",
            }
            # The release CLI verifies the host's durable state on every
            # operation; the ledger must already exist and be initialized.
            bootstrap = SqlDurableStore(durable["database_url"])
            bootstrap.create_schema()
            bootstrap.dispose()
            stack = _DeploymentStack(root, offline_python, delivery, business.server_address[1], durable=durable)
            yield stack, calls, durable["database_url"]
    finally:
        runner_harness.stop_business_api(business)


def _a_with_one_completed_run(stack: _DeploymentStack, calls: list, database_url: str, tmp_path: Path) -> dict:
    """The shared fault precondition: A active, one completed run, stopped.

    Activates release A (generation 1), serves one real acceptance read,
    stops gracefully, and resolves the durable terminal record while the
    runner is down. The returned facts are what each fault window compares
    against: pointer bytes and generation, deployment, run body, action
    ledger.
    """
    stack.activate(stack.sha_a)
    pointer = stack.pointer()
    deployment_a, digest_a = stack.compose(stack.sha_a, tmp_path)
    process, serving = stack.start(deployment_a, digest_a)
    try:
        assert "serving on" in serving, serving[-800:]
        port = _port(stack, deployment_a)
        runner = _instance(stack, process, port)
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200 and health["ready"] is True
        assert len(calls) == 0
        run_a, body_a = _authorized_read(stack, port)
        assert len(calls) == 1
        _graceful_stop(stack, process, port)
    finally:
        stack.stop(process)
    task_a, snapshot_a = _terminal_record(database_url, run_a)
    return {
        "pointer": pointer,
        "generation": json.loads(pointer)["generation"],
        "deployment": deployment_a,
        "digest": digest_a,
        "run_id": run_a,
        "run_body": body_a,
        "task_ref": task_a,
        "snapshot": snapshot_a,
        "business_calls": len(calls),
    }


def _assert_historical_delivery(runner: runner_harness.RunnerInstance, history: dict) -> None:
    status, run = runner.request("GET", f"/runs/{history['run_id']}")
    assert status == 200 and run == history["run_body"], run
    status, actions = runner.request("GET", f"/tasks/{history['task_ref'].id}/actions")
    assert status == 200 and actions["actions"] == history["snapshot"]["actions"], actions


def test_tampered_installed_release_refuses_activation_and_preserves_a(fault_stack, tmp_path: Path) -> None:
    """Fault window 1: the installed candidate B is corrupted before activation.

    The real release CLI refuses the tampered candidate without moving the
    pointer (original bytes and generation preserved), the refusal and the
    recovery make zero business calls, and release A cold starts again with
    the completed run's Task/Run and action ledger intact.
    """
    stack, calls, database_url = fault_stack
    isolation = _isolation_evidence()
    history = _a_with_one_completed_run(stack, calls, database_url, tmp_path)
    pointer_before = history["pointer"]

    victim = stack.installed_release_dir(stack.sha_b) / "files" / "agents/summary/main.json"
    victim.write_bytes(victim.read_bytes() + b"tampered")

    result = stack.run_release_cli(
        "activate",
        "--host",
        str(stack.host),
        "--release",
        stack.sha_b,
        "--expected-generation",
        str(history["generation"]),
    )
    assert result.returncode == 2, result.stderr[-2000:]
    assert "candidate release does not pass the current gates" in result.stderr
    assert "manifest digest mismatch" in result.stderr
    pointer_after = stack.pointer()
    assert pointer_after == pointer_before
    selection = json.loads(pointer_after)
    assert selection["manifest_sha256"] == stack.sha_a
    assert selection["generation"] == history["generation"]
    calls_after_fault = len(calls)
    assert calls_after_fault == history["business_calls"]

    process, serving = stack.start(history["deployment"], history["digest"])
    try:
        assert "serving on" in serving, serving[-800:]
        port = _port(stack, history["deployment"])
        runner = _instance(stack, process, port)
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200 and health["ready"] is True
        # Startup and the refused switch replayed nothing; history is free.
        assert len(calls) == calls_after_fault
        _assert_historical_delivery(runner, history)
        calls_after_recovery = len(calls)
        assert calls_after_recovery == calls_after_fault
        run_a2, _body_a2 = _authorized_read(stack, port)
        assert run_a2 != history["run_id"]
        calls_total = len(calls)
        assert calls_total == calls_after_recovery + 1
        _graceful_stop(stack, process, port)
    finally:
        stack.stop(process)

    task_after, snapshot_after = _terminal_record(database_url, history["run_id"])
    assert task_after == history["task_ref"] and snapshot_after == history["snapshot"]

    evidence = {
        "isolation": isolation,
        "delivery_reused_from_env": bool(os.environ.get("HECATE_LIFECYCLE_DELIVERY")),
        "scenario": "tampered-installed-candidate-refusal",
        "fault": {
            "boundary": "installed release files/agents/summary/main.json corrupted before activation",
            "cli_exit_code": result.returncode,
            "refusal": result.stderr.strip(),
        },
        "pointer": {
            "before_sha256": hashlib.sha256(pointer_before).hexdigest(),
            "after_sha256": hashlib.sha256(pointer_after).hexdigest(),
            "bytes_unchanged": pointer_after == pointer_before,
            "active": selection["manifest_sha256"],
            "generation": selection["generation"],
        },
        "phases": {
            "initial_a": {"run_id": history["run_id"], "business_calls": history["business_calls"]},
            "after_fault": {"business_calls": calls_after_fault},
            "recovered_a": {"run_id": history["run_id"], "business_calls": calls_after_recovery},
            "new_read_a": {"run_id": run_a2, "business_calls": calls_total},
        },
        "business_calls_total": calls_total,
    }
    evidence_path = tmp_path / "lifecycle-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nlifecycle evidence: {evidence_path}\n{evidence_path.read_text(encoding='utf-8')}")


def test_switch_interrupted_before_pointer_commit_preserves_a_and_retries(fault_stack, tmp_path: Path) -> None:
    """Fault window 2: the switch process dies before the pointer commit.

    A real offline-environment subprocess is killed at the commit boundary
    (the package tests' injection point): the previous pointer survives
    byte-for-byte, the host lock is re-acquirable immediately, release A
    cold starts with its completed run intact, and the retried switch to
    the still-valid B commits cleanly and serves the same history without
    replaying anything.
    """
    stack, calls, database_url = fault_stack
    isolation = _isolation_evidence()
    history = _a_with_one_completed_run(stack, calls, database_url, tmp_path)
    pointer_before = history["pointer"]

    from hecate_runner.release import HostLock, read_active_selection

    # The fault injection runs in a subprocess of the OFFLINE environment,
    # mirroring the package-level crash-at-the-commit-boundary test.
    script = (
        "import os\n"
        "from pathlib import Path\n"
        "from hecate_runner import release as r\n"
        "from hecate_runner.artifact_signing import load_trust_policy\n"
        "r._commit_active_selection = lambda *args, **kwargs: os._exit(17)\n"
        f"r.activate_release(Path({str(stack.host)!r}), {stack.sha_b!r}, "
        f"load_trust_policy(Path({str(stack.policy_path)!r})), expected_generation={history['generation']})\n"
    )
    result = subprocess.run(
        [str(stack.offline_python), "-I", "-c", script],
        cwd=str(stack.root),
        env={
            **{key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}},
            "RUNNER_SHUTDOWN_TOKEN": "sc-shutdown-token",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 17, result.stderr[-2000:]
    pointer_after = stack.pointer()
    assert pointer_after == pointer_before
    selection = read_active_selection(stack.host)
    assert selection is not None
    assert selection.manifest_sha256 == stack.sha_a
    assert selection.generation == history["generation"]
    with HostLock(stack.host):
        pass  # the crash released the OS lock with its descriptor
    calls_after_fault = len(calls)
    assert calls_after_fault == history["business_calls"]

    process, serving = stack.start(history["deployment"], history["digest"])
    try:
        assert "serving on" in serving, serving[-800:]
        port = _port(stack, history["deployment"])
        runner = _instance(stack, process, port)
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200 and health["ready"] is True
        # The interrupted switch replayed nothing; history is free.
        assert len(calls) == calls_after_fault
        _assert_historical_delivery(runner, history)
        calls_after_recovery = len(calls)
        assert calls_after_recovery == calls_after_fault
        run_a2, _body_a2 = _authorized_read(stack, port)
        assert run_a2 != history["run_id"]
        calls_under_a = len(calls)
        assert calls_under_a == calls_after_recovery + 1
        _graceful_stop(stack, process, port)
    finally:
        stack.stop(process)

    task_after_a, snapshot_after_a = _terminal_record(database_url, history["run_id"])
    assert task_after_a == history["task_ref"] and snapshot_after_a == history["snapshot"]

    # The interrupted attempt left nothing to clean up: the retried switch
    # to the valid candidate commits cleanly at the preserved generation.
    stack.activate(stack.sha_b)
    selection_b = json.loads(stack.pointer())
    assert selection_b["manifest_sha256"] == stack.sha_b
    assert selection_b["generation"] == history["generation"] + 1
    deployment_b, digest_b = stack.compose(stack.sha_b, tmp_path)
    process, serving = stack.start(deployment_b, digest_b)
    try:
        assert "serving on" in serving, serving[-800:]
        port = _port(stack, deployment_b)
        runner = _instance(stack, process, port)
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200 and health["ready"] is True
        # The completed switch replays nothing either: the completed run
        # still resolves under B with its original delivered result.
        assert len(calls) == calls_under_a
        _assert_historical_delivery(runner, history)
        _graceful_stop(stack, process, port)
    finally:
        stack.stop(process)

    calls_after_retry = len(calls)
    assert calls_after_retry == calls_under_a
    task_after_b, snapshot_after_b = _terminal_record(database_url, history["run_id"])
    assert task_after_b == history["task_ref"] and snapshot_after_b == history["snapshot"]

    evidence = {
        "isolation": isolation,
        "delivery_reused_from_env": bool(os.environ.get("HECATE_LIFECYCLE_DELIVERY")),
        "scenario": "pointer-commit-interrupt",
        "fault": {
            "boundary": "switch subprocess exited at _commit_active_selection, before the pointer swap",
            "exit_code": result.returncode,
        },
        "pointer": {
            "before_sha256": hashlib.sha256(pointer_before).hexdigest(),
            "after_interrupt_sha256": hashlib.sha256(pointer_after).hexdigest(),
            "bytes_unchanged": pointer_after == pointer_before,
            "active": selection.manifest_sha256,
            "generation": selection.generation,
            "after_retry": {"active": selection_b["manifest_sha256"], "generation": selection_b["generation"]},
        },
        "phases": {
            "initial_a": {"run_id": history["run_id"], "business_calls": history["business_calls"]},
            "after_fault": {"business_calls": calls_after_fault},
            "recovered_a": {"run_id": history["run_id"], "business_calls": calls_after_recovery},
            "new_read_a": {"run_id": run_a2, "business_calls": calls_under_a},
            "retry_switch_b": {"run_id": history["run_id"], "business_calls": calls_after_retry},
        },
        "business_calls_total": calls_after_retry,
    }
    evidence_path = tmp_path / "lifecycle-evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nlifecycle evidence: {evidence_path}\n{evidence_path.read_text(encoding='utf-8')}")
