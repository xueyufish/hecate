"""Dialect parameterization for the durable-core fault-injection suite.

SQLite (file) runs everywhere; PostgreSQL runs the identical suite when
``DURABLE_TEST_POSTGRES_URL`` names a reachable database (CI's migrations
job provides one; local runs use ``docker compose up postgres``). Crashes
are simulated by abandoning process state and reopening the same database —
only real durability crosses that boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _helpers import MutableClock, dialects, make_store
from hecate_durable.storage import SqlDurableStore


@pytest.fixture(params=dialects(), ids=lambda d: d)
def dialect(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture()
def store(dialect: str, tmp_path: Path) -> SqlDurableStore:
    fresh = make_store(dialect, tmp_path)
    fresh.create_schema()
    return fresh


@pytest.fixture()
def clock() -> MutableClock:
    return MutableClock()
