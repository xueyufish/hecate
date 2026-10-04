"""Dialect parameterization for the durable-core fault-injection suite.

SQLite (file) runs everywhere; PostgreSQL runs the identical suite when
``DURABLE_TEST_POSTGRES_URL`` names a reachable database (CI's migrations
job provides one; local runs use ``docker compose up postgres``). Crashes
are simulated by abandoning process state and reopening the same database —
only real durability crosses that boundary.

Isolation: SQLite tests each own a fresh file, but a PostgreSQL URL names
ONE physical database shared by the whole parameterized run. The ``store``
fixture therefore resets the package-owned tables before every test so the
PG sibling starts from the same blank schema (a crash test re-opening the
same database within one test is unaffected — that isolation is per-test,
not per-store-instance).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _helpers import MutableClock, dialects, make_store
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import Base


@pytest.fixture(params=dialects(), ids=lambda d: d)
def dialect(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture()
def store(dialect: str, tmp_path: Path) -> SqlDurableStore:
    fresh = make_store(dialect, tmp_path)
    Base.metadata.drop_all(fresh.engine)
    Base.metadata.create_all(fresh.engine)
    yield fresh
    fresh.dispose()


@pytest.fixture()
def clock() -> MutableClock:
    return MutableClock()
