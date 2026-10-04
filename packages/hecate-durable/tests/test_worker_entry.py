"""Standalone worker process entry: real subprocess smoke tests.

The entry is the reference for running the dispatch loop outside the
platform/runner process. These tests prove the full path — clean startup,
dispatch of a pre-existing queued task against the same database, honest
non-zero exits on startup failure — without importing host code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from hecate_durable.contracts.durable import IdempotencyKey, TaskLifecycleState
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage import SqlDurableStore

_DISPATCHER_MODULE = """
import sys
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_durable.storage import SqlDurableStore

_DSN = {dsn!r}


def make_dispatcher():
    store = SqlDurableStore(_DSN)

    async def dispatch(task_ref, record, lease):
        store.apply_task_state(task_ref, TaskLifecycleState.SUCCEEDED)

    return dispatch
"""


def _seed_task(db_path: Path) -> None:
    store = SqlDurableStore(f"sqlite:///{db_path}")
    store.create_schema()
    task = BackendRef(RefKind.TASK, "host", "entry-task")
    run = BackendRef(RefKind.RUN, "host", "entry-run")
    store.submit_task(
        key=IdempotencyKey(key="entry-key", subject="s", workspace="w", request_digest="d" * 8),
        task_ref=task,
        run_ref=run,
        input_payload={"goal": "entry smoke"},
    )
    store.dispose()


def test_entry_dispatches_preexisting_task(tmp_path: Path) -> None:
    db = tmp_path / "entry.db"
    dsn = f"sqlite:///{db}"
    _seed_task(db)
    (tmp_path / "fake_dispatcher.py").write_text(_DISPATCHER_MODULE.format(dsn=dsn), encoding="utf-8")

    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    proc = subprocess.run(  # noqa: S603 - venv python, fixed args
        [
            sys.executable,
            "-m",
            "hecate_durable.worker",
            "--dsn",
            dsn,
            "--dispatcher",
            "fake_dispatcher:make_dispatcher",
            "--run-seconds",
            "2",
            "--poll-interval",
            "0.05",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    store = SqlDurableStore(dsn)
    state = store.get_task_state(BackendRef(RefKind.TASK, "host", "entry-task"))
    assert state is not None and state.lifecycle_state is TaskLifecycleState.SUCCEEDED
    store.dispose()


def test_entry_fails_fast_on_unreachable_store(tmp_path: Path) -> None:
    bad_dir = tmp_path / "missing-directory"
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "hecate_durable.worker",
            "--dsn",
            f"sqlite:///{bad_dir / 'x.db'}",
            "--dispatcher",
            "fake_dispatcher:make_dispatcher",
            "--run-seconds",
            "0.1",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert "durable store unavailable" in (proc.stdout + proc.stderr)


def test_entry_fails_fast_on_bad_dispatcher_factory(tmp_path: Path) -> None:
    db = tmp_path / "entry2.db"
    dsn = f"sqlite:///{db}"
    _seed_task(db)
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "hecate_durable.worker",
            "--dsn",
            dsn,
            "--dispatcher",
            "no_such_module:make_dispatcher",
            "--run-seconds",
            "0.1",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2
    assert "dispatcher factory" in (proc.stdout + proc.stderr)
