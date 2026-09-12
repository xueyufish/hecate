"""Human-vs-machine score calibration analytics (7.4a).

Pairs automated score rows with human annotation rows on the same
``(target_id, metric_name)`` and reports agreement statistics per metric —
the judge-quality monitoring panel (the "shadow review" view of human
overrides). The window applies to the human rows (annotation time); the
automated counterpart is the latest non-error automated row for that
target+metric regardless of when it was produced.

Metric mode: a metric is treated as categorical when a queue in the
workspace declares it ``categorical``/``boolean`` — label pairs then
require ``value_label`` on both sides (automated graders do not emit
labels yet, so those stats stay zeroed until categorical machine graders
exist). Every other metric is numeric: agreement = paired |diff| <= 0.1,
plus MAE and a machine × human value heatmap.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import AnnotationQueueModel, EvaluationTaskScoreModel

_DEFAULT_WINDOW_DAYS = 30
_ERROR_SENTINEL = -1.0
_NUMERIC_TOLERANCE = 0.1
_HEATMAP_BINS = 10

_HUMAN_SOURCE = "human"


class CalibrationValidationError(ValueError):
    """Calibration request violates a filter/window rule."""


class CalibrationMetricSchema(PydanticBase):
    """Per-metric machine-vs-human agreement statistics."""

    metric_name: str
    pair_count: int = 0
    agreement_rate: float | None = None
    mae: float | None = None  # numeric metrics
    kappa: float | None = None  # categorical/boolean metrics
    data_mode: str = "numeric"  # "numeric" | "categorical"
    machine_only_count: int = 0
    human_only_count: int = 0
    heatmap: list[dict[str, int]] = []


class CalibrationReportSchema(PydanticBase):
    """Response payload for ``GET /api/evaluation/calibration``."""

    metrics: list[CalibrationMetricSchema]


class CalibrationService:
    """Aggregates machine-vs-human agreement over paired score samples."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def calibration(
        self,
        workspace_id: uuid.UUID,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        agent_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        metric_name: str | None = None,
    ) -> CalibrationReportSchema:
        """Per-metric agreement statistics for the workspace window."""
        from hecate.ops.evaluation.reports.service import resolve_window

        window = resolve_window(start_date, end_date)

        categorical_metrics = await self._categorical_metric_names(workspace_id)

        automated = await self._load_rows(
            workspace_id,
            is_human=False,
            start_date=window.start,
            end_date=window.end,
            agent_id=agent_id,
            task_id=task_id,
            metric_name=metric_name,
        )
        human = await self._load_rows(
            workspace_id,
            is_human=True,
            start_date=window.start,
            end_date=window.end,
            agent_id=agent_id,
            task_id=task_id,
            metric_name=metric_name,
        )

        # Latest non-error automated value per (target, metric).
        latest_auto: dict[tuple[uuid.UUID, str], EvaluationTaskScoreModel] = {}
        for row in automated:
            if float(row.value) == _ERROR_SENTINEL:
                continue
            key = (row.target_id, row.metric_name)
            current = latest_auto.get(key)
            if current is None or row.created_at > current.created_at:
                latest_auto[key] = row

        human_by_key: dict[tuple[uuid.UUID, str], list[EvaluationTaskScoreModel]] = defaultdict(list)
        for row in human:
            human_by_key[(row.target_id, row.metric_name)].append(row)

        metric_names = {row.metric_name for row in automated} | {row.metric_name for row in human}
        if metric_name is not None:
            metric_names &= {metric_name}

        metrics: list[CalibrationMetricSchema] = []
        for name in sorted(metric_names):
            mode = "categorical" if name in categorical_metrics else "numeric"
            machine_only = sum(1 for key in latest_auto if key[1] == name and key not in human_by_key)
            human_only = sum(1 for key, rows in human_by_key.items() if key[1] == name and key not in latest_auto)
            metrics.append(
                self._metric_stats(
                    name,
                    mode,
                    latest_auto,
                    human_by_key,
                    machine_only,
                    human_only,
                )
            )
        return CalibrationReportSchema(metrics=metrics)

    def _metric_stats(
        self,
        name: str,
        mode: str,
        latest_auto: dict[tuple[uuid.UUID, str], EvaluationTaskScoreModel],
        human_by_key: dict[tuple[uuid.UUID, str], list[EvaluationTaskScoreModel]],
        machine_only: int,
        human_only: int,
    ) -> CalibrationMetricSchema:
        """Compute pair statistics for one metric in its declared mode."""
        auto_rows: list[EvaluationTaskScoreModel] = []
        human_rows: list[EvaluationTaskScoreModel] = []
        for key, auto_row in latest_auto.items():
            if key[1] != name:
                continue
            for human_row in human_by_key.get(key, []):
                auto_rows.append(auto_row)
                human_rows.append(human_row)

        stat = CalibrationMetricSchema(
            metric_name=name,
            data_mode=mode,
            machine_only_count=machine_only,
            human_only_count=human_only,
        )
        if not auto_rows:
            return stat

        if mode == "categorical":
            # Both sides must carry labels; automated graders do not yet,
            # so these stats stay zeroed until categorical graders exist.
            labelled = [
                (a.value_label, h.value_label)
                for a, h in zip(auto_rows, human_rows, strict=False)
                if a.value_label is not None and h.value_label is not None
            ]
            stat.pair_count = len(labelled)
            if labelled:
                matches = sum(1 for a, h in labelled if a == h)
                stat.agreement_rate = round(matches / len(labelled), 4)
                stat.kappa = _cohens_kappa(labelled)
            return stat

        stat.pair_count = len(auto_rows)
        diffs = [abs(float(h.value) - float(a.value)) for a, h in zip(auto_rows, human_rows, strict=False)]
        stat.agreement_rate = round(sum(1 for d in diffs if d <= _NUMERIC_TOLERANCE) / len(diffs), 4)
        stat.mae = round(sum(diffs) / len(diffs), 4)
        stat.heatmap = _heatmap(auto_rows, human_rows)
        return stat

    async def _load_rows(
        self,
        workspace_id: uuid.UUID,
        is_human: bool,
        start_date: datetime,
        end_date: datetime,
        agent_id: uuid.UUID | None,
        task_id: uuid.UUID | None,
        metric_name: str | None,
    ) -> list[EvaluationTaskScoreModel]:
        """Score rows for one source class under the workspace scope.

        The window bounds ``created_at`` for human rows only (annotation
        time) — the automated counterpart is matched regardless of when it
        was produced. For ``task_id`` scoping, human rows (which carry
        ``task_id=NULL``) are matched via the targets scored by that task;
        agent scoping uses the denormalized ``agent_id`` column on both row
        classes.
        """
        conditions: list[Any] = [
            EvaluationTaskScoreModel.workspace_id == workspace_id,
            ~EvaluationTaskScoreModel.deleted,
        ]
        conditions.append(
            EvaluationTaskScoreModel.source == _HUMAN_SOURCE
            if is_human
            else EvaluationTaskScoreModel.source != _HUMAN_SOURCE
        )
        if is_human:
            conditions.append(EvaluationTaskScoreModel.created_at >= start_date.replace(tzinfo=None))
            conditions.append(EvaluationTaskScoreModel.created_at <= end_date.replace(tzinfo=None))
        if metric_name is not None:
            conditions.append(EvaluationTaskScoreModel.metric_name == metric_name)

        rows = (await self.db.execute(select(EvaluationTaskScoreModel).where(*conditions))).scalars().all()

        if agent_id is not None:
            rows = [r for r in rows if r.agent_id == agent_id]
        if task_id is not None:
            task_targets = set(
                (
                    await self.db.execute(
                        select(EvaluationTaskScoreModel.target_id).where(
                            EvaluationTaskScoreModel.task_id == task_id,
                            EvaluationTaskScoreModel.workspace_id == workspace_id,
                            ~EvaluationTaskScoreModel.deleted,
                        )
                    )
                )
                .scalars()
                .all()
            )
            rows = [r for r in rows if r.target_id in task_targets]
        return list(rows)

    async def _categorical_metric_names(self, workspace_id: uuid.UUID) -> set[str]:
        """Metric names declared categorical/boolean by any workspace queue."""
        queues = (
            (
                await self.db.execute(
                    select(AnnotationQueueModel).where(
                        AnnotationQueueModel.workspace_id == workspace_id,
                        ~AnnotationQueueModel.deleted,
                    )
                )
            )
            .scalars()
            .all()
        )
        names: set[str] = set()
        for queue in queues:
            for definition in queue.metric_defs or []:
                if definition.get("data_type") in ("categorical", "boolean"):
                    names.add(str(definition.get("name")))
        return names


