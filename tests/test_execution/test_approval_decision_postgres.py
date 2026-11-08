"""Approval decisions against real PostgreSQL: processes, races, recovery.

The SQLite suite (``test_approval_decision.py``) pins the decision
contracts; this module proves them with COMPETING INDEPENDENT PROCESSES
on a real multi-connection database: two processes racing approve/reject
behind a ready-file barrier produce exactly one authoritative decision, a
process killed in the decision→resume window is healed by a substitute
recovery consumer, competing recovery consumers plus an API replay stay
idempotent, and a cancel/revocation/expiry after the decision commit
converges without re-executing or extending anything. Connection material
travels through the child environment. Model and business-tool responses
are scripted while the Controller and approval consumer run unchanged.
Without ``HECATE_STEP6_POSTGRES_URL`` the
module reports skips (not passes).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from hecate.contracts.execution.durable import CommandState, TaskLifecycleState
from hecate.execution.approval_decision import (
    ApprovalDecisionError,
    ApprovalDecisionService,
    resume_command_id_for,
)
from hecate.execution.task_control import task_ref_of
from tests.test_execution._agent_child_db import child_environment

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("HECATE_STEP6_POSTGRES_URL"),
        reason="HECATE_STEP6_POSTGRES_URL not configured; the approval decision PostgreSQL matrix reports skip",
    ),
    pytest.mark.timeout(420),
]

_REPO = Path(__file__).resolve().parents[2]
WORKER = "tests.test_execution._approval_decision_process"
WS = uuid.UUID("00000000-0000-0000-0000-000000000030")
ORG = uuid.UUID("00000000-0000-0000-0000-000000000031")
INITIATOR = uuid.UUID("00000000-0000-0000-0000-000000000032")
ADMIN = uuid.UUID("00000000-0000-0000-0000-000000000033")


@pytest.fixture(autouse=True)
async def _tenant(harness) -> None:
    """The drill's own tenant: users, agent and deployment on the shared
    per-test schema (the shared harness seeds a different workspace)."""
    from hecate.models.agent import AgentModel
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.agent_principal import AgentPrincipalModel
    from hecate.models.agent_version import AgentVersionModel
    from hecate.models.organization import OrganizationModel
    from hecate.models.user import UserModel
    from hecate.models.workspace import WorkspaceModel

    async with harness["session_factory"]() as db:
        db.add(OrganizationModel(id=ORG, name="approval-pg-org", slug=f"approval-pg-{ORG.hex[:12]}", owner_id=ADMIN))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="approval-pg-ws", slug=f"approval-pg-{WS.hex[:12]}"))
        db.add(UserModel(id=ADMIN, email="approval-admin@example.com", hashed_password=uuid.uuid4().hex))
        db.add(UserModel(id=INITIATOR, email="approval-initiator@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=WS, name="approval-decision-pg-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={"model": "stub", "tools": ["query_inventory"]},
            content_hash="b" * 64,
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id, agent_id=agent.id, workspace_id=WS, organization_id=ORG, owner_user_id=INITIATOR
            )
        )
        db.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=WS,
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
        harness["approval_agent_id"] = agent.id


@pytest.fixture(autouse=True)
def _suppress_inline_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SUBPROCESS workers own every dispatch; the test process must not
    inline-run submitted tasks (requeues stay durable facts)."""

    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())


def _worker_env(harness, tmp_path, **knobs: Any) -> dict[str, str]:
    env = child_environment(harness)
    env["HECATE_APPROVAL_WORKSPACE_ID"] = str(WS)
    env["HECATE_APPROVAL_ACTION_TRACE"] = str(tmp_path / "action-trace.jsonl")
    for key, value in knobs.items():
        if isinstance(value, bool):
            value = "1" if value else ""
        env[f"HECATE_APPROVAL_{key.upper()}"] = str(value)
    return env


# Child log files registered at spawn, keyed by Popen identity; handles
# are closed by the reaper.
_CHILD_LOGS: dict[int, tuple[Any, Path]] = {}


