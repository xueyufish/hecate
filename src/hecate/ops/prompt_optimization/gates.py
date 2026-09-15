"""Gates for the prompt optimization pipeline (6.19).

Three gates, applied in order:

1. **Template integrity gate** — a candidate must parse in the prompt
   template engine and its extracted variable set must equal the base
   template's declared variables. Rejected candidates never consume
   rollout budget (a hallucinated ``{{variable}}`` or broken block would
   otherwise only fail at rollout time).
2. **Acceptance gate** — the primary metric must improve over baseline by
   ``min_improvement`` and no deterministic metric may regress beyond
   ``max_regression``. LLM-judge metrics other than the primary are
   advisory (7.3a posture: deterministic checks decide, judge output
   informs).
3. **Candidate pool (Pareto-lite)** — gate-passing candidates are all
   retained; the next mutation parent is the candidate with the highest
   validation-split primary score. Per-metric-best candidates stay in the
   pool so a future objective-Pareto selection strategy can switch over
   without a data-model change (design D2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hecate.ops.evaluation.types import Score

if TYPE_CHECKING:
    from hecate.studio.template_engine import TemplateEngine


@dataclass
class IntegrityReport:
    """Outcome of the template integrity gate."""

    passed: bool
    reason: str | None = None
    variables: list[str] = field(default_factory=list)


def check_template_integrity(
    candidate_template: str,
    base_variables: list[str],
    engine: TemplateEngine | None = None,
) -> IntegrityReport:
    """Gate a candidate template on parse success and variable-set equality.

    Args:
        candidate_template: The generated template text.
        engine: Optional template engine.
        base_variables: Declared variables of the base template.
        engine: Optional template engine (a default is created when omitted).

    Returns:
        IntegrityReport with ``passed=False`` and a reason on failure.
    """
    from hecate.studio.template_engine import TemplateEngine

    engine = engine or TemplateEngine()
    try:
        engine.validate(candidate_template)
    except Exception as e:  # noqa: BLE001 — any template engine failure blocks the candidate
        return IntegrityReport(passed=False, reason=f"template_parse_error: {e}")
    variables = engine.extract_variables(candidate_template)
    if sorted(variables) != sorted(base_variables):
        missing = sorted(set(base_variables) - set(variables))
        extra = sorted(set(variables) - set(base_variables))
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        if extra:
            detail.append(f"extra={extra}")
        return IntegrityReport(
            passed=False,
            reason=f"variable_set_mismatch ({'; '.join(detail)})",
            variables=variables,
        )
    return IntegrityReport(passed=True, variables=variables)


def metric_sources(item_scores: dict[str, list[Score]]) -> dict[str, str]:
    """Classify each metric as ``deterministic`` or ``llm_judge``.

    A metric counts as deterministic only when every recorded score for it
    came from a deterministic evaluator; anything else is treated as
    judge-produced (conservative: judge metrics never hard-gate).
    """
    sources: dict[str, set[str]] = {}
    for scores in item_scores.values():
        for score in scores:
            sources.setdefault(score.metric_name, set()).add(score.source)
    return {name: "deterministic" if vals <= {"deterministic"} else "llm_judge" for name, vals in sources.items()}


@dataclass
class GateCheck:
    """One acceptance-gate check with its inputs and verdict."""

    check: str
    metric: str
    baseline: float
    candidate: float
    delta: float
    threshold: float
    passed: bool
    advisory: bool = False


@dataclass
class GateReport:
    """Full acceptance-gate outcome for one candidate."""

    accepted: bool
    primary_metric: str
    baseline_scores: dict[str, float]
    candidate_scores: dict[str, float]
    checks: list[GateCheck] = field(default_factory=list)


def evaluate_acceptance(
    baseline_avg: dict[str, float],
    candidate_avg: dict[str, float],
    primary_metric: str,
    min_improvement: float,
    max_regression: float,
    sources: dict[str, str],
) -> GateReport:
    """Apply the acceptance gate to one candidate's validation-split averages.

    Deterministic metrics (including the primary, whose improvement is
    required regardless of source) gate; non-primary judge metrics are
    reported as advisory. Missing metrics on either side fail the check
    conservatively (a candidate that broke an evaluator cannot pass).
    """
    checks: list[GateCheck] = []
    metrics = sorted(set(baseline_avg) | set(candidate_avg))

    primary_base = baseline_avg.get(primary_metric)
    primary_cand = candidate_avg.get(primary_metric)
    if primary_base is None or primary_cand is None:
        checks.append(
            GateCheck(
                check="primary_improvement",
                metric=primary_metric,
                baseline=primary_base if primary_base is not None else float("nan"),
                candidate=primary_cand if primary_cand is not None else float("nan"),
                delta=float("nan"),
                threshold=min_improvement,
                passed=False,
            )
        )
    else:
        delta = primary_cand - primary_base
        checks.append(
            GateCheck(
                check="primary_improvement",
                metric=primary_metric,
                baseline=primary_base,
                candidate=primary_cand,
                delta=delta,
                threshold=min_improvement,
                passed=delta >= min_improvement,
            )
        )

    for metric in metrics:
        if metric == primary_metric:
            continue
        base = baseline_avg.get(metric)
        cand = candidate_avg.get(metric)
        if base is None or cand is None:
            passed = False
            delta = float("nan")
        else:
            delta = cand - base
            passed = delta >= -max_regression
        advisory = sources.get(metric) != "deterministic"
        checks.append(
            GateCheck(
                check="non_regression",
                metric=metric,
                baseline=base if base is not None else float("nan"),
                candidate=cand if cand is not None else float("nan"),
                delta=delta,
                threshold=max_regression,
                passed=True if advisory else passed,
                advisory=advisory,
            )
        )

    hard_checks = [c for c in checks if not c.advisory]
    return GateReport(
        accepted=all(c.passed for c in hard_checks),
        primary_metric=primary_metric,
        baseline_scores=dict(baseline_avg),
        candidate_scores=dict(candidate_avg),
        checks=checks,
    )


def gate_report_dict(report: GateReport) -> dict:
    """Serialize a GateReport for JSONB persistence."""
    return {
        "accepted": report.accepted,
        "primary_metric": report.primary_metric,
        "baseline_scores": report.baseline_scores,
        "candidate_scores": report.candidate_scores,
        "checks": [
            {
                "check": c.check,
                "metric": c.metric,
                "baseline": c.baseline,
                "candidate": c.candidate,
                "delta": c.delta,
                "threshold": c.threshold,
                "passed": c.passed,
                "advisory": c.advisory,
            }
            for c in report.checks
        ],
    }


def select_parent(
    candidates: list[tuple[str, dict[str, float]]],
    baseline_avg: dict[str, float],
    primary_metric: str,
) -> tuple[str, dict[str, float]]:
    """Pick the next mutation parent from the gate-passing pool (Pareto-lite).

    The parent is the pool candidate with the highest validation-split
    primary score; the baseline plays this role while the pool is empty.
    Returns ``(template, validation_averages)``.
    """
    best = ("__baseline__", baseline_avg)
    for template, val_avg in candidates:
        if val_avg.get(primary_metric, -1.0) > best[1].get(primary_metric, -1.0):
            best = (template, val_avg)
    return best
