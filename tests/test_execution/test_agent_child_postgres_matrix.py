"""Agent child calls against real PostgreSQL: the cross-process fault matrix.

This module exercises real PostgreSQL row locks and transaction boundaries,
with competing executors in independent processes (``_agent_child_worker``).
The SQLite regression tier has separate concurrency and recovery coverage.
Connection material travels through the child environment
(``_agent_child_db``); only the provider boundary and the tool effect are
doubles. Every scenario asserts the durable facts from the test process:
one child per logical call, no re-executed effect, budget never reset,
unknown charges never refunded, and requested versus applied outcomes
asserted separately.

Requires ``HECATE_STEP6_POSTGRES_URL``; without it the module reports skips
(not passes) — the SQLite tier of the shared scenarios lives in
``test_agent_child_run.py``.
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

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from tests.test_execution._agent_child_db import child_environment
from tests.test_execution.conftest import HARNESS_WS as WS
from tests.test_execution.test_agent_child_run import (
    _add_agent,
    _AgentChildLLM,
    _child_holds,
    _children_of,
    _declaration_of,
    _service_of,
    _submit_parent,
    _worker_of,
)

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("HECATE_STEP6_POSTGRES_URL"),
        reason="HECATE_STEP6_POSTGRES_URL not configured; the Agent child PostgreSQL matrix reports skip (not pass)",
    ),
    pytest.mark.timeout(420),
]

_REPO = Path(__file__).resolve().parents[2]
WORKER = "tests.test_execution._agent_child_worker"
BUDGET = "tests.test_execution._agent_child_budget_process"


@pytest.fixture
def llm_stub(monkeypatch: pytest.MonkeyPatch) -> _AgentChildLLM:
    """The in-process provider double for the parent turns this process drives."""

    import hecate_llm.service as llm_module

    stub = _AgentChildLLM()
    monkeypatch.setattr(llm_module, "llm_service", stub)
    return stub


@pytest.fixture(autouse=True)
def _suppress_inline_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive every task explicitly — in this process or in a worker process."""

    monkeypatch.setattr("hecate.execution.task_control._spawn_background", lambda coro: coro.close())


