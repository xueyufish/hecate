"""Performance benchmarks for EventStore backends.

Marked with ``@pytest.mark.perf`` — not in default test suite. Run with
``pytest -m perf`` to verify latency thresholds.

Thresholds (mock / in-memory only — testcontainers integration tests live in
``test_integration_postgres.py`` and run with ``RUN_INTEGRATION_TESTS=1``):

| Backend        | append p95 | append p99 |
|----------------|-----------:|-----------:|
| InMemory       |     < 1 ms |     < 5 ms |
| mock PG        |    < 10 ms |    < 30 ms |
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.engine.eventstore import Event, EventType, InMemoryEventStore
from hecate.services.event_state.postgres_store import PostgresEventStore


def _percentile(samples: list[float], p: float) -> float:
    """Compute the p-th percentile (0..100) from a list of latencies in seconds."""
    if not samples:
        return 0.0
    s = sorted(samples)
    k = int(len(s) * p / 100)
    k = min(k, len(s) - 1)
    return s[k]


def _factory_with_session(session: AsyncMock) -> MagicMock:
    factory = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    factory.return_value = cm
    return factory


def _mock_pg_session() -> AsyncMock:
    """Mock PG session whose execute() returns incremental MAX+1 then rowcount=1."""
    session = AsyncMock()
    counter = {"v": 0}

    async def execute(stmt, *args, **kwargs):  # noqa: ARG001
        result = AsyncMock()
        result.scalar_one = MagicMock(return_value=counter["v"])
        counter["v"] += 1
        result.rowcount = 1
        return result

    session.execute = AsyncMock(side_effect=execute)
    session.commit = AsyncMock()
    return session


@pytest.mark.perf
async def test_inmemory_append_latency():
    """InMemoryEventStore append p95 SHALL be < 1ms over 1000 ops."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    samples: list[float] = []
    for i in range(1000):
        event = Event(
            session_id=session_id,
            superstep=i,
            event_type=EventType.NODE_START,
            payload={"i": i},
        )
        start = time.monotonic()
        await store.append(event)
        samples.append(time.monotonic() - start)

    p95_ms = _percentile(samples, 95) * 1000
    p99_ms = _percentile(samples, 99) * 1000
    assert p95_ms < 1.0, f"InMemory append p95={p95_ms:.3f}ms exceeds 1ms threshold"
    assert p99_ms < 5.0, f"InMemory append p99={p99_ms:.3f}ms exceeds 5ms threshold"


@pytest.mark.perf
async def test_mock_pg_append_latency():
    """PostgresEventStore append (mocked) p95 SHALL be < 10ms over 1000 ops."""
    session = _mock_pg_session()
    factory = _factory_with_session(session)
    store = PostgresEventStore(async_session_factory=factory, max_append_retries=0)
    session_id = uuid.uuid4()
    samples: list[float] = []
    for i in range(1000):
        event = Event(
            session_id=session_id,
            superstep=i,
            event_type=EventType.NODE_START,
            payload={"i": i},
        )
        start = time.monotonic()
        await store.append(event)
        samples.append(time.monotonic() - start)

    p95_ms = _percentile(samples, 95) * 1000
    p99_ms = _percentile(samples, 99) * 1000
    assert p95_ms < 10.0, f"mock PG append p95={p95_ms:.3f}ms exceeds 10ms threshold"
    assert p99_ms < 30.0, f"mock PG append p99={p99_ms:.3f}ms exceeds 30ms threshold"
