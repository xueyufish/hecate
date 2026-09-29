"""Real PostgreSQL regressions for pooled locks and durable tool claims.

Set HECATE_TEST_POSTGRES_URL to a disposable PostgreSQL database. Separate
engines are necessary here to exercise two physical connections; SQLite and
SQL mocks cannot prove advisory-lock lifecycle or PostgreSQL query validity.
Only the events table is created; no existing rows or tables are deleted.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hecate.runtime.eventstore import Event, EventType
from hecate.runtime.workers.tool_worker import ToolWorker
from hecate.studio.event_state.models import EventModel
from hecate.studio.event_state.postgres_store import PostgresEventStore
from tests.test_runtime.test_g2_recovery_states import _execution_context, _payload, _StubPort

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("HECATE_TEST_POSTGRES_URL"), reason="requires disposable PostgreSQL test database"
    ),
]


@pytest_asyncio.fixture
async def postgres_stores():
    """Use independent single-connection pools to expose leaked session locks."""
    url = os.environ["HECATE_TEST_POSTGRES_URL"]
    first = create_async_engine(url, pool_size=2, max_overflow=0)
    second = create_async_engine(url, pool_size=2, max_overflow=0)
    try:
        async with first.begin() as connection:
            await connection.run_sync(EventModel.__table__.create, checkfirst=True)
        yield PostgresEventStore(async_sessionmaker(first)), PostgresEventStore(async_sessionmaker(second))
    finally:
        await first.dispose()
        await second.dispose()


@pytest.mark.parametrize("exit_mode", ["normal", "error", "cancel"])
async def test_lock_released_after_transaction_exit(postgres_stores, exit_mode):
    first, second = postgres_stores
    sid = uuid.uuid4()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with first.acquire_event_lock(sid):
            entered.set()
            await release.wait()
            if exit_mode == "error":
                raise ValueError("injected failure")

    task = asyncio.create_task(hold())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(DBAPIError) as error:
            async with second.acquire_event_lock(sid, timeout_ms=40):
                pytest.fail("second connection entered an occupied critical section")
        assert error.value.orig.sqlstate == "55P03"
    finally:
        if exit_mode == "cancel":
            task.cancel()
        else:
            release.set()
        try:
            await task
        except ValueError:
            assert exit_mode == "error"
        except asyncio.CancelledError:
            assert exit_mode == "cancel"
    async with second.acquire_event_lock(sid, timeout_ms=200):
        pass
    async with first.acquire_event_lock(sid, timeout_ms=200):
        pass


async def test_duplicate_workers_claim_once_on_postgres(postgres_stores):
    first, second = postgres_stores
    sid = uuid.uuid4()
    port = _StubPort()
    payload = _payload("same", "memory_add", {"content": "x"})
    ctx = _execution_context(sid)
    await asyncio.gather(
        ToolWorker(port=port, event_store=first).execute("tools", {}, payload, ctx),
        ToolWorker(port=port, event_store=second).execute("tools", {}, payload, ctx),
    )
    assert len(port.calls) == 1
    events = await first.get_events(sid)
    assert [event.event_type for event in events] == [EventType.TOOL_CALL, EventType.TOOL_RESULT]


async def test_concurrent_append_and_batch_have_contiguous_versions(postgres_stores):
    first, second = postgres_stores
    sid = uuid.uuid4()
    events = [Event(session_id=sid, superstep=0, event_type=EventType.CUSTOM) for _ in range(6)]
    await asyncio.gather(first.append_batch(events[:3]), second.append_batch(events[3:5]), first.append(events[5]))
    assert [event.version for event in await first.get_events(sid)] == list(range(1, 7))


async def test_batch_versions_are_per_session(postgres_stores):
    first, _second = postgres_stores
    a, b = uuid.uuid4(), uuid.uuid4()
    await first.append(Event(session_id=a, superstep=0, event_type=EventType.CUSTOM))
    await first.append_batch([Event(session_id=sid, superstep=0, event_type=EventType.CUSTOM) for sid in [b, a, b, a]])
    assert [event.version for event in await first.get_events(a)] == [1, 2, 3]
    assert [event.version for event in await first.get_events(b)] == [1, 2]
