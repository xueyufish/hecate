"""Checkpoint recovery (step6d): interrupted durable tasks resume on the
SAME attempt via the persisted task-level session, with the action ledger
still arbitrating every dispatch — succeeded actions backfill their recorded
real result (zero repeated business calls), claimed writes stop, and a task
without a usable checkpoint falls back to the existing replay semantics.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import pytest
from conftest_runner import write_profile
from hecate_durable.contracts.durable import (
    ActionIntent,
    ActionOutcome,
    ActionOutcomeRecord,
    IdempotencyKey,
    TaskLifecycleState,
)
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlCheckpointStore, SqlDurableStore
from hecate_runner.durable import DurableRuntime, action_key_for
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceStore
from hecate_runner.profile import load_profile

ISSUER = "standalone-host"
READ_ARGS = {"domain": "domain_a", "sku": "S-1"}
WRITE_ARGS = {"domain": "domain_a", "sku": "S-1", "quantity": 2}


@pytest.fixture(autouse=True)
def _shutdown_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")


class _BusinessApi:
    """Records every dispatch (the side-effect counter)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append({"tool": tool_name, "arguments": dict(arguments)})
        return {"status": "ok", "result": f"{tool_name}-result"}

    def tools(self) -> list[str]:
        return [call["tool"] for call in self.calls]


def _profile(tmp_path: Path, database_url: str) -> Path:
    return write_profile(
        tmp_path,
        allowlist=["query_inventory", "submit_inventory_update"],
        durable={"database_url": database_url, "workspace": "recovery"},
        with_write_tool=True,
    )


class _Harness:
    """One runner "process": durable store + engine (no HTTP needed here)."""

    def __init__(self, profile_dir: Path, *, api: _BusinessApi, database_url: str | None = None) -> None:
        self.profile = load_profile(profile_dir)
        assert self.profile.config.durable is not None
        self.database_url = database_url or self.profile.config.durable.database_url
        self.store = self._open_store(self.database_url)
        self.durable = DurableRuntime(self.store, workspace=self.profile.config.durable.workspace)
        self.evidence = EvidenceStore(self.profile.config.evidence_dir)
        self.api = api
        self.engine = ExecutionEngine(self.profile, self.evidence, api, durable=self.durable)

    @staticmethod
    def _open_store(url: str):
        store = SqlDurableStore(url)
        store.create_schema()
        return store

    async def submit(
        self, tool_arguments: dict, principal: str = "app-reader", domains: tuple[str, ...] = ("domain_a",)
    ):
        # Validation covers every allowlisted tool, so absent tools carry
        # their minimal arguments.
        full = {"query_inventory": READ_ARGS, "submit_inventory_update": WRITE_ARGS}
        full.update(tool_arguments)
        run_id, _state = await self.engine.submit(
            principal, {"input": {"prompt": "recovery"}, "tool_arguments": full}, domains
        )
        return run_id

    def close(self) -> None:
        self.store.dispose()


def _seed_crash_window(harness: _Harness, *, with_checkpoint: bool) -> tuple[BackendRef, BackendRef, uuid.UUID]:
    """Directly construct the durable state a crashed process leaves:

    the task RUNNING on its attempt, the read action succeeded with its real
    result, the write action claimed with no outcome — and optionally a
    persisted checkpoint for the task-level session at the superstep right
    after the read node.
    """

    task_id = f"task-{uuid.uuid4()}"
    run_id = f"run-{uuid.uuid4()}"
    task_ref = BackendRef(RefKind.TASK, ISSUER, task_id)
    run_ref = BackendRef(RefKind.RUN, ISSUER, run_id)
    persisted = {
        "input": {"prompt": "recovery"},
        "tool_arguments": {"query_inventory": READ_ARGS, "submit_inventory_update": WRITE_ARGS},
        "_host_identity": {"principal": "app-reader", "domains": ["domain_a"]},
    }
    harness.durable.store.submit_task(
        key=IdempotencyKey(
            key=f"recovery-{task_id}", subject="app-reader", workspace="recovery", request_digest=task_id
        ),
        task_ref=task_ref,
        run_ref=run_ref,
        input_payload=persisted,
    )
    harness.durable.store.apply_task_state(task_ref, TaskLifecycleState.RUNNING)

    read_key = action_key_for(run_id, "query_inventory")
    harness.durable.store.record_intent_ex(
        ActionIntent(
            action_key=read_key,
            action_name="query_inventory",
            arguments_digest="dig-read",
            side_effect_class=ToolSideEffectClass.READONLY,
        ),
        task_ref=task_ref,
        run_ref=run_ref,
        session_id=run_id,
        execution_id=read_key,
        tool_call_id=read_key,
    )
    harness.durable.store.record_outcome(ActionOutcomeRecord(action_key=read_key, outcome=ActionOutcome.SUCCEEDED))
    write_key = action_key_for(run_id, "submit_inventory_update")
    harness.durable.store.record_intent_ex(
        ActionIntent(
            action_key=write_key,
            action_name="submit_inventory_update",
            arguments_digest="dig-write",
            side_effect_class=ToolSideEffectClass.NON_IDEMPOTENT_WRITE,
        ),
        task_ref=task_ref,
        run_ref=run_ref,
        session_id=run_id,
        execution_id=write_key,
        tool_call_id=write_key,
    )
    receipt = harness.durable.store.claim(write_key)
    assert receipt.claimed

    session_id = harness.engine._session_for_task(task_ref)
    if with_checkpoint:
        cps = SqlCheckpointStore(harness.store.session_factory)
        cps.save_sync(
            session_id,
            2,
            "tool_0",
            {
                "input": {"input": persisted},
                "plan": {"tools": ["query_inventory", "submit_inventory_update"]},
                "tool_results": [{"tool": "query_inventory", "outcome": {"status": "ok"}}],
            },
            metadata={"log_version": 0},
        )
    return task_ref, run_ref, session_id


