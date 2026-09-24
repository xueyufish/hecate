"""Tests for the pre-compaction flush window mechanism (pre_compaction trigger).

Covers:

- ``mark_unit_flush_window`` — best-effort registration (row written;
  failures swallowed, counted, and non-fatal).
- ``flush_flagged_units`` / ``unit_from_key`` — pending window map keyed
  by unit key.
- Scheduler pickup — a flushed unit is scheduled even without any recall
  activity, sorts with pressure-flag priority, bypasses the quiet filter,
  and its review window extends to the registered window end.
- Consumption — window rows are deleted after the run attempt on both the
  success and failure paths (the watermark keeps coverage; at-least-once).
- Observability counters — registered / consumed / failures assertable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hecate_memory.memory.consolidation import (
    ConsolidationUnit,
    flush_flagged_units,
    flush_metrics,
    mark_unit_flush_window,
    pending_units,
    unit_from_key,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import hecate.core.database as core_db
from hecate.models.memory import ConsolidationFlushWindowModel, ConsolidationRunModel
from tests.conftest import test_session_factory
from tests.test_memory.test_consolidation import (
    _AGENT,
    _NOW,
    _USER,
    _WS,
    _engine,
    _scheduler,
    _seed_transcript,
    _stub_extract,
    _stub_plan,
)


def test_unit_from_key_roundtrip() -> None:
    unit = ConsolidationUnit(_WS, _AGENT, _USER)
    assert unit_from_key(unit.key) == unit
    agent_level = ConsolidationUnit(_WS, _AGENT, None)
    assert unit_from_key(agent_level.key) == agent_level
    assert unit_from_key("not-a-key") is None


def _utc(value: datetime) -> datetime:
    """Normalize a SQLite-read datetime (tzinfo stripped) back to UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def test_mark_flush_window_best_effort(db_session: AsyncSession) -> None:
    assert await mark_unit_flush_window(_WS, _AGENT, _USER, session_factory=test_session_factory) is True
    rows = (
        (await db_session.execute(select(ConsolidationFlushWindowModel).where(~ConsolidationFlushWindowModel.deleted)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].user_id == _USER
    # A duplicate registration is allowed (dedup happens at extraction time
    # via the watermark, not at the table level).
    assert await mark_unit_flush_window(_WS, _AGENT, _USER, session_factory=test_session_factory) is True
    assert len(await _flush_rows(db_session)) == 2


async def _flush_rows(db: AsyncSession) -> list[ConsolidationFlushWindowModel]:
    return (
        (await db.execute(select(ConsolidationFlushWindowModel).where(~ConsolidationFlushWindowModel.deleted)))
        .scalars()
        .all()
    )


async def test_flush_flagged_units_helper(db_session: AsyncSession) -> None:
    end = datetime.now(UTC)
    await mark_unit_flush_window(_WS, _AGENT, None, window_end=end, session_factory=test_session_factory)
    flagged = await flush_flagged_units(db_session)
    assert set(flagged) == {ConsolidationUnit(_WS, _AGENT, None).key}


async def test_flush_window_schedules_unit_without_transcript(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flushed unit is processed even with no recall activity at all."""
    monkeypatch.setattr(core_db, "async_session_factory", test_session_factory)
    assert await pending_units(db_session, now=_NOW) == []  # nothing pending

    registered = await mark_unit_flush_window(
        _WS, _AGENT, _USER, window_end=_NOW - timedelta(minutes=5), session_factory=test_session_factory
    )
    assert registered is True
    await db_session.commit()

    engine = _engine(_stub_extract([]), _stub_plan([]))
    scheduler = _scheduler(engine)
    processed = await scheduler.run_due_units("idle", quiet_seconds=3600, now=_NOW)

    assert processed == 1  # the quiet filter did not suppress the flush
    runs = (await db_session.execute(select(ConsolidationRunModel))).scalars().all()
    assert len(runs) == 1
    # The review window extends to the registered window end, not `now`.
    assert _utc(runs[0].window_end) == _NOW - timedelta(minutes=5)
    # The window row is consumed after the attempt.
    assert await _flush_rows(db_session) == []


async def test_flush_window_extends_existing_pending_window(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When activity exists, the flush end only ever extends the window."""
    monkeypatch.setattr(core_db, "async_session_factory", test_session_factory)
    await _seed_transcript(db_session, created=_NOW - timedelta(hours=1))
    await db_session.commit()

    await mark_unit_flush_window(
        _WS,
        _AGENT,
        _USER,
        window_end=_NOW - timedelta(hours=2),  # older than the transcript
        session_factory=test_session_factory,
    )

    engine = _engine(_stub_extract([]), _stub_plan([]))
    scheduler = _scheduler(engine)
    await scheduler.run_due_units("cron", now=_NOW)

    runs = (await db_session.execute(select(ConsolidationRunModel))).scalars().all()
    assert len(runs) == 1
    assert _utc(runs[0].window_end) == _NOW - timedelta(hours=1)  # transcript wins


async def test_flush_window_consumed_on_failed_run(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Extraction failure still consumes the window; coverage is the watermark."""
    monkeypatch.setattr(core_db, "async_session_factory", test_session_factory)
    await _seed_transcript(db_session)
    await db_session.commit()  # durable across the scheduler's transactions
    before = dict(flush_metrics)

    await mark_unit_flush_window(_WS, _AGENT, _USER, window_end=_NOW, session_factory=test_session_factory)
    await db_session.commit()

    engine = _engine(_stub_extract(RuntimeError("llm down")), _stub_plan([]))
    scheduler = _scheduler(engine)
    processed = await scheduler.run_due_units("cron", now=_NOW)

    assert processed == 1
    runs = (await db_session.execute(select(ConsolidationRunModel))).scalars().all()
    assert runs[0].status == "failed"
    assert await _flush_rows(db_session) == []
    # The unit is still pending (watermark did not advance) — the retry
    # path is the watermark, not the window row.
    assert len(await pending_units(db_session, now=_NOW + timedelta(hours=1))) == 1
    assert flush_metrics["run_failures"] >= before.get("run_failures", 0.0) + 1.0


async def test_flush_metrics_counters(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_db, "async_session_factory", test_session_factory)
    before_registered = flush_metrics["registered"]
    before_consumed = flush_metrics["consumed"]

    await mark_unit_flush_window(_WS, _AGENT, _USER, session_factory=test_session_factory)
    await db_session.commit()
    assert flush_metrics["registered"] == before_registered + 1

    engine = _engine(_stub_extract([]), _stub_plan([]))
    scheduler = _scheduler(engine)
    await scheduler.run_due_units("cron", now=_NOW)
    assert flush_metrics["consumed"] >= before_consumed + 1
    assert flush_metrics["extraction_latency_ms_last"] >= 0.0


async def test_registration_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken session factory must not raise — flush is best-effort."""
    before = flush_metrics["registration_failures"]

    def broken_factory():
        raise RuntimeError("db down")

    ok = await mark_unit_flush_window(_WS, _AGENT, _USER, session_factory=broken_factory)
    assert ok is False
    assert flush_metrics["registration_failures"] == before + 1
