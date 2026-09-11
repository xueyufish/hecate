"""Evaluation report aggregation service (7.2e Evaluation Report Dashboard).

Read-only, computed-on-demand aggregation over the existing evaluation
tables — no new persistence: ``evaluation_runs.summary`` already holds the
pass-rate aggregation computed at run completion, and
``evaluation_task_scores`` carries denormalized ``session_id``/``agent_id``
for rollups, so every report is a plain SELECT away.

Conventions (openspec change ``evaluation-report-dashboard``):

- **Window** — ``start_date``/``end_date`` optional, default last 30 days,
  clamped to at most 365 days; ``hour`` bucketing requires a window of at
  most 30 days. All bucketing is done in UTC; datetime bounds are bound to
  the database as naive UTC so SQLite (server_default ``CURRENT_TIMESTAMP``,
  naive) and PostgreSQL (``timestamptz``) behave identically.
- **Error sentinel** — evaluator failures persist ``value = -1.0``;
  aggregations exclude those rows and surface them as error counts.
- **Offline trends** derive from ``evaluation_runs.summary`` (pass_rate was
  computed at completion) instead of scanning ``evaluation_scores``, which
  has no ``created_at`` index.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel as PydanticBase
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    RunStatus,
)

_DEFAULT_WINDOW_DAYS = 30
_MAX_WINDOW_DAYS = 365
_MAX_HOUR_WINDOW_DAYS = 30
_LOW_SAMPLE_ITEM_THRESHOLD = 20
_ERROR_SENTINEL = -1.0
_HISTOGRAM_BIN_COUNT = 10

BreakdownDimension = str  # "agent" | "task" | "session" | "source"
TrendDimension = str  # "dataset" | "workflow" | "agent"


class EvaluationReportValidationError(ValueError):
    """Report request violates a window/scope rule."""


class EvaluationReportNotFoundError(LookupError):
    """The run/task referenced by a report scope does not exist in the workspace."""


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class QualityCardSchema(PydanticBase):
    offline_pass_rate: float | None = None
    offline_runs_counted: int = 0
    online_avg_score: float | None = None


class VolumeCardSchema(PydanticBase):
    completed_runs: int = 0
    online_scored: int = 0


class CoverageCardSchema(PydanticBase):
    active_datasets: int = 0
    median_items: float | None = None
    low_sample_run_ratio: float = 0.0


class ErrorRateCardSchema(PydanticBase):
    total_scores: int = 0
    error_count: int = 0
    ratio: float = 0.0


class OverviewReportSchema(PydanticBase):
    window_start: datetime
    window_end: datetime
    quality: QualityCardSchema
    volume: VolumeCardSchema
    coverage: CoverageCardSchema
    error_rate: ErrorRateCardSchema


class TrendPointSchema(PydanticBase):
    bucket: str
    value: float
    count: int


class TrendSeriesSchema(PydanticBase):
    group_id: str
    kind: str  # "offline" | "online"
    metric: str
    points: list[TrendPointSchema]


class TrendsReportSchema(PydanticBase):
    dimension: str
    bucket: str
    series: list[TrendSeriesSchema]


class HistogramBinSchema(PydanticBase):
    lower: float
    upper: float
    count: int


class MetricDistributionSchema(PydanticBase):
    metric_name: str
    bins: list[HistogramBinSchema]
    count: int
    error_count: int
    mean: float | None = None
    min: float | None = None
    max: float | None = None


class DistributionsReportSchema(PydanticBase):
    scope: str  # "run" | "task"
    scope_id: uuid.UUID
    metrics: list[MetricDistributionSchema]


class BreakdownMetricSchema(PydanticBase):
    metric_name: str
    avg: float
    count: int


class BreakdownGroupSchema(PydanticBase):
    key: str
    metrics: list[BreakdownMetricSchema]


class BreakdownsReportSchema(PydanticBase):
    group_by: str
    groups: list[BreakdownGroupSchema]
    total: int


class SessionRollupItemSchema(PydanticBase):
    session_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    trace_count: int
    last_scored_at: datetime
    metrics: list[BreakdownMetricSchema]


class SessionRollupReportSchema(PydanticBase):
    items: list[SessionRollupItemSchema]
    total: int


# ---------------------------------------------------------------------------
# Window / bucket helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportWindow:
    """Resolved UTC window bounds (aware)."""

    start: datetime
    end: datetime


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime to aware UTC; naive values are assumed UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _naive_utc(value: datetime) -> datetime:
    """Strip tzinfo after converting to UTC, matching server_default storage."""
    return _as_utc(value).replace(tzinfo=None)


def resolve_window(
    start_date: datetime | None,
    end_date: datetime | None,
    max_days: int = _MAX_WINDOW_DAYS,
) -> ReportWindow:
    """Resolve the report window, clamping the span to ``max_days``."""
    end = _as_utc(end_date) if end_date is not None else datetime.now(UTC)
    start = _as_utc(start_date) if start_date is not None else end - timedelta(days=_DEFAULT_WINDOW_DAYS)
    if start > end:
        raise EvaluationReportValidationError("start_date must be before end_date")
    if end - start > timedelta(days=max_days):
        start = end - timedelta(days=max_days)
    return ReportWindow(start=start, end=end)


def _bucket_key(value: datetime, bucket: str) -> str:
    value_utc = _as_utc(value)
    if bucket == "hour":
        return value_utc.strftime("%Y-%m-%dT%H:00")
    return value_utc.strftime("%Y-%m-%d")


def _in_window(column: Any, window: ReportWindow) -> tuple[Any, Any]:
    start = _naive_utc(window.start)
    end = _naive_utc(window.end)
    return column >= start, column <= end


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class EvaluationReportService:
    """On-demand aggregation queries behind the evaluation report endpoints."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -- overview -----------------------------------------------------------

    async def overview(
        self,
        workspace_id: uuid.UUID,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> OverviewReportSchema:
        """Aggregate the four overview cards for the workspace window."""
        window = resolve_window(start_date, end_date)
        start_cond, end_cond = _in_window(EvaluationRunModel.created_at, window)

        runs = (
            (
                await self.db.execute(
                    select(EvaluationRunModel).where(
                        EvaluationRunModel.workspace_id == workspace_id,
                        ~EvaluationRunModel.deleted,
                        EvaluationRunModel.status == RunStatus.COMPLETED.value,
                        start_cond,
                        end_cond,
                    )
                )
            )
            .scalars()
            .all()
        )

        pass_rates = [
            float(run.summary["pass_rate"])
            for run in runs
            if isinstance(run.summary, dict) and isinstance(run.summary.get("pass_rate"), (int, float))
        ]

        offline_total, offline_errors, _offline_avg = await self._score_stats(
            EvaluationScoreModel, workspace_id, window
        )
        online_total, online_errors, online_avg = await self._score_stats(
            EvaluationTaskScoreModel, workspace_id, window
        )

        total_scores = offline_total + online_total
        error_count = offline_errors + online_errors
        error_ratio = round(error_count / total_scores, 4) if total_scores else 0.0

        dataset_ids = {run.dataset_id for run in runs}
        item_counts = await self._dataset_item_counts(workspace_id, dataset_ids)
        low_sample_runs = sum(1 for run in runs if item_counts.get(run.dataset_id, 0) < _LOW_SAMPLE_ITEM_THRESHOLD)
        low_sample_ratio = round(low_sample_runs / len(runs), 4) if runs else 0.0

        return OverviewReportSchema(
            window_start=window.start,
            window_end=window.end,
            quality=QualityCardSchema(
                offline_pass_rate=_mean(pass_rates),
                offline_runs_counted=len(pass_rates),
                online_avg_score=online_avg,
            ),
            volume=VolumeCardSchema(
                completed_runs=len(runs),
                online_scored=online_total - online_errors,
            ),
            coverage=CoverageCardSchema(
                active_datasets=len(dataset_ids),
                median_items=float(statistics.median(item_counts.values())) if item_counts else None,
                low_sample_run_ratio=low_sample_ratio,
            ),
            error_rate=ErrorRateCardSchema(
                total_scores=total_scores,
                error_count=error_count,
                ratio=error_ratio,
            ),
        )

    async def _score_stats(
        self,
        model: type[EvaluationScoreModel] | type[EvaluationTaskScoreModel],
        workspace_id: uuid.UUID,
        window: ReportWindow,
    ) -> tuple[int, int, float | None]:
        """Return (total, error_count, avg of non-error values) for a score table."""
        start_cond, end_cond = _in_window(model.created_at, window)
        row = (
            await self.db.execute(
                select(
                    func.count(),
                    func.count(case((model.value == _ERROR_SENTINEL, 1))),
                    func.avg(case((model.value != _ERROR_SENTINEL, model.value))),
                ).where(
                    model.workspace_id == workspace_id,
                    ~model.deleted,
                    start_cond,
                    end_cond,
                )
            )
        ).one()
        total = int(row[0] or 0)
        errors = int(row[1] or 0)
        avg = round(float(row[2]), 4) if row[2] is not None else None
        return total, errors, avg

    async def _dataset_item_counts(
        self,
        workspace_id: uuid.UUID,
        dataset_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, int]:
        if not dataset_ids:
            return {}
        rows = await self.db.execute(
            select(EvaluationItemModel.dataset_id, func.count())
            .where(
                EvaluationItemModel.workspace_id == workspace_id,
                ~EvaluationItemModel.deleted,
                EvaluationItemModel.dataset_id.in_(dataset_ids),
            )
            .group_by(EvaluationItemModel.dataset_id)
        )
        return {row[0]: int(row[1]) for row in rows.all()}

    # -- trends ---------------------------------------------------------------

    async def trends(
        self,
        workspace_id: uuid.UUID,
        dimension: TrendDimension,
        bucket: str = "day",
        metric_name: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> TrendsReportSchema:
        """Per-bucket timeseries grouped by dataset, workflow, or agent."""
        if bucket not in ("day", "hour"):
            raise EvaluationReportValidationError("bucket must be 'day' or 'hour'")
        if dimension not in ("dataset", "workflow", "agent"):
            raise EvaluationReportValidationError("dimension must be 'dataset', 'workflow', or 'agent'")

        max_days = _MAX_HOUR_WINDOW_DAYS if bucket == "hour" else _MAX_WINDOW_DAYS
        window = resolve_window(start_date, end_date)
        if bucket == "hour" and window.end - window.start > timedelta(days=max_days):
            raise EvaluationReportValidationError(f"hour bucketing requires a window of at most {max_days} days")

        if dimension == "agent":
            series = await self._online_trends(workspace_id, window, bucket, metric_name)
        else:
            series = await self._offline_trends(workspace_id, window, bucket, dimension)
        return TrendsReportSchema(dimension=dimension, bucket=bucket, series=series)

    async def _offline_trends(
        self,
        workspace_id: uuid.UUID,
        window: ReportWindow,
        bucket: str,
        dimension: TrendDimension,
    ) -> list[TrendSeriesSchema]:
        start_cond, end_cond = _in_window(EvaluationRunModel.completed_at, window)
        runs = (
            (
                await self.db.execute(
                    select(EvaluationRunModel).where(
                        EvaluationRunModel.workspace_id == workspace_id,
                        ~EvaluationRunModel.deleted,
                        EvaluationRunModel.status == RunStatus.COMPLETED.value,
                        EvaluationRunModel.completed_at.is_not(None),
                        start_cond,
                        end_cond,
                    )
                )
            )
            .scalars()
            .all()
        )

        grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
        for run in runs:
            group_id = run.dataset_id if dimension == "dataset" else run.workflow_id
            if group_id is None:
                continue
            if not (isinstance(run.summary, dict) and isinstance(run.summary.get("pass_rate"), (int, float))):
                continue
            grouped[(str(group_id), _bucket_key(run.completed_at, bucket))].append(float(run.summary["pass_rate"]))
        return _points_to_series(grouped, kind="offline", metric="pass_rate")

    async def _online_trends(
        self,
        workspace_id: uuid.UUID,
        window: ReportWindow,
        bucket: str,
        metric_name: str | None,
    ) -> list[TrendSeriesSchema]:
        """Per-agent per-metric score averages over time (dimension=agent)."""
        start_cond, end_cond = _in_window(EvaluationTaskScoreModel.created_at, window)
        conditions: list[Any] = [
            EvaluationTaskScoreModel.workspace_id == workspace_id,
            ~EvaluationTaskScoreModel.deleted,
            EvaluationTaskScoreModel.agent_id.is_not(None),
            EvaluationTaskScoreModel.value != _ERROR_SENTINEL,
            start_cond,
            end_cond,
        ]
        if metric_name is not None:
            conditions.append(EvaluationTaskScoreModel.metric_name == metric_name)
        rows = await self.db.execute(
            select(
                EvaluationTaskScoreModel.agent_id,
                EvaluationTaskScoreModel.metric_name,
                EvaluationTaskScoreModel.created_at,
                EvaluationTaskScoreModel.value,
            ).where(*conditions)
        )
        # One series per (agent, metric) pair keeps distinct metrics separate.
        pair_buckets: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for agent_id, metric, created_at, value in rows.all():
            pair_buckets[(str(agent_id), metric)][_bucket_key(created_at, bucket)].append(float(value))

        return [
            TrendSeriesSchema(
                group_id=agent_id,
                kind="online",
                metric=metric,
                points=[
                    TrendPointSchema(bucket=bucket_key, value=round(sum(values) / len(values), 4), count=len(values))
                    for bucket_key, values in sorted(buckets.items())
                ],
            )
            for (agent_id, metric), buckets in sorted(pair_buckets.items())
        ]

    # -- distributions --------------------------------------------------------

    async def distributions(
        self,
        workspace_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        metric_name: str | None = None,
    ) -> DistributionsReportSchema:
        """Histogram per metric for one run (offline) or one task (online)."""
        if run_id is not None and task_id is not None:
            raise EvaluationReportValidationError("only one of run_id or task_id may be provided")

        if run_id is not None:
            run = (
                await self.db.execute(
                    select(EvaluationRunModel).where(
                        EvaluationRunModel.id == run_id,
                        EvaluationRunModel.workspace_id == workspace_id,
                        ~EvaluationRunModel.deleted,
                    )
                )
            ).scalar_one_or_none()
            if run is None:
                raise EvaluationReportNotFoundError(str(run_id))
            rows = await self.db.execute(
                select(EvaluationScoreModel.metric_name, EvaluationScoreModel.value).where(
                    EvaluationScoreModel.workspace_id == workspace_id,
                    EvaluationScoreModel.run_id == run_id,
                    ~EvaluationScoreModel.deleted,
                )
            )
            return self._histogram_report("run", run_id, rows, metric_name)

        if task_id is not None:
            task = (
                await self.db.execute(
                    select(EvaluationTaskModel).where(
                        EvaluationTaskModel.id == task_id,
                        EvaluationTaskModel.workspace_id == workspace_id,
                        ~EvaluationTaskModel.deleted,
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                raise EvaluationReportNotFoundError(str(task_id))
            rows = await self.db.execute(
                select(EvaluationTaskScoreModel.metric_name, EvaluationTaskScoreModel.value).where(
                    EvaluationTaskScoreModel.workspace_id == workspace_id,
                    EvaluationTaskScoreModel.task_id == task_id,
                    ~EvaluationTaskScoreModel.deleted,
                )
            )
            return self._histogram_report("task", task_id, rows, metric_name)

        raise EvaluationReportValidationError("exactly one of run_id or task_id is required")

    def _histogram_report(
        self,
        scope: str,
        scope_id: uuid.UUID,
        rows: Any,
        metric_name: str | None,
    ) -> DistributionsReportSchema:
        """Bin (metric, value) rows into per-metric histograms, errors counted apart."""
        by_metric: dict[str, list[float]] = defaultdict(list)
        errors: dict[str, int] = defaultdict(int)
        for metric, value in rows.all():
            if value == _ERROR_SENTINEL:
                errors[metric] += 1
            else:
                by_metric[metric].append(float(value))

        if metric_name is not None:
            metrics = [_histogram(metric_name, by_metric.get(metric_name, []), errors.get(metric_name, 0))]
        else:
            metrics = [
                _histogram(metric, values, errors.get(metric, 0)) for metric, values in sorted(by_metric.items())
            ]
        return DistributionsReportSchema(scope=scope, scope_id=scope_id, metrics=metrics)

    # -- breakdowns -----------------------------------------------------------

    async def breakdowns(
        self,
        workspace_id: uuid.UUID,
        group_by: BreakdownDimension,
        metric_name: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> BreakdownsReportSchema:
        """Group online task scores by agent/task/session/source with per-metric averages."""
        columns: dict[str, Any] = {
            "agent": EvaluationTaskScoreModel.agent_id,
            "task": EvaluationTaskScoreModel.task_id,
            "session": EvaluationTaskScoreModel.session_id,
            "source": EvaluationTaskScoreModel.source,
        }
        if group_by not in columns:
            raise EvaluationReportValidationError("group_by must be 'agent', 'task', 'session', or 'source'")
        group_col = columns[group_by]

        window = resolve_window(start_date, end_date)
        start_cond, end_cond = _in_window(EvaluationTaskScoreModel.created_at, window)
        conditions: list[Any] = [
            EvaluationTaskScoreModel.workspace_id == workspace_id,
            ~EvaluationTaskScoreModel.deleted,
            EvaluationTaskScoreModel.value != _ERROR_SENTINEL,
            start_cond,
            end_cond,
        ]
        if metric_name is not None:
            conditions.append(EvaluationTaskScoreModel.metric_name == metric_name)

        rows = await self.db.execute(
            select(
                group_col,
                EvaluationTaskScoreModel.metric_name,
                func.avg(EvaluationTaskScoreModel.value),
                func.count(),
            )
            .where(*conditions)
            .group_by(group_col, EvaluationTaskScoreModel.metric_name)
        )

        grouped: dict[str, dict[str, tuple[float, int]]] = defaultdict(dict)
        for key, metric, avg, count in rows.all():
            grouped[str(key) if key is not None else "none"][metric] = (float(avg), int(count))

        groups = [
            BreakdownGroupSchema(
                key=key,
                metrics=[
                    BreakdownMetricSchema(metric_name=metric, avg=round(avg, 4), count=count)
                    for metric, (avg, count) in sorted(metrics.items())
                ],
            )
            for key, metrics in grouped.items()
        ]
        groups.sort(key=lambda g: (-sum(m.count for m in g.metrics), g.key))
        return BreakdownsReportSchema(group_by=group_by, groups=groups, total=len(groups))

    # -- session rollup -------------------------------------------------------

    async def session_rollup(
        self,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> SessionRollupReportSchema:
        """Roll online task scores up to session granularity, newest-scored first."""
        window = resolve_window(start_date, end_date)
        start_cond, end_cond = _in_window(EvaluationTaskScoreModel.created_at, window)
        conditions: list[Any] = [
            EvaluationTaskScoreModel.workspace_id == workspace_id,
            ~EvaluationTaskScoreModel.deleted,
            EvaluationTaskScoreModel.session_id.is_not(None),
            start_cond,
            end_cond,
        ]
        if task_id is not None:
            conditions.append(EvaluationTaskScoreModel.task_id == task_id)

        session_rows = await self.db.execute(
            select(
                EvaluationTaskScoreModel.session_id,
                func.max(EvaluationTaskScoreModel.agent_id),
                func.count(func.distinct(EvaluationTaskScoreModel.target_id)),
                func.max(EvaluationTaskScoreModel.created_at),
            )
            .where(*conditions)
            .group_by(EvaluationTaskScoreModel.session_id)
            .order_by(func.max(EvaluationTaskScoreModel.created_at).desc())
        )
        all_sessions = list(session_rows.all())
        total = len(all_sessions)

        offset = (page - 1) * page_size
        page_sessions = all_sessions[offset : offset + page_size]
        if not page_sessions:
            return SessionRollupReportSchema(items=[], total=total)

        page_ids = [row[0] for row in page_sessions]
        metric_rows = await self.db.execute(
            select(
                EvaluationTaskScoreModel.session_id,
                EvaluationTaskScoreModel.metric_name,
                func.avg(EvaluationTaskScoreModel.value),
                func.count(),
            )
            .where(
                *conditions,
                EvaluationTaskScoreModel.session_id.in_(page_ids),
                EvaluationTaskScoreModel.value != _ERROR_SENTINEL,
            )
            .group_by(EvaluationTaskScoreModel.session_id, EvaluationTaskScoreModel.metric_name)
        )
        metrics_by_session: dict[uuid.UUID, list[BreakdownMetricSchema]] = defaultdict(list)
        for session_id, metric, avg, count in metric_rows.all():
            metrics_by_session[session_id].append(
                BreakdownMetricSchema(metric_name=metric, avg=round(float(avg), 4), count=int(count))
            )

        items = [
            SessionRollupItemSchema(
                session_id=row[0],
                agent_id=row[1],
                trace_count=int(row[2]),
                last_scored_at=row[3],
                metrics=sorted(metrics_by_session.get(row[0], []), key=lambda m: m.metric_name),
            )
            for row in page_sessions
        ]
        return SessionRollupReportSchema(items=items, total=total)


def _points_to_series(
    grouped: dict[tuple[str, str], list[float]],
    kind: str,
    metric: str,
) -> list[TrendSeriesSchema]:
    """Merge (group_id, bucket) -> values into one sorted series per group."""
    by_group: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for (group_id, bucket_key), values in grouped.items():
        by_group[group_id][bucket_key].extend(values)
    return [
        TrendSeriesSchema(
            group_id=group_id,
            kind=kind,
            metric=metric,
            points=[
                TrendPointSchema(bucket=bucket_key, value=round(sum(values) / len(values), 4), count=len(values))
                for bucket_key, values in sorted(buckets.items())
            ],
        )
        for group_id, buckets in sorted(by_group.items())
    ]


def _histogram(metric: str, values: list[float], error_count: int) -> MetricDistributionSchema:
    bin_size = 1.0 / _HISTOGRAM_BIN_COUNT
    bins = [
        HistogramBinSchema(lower=i * bin_size, upper=(i + 1) * bin_size, count=0) for i in range(_HISTOGRAM_BIN_COUNT)
    ]
    for value in values:
        # +1e-9 guards against float division landing just below a bin edge
        # (e.g. 0.3 / 0.1 == 2.999...).
        index = min(int(value / bin_size + 1e-9), _HISTOGRAM_BIN_COUNT - 1)
        bins[index].count += 1
    return MetricDistributionSchema(
        metric_name=metric,
        bins=bins,
        count=len(values),
        error_count=error_count,
        mean=round(sum(values) / len(values), 4) if values else None,
        min=min(values) if values else None,
        max=max(values) if values else None,
    )