def _cohens_kappa(pairs: list[tuple[str | None, str | None]]) -> float:
    """Cohen's Kappa over observed (automated, human) label pairs."""
    total = len(pairs)
    if total == 0:
        return 0.0
    auto_counts: dict[str | None, int] = defaultdict(int)
    human_counts: dict[str | None, int] = defaultdict(int)
    matches = 0
    for a, h in pairs:
        auto_counts[a] += 1
        human_counts[h] += 1
        if a == h:
            matches += 1
    po = matches / total
    label_population = set(auto_counts) | set(human_counts)
    pe = sum((auto_counts[label] / total) * (human_counts[label] / total) for label in label_population)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return round((po - pe) / (1 - pe), 4)


def _heatmap(
    auto_rows: list[EvaluationTaskScoreModel],
    human_rows: list[EvaluationTaskScoreModel],
) -> list[dict[str, int]]:
    """Non-zero cells of the machine × human value histogram (10×10)."""
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for a, h in zip(auto_rows, human_rows, strict=False):
        x = _bin(float(a.value))
        y = _bin(float(h.value))
        counts[(x, y)] += 1
    return [{"machine_bin": x, "human_bin": y, "count": count} for (x, y), count in sorted(counts.items())]


def _bin(value: float) -> int:
    if value < 0 or value > 1:
        value = max(0.0, min(1.0, value))
    return min(int(value / (1.0 / _HEATMAP_BINS) + 1e-9), _HEATMAP_BINS - 1)
