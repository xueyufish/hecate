"""Workflow controller steps against real PostgreSQL: cross-process recovery
and race matrix.

SQLite verifies the contracts; this module proves the same slice with
competing INDEPENDENT PROCESSES on a real multi-connection database:
worker death between the step's terminal commit and its verified callback,
two processes racing that callback behind a shared ready-file barrier, and
a cancel request recorded on a step that another process is still running.
Connection material travels through the child environment; provider
responses and the web_search effect are doubles. Without
``HECATE_STEP6_POSTGRES_URL`` the module reports skips (not passes).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

from hecate.contracts.execution.durable import CommandState, ControlCommandKind
from hecate.execution.task_control import task_ref_of
from tests.test_execution._agent_child_db import child_environment
from tests.test_execution.conftest import HARNESS_WS as WS
from tests.test_execution.test_workflow_child_adapter import _children_of

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("HECATE_STEP6_POSTGRES_URL"),
        reason="HECATE_STEP6_POSTGRES_URL not configured; the workflow controller PostgreSQL matrix reports skip",
    ),
    pytest.mark.timeout(420),
]

_REPO = Path(__file__).resolve().parents[2]
WORKER = "tests.test_execution._workflow_controller_process"


@pytest.fixture(autouse=True)
def _suppress_inline_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The SUBPROCESS workers own every dispatch; the test process must not
    inline-run submitted tasks (the harness suppresses spawns explicitly)."""

    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", lambda coro: coro.close())


def _worker_env(harness, tmp_path, **knobs: Any) -> dict[str, str]:
    env = child_environment(harness)
    env["HECATE_WF_CONTROLLER_WORKSPACE_ID"] = str(WS)
    env["HECATE_WF_CONTROLLER_EFFECT_FILE"] = str(tmp_path / "effects.jsonl")
    env["HECATE_WF_CONTROLLER_MODEL_TRACE_FILE"] = str(tmp_path / "model_trace.jsonl")
    env["HECATE_WF_CONTROLLER_CHILD_TOOL"] = "web_search"
    for key, value in knobs.items():
        env[f"HECATE_WF_CONTROLLER_{key.upper()}"] = str(value)
    return env


