"""Performance benchmarks for SessionStateStore implementations.

Validates the latency thresholds set in ``horizontal-scaling-validation``
spec: wired backends SHALL add ≤ 10ms p95 platform overhead vs unwired
(InMemory) baseline. Uses ``fakeredis`` and SQLAlchemy mocks — no Docker
dependency.

Marked ``@pytest.mark.perf`` so CI can opt out via ``-m "not perf"``.
"""

from __future__ import annotations

import statistics
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.runtime.session_state import (
    InMemorySessionStateStore,
    SessionState,
)
from hecate.studio.session_state.postgres_store import PostgresSessionStateStore
from hecate.studio.session_state.redis_store import RedisSessionStateStore

pytest.importorskip("fakeredis")
import fakeredis.aioredis  # noqa: E402

SAMPLE_SIZE = 200  # keep test runtime bounded under 5s
WARMUP = 20


def percentile(samples: list[float], p: float) -> float:
    """Compute the p-th percentile of ``samples`` (0 ≤ p ≤ 100)."""
    if not samples:
        return float("inf")
    s = sorted(samples)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def _make_state(i: int) -> SessionState:
    return SessionState(
        agent_state={"summary": f"turn-{i}", "context": [i]},
        metadata={"superstep": i},
    )


def _factory_with_session(session: AsyncMock):
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory


# ---------------------------------------------------------------------------
# InMemory baseline
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_inmemory_save_latency():
    """InMemory save p95 SHALL be < 1ms (baseline)."""
    store = InMemorySessionStateStore()
    org, user, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    for i in range(WARMUP):
        await store.save(org, user, session, _make_state(i))

    latencies: list[float] = []
    for i in range(SAMPLE_SIZE):
        t0 = time.monotonic()
        await store.save(org, user, session, _make_state(i))
        latencies.append((time.monotonic() - t0) * 1000.0)

    assert percentile(latencies, 95) < 1.0, f"p95={percentile(latencies, 95):.3f}ms"
    assert percentile(latencies, 99) < 5.0


@pytest.mark.perf
async def test_inmemory_load_latency():
    """InMemory load p95 SHALL be < 1ms."""
    store = InMemorySessionStateStore()
    org, user, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await store.save(org, user, session, _make_state(0))

    for _ in range(WARMUP):
        await store.load(org, user, session)

    latencies: list[float] = []
    for _ in range(SAMPLE_SIZE):
        t0 = time.monotonic()
        await store.load(org, user, session)
        latencies.append((time.monotonic() - t0) * 1000.0)

    assert percentile(latencies, 95) < 1.0


# ---------------------------------------------------------------------------
# fakeredis
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_fakeredis_save_latency():
    """Redis save (fakeredis) p95 SHALL be < 5ms."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RedisSessionStateStore(redis_url="redis://localhost:6379/0")
    store._redis = fake

    org, user, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for i in range(WARMUP):
        await store.save(org, user, session, _make_state(i))

    latencies: list[float] = []
    for i in range(SAMPLE_SIZE):
        t0 = time.monotonic()
        await store.save(org, user, session, _make_state(i))
        latencies.append((time.monotonic() - t0) * 1000.0)

    assert percentile(latencies, 95) < 5.0, f"p95={percentile(latencies, 95):.3f}ms"
    assert percentile(latencies, 99) < 20.0


@pytest.mark.perf
async def test_fakeredis_load_latency():
    """Redis load (fakeredis) p95 SHALL be < 5ms."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store = RedisSessionStateStore(redis_url="redis://localhost:6379/0")
    store._redis = fake

    org, user, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await store.save(org, user, session, _make_state(0))

    for _ in range(WARMUP):
        await store.load(org, user, session)

    latencies: list[float] = []
    for _ in range(SAMPLE_SIZE):
        t0 = time.monotonic()
        await store.load(org, user, session)
        latencies.append((time.monotonic() - t0) * 1000.0)

    assert percentile(latencies, 95) < 5.0


# ---------------------------------------------------------------------------
# Mock PG (SQLAlchemy AsyncMock — measures call overhead, not real PG)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_mock_pg_save_latency():
    """Mock PG save (AsyncMock) p95 SHALL be < 10ms."""
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock()
    factory = _factory_with_session(session)
    store = PostgresSessionStateStore(async_session_factory=factory)

    org, user, session_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for i in range(WARMUP):
        await store.save(org, user, session_id, _make_state(i))

    latencies: list[float] = []
    for i in range(SAMPLE_SIZE):
        t0 = time.monotonic()
        await store.save(org, user, session_id, _make_state(i))
        latencies.append((time.monotonic() - t0) * 1000.0)

    assert percentile(latencies, 95) < 10.0, f"p95={percentile(latencies, 95):.3f}ms"


# ---------------------------------------------------------------------------
# Platform overhead (wired - unwired)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_platform_overhead_under_10ms_p95():
    """Wired (fakeredis) - unwired (InMemory) save p95 SHALL be < 10ms.

    This is the headline threshold: configuring SESSION_STATE_STORE_BACKEND
    adds bounded overhead vs the single-process default.
    """
    inmem = InMemorySessionStateStore()
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_store = RedisSessionStateStore(redis_url="redis://localhost:6379/0")
    redis_store._redis = fake

    org, user, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # Warmup both
    for i in range(WARMUP):
        await inmem.save(org, user, session, _make_state(i))
        await redis_store.save(org, user, session, _make_state(i))

    inmem_lat: list[float] = []
    redis_lat: list[float] = []
    for i in range(SAMPLE_SIZE):
        t0 = time.monotonic()
        await inmem.save(org, user, session, _make_state(i))
        inmem_lat.append((time.monotonic() - t0) * 1000.0)

        t0 = time.monotonic()
        await redis_store.save(org, user, session, _make_state(i))
        redis_lat.append((time.monotonic() - t0) * 1000.0)

    overhead = percentile(redis_lat, 95) - percentile(inmem_lat, 95)
    assert overhead < 10.0, (
        f"platform overhead p95={overhead:.3f}ms exceeds 10ms threshold; "
        f"inmem p95={percentile(inmem_lat, 95):.3f}ms, "
        f"redis p95={percentile(redis_lat, 95):.3f}ms"
    )


# ---------------------------------------------------------------------------
# Stats sanity (informational; no threshold)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_perf_stats_summary(capsys: pytest.CaptureFixture[str]):
    """Print latency stats summary for manual inspection (no assertion)."""
    store = InMemorySessionStateStore()
    org, user, session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    latencies: list[float] = []
    for i in range(SAMPLE_SIZE):
        t0 = time.monotonic()
        await store.save(org, user, session, _make_state(i))
        latencies.append((time.monotonic() - t0) * 1000.0)

    mean = statistics.mean(latencies)
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)
    p99 = percentile(latencies, 99)
    with capsys.disabled():
        print(
            f"\nInMemory save stats (n={SAMPLE_SIZE}): "
            f"mean={mean:.3f}ms p50={p50:.3f}ms p95={p95:.3f}ms p99={p99:.3f}ms"
        )
