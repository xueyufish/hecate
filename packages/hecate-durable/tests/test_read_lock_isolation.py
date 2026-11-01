"""Read paths must never queue behind the SQLite writer lock.

Store write transactions take the WAL writer lock up front (BEGIN
IMMEDIATE) so CAS paths serialize deterministically. Read-only lookups run
on a separate autocommit engine and take no lock at all: a held writer
transaction — a dispatch loop's commit window — must never block a
concurrent read. Before the read engine existed, that convoy surfaced as
``database is locked`` after the full busy timeout under parallel CI.
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

    # A raw DBAPI connection holds the WAL writer lock exactly the way a
    # write transaction does; nothing is committed, so every other writer
    # must wait for it.
    holder = sqlite3.connect(tmp_path / "db.sqlite", timeout=5)
    try:
        holder.execute("BEGIN IMMEDIATE")
        # All three read paths must return without touching the writer
        # lock — previously each of these waited for the full busy timeout
        # and then failed with `database is locked`.
        assert store.get_task_state(task_ref) is None
        assert store.get_task_input(task_ref) is None
        assert store.list_tasks() == []
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
