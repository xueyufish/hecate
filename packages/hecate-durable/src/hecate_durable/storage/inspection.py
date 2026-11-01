"""Read-only local ledger inspection for offline Runner release management."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from hecate_durable.contracts.durable import TaskLifecycleState


def read_sqlite_task_states(database_path: Path) -> tuple[TaskLifecycleState, ...]:
    """Read existing task states without creating a file or changing its schema.

    Runner release activation is the consumer. A missing schema, inaccessible
    file or unknown lifecycle value raises rather than implying an empty ledger.
    """

    with closing(sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        rows = connection.execute("SELECT lifecycle_state FROM durable_task_state").fetchall()
    return tuple(TaskLifecycleState(row[0]) for row in rows)