def _spawn(env: dict[str, str], *args: str) -> subprocess.Popen[str]:
    # The handle's lifetime is the child process's; a log file (not a pipe)
    # keeps every child's output readable even when the parent is blocked
    # polling, and survives a hard os._exit in the crash-window drill.
    log_path = Path(env["HECATE_APPROVAL_ACTION_TRACE"]).parent / f"child-{os.getpid()}-{uuid.uuid4().hex[:8]}.log"
    log_handle = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 — closed by the reaper
    env.setdefault("HECATE_APPROVAL_LOG_PATH", str(log_path))
    process = subprocess.Popen(
        [sys.executable, "-m", WORKER, *args],
        cwd=str(_REPO),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _CHILD_LOGS[id(process)] = (log_handle, log_path)
    return process


def _child_logs() -> str:
    """Tail every child log of the CURRENT test (cleared by the reaper)."""
    chunks = []
    for _handle, path in _CHILD_LOGS.values():
        if os.path.exists(path):
            chunks.append(f"--- {path} ---\n" + Path(path).read_text(encoding="utf-8", errors="replace")[-4000:])
    return "\n".join(chunks)


def _child_log_path(proc: subprocess.Popen[str]) -> Path | None:
    entry = _CHILD_LOGS.get(id(proc))
    return entry[1] if entry else None


@pytest.fixture(autouse=True)
async def _reap_processes(harness, monkeypatch):
    """Reap owned subprocesses before the fixture drops their schema."""
    from tests.test_execution.test_agent_child_postgres_matrix import _kill_worker

    processes: list[subprocess.Popen[str]] = []
    original_spawn = _spawn

    def tracked_spawn(*args, **kwargs):
        process = original_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(sys.modules[__name__], "_spawn", tracked_spawn)
    yield
    for process in processes:
        if process.poll() is None:
            await asyncio.to_thread(_kill_worker, process)
        await asyncio.to_thread(process.communicate, timeout=10)
    for handle, _path in _CHILD_LOGS.values():
        handle.close()
    _CHILD_LOGS.clear()


def _as_text(value: str | bytes) -> str:
    """Popen.communicate's stubs are text/bytes unions even with text=True."""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


async def _finish(proc: subprocess.Popen[str], timeout: float = 240.0) -> tuple[int, dict[str, Any] | None, str]:
    from tests.test_execution.test_agent_child_postgres_matrix import _kill_worker

    try:
        await asyncio.to_thread(proc.communicate, timeout)
    except subprocess.TimeoutExpired:
        await asyncio.to_thread(_kill_worker, proc)
        await asyncio.to_thread(proc.communicate, timeout=10)
        logs = _child_logs()
        raise AssertionError(f"worker {proc.args!s} timed out; log tail:\n{logs}") from None
    text = ""
    log_path = _child_log_path(proc)
    if log_path and os.path.exists(log_path):
        text = Path(log_path).read_text(encoding="utf-8", errors="replace").strip()
    report = json.loads(text.splitlines()[-1]) if text and proc.returncode == 0 else None
    return proc.returncode, report, text[-4000:] if proc.returncode else ""


async def _await_death(proc: subprocess.Popen[str], marker: Path, timeout: float = 300.0) -> None:
    """Wait for the crash-window death: the marker file plus exit code 9.

    Any other outcome — an early exit or a hang — carries the child's own
    log tail in the failure so the crash point is diagnosable.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not marker.exists():
        code = proc.poll()
        if code is not None:
            logs = _child_logs()
            raise AssertionError(f"the decider exited early (code={code}) without the death marker; logs:{logs}")
        if loop.time() > deadline:
            logs = _child_logs()
            raise AssertionError(f"the decider never died; logs:{logs}")
        await asyncio.sleep(0.1)
    code = await asyncio.to_thread(proc.wait, 30)
    assert code == 9, f"the decider exited {code}; logs:{_child_logs()}"


async def _run(env: dict[str, str], *args: str, timeout: float = 240.0) -> tuple[int, dict[str, Any] | None, str]:
    return await _finish(_spawn(env, *args), timeout)


async def _await_ready(processes: list[subprocess.Popen[str]], ready_files: list[Path]) -> None:
    deadline = asyncio.get_running_loop().time() + 180
    while not all(path.exists() for path in ready_files):
        assert all(process.poll() is None for process in processes), _child_logs()
        assert asyncio.get_running_loop().time() < deadline, f"a process never reached its barrier; {_child_logs()}"
        await asyncio.sleep(0.05)


def _service(harness, db, *, worker: Any | None = None) -> Any:
    from hecate.execution.task_control import TaskControlService

    return TaskControlService(
        db,
        store=harness["store"],
        recorder=harness["store"],
        backend="postgres",
        ledger_source="core",
        session_factory=harness["session_factory"],
        worker=worker,
    )


def _decision_service(harness, db) -> ApprovalDecisionService:
    return ApprovalDecisionService(
        db,
        store=harness["store"],
        recorder=harness["store"],
        backend="postgres",
        ledger_source="core",
        session_factory=harness["session_factory"],
    )


def _trace_events(path: str | Path) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


async def _poll_state(harness, t_ref, *states: str, timeout: float = 300.0) -> Any:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    record = None
    while loop.time() < deadline:
        record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
        if record is not None and record.lifecycle_state.value in states:
            return record
        if record is not None and record.lifecycle_state.value in {
            "succeeded",
            "failed",
            "cancelled",
            "reconciliation_required",
        }:
            raise AssertionError(
                f"expected {states}, got {record.lifecycle_state.value}; "
                f"reason={record.extra.get('reconciliation_reason')}; {_child_logs()}"
            )
        await asyncio.sleep(0.3)
    raise AssertionError(f"task never reached {states}; last={record.lifecycle_state.value if record else None}")


async def _park_via_worker(harness, tmp_path, *, no_resume_recovery: bool = False) -> dict[str, Any]:
    """Real park through a worker subprocess: submit → signal → parked.

    ``no_resume_recovery`` runs the worker with the platform composite
    minus the approval-resume consumer, so a crash-window drill can prove
    the SUBSTITUTE recovery consumer heals the gap instead of the worker.
    """

    worker = _spawn(_worker_env(harness, tmp_path, mode="worker", no_resume_recovery=no_resume_recovery))
    async with harness["session_factory"]() as db:
        result = await _service(harness, db).submit(
            workspace_id=WS,
            user_id=INITIATOR,
            goal="read the governed stock",
            agent_id=harness["approval_agent_id"],
            input={
                "messages": [{"role": "user", "content": "read the governed stock"}],
                "controller_loop": True,
                "controller": {"max_turns": 3},
                "approval_wait": True,
            },
        )
    task_uuid = uuid.UUID(result.task_ref.id)
    t_ref = task_ref_of(task_uuid)
    try:
        record = await _poll_state(harness, t_ref, "waiting_approval", "failed")
    except AssertionError:
        logs = _child_logs()
        raise AssertionError(f"the worker subprocess never parked the task; child logs:\n{logs}") from None
    assert record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL, record.extra
    return {
        "task_id": task_uuid,
        "t_ref": t_ref,
        "worker": worker,
        "state": record,
        "wait": (record.extra or {})["wait"],
        "trace": str(tmp_path / "action-trace.jsonl"),
    }


async def _decide_in_process(harness, parked, *, operation_id: str, decision: str, reason: str) -> dict[str, Any]:
    state = parked["state"]
    async with harness["session_factory"]() as db:
        result = await _decision_service(harness, db).submit_decision(
            WS,
            parked["task_id"],
            operation_id=operation_id,
            approval_ref=parked["wait"]["contract_ref"]["approval_ref"],
            decision=decision,
            reason=reason,
            wait_token=parked["wait"]["wait_token"],
            expected_revision=state.revision,
            decided_by=str(ADMIN),
        )
        await db.commit()
    return result


def _decide_env(parked, operation_id: str, decision: str, reason: str, **knobs: Any) -> dict[str, str]:
    state = parked["state"]
    # A COPY per racer: two processes sharing one environment dict would
    # also share one ready file and one operation identity.
    env = dict(parked["_env"])
    env.update(
        {
            "HECATE_APPROVAL_MODE": "decide",
            "HECATE_APPROVAL_TASK_ID": str(parked["task_id"]),
            "HECATE_APPROVAL_REF": parked["wait"]["contract_ref"]["approval_ref"],
            "HECATE_APPROVAL_TOKEN": parked["wait"]["wait_token"],
            "HECATE_APPROVAL_REVISION": str(state.revision),
            "HECATE_APPROVAL_OPERATION_ID": operation_id,
            "HECATE_APPROVAL_DECISION": decision,
            "HECATE_APPROVAL_REASON": reason,
            "HECATE_APPROVAL_DECIDED_BY": str(ADMIN),
        }
    )
    for key, value in knobs.items():
        env[f"HECATE_APPROVAL_{key.upper()}"] = str(value)
    return env


# --- races ------------------------------------------------------------------


async def test_two_processes_race_to_one_authoritative_decision(harness, tmp_path) -> None:
    """approve vs reject across two processes: one winner, one explicit
    conflict, one consistent operation record, replays bind to it."""
    parked = await _park_via_worker(harness, tmp_path)
    parked["_env"] = _worker_env(harness, tmp_path, mode="decide")
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    envs = [
        _decide_env(parked, "op-race-approve", "approve", "approved across processes"),
        _decide_env(parked, "op-race-reject", "reject", "rejected across processes"),
    ]
    ready_files = [barrier / f"ready-{index}" for index in range(2)]
    for env, ready in zip(envs, ready_files, strict=True):
        env["HECATE_APPROVAL_READY_FILE"] = str(ready)
        env["HECATE_APPROVAL_GO_FILE"] = str(barrier / "go")
    procs = [_spawn(env) for env in envs]
    # Both requests validate the original wait before either can decide.
    await _await_ready(procs, ready_files)
    (barrier / "go").touch()
    outcomes = []
    for proc in procs:
        code, report, err = await _finish(proc)
        assert code == 0 and report is not None, err[-2000:]
        outcomes.append(report)
    winners = [report for report in outcomes if report["ok"]]
    losers = [report for report in outcomes if not report["ok"]]
    assert len(winners) == 1 and len(losers) == 1, outcomes
    assert losers[0]["code"] == "ALREADY_DECIDED", losers
    winner_decision = winners[0]["result"]["decision"]["decision"]

    approval_ref = parked["wait"]["contract_ref"]["approval_ref"]
    async with harness["session_factory"]() as db:
        from hecate.enterprise.governance_ledgers import SqlApprovalLedger
        from hecate.models.approval import ApprovalDecisionModel

        binding = await SqlApprovalLedger(db).get(approval_ref)
        assert binding.judgment.value == ("approved" if winner_decision == "approve" else "rejected")
        rows = list(await db.scalars(select(ApprovalDecisionModel).where(ApprovalDecisionModel.deleted.is_(False))))
        assert [row.operation_id for row in rows] == [winners[0]["result"]["decision"]["operation_id"]]

    # The winner's identity owns its operation: same body replays, a
    # changed body conflicts.
    replay = await _decide_in_process(
        harness,
        parked,
        operation_id=winners[0]["result"]["decision"]["operation_id"],
        decision=winner_decision,
        reason="approved across processes" if winner_decision == "approve" else "rejected across processes",
    )
    assert replay["decision"]["replay"] is True
    with pytest.raises(ApprovalDecisionError) as excinfo:
        await _decide_in_process(
            harness,
            parked,
            operation_id=winners[0]["result"]["decision"]["operation_id"],
            decision="reject" if winner_decision == "approve" else "approve",
            reason="changed my mind",
        )
    assert excinfo.value.code == "OPERATION_CONFLICT"
    expected_state = "succeeded" if winner_decision == "approve" else "failed"
    await _poll_state(harness, parked["t_ref"], expected_state)
    calls = [event for event in _trace_events(parked["trace"]) if event["event"] == "business_call"]
    assert len(calls) == (1 if winner_decision == "approve" else 0)
    async with harness["session_factory"]() as db:
        binding = await SqlApprovalLedger(db).get(approval_ref)
        invocation = parked["wait"]["contract_ref"]["invocation_id"] if winner_decision == "approve" else None
        assert binding.consumed_by_invocation_id == invocation


# --- the decision→resume crash window ---------------------------------------


async def test_process_death_between_decision_and_resume_is_healed(harness, tmp_path) -> None:
    """The decision committed, the process died before the resume: a new
    connection confirms the committed facts, a substitute recovery
    consumer completes the wake, the action runs exactly once."""
    parked = await _park_via_worker(harness, tmp_path, no_resume_recovery=True)
    parked["_env"] = _worker_env(harness, tmp_path, mode="decide")
    death_marker = tmp_path / "death-marker"
    env = _decide_env(
        parked,
        "op-crash-1",
        "approve",
        "approved before the crash",
        death_marker=death_marker,
    )
    proc = _spawn(env)
    await _await_death(proc, death_marker)

    approval_ref = parked["wait"]["contract_ref"]["approval_ref"]
    # A NEW connection reads the committed phase-one facts.
    async with harness["session_factory"]() as db:
        from hecate.enterprise.governance_ledgers import SqlApprovalLedger
        from hecate.models.approval import ApprovalDecisionModel

        row = await db.scalar(select(ApprovalDecisionModel).where(ApprovalDecisionModel.operation_id == "op-crash-1"))
        assert row is not None and row.decision == "approve" and row.resume_command_id is None
        binding = await SqlApprovalLedger(db).get(approval_ref)
        assert binding.judgment.value == "approved"
        original_expiry = binding.expires_at
    assert not [event for event in _trace_events(parked["trace"]) if event["event"] == "business_call"]
    receipt_probe = await asyncio.to_thread(harness["store"].get, resume_command_id_for("op-crash-1"))
    assert receipt_probe is None
    from tests.test_execution.test_agent_child_postgres_matrix import _kill_worker

    original_run = await asyncio.to_thread(harness["store"].run_for_task, parked["t_ref"])
    await asyncio.to_thread(_kill_worker, parked["worker"])
    await asyncio.to_thread(parked["worker"].communicate, timeout=30)
    _spawn(_worker_env(harness, tmp_path, mode="worker", no_resume_recovery=True))

    _recover_code, recover_report, recover_err = await _run(
        _worker_env(harness, tmp_path, mode="recover"), timeout=120.0
    )
    assert _recover_code == 0 and recover_report is not None, recover_err[-2000:]
    assert recover_report["processed"] >= 1, recover_report

    record = await _poll_state(harness, parked["t_ref"], "succeeded")
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    assert record.extra["wait"]["wait_expires_at"] == parked["wait"]["wait_expires_at"]
    events = _trace_events(parked["trace"])
    assert len([event for event in events if event["event"] == "business_call"]) == 1, events
    assert len([event for event in events if event["event"] == "model_call"]) == 2, events
    assert await asyncio.to_thread(harness["store"].run_for_task, parked["t_ref"]) == original_run
    async with harness["session_factory"]() as db:
        binding = await SqlApprovalLedger(db).get(approval_ref)
        assert binding.consumed_by_invocation_id == parked["wait"]["contract_ref"]["invocation_id"]
        assert binding.expires_at == original_expiry

    async with harness["session_factory"]() as db:
        view = await _decision_service(harness, db).get_approval_view(WS, parked["task_id"])
    assert view["decision"]["resume_command_id"] == resume_command_id_for("op-crash-1")
    assert view["decision"]["resume_receipt"]["state"] == "applied"
    # The healed linkage makes a second recovery pass a no-op.
    _code, again, _err = await _run(_worker_env(harness, tmp_path, mode="recover"), timeout=120.0)
    assert again is not None and again["processed"] == 0, again


async def test_recovery_consumers_and_api_retry_race_idempotently(harness, tmp_path) -> None:
    """Two recovery consumers and an API retry of the same operation race:
    the deterministic resume identity, the single-use token and the
    recorder converge to one wake and one execution."""
    parked = await _park_via_worker(harness, tmp_path, no_resume_recovery=True)
    parked["_env"] = _worker_env(harness, tmp_path, mode="decide")
    death_marker = tmp_path / "death-marker"
    proc = _spawn(_decide_env(parked, "op-race-recovery", "approve", "approved once", death_marker=death_marker))
    await _await_death(proc, death_marker)

    barrier = tmp_path / "recovery-barrier"
    barrier.mkdir()
    ready_files = [barrier / "ready-a", barrier / "ready-b"]
    env_a = _worker_env(harness, tmp_path, mode="recover", ready_file=ready_files[0], go_file=barrier / "go")
    env_b = _worker_env(harness, tmp_path, mode="recover", ready_file=ready_files[1], go_file=barrier / "go")
    proc_a, proc_b = _spawn(env_a), _spawn(env_b)
    await _await_ready([proc_a, proc_b], ready_files)

    async def _api_retry() -> dict[str, Any]:
        # The HTTP path replaying the SAME operation while the recovery
        # consumers run: the recorded decision replays and the resume
        # issuance is the same deterministic command.
        (barrier / "go").touch()
        return await _decide_in_process(
            harness, parked, operation_id="op-race-recovery", decision="approve", reason="approved once"
        )

    results = await asyncio.gather(
        _finish(proc_a, 120.0),
        _finish(proc_b, 120.0),
        _api_retry(),
    )
    for code, report, err in results[:2]:
        assert code == 0, err[-2000:]
        assert report is not None and report["processed"] >= 1
    retry = results[2]
    assert retry["decision"]["decision"] == "approve"

    record = await _poll_state(harness, parked["t_ref"], "succeeded")
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    events = _trace_events(parked["trace"])
    assert len([event for event in events if event["event"] == "business_call"]) == 1, events
    assert len([event for event in events if event["event"] == "model_call"]) == 2, events

    command_id = resume_command_id_for("op-race-recovery")
    receipts = await asyncio.to_thread(harness["store"].get, command_id)
    assert receipts is not None and receipts.state is CommandState.APPLIED
    async with harness["session_factory"]() as db:
        from hecate.models.approval import ApprovalDecisionModel

        row = await db.scalar(
            select(ApprovalDecisionModel).where(ApprovalDecisionModel.operation_id == "op-race-recovery")
        )
        assert row.resume_command_id == command_id
        commands = await asyncio.to_thread(harness["store"].list_task_commands, str(WS), parked["t_ref"], 100, 0)
    resume_commands = [record for record in commands[0] if record.kind.value == "resume"]
    assert len(resume_commands) == 1, resume_commands
    _code, again, _err = await _run(_worker_env(harness, tmp_path, mode="recover"), timeout=120.0)
    assert again is not None and again["processed"] == 0, again


# --- late authority changes -------------------------------------------------


async def test_cancel_after_decision_rejects_the_resume_and_preserves_facts(harness, tmp_path) -> None:
    """A cancel recorded after the decision commit still stops the wake;
    the decision and its audit trail survive verbatim."""
    parked = await _park_via_worker(harness, tmp_path, no_resume_recovery=True)
    from hecate.contracts.execution.durable import ControlCommandKind

    original_issue = ApprovalDecisionService._issue_resume

    async def _cancel_first(self, row, task_id, workspace_id):
        async with harness["session_factory"]() as cancel_db:
            await _service(harness, cancel_db).issue_command(
                workspace_id=WS, task_id=parked["task_id"], kind=ControlCommandKind.CANCEL, issuer="tester"
            )
        return await original_issue(self, row, task_id, workspace_id)

    ApprovalDecisionService._issue_resume = _cancel_first
    try:
        result = await _decide_in_process(
            harness, parked, operation_id="op-cancel-1", decision="approve", reason="approved while live"
        )
    finally:
        ApprovalDecisionService._issue_resume = original_issue
    assert result["decision"]["decision"] == "approve"
    assert result["resume"]["state"] == "rejected"
    cancelled = await _poll_state(harness, parked["t_ref"], "cancelled")
    assert cancelled.lifecycle_state is TaskLifecycleState.CANCELLED
    assert not [event for event in _trace_events(parked["trace"]) if event["event"] == "business_call"]

    approval_ref = parked["wait"]["contract_ref"]["approval_ref"]
    async with harness["session_factory"]() as db:
        from hecate.enterprise.governance_ledgers import SqlApprovalLedger
        from hecate.models.approval import ApprovalDecisionModel

        binding = await SqlApprovalLedger(db).get(approval_ref)
        assert binding.judgment.value == "approved" and binding.decided_by == str(ADMIN)
        row = await db.scalar(select(ApprovalDecisionModel).where(ApprovalDecisionModel.operation_id == "op-cancel-1"))
        assert row is not None and row.decision == "approve" and row.resume_command_id
    # The rejected resume is a recorded terminal: no pending recovery.
    _code, report, _err = await _run(_worker_env(harness, tmp_path, mode="recover"), timeout=120.0)
    assert report is not None and report["processed"] == 0, report


async def test_revoked_authority_rejects_the_decision_without_a_record(harness, tmp_path) -> None:
    """A revoked binding cannot be decided; nothing is recorded."""
    parked = await _park_via_worker(harness, tmp_path)
    approval_ref = parked["wait"]["contract_ref"]["approval_ref"]
    async with harness["session_factory"]() as db:
        from hecate.enterprise.governance_ledgers import SqlApprovalLedger

        await SqlApprovalLedger(db).revoke(approval_ref)
        await db.commit()
    with pytest.raises(ApprovalDecisionError) as excinfo:
        await _decide_in_process(
            harness, parked, operation_id="op-revoked-1", decision="approve", reason="late approval"
        )
    assert excinfo.value.code == "BINDING_INVALID"
    async with harness["session_factory"]() as db:
        from hecate.models.approval import ApprovalDecisionModel

        assert (
            await db.scalar(select(ApprovalDecisionModel).where(ApprovalDecisionModel.operation_id == "op-revoked-1"))
        ) is None
        record = await asyncio.to_thread(harness["store"].get_task_state, parked["t_ref"])
    assert record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL


async def test_expired_wait_rejects_the_decision_and_converges_without_action(harness, tmp_path) -> None:
    """An expired deadline refuses the decision; the recovery sweep then
    converges the wait without executing anything."""
    from hecate_durable.storage.models import TaskStateRow
    from sqlalchemy import update

    from hecate.models.approval import ApprovalAuthorityModel
    from tests.test_execution.test_agent_child_postgres_matrix import _kill_worker

    parked = await _park_via_worker(harness, tmp_path)
    # Stop the worker so the expirer cannot race the decision attempt.
    await asyncio.to_thread(_kill_worker, parked["worker"])
    await asyncio.to_thread(parked["worker"].communicate, timeout=30)

    approval_ref = parked["wait"]["contract_ref"]["approval_ref"]
    past = datetime.now(UTC) - timedelta(hours=1)
    with harness["store"].session_factory.begin() as raw_db:
        row = raw_db.get(TaskStateRow, (parked["t_ref"].issuer_domain, parked["t_ref"].id))
        row.extra = {**row.extra, "wait": {**row.extra["wait"], "wait_expires_at": past.isoformat()}}
    async with harness["session_factory"]() as db:
        await db.execute(
            update(ApprovalAuthorityModel)
            .where(ApprovalAuthorityModel.approval_ref == approval_ref)
            .values(expires_at=past)
        )
        await db.commit()

    with pytest.raises(ApprovalDecisionError) as excinfo:
        await _decide_in_process(harness, parked, operation_id="op-expired-1", decision="approve", reason="too late")
    assert excinfo.value.code == "WAIT_EXPIRED"
    async with harness["session_factory"]() as db:
        from hecate.models.approval import ApprovalDecisionModel

        assert (
            await db.scalar(select(ApprovalDecisionModel).where(ApprovalDecisionModel.operation_id == "op-expired-1"))
        ) is None

    # A fresh worker (with the wait reconciler) converges the expired wait
    # without a second execution boundary.
    _spawn(_worker_env(harness, tmp_path, mode="worker"))
    record = await _poll_state(harness, parked["t_ref"], "failed", "cancelled")
    assert record.lifecycle_state is TaskLifecycleState.FAILED, record.extra
    events = _trace_events(parked["trace"])
    assert not [event for event in events if event["event"] == "business_call"], events


# --- HTTP gates on the PostgreSQL tier ---------------------------------------


@pytest.mark.parametrize("disposition", ("revoked", "expired"))
async def test_committed_decision_rechecks_authority_before_action(harness, tmp_path, disposition) -> None:
    """A committed approval and resume cannot override later authority changes."""
    from sqlalchemy import update

    from hecate.enterprise.governance_ledgers import SqlApprovalLedger
    from hecate.models.approval import ApprovalAuthorityModel, ApprovalDecisionModel
    from tests.test_execution.test_agent_child_postgres_matrix import _kill_worker

    parked = await _park_via_worker(harness, tmp_path, no_resume_recovery=True)
    await asyncio.to_thread(_kill_worker, parked["worker"])
    await asyncio.to_thread(parked["worker"].communicate, timeout=30)
    result = await _decide_in_process(
        harness, parked, operation_id="op-before-authority-change", decision="approve", reason="approved before stop"
    )
    assert result["resume"]["state"] == "applied"
    approval_ref = parked["wait"]["contract_ref"]["approval_ref"]
    async with harness["session_factory"]() as db:
        if disposition == "revoked":
            await SqlApprovalLedger(db).revoke(approval_ref)
        else:
            await db.execute(
                update(ApprovalAuthorityModel)
                .where(ApprovalAuthorityModel.approval_ref == approval_ref)
                .values(expires_at=datetime.now(UTC) - timedelta(hours=1))
            )
        await db.commit()
    _spawn(_worker_env(harness, tmp_path, mode="worker"))
    terminal = await _poll_state(harness, parked["t_ref"], "failed", "succeeded")
    assert terminal.lifecycle_state is TaskLifecycleState.FAILED, terminal.extra
    events = _trace_events(parked["trace"])
    assert not [event for event in events if event["event"] == "business_call"]
    assert len([event for event in events if event["event"] == "model_call"]) == 1, events
    async with harness["session_factory"]() as db:
        binding = await SqlApprovalLedger(db).get(approval_ref)
        assert binding.judgment.value == "approved"
        assert binding.consumed_by_invocation_id is None
        row = await db.scalar(
            select(ApprovalDecisionModel).where(ApprovalDecisionModel.operation_id == "op-before-authority-change")
        )
        assert row is not None and row.decision == "approve"
        assert row.resume_command_id == resume_command_id_for("op-before-authority-change")


async def test_decision_api_gates_on_postgres(harness, tmp_path, monkeypatch) -> None:
    """ADMIN decides over HTTP on the PostgreSQL tier; editors, viewers,
    the initiator, foreign workspaces and system-scope keys are refused;
    the request body can never supply the decider identity."""
    from fastapi import FastAPI
    from hecate_durable.worker import DurableWorker
    from httpx import ASGITransport, AsyncClient

    from hecate.channel.api.tasks import get_task_control_service, router
    from hecate.core.auth_context import AuthContext
    from hecate.core.deps_workspace import get_auth_context
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher
    from tests.test_execution.test_approval_decision import _ApprovalParkStub

    stub = _ApprovalParkStub()
    monkeypatch.setattr(PlatformTaskDispatcher, "_execute", stub._execute)
    dispatcher = PlatformTaskDispatcher(harness["store"], harness["session_factory"])
    worker = DurableWorker(harness["store"], dispatcher, leases=harness["store"].leases)
    async with harness["session_factory"]() as db:
        result = await _service(harness, db, worker=worker).submit(
            workspace_id=WS,
            user_id=INITIATOR,
            goal="create the ticket",
            agent_id=harness["approval_agent_id"],
            input={"messages": [{"role": "user", "content": "create the ticket"}], "approval_wait": True},
        )
    task_uuid = uuid.UUID(result.task_ref.id)
    t_ref = task_ref_of(task_uuid)
    # Drive the in-process worker to the park (control-plane dispatches are
    # suppressed; the object-bound worker only marks requeues).
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 120
    while True:
        record = await asyncio.to_thread(harness["store"].get_task_state, t_ref)
        if record is not None and record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL:
            break
        if record is None or record.lifecycle_state is TaskLifecycleState.QUEUED:
            await worker.dispatch_once(t_ref)
        assert loop.time() < deadline, f"task never parked; last={record.lifecycle_state.value if record else None}"
        await asyncio.sleep(0.1)
    state = record
    wait = (record.extra or {})["wait"]
    approval_ref = wait["contract_ref"]["approval_ref"]

    app = FastAPI()
    app.include_router(router, prefix="/api")

    from hecate.models.workspace_member import WorkspaceRole

    def _ctx(role) -> AuthContext:
        return AuthContext(user_id=ADMIN, org_id=ORG, workspace_id=WS, role=role, auth_method="jwt", api_key_scope=None)

    async def _override_service():
        async with harness["session_factory"]() as db:
            yield _service(harness, db, worker=worker)

    app.dependency_overrides[get_task_control_service] = _override_service
    app.dependency_overrides[get_auth_context] = lambda: _ctx(WorkspaceRole.ADMIN)
    body = {
        "operation_id": "op-http-pg-1",
        "approval_ref": approval_ref,
        "decision": "approve",
        "reason": "http decision on postgres",
        "wait_token": wait["wait_token"],
        "expected_revision": state.revision,
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for role in (WorkspaceRole.EDITOR, WorkspaceRole.VIEWER, None):
            app.dependency_overrides[get_auth_context] = lambda role=role: _ctx(role)
            assert (await client.post(f"/api/tasks/{task_uuid}/approval", json=body)).status_code == 403
        from dataclasses import replace

        app.dependency_overrides[get_auth_context] = lambda: replace(_ctx(WorkspaceRole.ADMIN), user_id=INITIATOR)
        assert (await client.post(f"/api/tasks/{task_uuid}/approval", json=body)).status_code == 403
        app.dependency_overrides[get_auth_context] = lambda: replace(
            _ctx(WorkspaceRole.ADMIN), workspace_id=uuid.uuid4()
        )
        assert (await client.post(f"/api/tasks/{task_uuid}/approval", json=body)).status_code == 404
        app.dependency_overrides[get_auth_context] = lambda: replace(_ctx(WorkspaceRole.ADMIN), api_key_scope="system")
        assert (await client.post(f"/api/tasks/{task_uuid}/approval", json=body)).status_code == 403
        app.dependency_overrides[get_auth_context] = lambda: _ctx(WorkspaceRole.ADMIN)
        decided = await client.post(f"/api/tasks/{task_uuid}/approval", json={**body, "decided_by": str(INITIATOR)})
        assert decided.status_code == 200, decided.text
        facts = decided.json()
        assert facts["decision"]["decided_by"] == str(ADMIN)
        assert facts["resume"]["state"] == "applied"
        replay = await client.post(f"/api/tasks/{task_uuid}/approval", json=body)
        assert replay.status_code == 200 and replay.json()["decision"]["replay"] is True
        conflict = await client.post(f"/api/tasks/{task_uuid}/approval", json={**body, "decision": "reject"})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["error"]["code"] == "OPERATION_CONFLICT"
