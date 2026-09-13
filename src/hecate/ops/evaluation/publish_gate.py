"""Publish evaluation gate (7.3a).

A gate decides whether a workflow version's latest evaluation run is fit
to be published. The decision is purely deterministic: only scores with
``source="deterministic"`` participate. LLM-judge and human scores are
kept on the same run row for general observability but never gate a
publish — their non-determinism would inject noise into CI signals.

Single source of truth: :func:`evaluate_gate` consumes the gate
configuration plus the candidate and baseline runs and returns a
structured verdict. The studio publish service composes the verdict
into the ``evaluation_report`` and into the publish-time 409 response.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
)
from hecate.ops.evaluation.snapshot import compute_content_hash, serialize_snapshot_item

logger = logging.getLogger(__name__)

# Match the regression margin used in :meth:`EvaluationEngine._build_summary`
# so the gate and the on-run report speak the same regression semantics.
REGRESSION_DELTA = 0.05


# --- Configuration schema --------------------------------------------------


@dataclass(frozen=True)
class GateSignalConfig:
    """Resolved gate configuration (parse of the workflow's evaluation_gate JSON).

    Absent configuration (``raw is None``) is mapped to ``mode='off'``
    and short-circuits the gate; the publish service treats it as
    "no gate configured" and preserves the legacy publish contract.
    """

    mode: str  # "off" | "warn" | "require"
    min_pass_rate: float | None
    block_on_regression: bool
    block_on_drift: bool
    require_run: bool
    require_dataset_version: bool


@dataclass
class GateSignalResult:
    """One signal's verdict."""

    name: str
    enabled: bool
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateResult:
    """Outcome of evaluating a gate against a candidate run."""

    mode: str
    signals: list[GateSignalResult]
    deterministic_pass_rate: float | None
    deterministic_metric_averages: dict[str, float]
    dataset_drift: dict | None
    has_deterministic_scores: bool

    @property
    def enabled(self) -> bool:
        return self.mode in ("warn", "require")

    @property
    def blocking(self) -> bool:
        return self.mode == "require" and any(not s.passed for s in self.signals)


# --- Configuration helpers -------------------------------------------------


def resolve_gate_config(raw: dict | None) -> GateSignalConfig:
    """Normalize the workflow's ``evaluation_gate`` JSON.

    Unknown modes raise ``ValueError`` so the workflow update path
    never silently disables a configured gate.
    """
    if raw is None:
        return GateSignalConfig(
            mode="off",
            min_pass_rate=None,
            block_on_regression=False,
            block_on_drift=False,
            require_run=False,
            require_dataset_version=False,
        )
    mode = str(raw.get("mode") or "").strip()
    if mode not in ("warn", "require"):
        msg = f"evaluation_gate.mode must be 'warn' or 'require', got {mode!r}"
        raise ValueError(msg)
    min_pass_rate = raw.get("min_pass_rate")
    return GateSignalConfig(
        mode=mode,
        min_pass_rate=float(min_pass_rate) if min_pass_rate is not None else None,
        block_on_regression=bool(raw.get("block_on_regression") or False),
        block_on_drift=bool(raw.get("block_on_drift") or False),
        require_run=bool(raw.get("require_run") or False),
        require_dataset_version=bool(raw.get("require_dataset_version") or False),
    )


def validate_gate_config(raw: dict) -> None:
    """Service-layer validation for an incoming ``evaluation_gate`` JSON.

    Used by the workflow update path: the Pydantic schema enforces the
    ``mode='require' needs at least one signal`` rule, but raw JSON on a
    stored column or an admin-only update should still be re-checked.
    """
    config = resolve_gate_config(raw)
    if config.mode == "require" and not any(
        (
            config.min_pass_rate is not None,
            config.block_on_regression,
            config.block_on_drift,
            config.require_run,
            config.require_dataset_version,
        )
    ):
        msg = "evaluation_gate mode='require' needs at least one enabled signal"
        raise ValueError(msg)


# --- Core evaluation -------------------------------------------------------


async def evaluate_gate(
    db: AsyncSession,
    config: GateSignalConfig,
    candidate_run: EvaluationRunModel | None,
    baseline_run: EvaluationRunModel | None,
    live_dataset_hash: str | None,
) -> GateResult:
    """Compute the gate verdict for a publish attempt.

    Always returns a populated :class:`GateResult` — the publish service
    uses the verdict to render the report and (under ``require``) decide
    whether to reject with 409.
    """
    if config.mode == "off":
        return GateResult(
            mode="off",
            signals=[],
            deterministic_pass_rate=None,
            deterministic_metric_averages={},
            dataset_drift=None,
            has_deterministic_scores=False,
        )

    averages, has_det = await _deterministic_metric_averages(db, candidate_run)
    drift = _dataset_drift(candidate_run, live_dataset_hash) if config.block_on_drift else None
    signals: list[GateSignalResult] = []

    if config.min_pass_rate is not None:
        if not has_det:
            signals.append(
                GateSignalResult(
                    name="min_pass_rate",
                    enabled=True,
                    passed=False,
                    detail={"reason": "no_deterministic_scores", "min_pass_rate": config.min_pass_rate},
                )
            )
        else:
            threshold = _threshold(candidate_run)
            if threshold is None:
                signals.append(
                    GateSignalResult(
                        name="min_pass_rate",
                        enabled=True,
                        passed=False,
                        detail={
                            "reason": "no_threshold",
                            "min_pass_rate": config.min_pass_rate,
                        },
                    )
                )
            else:
                pass_rate, _passed, _active = await _per_item_deterministic_passes(db, candidate_run, threshold)
                signals.append(
                    GateSignalResult(
                        name="min_pass_rate",
                        enabled=True,
                        passed=pass_rate >= config.min_pass_rate,
                        detail={
                            "min_pass_rate": config.min_pass_rate,
                            "deterministic_pass_rate": pass_rate,
                            "threshold": threshold,
                        },
                    )
                )

    if config.block_on_regression:
        if not has_det:
            signals.append(
                GateSignalResult(
                    name="block_on_regression",
                    enabled=True,
                    passed=False,
                    detail={"reason": "no_deterministic_scores"},
                )
            )
        else:
            regressions = await _deterministic_regressions(db, averages, baseline_run)
            signals.append(
                GateSignalResult(
                    name="block_on_regression",
                    enabled=True,
                    passed=not regressions,
                    detail={"regressions": regressions},
                )
            )

    if config.block_on_drift:
        signals.append(
            GateSignalResult(
                name="block_on_drift",
                enabled=True,
                passed=drift is None,
                detail=drift or {},
            )
        )

    if config.require_run:
        signals.append(
            GateSignalResult(
                name="require_run",
                enabled=True,
                passed=candidate_run is not None,
                detail={"candidate_run_id": str(candidate_run.id) if candidate_run else None},
            )
        )

    if config.require_dataset_version:
        signals.append(
            GateSignalResult(
                name="require_dataset_version",
                enabled=True,
                passed=candidate_run is not None and candidate_run.dataset_version_id is not None,
                detail={
                    "dataset_version_id": (
                        str(candidate_run.dataset_version_id)
                        if candidate_run and candidate_run.dataset_version_id
                        else None
                    )
                },
            )
        )

    pass_rate_out: float | None = None
    if config.min_pass_rate is not None and has_det:
        for signal in signals:
            if signal.name == "min_pass_rate" and "deterministic_pass_rate" in signal.detail:
                pass_rate_out = float(signal.detail["deterministic_pass_rate"])
                break

    return GateResult(
        mode=config.mode,
        signals=signals,
        deterministic_pass_rate=pass_rate_out,
        deterministic_metric_averages=averages if has_det else {},
        dataset_drift=drift,
        has_deterministic_scores=has_det,
    )


def result_to_report_payload(result: GateResult, bypassed: bool) -> dict:
    """Render the ``gate`` block embedded in ``evaluation_report``."""
    payload: dict = {
        "mode": result.mode,
        "bypassed_by_force": bypassed,
        "deterministic_pass_rate": result.deterministic_pass_rate,
        "deterministic_metric_averages": result.deterministic_metric_averages,
        "signals": [
            {
                "name": signal.name,
                "enabled": signal.enabled,
                "passed": signal.passed,
                "detail": signal.detail,
            }
            for signal in result.signals
        ],
    }
    for signal in result.signals:
        if signal.name == "block_on_regression" and signal.detail.get("regressions"):
            payload["regressions"] = signal.detail["regressions"]
    return payload


# --- Deterministic scoring (the core invariant) -----------------------------


def _threshold(run: EvaluationRunModel | None) -> float | None:
    """The effective threshold persisted on the run summary (D9)."""
    if run is None:
        return None
    value = (run.summary or {}).get("threshold")
    return None if value is None else float(value)


async def _deterministic_metric_averages(
    db: AsyncSession,
    run: EvaluationRunModel | None,
) -> tuple[dict[str, float], bool]:
    """Average of ``source='deterministic'`` scores per metric (negative values excluded)."""
    if run is None:
        return {}, False
    stmt = select(
        EvaluationScoreModel.metric_name,
        EvaluationScoreModel.value,
    ).where(
        EvaluationScoreModel.run_id == run.id,
        EvaluationScoreModel.source == "deterministic",
        EvaluationScoreModel.value >= 0,
    )
    rows = (await db.execute(stmt)).all()
    if not rows:
        return {}, False
    by_metric: dict[str, list[float]] = {}
    for metric_name, value in rows:
        by_metric.setdefault(metric_name, []).append(float(value))
    return {metric: sum(values) / len(values) for metric, values in by_metric.items()}, True


async def _per_item_deterministic_passes(
    db: AsyncSession,
    run: EvaluationRunModel,
    threshold: float,
) -> tuple[float, int, int]:
    """Compute ``(pass_rate, passed_items, active_items)`` over deterministic scores.

    An item passes when every deterministic score on it meets the
    threshold; ``active_items`` is the count of items that received at
    least one deterministic score.
    """
    stmt = select(
        EvaluationScoreModel.item_id,
        EvaluationScoreModel.value,
    ).where(
        EvaluationScoreModel.run_id == run.id,
        EvaluationScoreModel.source == "deterministic",
        EvaluationScoreModel.value >= 0,
    )
    rows = (await db.execute(stmt)).all()
    by_item: dict[uuid.UUID, list[float]] = {}
    for item_id, value in rows:
        by_item.setdefault(item_id, []).append(float(value))
    if not by_item:
        return 0.0, 0, 0
    passed = sum(1 for values in by_item.values() if all(v >= threshold for v in values))
    active = len(by_item)
    return (passed / active) if active else 0.0, passed, active


async def _deterministic_regressions(
    db: AsyncSession,
    candidate_averages: dict[str, float],
    baseline_run: EvaluationRunModel | None,
) -> list[dict]:
    """Compare deterministic metric averages versus the baseline run."""
    if baseline_run is None:
        return []
    baseline_avgs, _ = await _deterministic_metric_averages(db, baseline_run)
    regressions: list[dict] = []
    for metric, baseline_value in baseline_avgs.items():
        candidate_value = candidate_averages.get(metric)
        if candidate_value is None:
            continue
        if candidate_value < baseline_value * (1.0 - REGRESSION_DELTA):
            regressions.append(
                {
                    "metric_name": metric,
                    "baseline": baseline_value,
                    "candidate": candidate_value,
                    "drop": baseline_value - candidate_value,
                }
            )
    return regressions


# --- Drift -----------------------------------------------------------------


def _dataset_drift(run: EvaluationRunModel | None, live_dataset_hash: str | None) -> dict | None:
    """Compute ``dataset_drift`` between the run's snapshot and the live dataset."""
    if run is None:
        return None
    snapshot = run.dataset_snapshot or {}
    snapshot_hash = snapshot.get("hash")
    if not snapshot_hash or live_dataset_hash is None:
        return None
    if snapshot_hash == live_dataset_hash:
        return None
    snapshot_ids = {str(row.get("id")) for row in snapshot.get("items", []) if row.get("id")}
    # ``changed_item_ids`` is the symmetric difference of snapshot and
    # live item sets; the full live-id set is fetched by the caller if
    # it wants a more precise list — gate only needs to know it
    # diverged, not what changed.
    return {
        "snapshot_hash": str(snapshot_hash),
        "current_hash": str(live_dataset_hash),
        "snapshot_item_count": len(snapshot_ids),
    }


async def live_dataset_hash(db: AsyncSession, dataset_id: uuid.UUID) -> str:
    """Content hash of the dataset's live items (matches runner snapshot hash)."""
    stmt = (
        select(EvaluationItemModel)
        .where(
            EvaluationItemModel.dataset_id == dataset_id,
            ~EvaluationItemModel.deleted,
        )
        .order_by(EvaluationItemModel.created_at.asc(), EvaluationItemModel.id.asc())
    )
    items = list((await db.execute(stmt)).scalars().all())
    return compute_content_hash([serialize_snapshot_item(item) for item in items])