@pytest.fixture(autouse=True)
async def _reap_worker_processes(harness, monkeypatch):
    """Stop test-owned workers before their isolated schema is dropped, even on failure."""
    original = _spawn
    children = []

    def tracked_spawn(*args, **kwargs):
        proc = original(*args, **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(sys.modules[__name__], "_spawn", tracked_spawn)
    yield
    for proc in children:
        if proc.poll() is None:
            await asyncio.to_thread(_kill_worker, proc)
        await asyncio.to_thread(proc.communicate, timeout=10)


# --- harness helpers ---------------------------------------------------------


async def _parent_with_tool_child(harness, *, total: int = 50000, per_call: int = 20000) -> Any:
    """A controller parent whose declared target carries the web_search effect."""

    target = await _add_agent(harness, "pg-matrix-worker", tools=["web_search"])
    declaration = _declaration_of(harness, target)
    declaration["budget_units"] = total
    declaration["targets"][0]["budget_units"] = per_call
    return await _submit_parent(harness, declaration)


def _worker_env(harness, tmp_path, **knobs: Any) -> dict[str, str]:
    env = child_environment(harness)
    env["HECATE_AGENT_CHILD_WORKSPACE_ID"] = str(WS)
    env["HECATE_AGENT_CHILD_EFFECT_FILE"] = str(tmp_path / "effects.jsonl")
    env["HECATE_AGENT_CHILD_MODEL_TRACE_FILE"] = str(tmp_path / "model_trace.jsonl")
    env["HECATE_AGENT_CHILD_CHILD_TOOL"] = "web_search"
    for key, value in knobs.items():
        env[f"HECATE_AGENT_CHILD_{key.upper()}"] = str(value)
    return env


def _spawn(env: dict[str, str], module: str, *args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", module, *args],
        cwd=str(_REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


async def _finish(proc: subprocess.Popen[str], timeout: float = 240.0) -> tuple[int, dict[str, Any] | None, str]:
    try:
        out, err = await asyncio.to_thread(proc.communicate, timeout)
    except subprocess.TimeoutExpired:
        await asyncio.to_thread(_kill_worker, proc)
        out, err = await asyncio.to_thread(proc.communicate)
        raise AssertionError(f"worker {proc.args} timed out; stderr tail:\n{(err or '')[-2000:]}") from None
    text = (out or "").strip()
    report = json.loads(text.splitlines()[-1]) if text else None
    return proc.returncode, report, err or ""


def _kill_worker(proc):
    """Kill only this test-owned process tree, including Windows venv redirectors."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10, check=False)
    elif proc.poll() is None:
        proc.kill()


async def _run(
    env: dict[str, str], module: str, *args: str, timeout: float = 240.0
) -> tuple[int, dict[str, Any] | None, str]:
    return await _finish(_spawn(env, module, *args), timeout)


def _lines(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip() and line.endswith("\n")]


async def _wait_child_request(env, proc):
    end = asyncio.get_running_loop().time() + 60
    while asyncio.get_running_loop().time() < end:
        # Windows venv python.exe can be a redirector: Popen.pid is then
        # different from the interpreter's os.getpid(). A is the sole actor
        # before this handshake; its report later confirms the trace PID.
        if _trace_requests(env["HECATE_AGENT_CHILD_MODEL_TRACE_FILE"], "child"):
            return
        if proc.poll() is not None:
            code, report, err = await _finish(proc)
            raise AssertionError(f"worker exited before its model request: {code}, {report}, {err[-3000:]}")
        await asyncio.sleep(0.05)
    raise AssertionError("the worker never reached its model request")


def _state(harness, ref) -> Any:
    return harness["store"].get_task_state(ref)


def _slice_usage(harness, child_ref, allocation: int) -> dict[str, Any]:
    from hecate.execution.agent_child import derive_slice_usage

    block = (_state(harness, child_ref).extra or {}).get("budget_slice")
    assert block is not None, "the slice state must be durable on the child row"
    return derive_slice_usage(block, allocated=allocation)


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


def _trace_requests(path: str, role: str) -> list[dict[str, Any]]:
    return [line for line in _lines(path) if line.get("role") == role]


async def _assert_completed_pair(harness, parent, *, effects: int, effects_path: str, budget: str = "settled") -> Any:
    """The shared end-state invariants: one child, one effect, one verified
    revision, the parent's cumulative accounting, and the workspace hold —
    ``settled`` to the trusted figure, or (``unknown``) kept open as an
    unknown hold that is neither charged as zero nor refunded."""

    children = await asyncio.to_thread(_children_of, harness, parent.task_ref)
    assert len(children) == 1, f"exactly one child per logical call, got {len(children)}"
    child_ref = children[0][0]
    child_record = await asyncio.to_thread(_state, harness, child_ref)
    assert child_record.lifecycle_state.value == "succeeded", child_record.extra

    assert len(_lines(effects_path)) == effects, _lines(effects_path)

    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, parent.task_ref)
    assert [row["source"] for row in revisions] == ["agent_child_result"], revisions

    holds = await _child_holds(harness, parent)
    assert len(holds) == 1 and not holds[0].released, holds
    final = await asyncio.to_thread(_state, harness, parent.task_ref)
    assert final.lifecycle_state.value == "succeeded", final.extra
    from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE

    if budget == "settled":
        assert holds[0].settled, holds
        assert final.extra["agent_child_budget_used"] == round(holds[0].settled_units / MODEL_TOKEN_UNIT_SCALE), (
            final.extra,
            holds[0].settled_units,
        )
    else:
        assert not holds[0].settled and holds[0].unknown_hold and holds[0].usage_status == "unknown", vars(holds[0])
        slice_used = (child_record.extra or {}).get("result", {}).get("budget", {}).get("used")
        assert final.extra["agent_child_budget_used"] == slice_used and slice_used > 0, (final.extra, slice_used)
    return child_ref


# --- 1. two workers race one logical call; the stale owner is fenced ---------


async def test_stale_executor_cannot_commit_after_a_lease_takeover(harness, llm_stub, tmp_path) -> None:
    """A hung child executor loses its dispatch lease; an independent worker
    takes the call over from PostgreSQL only, and the hung executor's late
    writes (state, budget, effects) are refused by the fence."""

    parent = await _parent_with_tool_child(harness)
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    child_id = child_ref.id

    env = _worker_env(harness, tmp_path, lease_ttl=5)
    # A claims the child's dispatch lease, then stalls INSIDE the model call:
    # the loop blocks, the heartbeat dies, the lease truly expires.
    release = tmp_path / "release-model"
    slow = _spawn(
        {**env, "HECATE_AGENT_CHILD_RELEASE_MODEL": str(release)},
        WORKER,
        "dispatch-child",
        parent.task_ref.id,
        child_id,
    )
    await _wait_child_request(env, slow)
    # B sees the expired lease from the shared database and takes the call over.
    taker = _spawn({**env, "HECATE_AGENT_CHILD_MAX_SECONDS": "90"}, WORKER, "drive", parent.task_ref.id)
    taker_code, taker_report, taker_err = await _finish(taker)
    release.touch()
    slow_code, slow_report, slow_err = await _finish(slow)

    assert taker_code == 0, (taker_report, taker_err)
    assert taker_report["parent_state"] == "succeeded", (taker_report, taker_err[-3000:])
    assert slow_code == 0, slow_err
    # The stale executor attempted its writes after the takeover and the
    # fencing token refused every one — it never reports a dispatch.
    assert slow_report["dispatched"] is False, slow_report
    assert "fencing token" in slow_err and "is stale" in slow_err and "the write is rejected" in slow_err, slow_err[
        -3000:
    ]

    # Both executors reached the model — A's single stalled request and B's
    # full two-turn run (tool call, then answer) — but exactly one effect
    # ran, owned by the executor that held the live lease.
    child_requests = _trace_requests(env["HECATE_AGENT_CHILD_MODEL_TRACE_FILE"], "child")
    assert len(child_requests) == 3, child_requests
    assert len([row for row in child_requests if row["pid"] == slow_report["pid"]]) == 1, child_requests
    effects = _lines(env["HECATE_AGENT_CHILD_EFFECT_FILE"])
    assert len(effects) == 1 and effects[0]["pid"] == taker_report["pid"], effects

    child = await _assert_completed_pair(
        harness, parent, effects=1, effects_path=env["HECATE_AGENT_CHILD_EFFECT_FILE"], budget="unknown"
    )
    # Durable fence evidence: exactly two dispatch starts are recorded —
    # A's original claim and B's takeover. A's post-unblock writes advanced
    # nothing; had any landed, the counter would read three.
    child_state = await asyncio.to_thread(_state, harness, child)
    assert child_state.extra.get("dispatch_starts") == 2, child_state.extra
    usage = _slice_usage(harness, child, allocation=20000)
    assert usage["unknown_count"] == 1, "the hung executor's unsettled attempt must stay an unknown charge"
    assert usage["status"] == "unknown", usage


# --- 2. concurrent calls cannot overdraw the workspace quota -----------------


async def test_concurrent_calls_cannot_overdraw_the_workspace_quota(harness, llm_stub, tmp_path) -> None:
    """Two calls racing for one workspace quota window: the quota row lock
    serializes them, the loser is refused with no child and no open hold."""

    from sqlalchemy import select, text

    from hecate.models.quota import QuotaModel
    from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE

    per_call = 10000
    hold = per_call * MODEL_TOKEN_UNIT_SCALE
    async with harness["session_factory"]() as db:
        db.add(
            QuotaModel(
                name="cost",
                resource_type="cost",
                scope="workspace",
                scope_id=WS,
                limit_value=hold * 1.5,
                window_type="monthly",
                workspace_id=WS,
            )
        )
        await db.commit()

    parents = []
    for index in range(2):
        target = await _add_agent(harness, f"quota-worker-{index}")
        declaration = _declaration_of(harness, target)
        declaration["budget_units"] = per_call
        declaration["targets"][0]["budget_units"] = per_call
        parents.append(await _submit_parent(harness, declaration))

    tag = f"child-quota-{uuid.uuid4().hex}"
    env = _worker_env(harness, tmp_path, application_name=tag)
    # Hold the real quota row until BOTH independent dispatchers are waiting
    # on its PostgreSQL lock. A sequential run cannot satisfy this assertion.
    async with harness["session_factory"]() as locker:
        await locker.execute(select(QuotaModel).where(QuotaModel.scope_id == WS).with_for_update())
        procs = [_spawn(env, WORKER, "dispatch-parent", parent.task_ref.id) for parent in parents]
        end = asyncio.get_running_loop().time() + 60
        blocked = 0
        while asyncio.get_running_loop().time() < end:
            async with harness["session_factory"]() as observer:
                blocked = await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE application_name = :tag AND wait_event_type = 'Lock'"
                    ),
                    {"tag": tag},
                )
            if blocked == 2:
                break
            await asyncio.sleep(0.05)
        assert blocked == 2, "both quota contenders must reach the real row-lock boundary"
        await locker.commit()
    reports = []
    for proc in procs:
        code, report, err = await _finish(proc)
        assert code == 0, (report, err)
        assert report["backend"] == "postgresql" and report["dispatched"] is True
        reports.append(report)
    assert len({report["pid"] for report in reports}) == 2
    worker = _worker_of(harness)

    children: list[Any] = []
    holds = []
    winner: Any = None
    for parent in parents:
        children.extend(await asyncio.to_thread(_children_of, harness, parent.task_ref))
        holds.extend(await _child_holds(harness, parent))
        record = await asyncio.to_thread(_state, harness, parent.task_ref)
        if record.lifecycle_state is TaskLifecycleState.WAITING_INPUT:
            winner = parent
    assert len(children) == 1, "exactly one call may win the quota race"
    assert len(holds) == 1, holds
    open_units = sum(row.units for row in holds if not row.released and not row.settled)
    assert open_units <= hold * 1.5, holds
    assert winner is not None

    loser = next(parent for parent in parents if parent.task_ref.id != winner.task_ref.id)
    loser_record = await asyncio.to_thread(_state, harness, loser.task_ref)
    assert loser_record.lifecycle_state.value == "succeeded", loser_record.extra

    child_ref = children[0][0]
    assert await worker.dispatch_once(child_ref) is True
    assert await worker.dispatch_once(winner.task_ref) is True
    final = await asyncio.to_thread(_state, harness, winner.task_ref)
    assert final.lifecycle_state.value == "succeeded", final.extra
    usage = _slice_usage(harness, child_ref, allocation=per_call)
    assert usage["used"] > 0


# --- 2b. concurrent slice writers never lose an entry ------------------------


async def test_concurrent_slice_writers_from_two_processes_do_not_lose_entries(harness, llm_stub, tmp_path) -> None:
    """Two processes hammer one durable slice; every write a process saw
    succeed must be durable — the CAS retry loop converges, never overwrites."""

    allocation = 200
    parent = await _parent_with_tool_child(harness, total=50000, per_call=allocation)
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    await asyncio.to_thread(harness["store"].apply_task_state, child_ref, TaskLifecycleState.RUNNING)

    barrier = tmp_path / "slice-read-barrier"
    barrier.mkdir()
    env = {
        **child_environment(harness),
        "HECATE_AGENT_CHILD_SLICE_ALLOCATION": str(allocation),
        "HECATE_AGENT_CHILD_CAS_BARRIER": str(barrier),
    }
    procs = [_spawn(env, BUDGET, child_ref.id, "hammer", f"hammer-{index}", "30") for index in range(2)]
    reports = []
    for proc in procs:
        code, report, err = await _finish(proc)
        assert code == 0, (report, err)
        reports.append(report)

    usage = _slice_usage(harness, child_ref, allocation=allocation)
    block = (_state(harness, child_ref).extra or {}).get("budget_slice")
    entries = block["entries"]
    assert len({report["pid"] for report in reports}) == 2
    assert reports[0]["initial_revision"] == reports[1]["initial_revision"]
    assert reports[0]["initial_revision"] is not None
    settled_keys: set[str] = set()
    settle_failed: set[str] = set()
    reserve_failed: set[str] = set()
    for report in reports:
        settled_keys.update(report["settled"])
        settle_failed.update(report["settle_failed"])
        reserve_failed.update(report["reserve_failed"])
    # Every write a process saw succeed is durable with its exact figure.
    for key in settled_keys:
        assert entries.get(key, {}).get("settled") == 1, f"lost update: {key} not durable"
    # A reservation whose settle lost every CAS retry stays an unknown hold —
    # charged, never silently dropped; a reserve that never persisted left
    # no phantom entry behind.
    for key in settle_failed:
        assert key in entries and entries[key].get("settled") is None, f"unknown hold lost: {key}"
    for key in reserve_failed:
        assert key not in entries, f"phantom reservation: {key}"
    total_settled = sum(entry["settled"] for entry in entries.values() if entry.get("settled") is not None)
    assert total_settled == len(settled_keys), (entries, reports)
    assert usage["unknown_count"] == len(settle_failed), (usage, reports)
    assert usage["used"] == total_settled + len(settle_failed), usage
    assert settled_keys, "the hammer must have made progress"


# --- 3. abrupt death in every window; recovery from PostgreSQL only ----------


@pytest.mark.parametrize(
    "fault",
    [
        "after_child_submit",
        "before_parent_park",
        "after_parent_dispatch",
        "after_child_accept",
        "after_settlement_before_wake",
    ],
)
async def test_process_death_windows_recover_the_same_child_without_repeating_effects(
    harness, llm_stub, tmp_path, fault
) -> None:
    """Kill an independent worker in each commit window; a fresh worker must
    converge the SAME child, never re-execute the settled effect, and never
    duplicate the verified revision or the budget settlement."""

    parent = await _parent_with_tool_child(harness)
    env = _worker_env(harness, tmp_path, lease_ttl=2)
    effects_path = env["HECATE_AGENT_CHILD_EFFECT_FILE"]
    if fault in ("after_child_submit", "before_parent_park", "after_parent_dispatch"):
        code, report, err = await _run(
            {**env, "HECATE_AGENT_CHILD_FAULT": fault}, WORKER, "dispatch-parent", parent.task_ref.id
        )
        assert code == 73, (report, err)
    else:
        code, report, err = await _run(env, WORKER, "dispatch-parent", parent.task_ref.id)
        assert code == 0 and report["dispatched"] is True, (report, err)
        child_id = report["children"][0]["id"]
        code, report, err = await _run(
            {**env, "HECATE_AGENT_CHILD_FAULT": fault}, WORKER, "dispatch-child", parent.task_ref.id, child_id
        )
        assert code == 73, (report, err)

    effects_before_death = len(_lines(effects_path))
    children_before = await asyncio.to_thread(_children_of, harness, parent.task_ref)
    assert len(children_before) == 1
    if fault == "after_child_submit":
        run = await asyncio.to_thread(harness["store"].run_for_task, parent.task_ref)
        receipt = await asyncio.to_thread(harness["store"].get_run_action, run, children_before[0][1]["call_id"])
        assert receipt is not None and (receipt.get("last_outcome") or {}).get("outcome") != "succeeded", (
            "acceptance must not have settled before death"
        )
    holds_before_death = await _child_holds(harness, parent)
    settled_before = {(row.invocation_key, row.settled_units) for row in holds_before_death if row.settled}

    code, report, err = await _run(env, WORKER, "drive", parent.task_ref.id, timeout=300)
    assert code == 0, (report, err)
    assert report["parent_state"] == "succeeded", (
        report.get("parent_reconciliation_reason"),
        report.get("parent_error"),
        report,
        err,
    )

    expected_effects = 0 if fault in ("after_child_submit", "before_parent_park", "after_parent_dispatch") else 1
    assert effects_before_death == expected_effects, f"effect count at death for {fault}: {effects_before_death}"
    child_ref = await _assert_completed_pair(harness, parent, effects=1, effects_path=effects_path)
    assert child_ref == children_before[0][0], "recovery must reuse the child created before the crash"
    trace = env["HECATE_AGENT_CHILD_MODEL_TRACE_FILE"]
    assert len(_trace_requests(trace, "parent")) == 2, (
        "the committed parent decision must replay without another model call"
    )
    assert len(_trace_requests(trace, "child")) == 2, (
        "the committed child decision must replay without another model call"
    )

    holds = await _child_holds(harness, parent)
    assert len(holds) == 1 and holds[0].settled, holds
    if fault == "after_settlement_before_wake":
        assert (holds[0].invocation_key, holds[0].settled_units) in settled_before, (
            "the settlement committed before the crash must survive unchanged"
        )
    usage = _slice_usage(harness, child_ref, allocation=20000)
    if fault == "after_child_accept":
        # The dead process's in-flight model attempt for the settled turn
        # stays held; the recovery replays the decision without re-charging.
        assert usage["status"] in ("unknown", "unverified", "estimated", "actual"), usage


# --- 4. a model hold survives abrupt death and refuses the retry -------------


async def test_model_hold_survives_process_death_and_refuses_before_the_next_model_request(
    harness, llm_stub, tmp_path
) -> None:
    """A worker dying right after the slice reservation leaves an unknown
    charge; a fresh worker's retry is refused BEFORE any model request."""

    parent = await _parent_with_tool_child(harness, total=20000, per_call=5000)  # one estimate fits, two do not
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    child_id = child_ref.id

    env = _worker_env(harness, tmp_path, lease_ttl=2)
    code, report, err = await _run(
        {**env, "HECATE_AGENT_CHILD_FAULT": "after_model_reserve"},
        WORKER,
        "dispatch-child",
        parent.task_ref.id,
        child_id,
    )
    assert code == 73, (report, err)
    assert _trace_requests(env["HECATE_AGENT_CHILD_MODEL_TRACE_FILE"], "child") == [], "died before the model"

    usage = _slice_usage(harness, child_ref, allocation=5000)
    assert usage["unknown_count"] == 1 and usage["used"] > 0, usage

    code, report, err = await _run(
        {**env, "HECATE_AGENT_CHILD_MAX_SECONDS": "120"}, WORKER, "drive", parent.task_ref.id, timeout=300
    )
    assert code == 0, (report, err)
    # No process ever got a second model request through: the surviving hold
    # alone denied every retry before the model.
    assert len(_trace_requests(env["HECATE_AGENT_CHILD_MODEL_TRACE_FILE"], "child")) == 0
    usage = _slice_usage(harness, child_ref, allocation=5000)
    assert usage["unknown_count"] == 1, usage
    holds = await _child_holds(harness, parent)
    assert holds and holds[0].released is False, holds  # the unknown charge is never refunded
    await _poll_state(harness, parent.task_ref, "reconciliation_required", "failed", timeout=60)


# --- 5. convergence: cancel closes queued holds, unknown stays held ----------


async def test_parent_cancel_converges_the_pair_but_keeps_the_unknown_hold(harness, llm_stub, tmp_path) -> None:
    """Requested cancel versus applied stop are asserted separately, and the
    child's unknown execution charge survives the convergence."""

    parent = await _parent_with_tool_child(harness)
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    child_id = child_ref.id

    env = _worker_env(harness, tmp_path, lease_ttl=2)
    code, report, err = await _run(
        {**env, "HECATE_AGENT_CHILD_FAULT": "after_model_reserve"},
        WORKER,
        "dispatch-child",
        parent.task_ref.id,
        child_id,
    )
    assert code == 73, (report, err)

    # REQUESTED vs APPLIED: the control plane applies the cancel to the parked
    # parent itself (no executor holds it), but the child — whose dead
    # executor still holds the dispatch lease — has NOT stopped yet.
    async with harness["session_factory"]() as db:
        issued = await _service_of(harness, db).issue_command(
            workspace_id=WS, task_id=uuid.UUID(parent.task_ref.id), kind=ControlCommandKind.CANCEL, issuer="matrix"
        )
    assert issued.record.state is not CommandState.REJECTED, issued.record
    record = await asyncio.to_thread(_state, harness, parent.task_ref)
    assert record.lifecycle_state is TaskLifecycleState.CANCELLED, record.extra
    child_record = await asyncio.to_thread(_state, harness, child_ref)
    assert child_record.lifecycle_state.value == "running", "the child's actual stop is a separate, later fact"

    # The actual stop: an independent worker observes the terminal parent
    # from PostgreSQL and cancels the recovered child without model work.
    code, report, err = await _run(env, WORKER, "drive", parent.task_ref.id, timeout=240)
    assert code == 0, (report, err)
    final = await asyncio.to_thread(_state, harness, parent.task_ref)
    assert final.lifecycle_state.value == "cancelled", final.extra
    child_record = await asyncio.to_thread(_state, harness, child_ref)
    # The recovered child is stopped at its dispatch entry by the terminal
    # parent (the same fact the in-process tier asserts): failed with the
    # parent_cancelled category, no model work, no effect.
    assert child_record.lifecycle_state.value == "failed", child_record.extra
    assert (child_record.extra or {}).get("result", {}).get("error_category") == "parent_cancelled", child_record.extra
    assert _trace_requests(env["HECATE_AGENT_CHILD_MODEL_TRACE_FILE"], "child") == [], "no model work after the cancel"
    assert len(_lines(env["HECATE_AGENT_CHILD_EFFECT_FILE"])) == 0, "the unknown attempt never executed an effect"

    holds = await _child_holds(harness, parent)
    assert len(holds) == 1 and holds[0].released is False, holds  # never refunded
    usage = _slice_usage(harness, child_ref, allocation=20000)
    assert usage["unknown_count"] == 1 and usage["status"] == "unknown", usage
    charged = holds[0].settled_units or holds[0].units
    assert charged > 0, holds  # the unknown execution stays charged


# --- 6. concurrent and late callbacks/convergers advance the parent once -----


async def test_concurrent_and_late_callbacks_advance_the_parent_exactly_once(
    harness, llm_stub, tmp_path, monkeypatch
) -> None:
    """Two independent processes submit the same verified callback
    concurrently: exactly one is APPLIED. A late third is REJECTED. The
    parent advances once."""

    target = await _add_agent(harness, "cb-matrix-worker")
    parent = await _submit_parent(harness, _declaration_of(harness, target))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]

    async def silent_callback(self, db, context, outcome, *, retry_errors=False):
        return None

    monkeypatch.setattr(
        "hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_agent_child_callback", silent_callback
    )
    assert await worker.dispatch_once(child_ref) is True
    assert (await asyncio.to_thread(_state, harness, child_ref)).lifecycle_state.value == "succeeded"

    env = _worker_env(harness, tmp_path)
    procs = [_spawn(env, WORKER, "callback", parent.task_ref.id, child_ref.id, f"cb-{index}") for index in range(2)]
    receipts = []
    for proc in procs:
        code, report, err = await _finish(proc)
        assert code == 0, (report, err)
        receipts.append(report["receipt_state"])
    assert receipts.count("applied") == 1, receipts  # exactly one of the two racing submissions applied
    assert receipts.count("rejected") == 1, receipts

    late = await _run(env, WORKER, "callback", parent.task_ref.id, child_ref.id, "late-callback")
    assert late[1]["receipt_state"] == "rejected", late  # requested, but never applied again

    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, parent.task_ref)
    assert [row["source"] for row in revisions] == ["agent_child_result"], revisions
    wait = ((await asyncio.to_thread(_state, harness, parent.task_ref)).extra or {}).get("wait") or {}
    assert wait.get("consumed") is True, wait

    assert await worker.dispatch_once(parent.task_ref) is True
    final = await asyncio.to_thread(_state, harness, parent.task_ref)
    assert final.lifecycle_state.value == "succeeded", final.extra


async def test_concurrent_convergers_wake_the_parent_once(harness, llm_stub, tmp_path, monkeypatch) -> None:
    """Two fresh workers converge the same lost callback concurrently: the
    wait token is single-use, so the parent wakes exactly once."""

    target = await _add_agent(harness, "converge-matrix-worker")
    parent = await _submit_parent(harness, _declaration_of(harness, target))
    worker = _worker_of(harness)
    assert await worker.dispatch_once(parent.task_ref) is True
    child_ref, _stamp = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]

    async def silent_callback(self, db, context, outcome, *, retry_errors=False):
        return None

    monkeypatch.setattr(
        "hecate.execution.task_dispatcher.PlatformTaskDispatcher._issue_agent_child_callback", silent_callback
    )
    assert await worker.dispatch_once(child_ref) is True

    env = _worker_env(harness, tmp_path)
    procs = [
        _spawn({**env, "HECATE_AGENT_CHILD_MAX_SECONDS": "60"}, WORKER, "drive", parent.task_ref.id) for _ in range(2)
    ]
    reports = []
    stderrs = []
    for proc in procs:
        code, report, err = await _finish(proc)
        assert code == 0, (report, err)
        reports.append(report)
        stderrs.append(err[-2000:])

    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, parent.task_ref)
    assert [row["source"] for row in revisions] == ["agent_child_result"], (revisions, reports, stderrs)
    final = await asyncio.to_thread(_state, harness, parent.task_ref)
    assert final.lifecycle_state.value == "succeeded", (final.extra, reports, stderrs)
    children = await asyncio.to_thread(_children_of, harness, parent.task_ref)
    assert len(children) == 1


