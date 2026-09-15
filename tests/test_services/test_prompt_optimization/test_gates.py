"""Tests for optimization gates — template integrity, acceptance, sources."""

from __future__ import annotations

import pytest

from hecate.ops.evaluation.types import Score
from hecate.ops.prompt_optimization.gates import (
    check_template_integrity,
    evaluate_acceptance,
    metric_sources,
)


class TestTemplateIntegrityGate:
    def test_valid_candidate_passes(self) -> None:
        report = check_template_integrity(
            "Answer {{question}} carefully.",
            ["question"],
        )
        assert report.passed is True
        assert report.variables == ["question"]

    def test_parse_failure_blocks(self) -> None:
        report = check_template_integrity(
            "Broken {{ unclosed block",
            ["question"],
        )
        assert report.passed is False
        assert "template_parse_error" in (report.reason or "")

    def test_missing_variable_blocks(self) -> None:
        report = check_template_integrity(
            "No placeholder here.",
            ["question"],
        )
        assert report.passed is False
        assert "variable_set_mismatch" in (report.reason or "")
        assert "missing=['question']" in (report.reason or "")

    def test_extra_variable_blocks(self) -> None:
        report = check_template_integrity(
            "Answer {{question}} for {{hallucinated_var}}.",
            ["question"],
        )
        assert report.passed is False
        assert "extra=['hallucinated_var']" in (report.reason or "")


class TestMetricSources:
    def test_all_deterministic(self) -> None:
        scores = {
            "i1": [
                Score(metric_name="exact", value=1.0, source="deterministic"),
                Score(metric_name="judge", value=0.5, source="llm_judge"),
            ]
        }
        sources = metric_sources(scores)
        assert sources == {"exact": "deterministic", "judge": "llm_judge"}

    def test_mixed_sources_treated_as_judge(self) -> None:
        scores = {
            "i1": [Score(metric_name="m", value=1.0, source="deterministic")],
            "i2": [Score(metric_name="m", value=0.0, source="llm_judge")],
        }
        assert metric_sources(scores) == {"m": "llm_judge"}


class TestAcceptanceGate:
    def test_accepted_when_primary_improves_and_no_regression(self) -> None:
        report = evaluate_acceptance(
            baseline_avg={"exact": 0.5, "grounded": 0.8},
            candidate_avg={"exact": 0.7, "grounded": 0.8},
            primary_metric="exact",
            min_improvement=0.02,
            max_regression=0.05,
            sources={"exact": "deterministic", "grounded": "deterministic"},
        )
        assert report.accepted is True
        primary_check = report.checks[0]
        assert primary_check.check == "primary_improvement"
        assert primary_check.passed is True

    def test_rejected_when_primary_below_threshold(self) -> None:
        report = evaluate_acceptance(
            baseline_avg={"exact": 0.5},
            candidate_avg={"exact": 0.51},
            primary_metric="exact",
            min_improvement=0.02,
            max_regression=0.05,
            sources={"exact": "deterministic"},
        )
        assert report.accepted is False

    def test_rejected_when_deterministic_metric_regresses(self) -> None:
        report = evaluate_acceptance(
            baseline_avg={"exact": 0.5, "grounded": 0.8},
            candidate_avg={"exact": 0.9, "grounded": 0.6},
            primary_metric="exact",
            min_improvement=0.02,
            max_regression=0.05,
            sources={"exact": "deterministic", "grounded": "deterministic"},
        )
        assert report.accepted is False
        regression = next(c for c in report.checks if c.metric == "grounded")
        assert regression.passed is False
        assert regression.advisory is False

    def test_judge_regression_is_advisory_only(self) -> None:
        report = evaluate_acceptance(
            baseline_avg={"exact": 0.5, "tone": 0.9},
            candidate_avg={"exact": 0.9, "tone": 0.3},
            primary_metric="exact",
            min_improvement=0.02,
            max_regression=0.05,
            sources={"exact": "deterministic", "tone": "llm_judge"},
        )
        assert report.accepted is True
        advisory = next(c for c in report.checks if c.metric == "tone")
        assert advisory.advisory is True
        assert advisory.passed is True  # reported, not gating

    def test_judge_primary_gates_with_margin(self) -> None:
        report = evaluate_acceptance(
            baseline_avg={"quality": 0.5},
            candidate_avg={"quality": 0.55},
            primary_metric="quality",
            min_improvement=0.02,
            max_regression=0.05,
            sources={"quality": "llm_judge"},
        )
        assert report.accepted is True

    def test_missing_metric_fails_conservatively(self) -> None:
        report = evaluate_acceptance(
            baseline_avg={"exact": 0.5, "grounded": 0.8},
            candidate_avg={"exact": 0.9},
            primary_metric="exact",
            min_improvement=0.02,
            max_regression=0.05,
            sources={"exact": "deterministic", "grounded": "deterministic"},
        )
        assert report.accepted is False

    def test_nan_serializes_as_reported(self) -> None:
        report = evaluate_acceptance(
            baseline_avg={"exact": 0.5, "grounded": 0.8},
            candidate_avg={"exact": 0.9},
            primary_metric="exact",
            min_improvement=0.0,
            max_regression=0.05,
            sources={"exact": "deterministic", "grounded": "deterministic"},
        )
        regression = next(c for c in report.checks if c.metric == "grounded")
        assert regression.passed is False
        assert pytest.approx(regression.baseline) == 0.8
