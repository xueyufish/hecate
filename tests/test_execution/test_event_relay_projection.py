"""Outbox relay projection tests (step6 worker change).

The durable event log is the authoritative outbox; the platform read model
(``platform_events``) is fed by the relay. These tests run a real
``OutboxRelay`` with the platform projector against one file-backed SQLite
database holding both schemas: governance events committed with the seam
state writes must appear in the read model exactly once, workspace-scoped,
with re-sequenced monotonic streams — and a relay outage loses nothing.
"""

from __future__ import annotations

import uuid

import pytest
from hecate_durable.storage import SqlDurableStore
from hecate_durable.worker import OutboxRelay
from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import IdempotencyKey, TaskLifecycleState
from hecate.contracts.execution.references import BackendRef, RefKind, run_ref, task_ref
from hecate.core.database import Base
from hecate.execution.event_relay import PlatformOutboxProjector
from hecate.execution.governance_events import TASK_STATE_CHANGED, TASK_SUBMITTED, PlatformEventService

WS = uuid.uuid4()


@pytest.fixture
async def env(tmp_path):
    db_file = tmp_path / "relay.db"
    dsn = f"sqlite:///{db_file}"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")

    @sa_event.listens_for(async_engine.sync_engine, "connect")
    def _wal_async(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    store = SqlDurableStore(dsn, source="platform")

    @sa_event.listens_for(store.engine, "connect")
    def _wal_sync(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    store.create_schema()
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    t = task_ref("host", "relay-task")
    r = run_ref("host", "relay-run")
    store.submit_task(
        key=IdempotencyKey(key="relay-key", subject="s", workspace=str(WS), request_digest="d" * 8),
        task_ref=t,
        run_ref=r,
        input_payload={"goal": "relay", "workspace_id": str(WS)},
    )
    store.apply_task_state(t, TaskLifecycleState.RUNNING)
    store.apply_task_state(t, TaskLifecycleState.FAILED)

    yield {"store": store, "session_factory": session_factory}

    await async_engine.dispose()
    store.dispose()


async def test_governance_events_project_once_with_resequencing(env):
    projector = PlatformOutboxProjector(env["store"], env["session_factory"])
    relay = OutboxRelay(env["store"].session_factory, projector, relay_key="test")

    projected = await relay.pump_once()
    assert projected == 3  # submitted + two state transitions
    assert await relay.pump_once() == 0  # idempotent: no double projection

    async with env["session_factory"]() as db:
        page = await PlatformEventService(db).read_run_events(
            BackendRef(RefKind.RUN, "host", "relay-run"), workspace_id=WS
        )
        schemas = [e.payload_schema_ref for e in page.events]
        assert schemas[0] == TASK_SUBMITTED
        assert TASK_STATE_CHANGED in schemas
        sequences = [e.source_sequence for e in page.events]
        assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)


async def test_relay_outage_then_recovery_projects_everything(env):
    projector = PlatformOutboxProjector(env["store"], env["session_factory"])

    class FlakyProjector:
        def __init__(self, inner) -> None:
            self.inner = inner
            self.down = True

        async def __call__(self, envelope: dict) -> None:
            if self.down:
                raise RuntimeError("read model down")
            await self.inner(envelope)

    flaky = FlakyProjector(projector)
    relay = OutboxRelay(env["store"].session_factory, flaky, relay_key="test", max_consecutive_failures=50)
    assert await relay.pump_once() == 0  # outage: nothing projected, nothing lost
    flaky.down = False
    assert await relay.pump_once() == 3  # full recovery
    async with env["session_factory"]() as db:
        page = await PlatformEventService(db).read_run_events(
            BackendRef(RefKind.RUN, "host", "relay-run"), workspace_id=WS
        )
        assert len(list(page.events)) == 3
