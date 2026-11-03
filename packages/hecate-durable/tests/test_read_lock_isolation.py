"""Read paths must never queue behind the SQLite writer lock.

Store write transactions take the WAL writer lock up front (BEGIN
IMMEDIATE) so CAS paths serialize deterministically. Read-only lookups run
on a separate autocommit engine and take no lock at all: a held writer
transaction — a dispatch loop's commit window — must never block a
concurrent read. Before the read engine existed, that convoy surfaced as
``database is locked`` after the full busy timeout under parallel CI
(first on ``get_task_state``, then on ``run_for_task``/``read_events`` as
each remaining read path was reached).
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage import SqlDurableStore


def test_reads_do_not_block_behind_a_held_writer_transaction(tmp_path: Path) -> None:
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'db.sqlite'}", source="test")
    store.create_schema()
    task_ref = BackendRef(kind=RefKind.TASK, issuer_domain="hecate", id=str(uuid.uuid4()))
    run_ref = BackendRef(kind=RefKind.RUN, issuer_domain="hecate", id=str(uuid.uuid4()))

    # A raw DBAPI connection holds the WAL writer lock exactly the way a
    # write transaction does; nothing is committed, so every other writer
    # must wait for it.
    holder = sqlite3.connect(tmp_path / "db.sqlite", timeout=5)
    try:
        holder.execute("BEGIN IMMEDIATE")
        # Every pure-read path must return without touching the writer lock.
        # run_for_task and read_events are the paths CI hit under parallel
        # load after the first four methods were converted; the rest pin the
        # whole read surface so a future method cannot silently regress onto
        # the write engine.
        assert store.get_task_state(task_ref) is None
        assert store.get_task_input(task_ref) is None
        assert store.list_tasks() == []
        assert store.run_for_task(task_ref) is None
        assert store.task_for_run(run_ref) is None
        assert store.read_events(run_ref).events == []
        assert store.list_run_actions(run_ref) == []
        assert store.list_accepted_actions(run_ref) == []
        assert store.count_accepted_decisions(run_ref) == 0
        assert store.get(str(uuid.uuid4())) is None
        assert store.list_pending_commands(str(uuid.uuid4())) == []
        commands_page = store.list_task_commands(str(uuid.uuid4()), task_ref, 10, 0)
        assert commands_page[0] == []
        assert store.event_runs_for_task(task_ref) == []
        assert store.action_result(f"{uuid.uuid4()}:t", task_ref) is None
        assert store.submission_received_at(run_ref) is None
        assert store.wait_now() is not None
    finally:
        holder.rollback()
        holder.close()


def test_memory_backed_store_shares_the_write_engine() -> None:
    store = SqlDurableStore("sqlite:///:memory:", source="test")
    store.create_schema()
    # A shared in-memory database cannot have a second engine; reads and
    # writes must use the same StaticPool connection.
    assert store._read_engine is store.engine
    task_ref = BackendRef(kind=RefKind.TASK, issuer_domain="hecate", id=str(uuid.uuid4()))
    assert store.get_task_state(task_ref) is None
    store.dispose()
