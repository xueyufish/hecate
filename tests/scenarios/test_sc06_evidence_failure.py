"""SC06 (step6f): local audit store failure windows and no-repeat execution.

The installed durable runner's JSONL evidence store fails in two windows:
before dispatch (submission admission refuses protected runs with an
explicit category and the business write boundary is never reached) and
after a business write committed but before the runner retained the
result evidence (run and action ledger converge on the provable facts,
and a replay of the same idempotent request returns the original
execution identity without repeating the business action). A profile
with only read tools records the current conservative behavior around
its own evidence writes. The fault injection replaces the evidence
directory with a plain file, so an append fails with a plain OSError —
it does not depend on day-file names, directory read-only attributes,
or test user permissions.

Every test runs on the default SQLite task store and, when
``HECATE_STEP6_POSTGRES_URL`` is set (the ``step6-pg-process-matrix``
CI job), again on PostgreSQL with its own schema: the recorded
behaviors must hold identically on both Runner tiers, and the
scenario-side ledger queries open the same database and schema the
Runner was actually configured with — never a hardcoded ``host.db``.
Each test writes one JSON evidence file (``HECATE_SC06_EVIDENCE_FAILURE_DIR``,
default: the test's tmp dir) holding the database tier, commit, source
digests, runner process identities, the fault window, observed states
and business call counts — references and counts only, never payloads
or credentials.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from hecate_durable.contracts.durable import BackendRef, RefKind, TaskLifecycleState
from hecate_durable.storage import SqlDurableStore
from hecate_runner.durable import ISSUER

from tests.scenarios.tools import runner_harness

pytestmark = [
    pytest.mark.skipif(
        not runner_harness.uv_available(),
        reason="uv is required for the clean-install harness",
    )
]

_REPO = Path(__file__).resolve().parents[2]

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
    {"name": "query_inventory", "schema_ref": "schemas/read.json", "permission": "read"},
    {"name": "submit_inventory_update", "schema_ref": "schemas/write.json", "permission": "write"},
]
TOOL_SCHEMAS = {"schemas/read.json": READ_SCHEMA, "schemas/write.json": WRITE_SCHEMA}
READONLY_MANIFEST_TOOLS = [{"name": "query_inventory", "schema_ref": "schemas/read.json", "permission": "read"}]
READONLY_TOOL_SCHEMAS = {"schemas/read.json": READ_SCHEMA}

BOTH_TOOLS_ARGUMENTS = {
    "query_inventory": {"domain": "domain_a", "sku": "SKU-A1"},
    "submit_inventory_update": {"domain": "domain_a", "sku": "SKU-A1", "quantity": 1},
}
READONLY_ARGUMENTS = {"query_inventory": {"domain": "domain_a", "sku": "SKU-A1"}}


@pytest.fixture(scope="module")
def wheel_dist(tmp_path_factory) -> Path:
    """One wheel build shared by every tier of this module's runners."""

    dist = Path(tmp_path_factory.mktemp("sc06-evidence-wheels"))
    runner_harness.ensure_wheels(dist)
    return dist


