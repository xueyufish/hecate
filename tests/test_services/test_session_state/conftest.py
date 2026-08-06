"""Shared fixtures for session-state-store tests.

Provides ``fakeredis_client`` (an in-memory Redis double) used across the
``RedisSessionStateStore`` and ``TieredSessionStateStore`` unit tests.

The ``fakeredis`` package (``pip install ".[redis]"``) provides a complete
in-memory implementation of the ``redis.asyncio`` client API, with full
support for GET/SET/EX/SCAN/EXPIRE/SCAN_ITER. Tests that need to exercise
real Redis behavior (Cluster hash-tag routing, real TTL eviction, real
network failure modes) belong in ``test_integration_*.py`` and are gated by
the ``RUN_INTEGRATION_TESTS=1`` environment variable.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest_asyncio


@pytest_asyncio.fixture
async def fakeredis_client() -> fakeredis.aioredis.FakeRedis:
    """Yield a fresh in-memory fakeredis async client per test.

    The client implements the full ``redis.asyncio`` API surface (GET, SET,
    SCAN_ITER, etc.) so the store under test cannot tell the difference
    from a real Redis connection.
    """
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()
