"""Unit tests for TrajectoryAnalyzer."""

from __future__ import annotations

import pytest

from hecate.services.evolution.trajectory_analyzer import (
    TrajectoryAnalyzer,
    TrajectoryPoint,
)


@pytest.fixture
def analyzer() -> TrajectoryAnalyzer:
    return TrajectoryAnalyzer()


def test_analyze_success_extracts_tools(analyzer: TrajectoryAnalyzer) -> None:
    trajectory = [
        TrajectoryPoint(step=1, action="search", tool_name="search", success=True, duration_ms=100),
        TrajectoryPoint(step=2, action="calculate", tool_name="calculator", success=True, duration_ms=200),
        TrajectoryPoint(step=3, action="summarize", success=True, duration_ms=150),
    ]
    result = analyzer.analyze_success(trajectory)
    assert result.pattern_type == "success"
    assert any("search" in f for f in result.key_factors)
    assert any("calculator" in f for f in result.key_factors)
    assert result.confidence > 0.5
    assert result.trajectory_id


def test_analyze_failure_identifies_step(analyzer: TrajectoryAnalyzer) -> None:
    trajectory = [
        TrajectoryPoint(step=1, action="search", tool_name="search", success=True),
        TrajectoryPoint(
            step=2, action="calculate", tool_name="calculator", success=False, result_summary="division by zero"
        ),
    ]
    result = analyzer.analyze_failure(trajectory)
    assert result.pattern_type == "failure"
    assert any("step 1" in f for f in result.key_factors)
    assert len(result.improvement_suggestions) > 0


def test_analyze_failure_timeout(analyzer: TrajectoryAnalyzer) -> None:
    trajectory = [
        TrajectoryPoint(
            step=1,
            action="compute",
            tool_name="heavy_tool",
            success=False,
            duration_ms=30_000,
            result_summary="timeout",
        ),
    ]
    result = analyzer.analyze_failure(trajectory)
    assert any("timeout" in s.lower() for s in result.improvement_suggestions)


def test_analyze_auto_detects_success(analyzer: TrajectoryAnalyzer) -> None:
    trajectory = [
        TrajectoryPoint(step=1, action="run", success=True),
    ]
    result = analyzer.analyze(trajectory)
    assert result.pattern_type == "success"


def test_analyze_auto_detects_failure(analyzer: TrajectoryAnalyzer) -> None:
    trajectory = [
        TrajectoryPoint(step=1, action="run", success=False),
    ]
    result = analyzer.analyze(trajectory)
    assert result.pattern_type == "failure"


def test_analyze_empty_trajectory(analyzer: TrajectoryAnalyzer) -> None:
    result = analyzer.analyze([])
    assert result.pattern_type == "failure"
    assert result.confidence == 0.0