async def test_deadline_converges_from_a_fresh_process_and_closes_the_queued_hold(harness, llm_stub, tmp_path):
    """An injected expiry clock exercises the frozen deadline in another process."""
    from datetime import UTC, datetime, timedelta

    target = await _add_agent(harness, "deadline-worker", tools=["web_search"])
    declaration = _declaration_of(harness, target)
    declaration["budget_units"] = 50000
    declaration["targets"][0]["budget_units"] = 20000
    deadline = datetime.now(UTC) + timedelta(seconds=120)
    parent = await _submit_parent(harness, declaration, controller_extra={"deadline": deadline.isoformat()})
    await _worker_of(harness).dispatch_once(parent.task_ref)
    child, _ = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    env = _worker_env(harness, tmp_path, wait_clock=(deadline + timedelta(seconds=1)).isoformat())
    code, report, err = await _run(env, WORKER, "converge", parent.task_ref.id)
    assert code == 0 and report["backend"] == "postgresql", (report, err)
    assert report["parent_state"] == "failed"
    assert (await asyncio.to_thread(_state, harness, child)).lifecycle_state is TaskLifecycleState.CANCELLED
    assert _trace_requests(env["HECATE_AGENT_CHILD_MODEL_TRACE_FILE"], "child") == []
    assert _lines(env["HECATE_AGENT_CHILD_EFFECT_FILE"]) == []
    assert await asyncio.to_thread(harness["store"].list_input_revisions, parent.task_ref) == []
    holds = await _child_holds(harness, parent)
    assert len(holds) == 1 and (holds[0].released or (holds[0].settled and holds[0].settled_units == 0))


