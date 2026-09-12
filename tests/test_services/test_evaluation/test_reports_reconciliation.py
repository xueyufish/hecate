"""Tests for human-override reconciliation in the report service (7.4a).

Rule under test: the latest human override replaces the automated row it
supersedes in overview/trends/distributions/session-rollup aggregates; the
overridden automated row and non-override human rows never contribute.
The breakdowns endpoint stays raw per source.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import EvaluationTaskModel, EvaluationTaskScoreModel, TaskType
from hecate.ops.evaluation.reports.service import EvaluationReportService

_WS = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
_AGENT = uuid.UUID("00000000-0000-0000-0000-0000000000cc")
_SESSION = uuid.UUID("00000000-0000-0000-0000-0000000000dd")
_BASE = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


async def _seed_task(db_session: AsyncSession) -> EvaluationTaskModel:
    task = EvaluationTaskModel(
        task_type=TaskType.ONLINE.value,
        name="prod-scoring",
        evaluator_configs=["stub"],
        config={"agent_id": str(_AGENT), "sampling_rate": 1.0},
        workspace_id=_WS,
    )
    db_session.add(task)
    await db_session.flush()
    return task


async def _seed_score(
    db_session: AsyncSession,
    task: EvaluationTaskModel,
    target_id: uuid.UUID,
    *,
    metric_name: str = "helpfulness",
    value: float = 0.5,
    source: str = "llm_judge",
    created_at: datetime | None = None,
    overrides_score_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = _SESSION,
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=None if source == "human" else task.id,
        target_id=target_id,
        session_id=session_id,
        agent_id=_AGENT,
        metric_name=metric_name,
        value=value,
        source=source,
        overrides_score_id=overrides_score_id,
        created_at=created_at or _BASE,
        workspace_id=_WS,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def test_overview_quality_uses_override(db_session: AsyncSession) -> None:
    task = await _seed_task(db_session)
    trace_a, trace_b, trace_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    machine = await _seed_score(db_session, task, trace_a, value=0.35)
    await _seed_score(db_session, task, trace_b, value=0.6)
    await _seed_score(db_session, task, trace_c, value=0.8)
    await _seed_score(
        db_session,
        task,
        trace_a,
        value=0.9,
        source="human",
        overrides_score_id=machine.id,
        created_at=_BASE + timedelta(hours=1),
    )

    report = await EvaluationReportService(db_session).overview(workspace_id=_WS)
    assert report.quality.online_avg_score == round((0.9 + 0.6 + 0.8) / 3, 4)
    assert report.volume.online_scored == 3
    assert report.quality.note is not None


async def test_non_override_human_row_not_double_counted(db_session: AsyncSession) -> None:
    task = await _seed_task(db_session)
    trace = uuid.uuid4()
    await _seed_score(db_session, task, trace, value=0.8)
    await _seed_score(db_session, task, trace, value=0.8, source="human", created_at=_BASE + timedelta(hours=1))

    report = await EvaluationReportService(db_session).overview(workspace_id=_WS)
    assert report.volume.online_scored == 1
    assert report.quality.online_avg_score == 0.8


async def test_trends_attribute_override_to_its_own_bucket(db_session: AsyncSession) -> None:
    task = await _seed_task(db_session)
    trace = uuid.uuid4()
    machine = await _seed_score(db_session, task, trace, value=0.35, created_at=_BASE)
    await _seed_score(
        db_session,
        task,
        trace,
        value=0.9,
        source="human",
        overrides_score_id=machine.id,
        created_at=_BASE + timedelta(days=3),
    )

    report = await EvaluationReportService(db_session).trends(
        workspace_id=_WS,
        dimension="agent",
        start_date=_BASE - timedelta(days=1),
        end_date=_BASE + timedelta(days=5),
    )
    online_series = [s for s in report.series if s.kind == "online"]
    assert len(online_series) == 1
    buckets = {p.bucket: p.value for p in online_series[0].points}
    assert _BASE.strftime("%Y-%m-%d") not in buckets  # original score gone
    override_day = (_BASE + timedelta(days=3)).strftime("%Y-%m-%d")
    assert buckets[override_day] == 0.9


async def test_distributions_task_scope_bins_reconciled_once(db_session: AsyncSession) -> None:
    task = await _seed_task(db_session)
    trace = uuid.uuid4()
    machine = await _seed_score(db_session, task, trace, value=0.35)
    await _seed_score(
        db_session,
        task,
        trace,
        value=0.9,
        source="human",
        overrides_score_id=machine.id,
        created_at=_BASE + timedelta(hours=1),
    )

    report = await EvaluationReportService(db_session).distributions(workspace_id=_WS, task_id=task.id)
    metric = report.metrics[0]
    assert metric.count == 1
    assert metric.bins[9].count == 1  # [0.9, 1.0]
    assert metric.bins[3].count == 0


async def test_session_rollup_includes_override(db_session: AsyncSession) -> None:
    task = await _seed_task(db_session)
    trace = uuid.uuid4()
    machine = await _seed_score(db_session, task, trace, value=0.2)
    await _seed_score(db_session, task, uuid.uuid4(), value=0.6)
    await _seed_score(db_session, task, uuid.uuid4(), value=1.0)
    await _seed_score(
        db_session,
        task,
        trace,
        value=0.8,
        source="human",
        overrides_score_id=machine.id,
        created_at=_BASE + timedelta(hours=2),
    )

    report = await EvaluationReportService(db_session).session_rollup(workspace_id=_WS)
    # All three traces share one session; the override replaces 0.2 in the average.
    assert report.total == 1
    session_item = report.items[0]
    assert session_item.trace_count == 3
    helpfulness_rows = [m for m in session_item.metrics if m.metric_name == "helpfulness"]
    assert len(helpfulness_rows) == 1
    assert helpfulness_rows[0].avg == round((0.8 + 0.6 + 1.0) / 3, 4)


async def test_breakdowns_stay_raw_per_source(db_session: AsyncSession) -> None:
    task = await _seed_task(db_session)
    trace = uuid.uuid4()
    machine = await _seed_score(db_session, task, trace, value=0.35)
    await _seed_score(
        db_session,
        task,
        trace,
        value=0.9,
        source="human",
        overrides_score_id=machine.id,
        created_at=_BASE + timedelta(hours=1),
    )

    report = await EvaluationReportService(db_session).breakdowns(workspace_id=_WS, group_by="source")
    by_source = {g.key: g.metrics for g in report.groups}
    assert by_source["llm_judge"][0].count == 1 and by_source["llm_judge"][0].avg == 0.35
    assert by_source["human"][0].count == 1 and by_source["human"][0].avg == 0.9