async def _wait_task_state(store: SqlDurableStore, task_ref: BackendRef, *states: str, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = store.get_task_state(task_ref)
        if record is not None and record.lifecycle_state.value in states:
            return record
        await asyncio.sleep(0.05)
    current = store.get_task_state(task_ref)
    raise AssertionError(f"task {task_ref.id} at {current.lifecycle_state.value if current else None}, wanted {states}")


async def test_crash_window_resumes_same_attempt_without_redo(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'host.db'}"
    profile_dir = _profile(tmp_path, database_url)
    api = _BusinessApi()
    harness = _Harness(profile_dir, api=api, database_url=database_url)
    task_ref, _run_ref, session_id = _seed_crash_window(harness, with_checkpoint=True)
    assert harness.engine._resume_from_checkpoint(task_ref) is True
    harness.close()  # process death; the durable rows and checkpoint outlive it

    # A fresh process resumes the interrupted attempt: same attempt (no new
    # run), continuing from the checkpoint; the ledger arbitrates — the read
    # backfills its recorded real result, the claimed write stops.
    restarted = _Harness(profile_dir, api=api, database_url=database_url)
    try:
        assert restarted.engine._session_for_task(task_ref) == session_id
        before_run = restarted.durable.store.run_for_task(task_ref)
        resumed = restarted.engine.resume(task_ref, before_run, {})
        assert resumed is not None
        await _wait_task_state(restarted.store, task_ref, "reconciliation_required")
        # Same attempt: the run linkage never changed.
        assert restarted.durable.store.run_for_task(task_ref).id == before_run.id
        # Zero business side effects: the read backfilled from the ledger,
        # the claimed write stopped for review.
        assert api.tools() == []
        actions = restarted.durable.run_actions(restarted.durable.store.run_for_task(task_ref))
        write_actions = [a for a in actions if (a.get("intent") or {}).get("action_name") == "submit_inventory_update"]
        assert write_actions and (
            write_actions[0].get("active_claim") or write_actions[0].get("pending_reconciliation")
        )
    finally:
        restarted.close()


async def test_no_checkpoint_falls_back_to_replay(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'host.db'}"
    profile_dir = _profile(tmp_path, database_url)
    api = _BusinessApi()
    harness = _Harness(profile_dir, api=api, database_url=database_url)

    # Same crash window but WITHOUT a persisted checkpoint: the resume falls
    # back to the full replay path (graph from the top, ledger arbitration).
    task_ref, run_ref, _session = _seed_crash_window(harness, with_checkpoint=False)
    assert harness.engine._resume_from_checkpoint(task_ref) is False

    resumed = harness.engine.resume(task_ref, run_ref, {})
    assert resumed is not None
    await _wait_task_state(harness.store, task_ref, "reconciliation_required")
    # Replay arbitrates through the SAME attempt's ledger: the read's
    # succeeded outcome backfills its real result instead of re-executing,
    # and the claimed write stops — zero business calls either way. The
    # fallback differs from the checkpoint path only in graph position, not
    # in side-effect safety.
    assert api.tools() == []
    actions = harness.durable.run_actions(run_ref)
    read_actions = [a for a in actions if (a.get("intent") or {}).get("action_name") == "query_inventory"]
    assert read_actions and read_actions[0].get("last_outcome") is not None
    harness.close()


async def test_session_is_task_stable_across_processes(tmp_path: Path):
    database_url = f"sqlite:///{tmp_path / 'host.db'}"
    profile_dir = _profile(tmp_path, database_url)
    api = _BusinessApi()
    harness = _Harness(profile_dir, api=api, database_url=database_url)
    run_id = await harness.submit({"query_inventory": READ_ARGS})
    run_ref = BackendRef(RefKind.RUN, ISSUER, run_id)
    task_ref = harness.durable.store.task_for_run(run_ref)
    await _wait_task_state(harness.store, task_ref, "succeeded")
    session_id = harness.engine._session_for_task(task_ref)
    assert session_id == uuid.uuid5(uuid.NAMESPACE_URL, f"runner-task:{task_ref.id}")
    assert harness.engine._resume_from_checkpoint(task_ref) is True
    harness.close()

    restarted = _Harness(profile_dir, api=api, database_url=database_url)
    try:
        assert restarted.engine._session_for_task(task_ref) == session_id
        assert restarted.engine._resume_from_checkpoint(task_ref) is True
    finally:
        restarted.close()