async def test_child_schema_failure_in_another_process_delivers_only_failure_facts(harness, llm_stub, tmp_path):
    """A failed child cannot be relabelled as a successful delivery during recovery."""
    parent = await _parent_with_tool_child(harness)
    await _worker_of(harness).dispatch_once(parent.task_ref)
    env = _worker_env(harness, tmp_path, invalid_answer=1)
    code, report, err = await _run(env, WORKER, "drive", parent.task_ref.id)
    assert code == 0 and report["backend"] == "postgresql", (report, err)
    child, _ = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]
    child_state = await asyncio.to_thread(_state, harness, child)
    assert child_state.lifecycle_state is TaskLifecycleState.FAILED, child_state.extra
    assert child_state.extra["result"]["acceptance"]["status"] == "acceptance_failed"
    revisions = await asyncio.to_thread(harness["store"].list_input_revisions, parent.task_ref)
    assert len(revisions) == 1 and revisions[0]["source"] == "agent_child_result"
    assert revisions[0]["content"]["child_state"] == "failed"
    assert revisions[0]["content"]["content"] is None
    assert len(_lines(env["HECATE_AGENT_CHILD_EFFECT_FILE"])) == 1
    holds = await _child_holds(harness, parent)
    assert len(holds) == 1 and holds[0].settled and holds[0].settled_units > 0