def _git(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=_REPO, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _write_evidence(
    request: pytest.FixtureRequest,
    database_url: str | None,
    *,
    scenario: str,
    pids: list[int],
    fault_window: str,
    observations: list[dict[str, Any]],
    outcome: str,
    tmp_path: Path,
) -> None:
    """One sanitized JSON record per test: tier, identity, fault, counts."""

    payload: dict[str, Any] = {
        "schema_version": 1,
        "scenario": scenario,
        "test_id": request.node.nodeid,
        "code_version": _git("rev-parse", "HEAD"),
        "code_dirty": bool(_git("status", "--porcelain")),
        "source_sha256": {
            name: hashlib.sha256((_REPO / name).read_bytes()).hexdigest()
            for name in (
                "tests/scenarios/test_sc06_evidence_failure.py",
                "tests/scenarios/tools/runner_harness.py",
            )
        },
        "databases": {"runner": "postgresql" if database_url else "sqlite"},
        "processes": {"runner_pids": pids},
        "fault_window": fault_window,
        "observations": observations,
        "outcome": outcome,
    }
    directory = Path(os.environ.get("HECATE_SC06_EVIDENCE_FAILURE_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    tier = payload["databases"]["runner"]
    path = directory / f"sc06-evidence-failure-{scenario}-{tier}-{uuid.uuid4().hex[:8]}.json"
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    assert "Bearer " not in text
    if database_url and "@" in database_url:
        assert database_url.split("@", 1)[0] not in text
    path.write_text(text, encoding="utf-8")


def _write_arguments(quantity: int) -> dict:
    arguments = dict(BOTH_TOOLS_ARGUMENTS)
    arguments["submit_inventory_update"] = {"domain": "domain_a", "sku": "SKU-A1", "quantity": quantity}
    return arguments


def _submit_run(
    runner: runner_harness.RunnerInstance,
    tool_arguments: dict,
    *,
    idempotency_key: str | None = None,
) -> tuple[int, dict, str]:
    # The fixed tool plan validates every allowlisted tool, so
    # ``tool_arguments`` carries arguments for each of them.
    status, body = runner.request(
        "POST",
        "/runs",
        {"input": {"prompt": "sc06"}, "tool_arguments": tool_arguments},
        headers={"Idempotency-Key": idempotency_key} if idempotency_key is not None else None,
    )
    run_id = body.get("run_ref", "").rsplit("/", 1)[-1] if status == 202 else ""
    return status, body, run_id


def _write_calls(calls: list[dict]) -> list[dict]:
    # The write tool is the only dispatch that carries a quantity.
    return [c for c in calls if (c.get("arguments") or {}).get("quantity") is not None]


def _break_evidence(runner: runner_harness.RunnerInstance) -> None:
    """Make every evidence append fail with a plain OSError.

    The evidence DIRECTORY becomes a plain file, so any open/append under
    it fails. Unlike locking day files, this does not depend on which day
    file the next record targets, directory attributes, or test user
    permissions.
    """

    profile_dir = runner.profile_dir.resolve()
    evidence_dir = profile_dir / "evidence"
    backup = profile_dir / "evidence-before-fault"
    assert evidence_dir.resolve().parent == profile_dir and not backup.exists()
    evidence_dir.rename(backup)
    evidence_dir.touch(exist_ok=False)


def _restore_evidence(runner: runner_harness.RunnerInstance) -> None:
    """Restore the original evidence directory and retained records."""

    profile_dir = runner.profile_dir.resolve()
    evidence_dir = profile_dir / "evidence"
    backup = profile_dir / "evidence-before-fault"
    if backup.is_dir():
        assert backup.resolve().parent == profile_dir
        evidence_dir.unlink(missing_ok=True)
        backup.rename(evidence_dir)


@contextmanager
def _open_store(runner: runner_harness.RunnerInstance, database_url: str | None) -> Iterator[SqlDurableStore]:
    config = json.loads((runner.profile_dir / "runner.json").read_text(encoding="utf-8"))
    configured_url = config["durable"]["database_url"]
    expected_url = database_url or f"sqlite:///{(runner.workdir / 'host.db').as_posix()}"
    if configured_url != expected_url:
        pytest.fail("Runner profile did not use the expected task store for this tier", pytrace=False)
    store = SqlDurableStore(configured_url)
    try:
        yield store
    finally:
        store.dispose()


def test_sc06_unwritable_evidence_refuses_protected_submission_before_dispatch(
    tmp_path: Path, step6_runner_database_url, wheel_dist, request
) -> None:
    observations: list[dict[str, Any]] = []
    pids: list[int] = []
    outcome = "incomplete"
    runner, server, calls = runner_harness.start_runner(
        tmp_path,
        manifest_tools=MANIFEST_TOOLS,
        durable=True,
        tool_allowlist=["query_inventory", "submit_inventory_update"],
        tool_schemas=TOOL_SCHEMAS,
        durable_database_url=step6_runner_database_url,
        dist_dir=wheel_dist,
    )
    try:
        pids.append(runner.process.pid)
        # Baseline: a protected run succeeds while the store is writable.
        status, body, run_id = _submit_run(runner, _write_arguments(1))
        assert status == 202, body
        assert runner.wait_run(run_id)[1]["status"] == "succeeded"
        baseline_run = BackendRef(kind=RefKind.RUN, issuer_domain=ISSUER, id=run_id)
        with _open_store(runner, step6_runner_database_url) as store:
            assert store.task_for_run(baseline_run) is not None
            baseline_tasks = {task.task_ref for task in store.list_tasks()}

        # The store goes bad in a repeatable way.
        retained = {path.name: path.read_bytes() for path in (runner.profile_dir / "evidence").glob("*.jsonl")}
        assert retained
        _break_evidence(runner)

        # Admission refuses the protected submission with the explicit
        # evidence-unavailable category before any task is registered.
        key = "sc06-admission-refused"
        status, body, _ = _submit_run(runner, _write_arguments(2), idempotency_key=key)
        assert status == 503, (status, body)
        assert body["type"] == "urn:hecate:problem:evidence-unavailable", body
        assert "unwritable" in body["detail"], body
        with _open_store(runner, step6_runner_database_url) as store:
            assert store.submission_for_key(key) is None
            assert {task.task_ref for task in store.list_tasks()} == baseline_tasks
        observations.append(
            {
                "step": "admission-refused",
                "http_status": status,
                "problem": body["type"],
                "idempotency_key": key,
                "submission_persisted": False,
                "business_writes": len(_write_calls(calls)),
            }
        )

        # The gate denial is queryable on health with its category.
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200, health
        assert health["evidence_last_gate_failure"]["category"] == "unwritable", health
        observations.append({"step": "health-gate-category", "category": "unwritable"})

        # Independent business record: the refused request never reached
        # the write boundary.
        assert len(_write_calls(calls)) == 1

        # Recovery: a fresh protected run is served normally again.
        _restore_evidence(runner)
        assert all((runner.profile_dir / "evidence" / name).read_bytes() == data for name, data in retained.items())
        status, body, run_id2 = _submit_run(runner, _write_arguments(3))
        assert status == 202, body
        assert runner.wait_run(run_id2)[1]["status"] == "succeeded"
        assert len(_write_calls(calls)) == 2
        observations.append(
            {
                "step": "recovered-run-succeeded",
                "http_status": status,
                "business_writes": len(_write_calls(calls)),
            }
        )
        outcome = "passed"
    finally:
        _restore_evidence(runner)
        if runner.process.poll() is None:
            runner.stop()
        runner_harness.stop_business_api(server)
        _write_evidence(
            request,
            step6_runner_database_url,
            scenario="admission-refusal",
            pids=pids,
            fault_window="evidence directory replaced by a plain file before a protected submission",
            observations=observations,
            outcome=outcome,
            tmp_path=tmp_path,
        )


def test_sc06_post_dispatch_evidence_failure_converges_and_replays_without_reexecution(
    tmp_path: Path, step6_runner_database_url, wheel_dist, request
) -> None:
    stock = 100
    writes: list[int] = []
    fault_triggered = False

    def commit_write(arguments: dict, record: dict) -> dict:
        nonlocal stock, fault_triggered
        quantity = arguments["quantity"]
        stock += quantity
        writes.append(quantity)
        if quantity == 2 and not fault_triggered:
            _break_evidence(runner)
            fault_triggered = True
        return {**record, "quantity": stock}

    runner, server, _calls = runner_harness.start_runner(
        tmp_path,
        manifest_tools=MANIFEST_TOOLS,
        durable=True,
        tool_allowlist=["query_inventory", "submit_inventory_update"],
        tool_schemas=TOOL_SCHEMAS,
        commit_write=commit_write,
        durable_database_url=step6_runner_database_url,
        dist_dir=wheel_dist,
    )
    observations: list[dict[str, Any]] = []
    pids: list[int] = []
    outcome = "incomplete"
    try:
        pids.append(runner.process.pid)
        # Baseline: normal service with one recorded business write.
        status, body, run1 = _submit_run(runner, _write_arguments(1))
        assert status == 202, body
        assert runner.wait_run(run1)[1]["status"] == "succeeded"
        assert writes == [1] and stock == 101

        # Arm the fault and submit a new request under a fixed idempotency
        # key: the business write commits, then result-evidence retention
        # fails.
        replay_arguments = _write_arguments(2)
        key = "sc06-post-dispatch"
        status, body, run2 = _submit_run(runner, replay_arguments, idempotency_key=key)
        assert status == 202, body
        status, run2_body = runner.wait_run(run2)
        # Missing evidence is never presented as success: the run converges
        # on the provable facts — a committed business write with no
        # retained result — as reconciliation-required, with no success
        # result or artifact reference presented to the caller.
        assert status == 200 and run2_body["status"] == "reconciliation_required", (status, run2_body)
        assert run2_body["result_ref"] is None, run2_body
        assert "result" not in run2_body, run2_body
        assert fault_triggered and (runner.profile_dir / "evidence").is_file()
        assert writes == [1, 2] and stock == 103
        runner_log = (runner.workdir / "runner.log").read_text(encoding="utf-8", errors="replace")
        assert f"Runner execution {run2} could not retain evidence" in runner_log
        observations.append(
            {
                "step": "reconciliation-required",
                "http_status": status,
                "run": run2,
                "result_ref_present": run2_body["result_ref"] is not None,
                "business_writes": len(writes),
                "fault_triggered": fault_triggered,
            }
        )

        # The durable action ledger holds the claimed write with NO
        # outcome: recovery stays fail-closed (pending reconciliation),
        # never succeeded.
        run_ref = BackendRef(kind=RefKind.RUN, issuer_domain=ISSUER, id=run2)
        with _open_store(runner, step6_runner_database_url) as store:
            submission = store.submission_for_key(key)
            assert submission is not None and submission.run_ref == run_ref
            assert store.task_for_run(run_ref) == submission.task_ref
            actions = store.list_run_actions(run_ref)
        write_actions = [action for action in actions if action["action_key"].endswith("submit_inventory_update")]
        assert len(write_actions) == 1, actions
        action = write_actions[0]
        assert action["state"] == "claimed", action
        assert action["pending_reconciliation"] is True, action
        assert action["result_payload"] is None, action
        observations.append(
            {
                "step": "action-ledger-fail-closed",
                "write_actions": len(write_actions),
                "state": action["state"],
                "pending_reconciliation": action["pending_reconciliation"],
                "result_payload_present": action["result_payload"] is not None,
            }
        )

        # Restore the store, restart over the same database, and replay the
        # SAME idempotent request: the original execution identity comes
        # back and the business write is NOT repeated.
        _restore_evidence(runner)
        runner = runner.restart(kill_previous=True)
        pids.append(runner.process.pid)
        status, body, replay_id = _submit_run(runner, replay_arguments, idempotency_key=key)
        assert status == 202, body
        assert replay_id == run2, (replay_id, run2)
        assert body.get("replayed") is True, body
        assert body["status"] == "reconciliation_required", body
        assert writes == [1, 2] and stock == 103
        with _open_store(runner, step6_runner_database_url) as store:
            assert store.submission_for_key(key) == submission
            assert store.task_for_run(run_ref) == submission.task_ref
        observations.append(
            {
                "step": "restart-replay-original-identity",
                "http_status": status,
                "task": submission.task_ref.id,
                "run": replay_id,
                "replayed": body.get("replayed") is True,
                "business_writes": len(writes),
            }
        )

        # A new request after recovery is served normally.
        status, body, run3 = _submit_run(runner, _write_arguments(3))
        assert status == 202, body
        assert runner.wait_run(run3)[1]["status"] == "succeeded"
        assert writes == [1, 2, 3] and stock == 106
        with _open_store(runner, step6_runner_database_url) as store:
            assert store.list_run_actions(run_ref) == actions
        observations.append(
            {
                "step": "post-recovery-run-succeeded",
                "http_status": status,
                "business_writes": len(writes),
                "ledger_unchanged_after_replay": True,
            }
        )
        outcome = "passed"
    finally:
        _restore_evidence(runner)
        if runner.process.poll() is None:
            runner.stop()
        runner_harness.stop_business_api(server)
        _write_evidence(
            request,
            step6_runner_database_url,
            scenario="post-dispatch-reconciliation",
            pids=pids,
            fault_window=(
                "evidence directory replaced by a plain file inside commit_write after the second business write"
            ),
            observations=observations,
            outcome=outcome,
            tmp_path=tmp_path,
        )


def test_sc06_readonly_profile_records_conservative_behavior(
    tmp_path: Path, step6_runner_database_url, wheel_dist, request
) -> None:
    observations: list[dict[str, Any]] = []
    pids: list[int] = []
    outcome = "incomplete"
    runner, server, calls = runner_harness.start_runner(
        tmp_path,
        manifest_tools=READONLY_MANIFEST_TOOLS,
        durable=True,
        tool_allowlist=["query_inventory"],
        tool_schemas=READONLY_TOOL_SCHEMAS,
        durable_database_url=step6_runner_database_url,
        dist_dir=wheel_dist,
    )
    try:
        pids.append(runner.process.pid)
        # Baseline read succeeds.
        status, body, run_id = _submit_run(runner, READONLY_ARGUMENTS)
        assert status == 202, body
        assert runner.wait_run(run_id)[1]["status"] == "succeeded"
        assert len(calls) == 1, calls

        # Read-only profiles skip the admission gate (no protected effect
        # is registered), so the current conservative behavior is: the
        # durable submission registers, the submission's own evidence
        # write fails, and the HTTP request ends in an internal error —
        # no business call, no run executed. This is the registered
        # readonly-continuation policy gap, recorded as-is.
        _break_evidence(runner)
        key = "sc06-readonly-evidence-fault"
        status, body, _ = _submit_run(runner, READONLY_ARGUMENTS, idempotency_key=key)
        assert status == 500, (status, body)
        assert body["type"] == "urn:hecate:problem:internal-error", body
        assert len(calls) == 1, calls
        with _open_store(runner, step6_runner_database_url) as store:
            submission = store.submission_for_key(key)
            assert submission is not None
            task = store.get_task_state(submission.task_ref)
            assert task is not None and task.lifecycle_state is TaskLifecycleState.QUEUED
        observations.append(
            {
                "step": "readonly-internal-error",
                "http_status": status,
                "problem": body["type"],
                "idempotency_key": key,
                "business_calls": len(calls),
                "task_state": "queued",
            }
        )

        # Recovery: once the store is writable again, the queued
        # submission re-drives honestly at startup under its original
        # identity.
        _restore_evidence(runner)
        runner = runner.restart(kill_previous=True)
        pids.append(runner.process.pid)
        status, result = runner.wait_run(submission.run_ref.id)
        assert status == 200 and result["status"] == "succeeded", result
        completion_status = status
        status, replay, replay_id = _submit_run(runner, READONLY_ARGUMENTS, idempotency_key=key)
        assert status == 202 and replay_id == submission.run_ref.id, replay
        assert replay.get("replayed") is True, replay
        assert len(calls) == 2, calls
        with _open_store(runner, step6_runner_database_url) as store:
            assert store.submission_for_key(key) == submission
            task = store.get_task_state(submission.task_ref)
            assert task is not None and task.lifecycle_state is TaskLifecycleState.SUCCEEDED
        observations.append(
            {
                "step": "restart-completed-and-replayed",
                "wait_status": completion_status,
                "replay_status": status,
                "task": submission.task_ref.id,
                "run": replay_id,
                "replayed": replay.get("replayed") is True,
                "business_calls": len(calls),
            }
        )
        outcome = "passed"
    finally:
        _restore_evidence(runner)
        if runner.process.poll() is None:
            runner.stop()
        runner_harness.stop_business_api(server)
        _write_evidence(
            request,
            step6_runner_database_url,
            scenario="readonly-conservative",
            pids=pids,
            fault_window="evidence directory replaced by a plain file before a read-only submission",
            observations=observations,
            outcome=outcome,
            tmp_path=tmp_path,
        )
