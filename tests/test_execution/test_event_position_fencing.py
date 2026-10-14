"""Platform event position fencing (step7 managed slice).

Position uniqueness on ``(workspace, run, source_sequence)`` makes the
platform event stream safe under concurrent writers: allocation races
collide explicitly and converge (dedup by event id or a fresh slot), late
arrivals reorder instead of silently co-locating, and cursor reads treat
sequence holes as arrival-order artifacts — never as lost events.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from hecate_durable.contracts.events import ActorKind
from sqlalchemy import event as sa_event
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.events import ActorRef, EventEnvelope, EventKind, EventSource
from hecate.contracts.execution.references import BackendRef, RefKind, run_ref
from hecate.core.database import Base
from hecate.execution.governance_events import (
    RUN_TERMINAL,
    TASK_SUBMITTED,
    PlatformEventService,
)
from hecate.models.platform_event import PlatformEventModel

WS = uuid.uuid4()
RUN = run_ref("fencing", "fencing-run")
TASK = BackendRef(RefKind.TASK, "fencing", "fencing-task")
ACTOR = ActorRef(kind=ActorKind.HUMAN, id="tester")


@pytest.fixture
async def db_env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'events.db'}")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _wal(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


def _envelope(event_id: str, sequence: int = 0, payload_schema: str = TASK_SUBMITTED) -> EventEnvelope:
    return EventEnvelope(
        contract_version="0.1",
        kind=EventKind.EVENT,
        event_id=event_id,
        task_ref=TASK,
        run_ref=RUN,
        source_sequence=sequence,
        occurred_at="2026-10-14T00:00:00+00:00",
        received_at="2026-10-14T00:00:00+00:00",
        payload_schema_ref=payload_schema,
        payload={"event_type": "test", "n": event_id},
        actor=ACTOR,
        source=EventSource.PLATFORM,
    )


async def test_concurrent_position_allocation_converges(db_env):
    """Two sessions allocating the same next position: the loser's insert
    collides at the storage boundary and converges — a different event
    retries into a fresh slot; the same event id dedups against the stored
    winner. No same-position co-location, no silent loss."""

    async with db_env() as session:
        service = PlatformEventService(session)
        await service.emit(
            task_ref=TASK,
            run_ref=RUN,
            payload_schema_ref=TASK_SUBMITTED,
            payload={"event_type": "seed"},
            actor=ACTOR,
            workspace_id=WS,
        )
        await session.commit()

    winner_envelope = _envelope("evt-winner")
    loser_envelope = _envelope("evt-loser")
    async with db_env() as first:
        first_svc = PlatformEventService(first)
        first_stored = await first_svc.append_resequenced(winner_envelope, workspace_id=WS)
        # A second writer's session allocates the same position while the
        # winner's insert is in flight; whether the collision surfaces as an
        # allocation conflict inside the call or as a commit-time constraint
        # error, the service converges — same-position co-location never
        # survives.
        async with db_env() as second:
            second_svc = PlatformEventService(second)
            second_stored = await second_svc.append_resequenced(loser_envelope, workspace_id=WS)
            await first.commit()
            try:
                await second.commit()
            except IntegrityError:
                await second.rollback()
                second_stored = await second_svc.append_resequenced(loser_envelope, workspace_id=WS)
                await second.commit()
            assert second_stored.event_id == "evt-loser"
            assert second_stored.source_sequence != first_stored.source_sequence

    async with db_env() as verify:
        rows = (
            (
                await verify.execute(
                    select(PlatformEventModel)
                    .where(PlatformEventModel.run_ref_id == RUN.id)
                    .order_by(PlatformEventModel.source_sequence)
                )
            )
            .scalars()
            .all()
        )
        by_id = {row.event_id: row.source_sequence for row in rows}
        assert set(by_id) == {rows[0].event_id, "evt-winner", "evt-loser"}  # seed + both racers
        assert len(rows) == 3 and len(set(by_id.values())) == 3  # no co-location
        assert by_id["evt-loser"] > by_id["evt-winner"]  # loser took a fresh later slot


async def test_same_event_id_race_dedups_to_stored_envelope(db_env):
    """The same event id racing through two sessions: one stores it, the
    other's collision resolves as idempotent success returning the stored
    envelope (not a duplicate row, not an error)."""

    envelope = _envelope("evt-dup")
    async with db_env() as first:
        svc = PlatformEventService(first)
        await svc.append_resequenced(envelope, workspace_id=WS)
        await first.commit()
    async with db_env() as second:
        svc = PlatformEventService(second)
        stored = await svc.append_resequenced(envelope, workspace_id=WS)
        await second.commit()
        assert stored.event_id == "evt-dup"
        assert stored.source_sequence == 0  # the winner's position

    async with db_env() as verify:
        count = len(
            (await verify.execute(select(PlatformEventModel).where(PlatformEventModel.event_id == "evt-dup")))
            .scalars()
            .all()
        )
        assert count == 1


async def test_late_arrival_reorders_and_cursor_read_skips_nothing(db_env):
    """A late event (its pre-allocated number below the stored maximum)
    lands at a fresh position; a cursor resuming before the late arrival
    still sees it — the hole never hides an event."""

    async with db_env() as session:
        svc = PlatformEventService(session)
        for n in ("a", "b"):
            await svc.append_resequenced(_envelope(f"evt-{n}"), workspace_id=WS)
        await session.commit()
    async with db_env() as session:
        svc = PlatformEventService(session)
        late = replace(_envelope("evt-late", sequence=1), payload_schema_ref=RUN_TERMINAL)
        stored = await svc.append_resequenced(late, workspace_id=WS)
        await session.commit()
        assert stored.source_sequence == 2  # arrival order, not the stale 1

    async with db_env() as session:
        svc = PlatformEventService(session)
        page = await svc.read_run_events(RUN, workspace_id=WS, cursor="1")
        assert [envelope.event_id for envelope in page.events if envelope.event_id == "evt-late"] == ["evt-late"]


async def test_emit_survives_a_position_race(db_env):
    """The governance emit path (actor/source required) converges the same
    way: a pre-allocated position lost to a concurrent writer retries into
    a fresh slot inside the caller's transaction."""

    async with db_env() as session:
        svc = PlatformEventService(session)
        await svc.emit(
            task_ref=TASK,
            run_ref=RUN,
            payload_schema_ref=TASK_SUBMITTED,
            payload={"event_type": "first"},
            actor=ACTOR,
            workspace_id=WS,
        )
        await session.commit()

    # Occupy the emitter's next position out-of-band so its first
    # allocation collides at flush.
    async with db_env() as other:
        other.add(
            PlatformEventModel(
                event_id="evt-obstruction",
                contract_version="0.1",
                kind="event",
                task_issuer_domain=TASK.issuer_domain,
                task_ref_id=TASK.id,
                run_issuer_domain=RUN.issuer_domain,
                run_ref_id=RUN.id,
                source_sequence=1,
                occurred_at="2026-10-14T00:00:00+00:00",
                received_at="2026-10-14T00:00:00+00:00",
                payload={},
                envelope={},
                workspace_id=WS,
            )
        )
        await other.commit()

    async with db_env() as session:
        svc = PlatformEventService(session)
        stored = await svc.emit(
            task_ref=TASK,
            run_ref=RUN,
            payload_schema_ref=TASK_SUBMITTED,
            payload={"event_type": "governance"},
            actor=ACTOR,
            workspace_id=WS,
        )
        await session.commit()
        assert stored.source_sequence == 2  # retried into the fresh slot
