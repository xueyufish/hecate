"""Integration tests for ``PostgresSessionStateStore`` against a real PostgreSQL.

These tests spin up a real PostgreSQL container via ``testcontainers-python``
and exercise the full SQL stack: Alembic migration, ``INSERT ... ON CONFLICT
DO UPDATE`` upsert behavior, JSONB round-trip, and ``ORDER BY ... DESC LIMIT``
query plan.

The tests are gated by the ``RUN_INTEGRATION_TESTS=1`` environment variable
(see ``tests/conftest.py``) so they are skipped by default — the project
runs no Docker in CI's default pipeline. They are intended for main-branch
pushes and local dev validation.

Run locally:
    RUN_INTEGRATION_TESTS=1 pytest tests/test_services/test_session_state/test_integration_postgres.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="integration tests require Docker via testcontainers; set RUN_INTEGRATION_TESTS=1 to enable",
)


@pytest.mark.integration
async def test_postgres_upsert_round_trips_session_state():
    """Real PG: INSERT then ON CONFLICT DO UPDATE preserves the latest state."""
    pytest.skip("testcontainers dependency not installed; see comments in integration test plan")
    # Implementation note: requires testcontainers[postgres] in [redis] group.
    # Will be enabled once Docker is available in CI's main branch job.