def _spawn(env: dict[str, str], *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", WORKER, *args],
        cwd=str(_REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.fixture(autouse=True)
async def _reap_workers(harness, monkeypatch):
    """Reap owned subprocesses before the fixture drops their schema."""
    from tests.test_execution.test_agent_child_postgres_matrix import _kill_worker

    spawn = _spawn
    processes = []

    def tracked_spawn(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(sys.modules[__name__], "_spawn", tracked_spawn)
    yield
    for process in processes:
        if process.poll() is None:
            await asyncio.to_thread(_kill_worker, process)
        await asyncio.to_thread(process.communicate, timeout=10)


async def _finish(proc: subprocess.Popen[str], timeout: float = 240.0) -> tuple[int, dict[str, Any] | None, str]:
    try:
        out, err = await asyncio.to_thread(proc.communicate, timeout)
    except subprocess.TimeoutExpired:
        from tests.test_execution.test_agent_child_postgres_matrix import _kill_worker

        await asyncio.to_thread(_kill_worker, proc)
        out, err = await asyncio.to_thread(proc.communicate, timeout=10)
        raise AssertionError(f"worker {proc.args} timed out; stderr tail:\n{(err or '')[-2000:]}") from None
    text = (out or "").strip()
    report = json.loads(text.splitlines()[-1]) if text else None
    return proc.returncode, report, err or ""


async def _run(env: dict[str, str], *args: str, timeout: float = 240.0) -> tuple[int, dict[str, Any] | None, str]:
    return await _finish(_spawn(env, *args), timeout)


def _lines(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip() and line.endswith("\n")]


def _state(harness, ref) -> Any:
    return harness["store"].get_task_state(ref)


async def _agent_with_tools(harness, name: str) -> Any:
    from hecate.models.agent import AgentModel
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.agent_principal import AgentPrincipalModel
    from hecate.models.agent_version import AgentVersionModel
    from tests.test_execution.conftest import HARNESS_ORG as ORG
    from tests.test_execution.conftest import HARNESS_OWNER as OWNER

    async with harness["session_factory"]() as db:
        agent = AgentModel(workspace_id=WS, name=name)
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id,
            version=1,
            config_snapshot={"model": "stub", "tools": ["web_search"]},
            content_hash="c" * 64,
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id, agent_id=agent.id, workspace_id=WS, organization_id=ORG, owner_user_id=OWNER
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
        return agent.id


async def _submit_workflow(harness, agent_id, steps: list[dict], **workflow_extra: Any) -> Any:
    from hecate.execution.task_control import TaskControlService

    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["store"],
            recorder=harness["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        )
        return await service.submit(
            workspace_id=WS,
            user_id=None,
            goal="parent workflow",
            agent_id=agent_id,
            input={
                "messages": [{"role": "user", "content": "run parent workflow"}],
                "workflow": {"steps": steps, **workflow_extra},
            },
        )


async def _poll_state(harness, ref, *states: str, timeout: float = 120.0) -> Any:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    record = None
    while loop.time() < deadline:
        record = await asyncio.to_thread(_state, harness, ref)
        if record is not None and record.lifecycle_state.value in states:
            return record
        await asyncio.sleep(0.3)
    raise AssertionError(f"task never reached {states}; last={record.lifecycle_state.value if record else None}")


async def test_step_survives_worker_death_before_the_callback(harness, tmp_path) -> None:
    """A worker that dies between the step's terminal commit and the verified
    callback leaves convergent facts: a fresh process reissues the callback
    once and the parent advances on the SAME child."""

    from sqlalchemy import select

    from hecate.models.quota import QuotaReservationModel

    agent_id = await _agent_with_tools(harness, "wf-pg-recovery-agent")
    parent = await _submit_workflow(
        harness, agent_id, [{"goal": "research the stock", "mode": "controller", "budget_units": 50000}]
    )
    env = _worker_env(harness, tmp_path, child_tool="web_search")
    code, report, err = await _run(env, "dispatch-parent", parent.task_ref.id)
    assert code == 0 and report["dispatched"] is True, (report, err[-2000:])
    child_id = report["children"][0]["id"]

    code, report, err = await _run(
        {**env, "HECATE_WF_CONTROLLER_FAULT": "after_step_terminal"}, "dispatch-child", parent.task_ref.id, child_id
    )
    assert code == 73, (report, err)
    child_state = await asyncio.to_thread(_state, harness, task_ref_of(child_id))
    assert child_state.lifecycle_state.value == "succeeded", child_state.extra
    parent_state = await asyncio.to_thread(_state, harness, parent.task_ref)
    assert parent_state.lifecycle_state.value == "waiting_input"

    code, report, err = await _run(env, "converge-callback", parent.task_ref.id, child_id)
    assert code == 0, (report, err)
    assert report["receipt_state"] == "applied", (report, err[-3000:])
    code, report, err = await _run({**env, "HECATE_WF_CONTROLLER_MAX_SECONDS": "60"}, "drive", parent.task_ref.id)
    assert code == 0, (report, err)
    assert report["parent_state"] == "succeeded", (report, err[-3000:])

    children = await asyncio.to_thread(_children_of, harness, parent.task_ref)
    assert len(children) == 1, "recovery must not duplicate the step child"
    final = await asyncio.to_thread(_state, harness, parent.task_ref)
    callbacks = (final.extra or {}).get("_workflow_callbacks") or {}
    assert sorted(callbacks) == ["0"], final.extra
    used = (final.extra or {}).get("workflow_budget_used")
    assert isinstance(used, int) and 0 < used < 50000, final.extra
    async with harness["session_factory"]() as db:
        holds = (
            (
                await db.execute(
                    select(QuotaReservationModel).where(
                        QuotaReservationModel.invocation_key.like(f"workflow-step:{parent.task_ref.id}:%")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(holds) == 1 and holds[0].settled and not holds[0].released, holds


async def test_two_processes_race_the_step_callback_once(harness, tmp_path) -> None:
    """Two fresh processes reissue the same lost verified callback at the
    same barrier boundary: the single-use wake applies exactly once."""

    agent_id = await _agent_with_tools(harness, "wf-pg-race-agent")
    parent = await _submit_workflow(harness, agent_id, [{"goal": "research the stock", "mode": "controller"}])
    env = _worker_env(harness, tmp_path, child_tool="web_search")
    code, report, err = await _run(env, "dispatch-parent", parent.task_ref.id)
    assert code == 0, (report, err)
    child_id = report["children"][0]["id"]
    code, report, err = await _run(
        {**env, "HECATE_WF_CONTROLLER_FAULT": "after_step_terminal"}, "dispatch-child", parent.task_ref.id, child_id
    )
    assert code == 73, (report, err)

    ready = tmp_path / "ready"
    ready.mkdir()
    race_env = {
        **env,
        "HECATE_WF_CONTROLLER_READY_DIR": str(ready),
        "HECATE_WF_CONTROLLER_ERROR_FILE": str(tmp_path / "racer-error.log"),
    }
    procs = [_spawn(race_env, "converge-callback", parent.task_ref.id, child_id) for _ in range(2)]
    receipts = []
    for index, proc in enumerate(procs):
        code, report, err = await _finish(proc)
        if code != 0:
            error_log = tmp_path / f"racer-error-{index}.log"
            if error_log.exists():
                detail = error_log.read_text(encoding="utf-8")[-2500:]
                raise AssertionError(f"racer {index} crashed; full traceback in {error_log}: {detail}")
        assert code == 0, (report, err)
        receipts.append(report)
    details = sorted(receipt["receipt_detail"] for receipt in receipts)
    # Exactly one wake applies. The loser's receipt names its own honest
    # loss — a settled-command/concurrent-recording replay, a consumed
    # token, or a wake that lost the CAS — never a second acceptance.
    assert sum("wake accepted" in detail for detail in details) == 1, details
    assert sum("wake accepted" not in detail for detail in details) == 1, details

    parent_state = await asyncio.to_thread(_state, harness, parent.task_ref)
    assert parent_state.lifecycle_state.value == "queued", parent_state.extra
    callbacks = (parent_state.extra or {}).get("_workflow_callbacks") or {}
    assert sorted(callbacks) == ["0"], parent_state.extra


async def test_cancel_is_requested_on_a_step_another_process_is_running(harness, tmp_path) -> None:
    """The parent's committed cancel records a REQUESTED cancel on the step a
    worker is still executing — requested, not stopped — and the late
    verified callback cannot re-activate the terminal parent."""

    agent_id = await _agent_with_tools(harness, "wf-pg-cancel-agent")
    parent = await _submit_workflow(harness, agent_id, [{"goal": "research the stock", "mode": "controller"}])
    release = tmp_path / "release-tool"
    env = _worker_env(harness, tmp_path, child_tool="web_search", release_tool=release)
    code, report, err = await _run(env, "dispatch-parent", parent.task_ref.id)
    assert code == 0, (report, err)
    child_id = report["children"][0]["id"]

    runner = _spawn(env, "dispatch-child", parent.task_ref.id, child_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 60
    effects_path = env["HECATE_WF_CONTROLLER_EFFECT_FILE"]
    while loop.time() < deadline:
        if any(line.get("event") == "entered" for line in _lines(effects_path)):
            break
        if runner.poll() is not None:
            code, report, err = await _finish(runner)
            raise AssertionError(f"the step worker exited inside its effect: {code}, {report}, {err[-2000:]}")
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("the step worker never reached the effect boundary")

    from hecate.execution.task_control import TaskControlService

    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["store"],
            recorder=harness["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        )
        issued = await service.issue_command(
            workspace_id=WS, task_id=uuid.UUID(parent.task_ref.id), kind=ControlCommandKind.CANCEL, issuer="tester"
        )
    assert issued.record.state is not CommandState.REJECTED
    # REQUESTED, not stopped: the running worker still holds the dispatch.
    receipt = await service.get_command(workspace_id=WS, command_id=f"workflow-cancel:{parent.task_ref.id}:{child_id}")
    assert receipt.state is CommandState.REQUESTED, receipt
    child_state = await asyncio.to_thread(_state, harness, task_ref_of(child_id))
    assert child_state.lifecycle_state.value == "running", child_state.extra

    release.touch()
    code, report, err = await _finish(runner)
    assert code == 0, (report, err)
    child_state = await _poll_state(harness, task_ref_of(child_id), "cancelled")
    parent_state = await _poll_state(harness, parent.task_ref, "cancelled")
    assert child_state.lifecycle_state.value == "cancelled"
    assert parent_state.lifecycle_state.value == "cancelled"

    wait = (parent_state.extra or {}).get("wait") or {}
    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["store"],
            recorder=harness["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        )
        late = await service.submit_workflow_callback(
            workspace_id=WS,
            task_id=uuid.UUID(parent.task_ref.id),
            command_id="late-wf-pg-callback",
            wait_token=str(wait.get("wait_token")),
            child_task_id=uuid.UUID(child_id),
            declared_state="cancelled",
        )
    assert late.record.state is CommandState.REJECTED
    assert (await asyncio.to_thread(_state, harness, parent.task_ref)).lifecycle_state.value == "cancelled"


@pytest.mark.parametrize("pause", ["after_reserve", "_lock_scope", "_reservation"])
async def test_paused_executor_cannot_submit_after_the_takeover_reclaims(harness, tmp_path, pause) -> None:
    """The reservation window, closed by two independent processes and a
    barrier: the paused old worker holds the step's reservation mid-dispatch;
    a cancel lands and the takeover owner's reconcile converges the parent,
    then the reclaim sweep closes the step and restores the quota — and only
    THEN the old worker is released. Its late submission is refused on its
    stale fence, no child exists, and nothing re-opens the reservation."""

    from sqlalchemy import select

    from hecate.contracts.execution.durable import TaskLifecycleState
    from hecate.execution.task_control import TaskControlService
    from hecate.models.quota import QuotaReservationModel

    agent_id = await _agent_with_tools(harness, "wf-pg-reclaim-agent")
    parent = await _submit_workflow(
        harness, agent_id, [{"goal": "research the stock", "mode": "controller", "budget_units": 50000}]
    )
    ready = tmp_path / "ready"
    ready.mkdir()
    release = tmp_path / "release-barrier"
    env = _worker_env(harness, tmp_path, child_tool="web_search", lease_ttl=2)
    hold_env = {
        **env,
        "HECATE_WF_CONTROLLER_READY_DIR": str(ready),
        "HECATE_WF_CONTROLLER_RELEASE_BARRIER": str(release),
        "HECATE_WF_CONTROLLER_ERROR_FILE": str(tmp_path / "hold-error.log"),
    }
    if pause != "after_reserve":
        hold_env["HECATE_WF_CONTROLLER_RESERVE_PAUSE"] = pause
    close_env = {**env, "HECATE_WF_CONTROLLER_MAX_SECONDS": "90"}

    # The paused executor: lease claimed, reservation committed behind the
    # fence, then stalled before the child submission.
    paused = _spawn(hold_env, "hold-step", parent.task_ref.id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 60
    while loop.time() < deadline and not list(ready.glob("ready-*")):
        if paused.poll() is not None:
            code, report, err = await _finish(paused)
            raise AssertionError(f"the paused worker exited early: {code}, {report}, {err[-2000:]}")
        await asyncio.sleep(0.1)
    assert list(ready.glob("ready-*")), "the paused worker never reached its barrier"
    async with harness["session_factory"]() as db:
        holds = (
            (
                await db.execute(
                    select(QuotaReservationModel).where(
                        QuotaReservationModel.invocation_key == f"workflow-step:{parent.task_ref.id}:0"
                    )
                )
            )
            .scalars()
            .all()
        )
    if pause == "after_reserve":
        assert len(holds) == 1 and not holds[0].released and not holds[0].settled, holds
    else:
        assert holds == [], holds

    # The user cancels a workflow whose dispatch has visibly stalled.
    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["store"],
            recorder=harness["store"],
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
        )
        issued = await service.issue_command(
            workspace_id=WS, task_id=uuid.UUID(parent.task_ref.id), kind=ControlCommandKind.CANCEL, issuer="tester"
        )
    assert issued.record.state is CommandState.REQUESTED, issued.record.state

    # The takeover owner: reconcile requeues the expired dispatch, the fresh
    # dispatch observes the cancel and converges the parent, and the sweep
    # closes the step and releases the hold on those durable facts.
    takeover = _spawn(close_env, "drive", parent.task_ref.id)
    if pause == "_reservation":
        # The late insert holds quota; the sweep must wait for its transaction
        # after committing the close marker, even though no hold is visible yet.
        deadline = loop.time() + 60
        while loop.time() < deadline:
            current = await asyncio.to_thread(_state, harness, parent.task_ref)
            if (current.extra or {}).get("workflow_step_states") == {"0": "closed_unexecuted"}:
                break
            await asyncio.sleep(0.1)
        assert (current.extra or {}).get("workflow_step_states") == {"0": "closed_unexecuted"}
        assert takeover.poll() is None, "recovery acknowledged a reservation still in flight"
        release.touch()
    code, report, err = await _finish(takeover)
    assert code == 0 and report["parent_state"] == "cancelled", (report, err[-3000:])
    parent_state = await asyncio.to_thread(_state, harness, parent.task_ref)
    assert parent_state.lifecycle_state is TaskLifecycleState.CANCELLED
    assert (parent_state.extra or {}).get("workflow_step_states") == {"0": "closed_unexecuted"}, parent_state.extra
    async with harness["session_factory"]() as db:
        holds = (
            (
                await db.execute(
                    select(QuotaReservationModel).where(
                        QuotaReservationModel.invocation_key == f"workflow-step:{parent.task_ref.id}:0"
                    )
                )
            )
            .scalars()
            .all()
        )
    if pause == "_lock_scope":
        assert holds == [], holds
    else:
        assert len(holds) == 1 and holds[0].released and not holds[0].settled, holds

    # ONLY NOW the barrier opens: the paused worker replays its submission
    # on the stale fence and is refused — honestly releasing nothing new.
    release.touch()
    code, report, err = await _finish(paused)
    assert code == 0, (report, err[-3000:])
    assert report["reserved"] == ("StaleFenceError" if pause == "_lock_scope" else "reserved"), report
    assert report["late_submit"] == "StaleFenceError", report
    assert report["tail_release"] == "no_reservation", report
    assert report["children"] == [], report

    children = await asyncio.to_thread(_children_of, harness, parent.task_ref)
    assert len(children) == 0, "the refused late submission must not mint a child"
    async with harness["session_factory"]() as db:
        holds = (
            (
                await db.execute(
                    select(QuotaReservationModel).where(
                        QuotaReservationModel.invocation_key.like(f"workflow-step:{parent.task_ref.id}:%")
                    )
                )
            )
            .scalars()
            .all()
        )
    if pause == "_lock_scope":
        assert holds == []
    else:
        assert len(holds) == 1 and holds[0].released, "no reservation may be re-opened for the closed step"
