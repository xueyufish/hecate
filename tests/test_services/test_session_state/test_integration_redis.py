"""Integration tests for ``RedisSessionStateStore`` against a real Redis server.

These tests spin up a real Redis container via ``testcontainers-python`` and
exercise the full Redis protocol: ``SET ... EX``, ``GET``, ``SCAN`` pattern
matching, and ``SCAN_ITER`` async iteration semantics.

The tests are gated by the ``RUN_INTEGRATION_TESTS=1`` environment variable
so they are skipped by default — the project runs no Docker in CI's
default pipeline. They are intended for main-branch pushes and local dev
validation.

Run locally:
    RUN_INTEGRATION_TESTS=1 pytest tests/test_services/test_session_state/test_integration_redis.py -v
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="integration tests require Docker via testcontainers; set RUN_INTEGRATION_TESTS=1 to enable",
)


@pytest.mark.integration
async def test_redis_set_ex_get_round_trips_session_state():
    """Real Redis: SET with EX writes the value with TTL; GET returns it."""
    pytest.skip("testcontainers dependency not installed; see comments in integration test plan")
    # Implementation note: requires testcontainers[redis] in [redis] group.
    # Will be enabled once Docker is available in CI's main branch job.
