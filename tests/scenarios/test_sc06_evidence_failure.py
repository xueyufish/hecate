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
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

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
def _open_store(runner: runner_harness.RunnerInstance) -> Iterator[SqlDurableStore]:
    store = SqlDurableStore(f"sqlite:///{(runner.workdir / 'host.db').as_posix()}")
    try:
        yield store
    finally:
        store.dispose()


def test_sc06_unwritable_evidence_refuses_protected_submission_before_dispatch(tmp_path: Path) -> None:
    runner, server, calls = runner_harness.start_runner(
        tmp_path,
        manifest_tools=MANIFEST_TOOLS,
        durable=True,
        tool_allowlist=["query_inventory", "submit_inventory_update"],
        tool_schemas=TOOL_SCHEMAS,
    )
    try:
        # Baseline: a protected run succeeds while the store is writable.
        status, body, run_id = _submit_run(runner, _write_arguments(1))
        assert status == 202, body
        assert runner.wait_run(run_id)[1]["status"] == "succeeded"

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
        with _open_store(runner) as store:
            assert store.submission_for_key(key) is None

        # The gate denial is queryable on health with its category.
        status, health = runner.request("GET", "/healthz", token=None)
        assert status == 200, health
        assert health["evidence_last_gate_failure"]["category"] == "unwritable", health

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
    finally:
        _restore_evidence(runner)
        if runner.process.poll() is None:
            runner.stop()
        runner_harness.stop_business_api(server)


def test_sc06_post_dispatch_evidence_failure_converges_and_replays_without_reexecution(tmp_path: Path) -> None:
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
    )
    try:
        # Baseline: normal service with one recorded business write.
        status, body, run1 = _submit_run(runner, _write_arguments(1))
        assert status == 202, body
        assert runner.wait_run(run1)[1]["status"] == "succeeded"
        assert writes == [1] and stock == 101

        # Arm the fault and submit a new request under a fixed idempotency
        # key: the business write commits, then result-evidence retention
        # fails.
        replay_arguments = _write_arguments(2)
        status, body, run2 = _submit_run(runner, replay_arguments, idempotency_key="sc06-post-dispatch")
        assert status == 202, body
        status, run2_body = runner.wait_run(run2)
        # Missing evidence is never presented as success: the run converges
        # on the provable facts — a committed business write with no
        # retained result — as reconciliation-required, with no success
        # result or artifact reference presented to the caller.
        assert run2_body["status"] == "reconciliation_required", run2_body
        assert run2_body["result_ref"] is None, run2_body
        assert "result" not in run2_body, run2_body
        assert fault_triggered and (runner.profile_dir / "evidence").is_file()
        assert writes == [1, 2] and stock == 103
        runner_log = (runner.workdir / "runner.log").read_text(encoding="utf-8", errors="replace")
        assert f"Runner execution {run2} could not retain evidence" in runner_log

        # The durable action ledger holds the claimed write with NO
        # outcome: recovery stays fail-closed (pending reconciliation),
        # never succeeded.
        run_ref = BackendRef(kind=RefKind.RUN, issuer_domain=ISSUER, id=run2)
        with _open_store(runner) as store:
            actions = store.list_run_actions(run_ref)
        write_actions = [action for action in actions if action["action_key"].endswith("submit_inventory_update")]
        assert len(write_actions) == 1, actions
        action = write_actions[0]
        assert action["state"] == "claimed", action
        assert action["pending_reconciliation"] is True, action
        assert action["result_payload"] is None, action

        # Restore the store, restart over the same database, and replay the
        # SAME idempotent request: the original execution identity comes
        # back and the business write is NOT repeated.
        _restore_evidence(runner)
        runner = runner.restart(kill_previous=True)
        status, body, replay_id = _submit_run(runner, replay_arguments, idempotency_key="sc06-post-dispatch")
        assert status == 202, body
        assert replay_id == run2, (replay_id, run2)
        assert body.get("replayed") is True, body
        assert body["status"] == "reconciliation_required", body
        assert writes == [1, 2] and stock == 103

        # A new request after recovery is served normally.
        status, body, run3 = _submit_run(runner, _write_arguments(3))
        assert status == 202, body
        assert runner.wait_run(run3)[1]["status"] == "succeeded"
        assert writes == [1, 2, 3] and stock == 106
        with _open_store(runner) as store:
            assert store.list_run_actions(run_ref) == actions
    finally:
        _restore_evidence(runner)
        if runner.process.poll() is None:
            runner.stop()
        runner_harness.stop_business_api(server)


def test_sc06_readonly_profile_records_conservative_behavior(tmp_path: Path) -> None:
    runner, server, calls = runner_harness.start_runner(
        tmp_path,
        manifest_tools=READONLY_MANIFEST_TOOLS,
        durable=True,
        tool_allowlist=["query_inventory"],
        tool_schemas=READONLY_TOOL_SCHEMAS,
    )
    try:
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
        with _open_store(runner) as store:
            submission = store.submission_for_key(key)
            assert submission is not None
            task = store.get_task_state(submission.task_ref)
            assert task is not None and task.lifecycle_state is TaskLifecycleState.QUEUED

        # Recovery: once the store is writable again, the queued
        # submission re-drives honestly at startup under its original
        # identity.
        _restore_evidence(runner)
        runner = runner.restart(kill_previous=True)
        status, result = runner.wait_run(submission.run_ref.id)
        assert status == 200 and result["status"] == "succeeded", result
        status, replay, replay_id = _submit_run(runner, READONLY_ARGUMENTS, idempotency_key=key)
        assert status == 202 and replay_id == submission.run_ref.id, replay
        assert replay.get("replayed") is True, replay
        assert len(calls) == 2, calls
    finally:
        _restore_evidence(runner)
        if runner.process.poll() is None:
            runner.stop()
        runner_harness.stop_business_api(server)
