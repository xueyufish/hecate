"""Shared helpers for the durable-core fault-injection suite.

SQLite (file) runs everywhere; PostgreSQL runs the identical suite when
``DURABLE_TEST_POSTGRES_URL`` names a reachable database (CI's migrations
job provides one; local runs use ``docker compose up postgres``). Crashes
are simulated by abandoning process state and reopening the same database —
only real durability crosses that boundary.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hecate_durable.storage import SqlDurableStore

POSTGRES_URL = os.environ.get("DURABLE_TEST_POSTGRES_URL", "")


def dialects() -> list[str]:
    return ["sqlite"] + (["postgres"] if POSTGRES_URL else [])


def make_store(dialect: str, tmp_path: Path, *, name: str = "state.db") -> SqlDurableStore:
    if dialect == "sqlite":
        return SqlDurableStore(f"sqlite:///{tmp_path / name}")
    return SqlDurableStore(POSTGRES_URL)


class MutableClock:
    """Injectable clock for lease expiry tests."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def iso(self) -> str:
        return self.now.isoformat()

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)
