"""Frozen deterministic evaluation scope and publish-time evidence verification."""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import EvaluationItemModel, EvaluationRunModel, EvaluationScoreModel
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.format_evaluators import (
    ContainsEvaluator,
    ExactMatchEvaluator,
    IsJsonEvaluator,
    RegexMatchEvaluator,
)
from hecate.ops.evaluation.publish_gate import GateSignalResult
from hecate.ops.evaluation.safety_evaluators import PiiLeakageEvaluator

_DETERMINISTIC_TYPES = (
    ContainsEvaluator,
    ExactMatchEvaluator,
    IsJsonEvaluator,
    RegexMatchEvaluator,
    PiiLeakageEvaluator,
)
_SUPPORTED_METRICS = frozenset(cls().name for cls in _DETERMINISTIC_TYPES)


def freeze_evaluation_scope(
    items: list[EvaluationItemModel],
    evaluators: list[Evaluator],
    exempted: set[str],
    snapshot_hash: str | None,
) -> dict[str, Any]:
    """Describe actual selected items and supported deterministic requirements before execution."""
    item_ids = [str(item.id) for item in items]
    return {
        "schema_version": 1,
        "item_ids": item_ids,
        "required_metrics": list(
            dict.fromkeys(
                evaluator.name
                for evaluator in evaluators
                if isinstance(evaluator, _DETERMINISTIC_TYPES) and evaluator.name in _SUPPORTED_METRICS
            )
        ),
        "exempted_item_ids": sorted(exempted.intersection(item_ids)),
        "dataset_snapshot_hash": snapshot_hash,
    }


@dataclass
class ScopedEvidence:
    """Validated scope, verified score rows and coverage verdict for a single run."""

    signal: GateSignalResult
    metrics: set[str] = field(default_factory=set)
    scores: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def averages(self) -> dict[str, float]:
        """Average only valid deterministic scores belonging to active scoped pairs."""
        values: dict[str, list[float]] = defaultdict(list)
        for _item_id, metric, value in self.scores:
            values[metric].append(value)
        return {metric: sum(scores) / len(scores) for metric, scores in values.items()}

    def pass_rate(self, threshold: float) -> float:
        """Apply the existing item-level threshold rule within the frozen active scope."""
        values: dict[str, list[float]] = defaultdict(list)
        for item_id, _metric, value in self.scores:
            values[item_id].append(value)
        return (
            sum(all(score >= threshold for score in scores) for scores in values.values()) / len(values)
            if values
            else 0.0
        )


def _failure(reason: str) -> ScopedEvidence:
    return ScopedEvidence(GateSignalResult("evidence_coverage", True, False, {"reason": reason}))


def _scope_sets(scope: Any) -> tuple[set[str], set[str], set[str]]:
    if not isinstance(scope, dict) or type(scope.get("schema_version")) is not int or scope["schema_version"] != 1:
        raise ValueError("invalid_evaluation_scope")
    sets = []
    for key in ("item_ids", "required_metrics", "exempted_item_ids"):
        values = scope.get(key)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            raise ValueError("invalid_evaluation_scope")
        normalized = {str(uuid.UUID(value)) for value in values} if key != "required_metrics" else set(values)
        if len(normalized) != len(values):
            raise ValueError("invalid_evaluation_scope")
        sets.append(normalized)
    items, metrics, exemptions = sets
    if not exemptions.issubset(items) or not metrics.issubset(_SUPPORTED_METRICS):
        raise ValueError("invalid_evaluation_scope")
    if not items or not items - exemptions:
        raise ValueError("no_required_samples")
    if not metrics:
        raise ValueError("no_required_deterministic_metrics")
    snapshot_hash = scope.get("dataset_snapshot_hash")
    if "dataset_snapshot_hash" not in scope or (
        snapshot_hash is not None
        and (
            not isinstance(snapshot_hash, str)
            or len(snapshot_hash) != 64
            or any(char not in "0123456789abcdef" for char in snapshot_hash)
        )
    ):
        raise ValueError("invalid_evaluation_scope")
    return items - exemptions, metrics, exemptions


async def load_scoped_evidence(db: AsyncSession, run: EvaluationRunModel | None) -> ScopedEvidence:
    """Fail closed on unknown scope and require trusted scores for every active pair."""
    if run is None:
        return _failure("missing_matching_evaluation")
    if run.evaluation_scope is None:
        return _failure("missing_evaluation_scope")
    try:
        items, metrics, exemptions = _scope_sets(run.evaluation_scope)
        if run.evaluation_scope["dataset_snapshot_hash"] != (run.dataset_snapshot or {}).get("hash"):
            raise ValueError("evaluation_scope_snapshot_mismatch")
    except (ValueError, TypeError, AttributeError) as exc:
        reason = str(exc)
        return _failure(
            reason
            if reason
            in {
                "no_required_samples",
                "no_required_deterministic_metrics",
                "evaluation_scope_snapshot_mismatch",
            }
            else "invalid_evaluation_scope"
        )

    # Query by run identity without large IN lists; verify membership against the frozen sets in memory.
    rows = (
        await db.execute(
            select(
                EvaluationScoreModel.item_id,
                EvaluationScoreModel.metric_name,
                EvaluationScoreModel.value,
            ).where(
                EvaluationScoreModel.run_id == run.id,
                EvaluationScoreModel.workspace_id == run.workspace_id,
                EvaluationScoreModel.source == "deterministic",
                ~EvaluationScoreModel.deleted,
                EvaluationScoreModel.value >= 0,
                EvaluationScoreModel.value <= 1,
            )
        )
    ).all()
    scores = [
        (str(item), metric, float(value))
        for item, metric, value in rows
        if str(item) in items and metric in metrics and math.isfinite(value)
    ]
    covered = {(item, metric) for item, metric, _value in scores}
    missing_count = len(items) * len(metrics) - len(covered)
    missing_samples = []
    if missing_count:
        for item in sorted(items):
            for metric in sorted(metrics):
                if (item, metric) not in covered:
                    missing_samples.append({"item_id": item, "metric_name": metric})
                    if len(missing_samples) == 20:
                        break
            if len(missing_samples) == 20:
                break
    detail: dict[str, Any] = {
        "required_items": len(items),
        "exempted_items": len(exemptions),
        "required_metrics": sorted(metrics),
        "missing_count": missing_count,
        "missing_samples": missing_samples,
    }
    if missing_count:
        detail["reason"] = "incomplete_evaluation_evidence"
    return ScopedEvidence(GateSignalResult("evidence_coverage", True, not missing_count, detail), metrics, scores)