async def test_lost_acceptance_with_rewritten_child_input_is_not_confirmed(harness, llm_stub, tmp_path):
    """Finding a child alone cannot authorize backfilling its caller's receipt."""
    from hecate_durable.storage.models import TaskStateRow

    parent = await _parent_with_tool_child(harness)
    env = _worker_env(harness, tmp_path, lease_ttl=2)
    code, report, err = await _run(
        {**env, "HECATE_AGENT_CHILD_FAULT": "after_child_submit"}, WORKER, "dispatch-parent", parent.task_ref.id
    )
    assert code == 73, (report, err)
    child, stamp = (await asyncio.to_thread(_children_of, harness, parent.task_ref))[0]

    def corrupt_input():
        with harness["store"].session_factory() as db:
            row = db.get(TaskStateRow, (child.issuer_domain, child.id))
            row.input_payload = {
                **row.input_payload,
                "messages": [{"role": "user", "content": json.dumps({"input": {"task": "rewritten"}})}],
            }
            db.commit()

    await asyncio.to_thread(corrupt_input)
    code, report, err = await _run(env, WORKER, "drive", parent.task_ref.id)
    assert code == 0, (report, err)
    assert report["parent_state"] == "reconciliation_required", (report, err)
    run = await asyncio.to_thread(harness["store"].run_for_task, parent.task_ref)
    receipt = await asyncio.to_thread(harness["store"].get_run_action, run, stamp["call_id"])
    assert receipt.get("last_outcome") is None
    assert await asyncio.to_thread(harness["store"].list_input_revisions, parent.task_ref) == []
    assert len(await asyncio.to_thread(_children_of, harness, parent.task_ref)) == 1
