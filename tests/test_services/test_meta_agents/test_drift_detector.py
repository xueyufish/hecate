"""Unit tests for DriftDetectorAgent."""

from __future__ import annotations

import pytest

from hecate.services.meta_agents.drift_detector import (
    DriftDetectorAgent,
    DriftReport,
)


@pytest.fixture
def agent() -> DriftDetectorAgent:
    expected = {
        "database": {"url": "postgres://localhost/db", "pool_size": 10},
        "llm": {"model": "gpt-4o", "timeout": 30},
        "security": {"api_key": "expected-key"},
    }
    return DriftDetectorAgent(expected_config=expected)


def test_detect_no_drift(agent: DriftDetectorAgent) -> None:
    actual = {
        "database": {"url": "postgres://localhost/db", "pool_size": 10},
        "llm": {"model": "gpt-4o", "timeout": 30},
        "security": {"api_key": "expected-key"},
    }
    drifts = agent.detect_config_drift(actual)
    assert drifts == []


def test_detect_value_drift(agent: DriftDetectorAgent) -> None:
    actual = {
        "database": {"url": "postgres://localhost/db", "pool_size": 5},
        "llm": {"model": "gpt-4o", "timeout": 30},
        "security": {"api_key": "expected-key"},
    }
    drifts = agent.detect_config_drift(actual)
    assert len(drifts) == 1
    assert drifts[0].config_key == "database.pool_size"
    assert drifts[0].category == "database"


def test_detect_missing_key(agent: DriftDetectorAgent) -> None:
    actual = {
        "database": {"url": "postgres://localhost/db", "pool_size": 10},
        "llm": {"model": "gpt-4o", "timeout": 30},
    }
    drifts = agent.detect_config_drift(actual)
    assert len(drifts) == 1
    assert drifts[0].actual_value == "<missing>"
    assert "security" in drifts[0].config_key


def test_impact_classification_security(agent: DriftDetectorAgent) -> None:
    actual = {
        "database": {"url": "postgres://localhost/db", "pool_size": 10},
        "llm": {"model": "gpt-4o", "timeout": 30},
        "security": {"api_key": "wrong-key"},
    }
    drifts = agent.detect_config_drift(actual)
    assert len(drifts) == 1
    assert drifts[0].impact == "high"
    assert drifts[0].category == "security"


def test_impact_classification_performance(agent: DriftDetectorAgent) -> None:
    actual = {
        "database": {"url": "postgres://localhost/db", "pool_size": 10},
        "llm": {"model": "gpt-4o", "timeout": 60},
        "security": {"api_key": "expected-key"},
    }
    drifts = agent.detect_config_drift(actual)
    assert len(drifts) == 1
    assert drifts[0].impact == "medium"
    assert drifts[0].category == "llm"


def test_generate_drift_report(agent: DriftDetectorAgent) -> None:
    actual = {
        "database": {"url": "postgres://other/db", "pool_size": 10},
        "llm": {"model": "gpt-4o", "timeout": 30},
        "security": {"api_key": "expected-key"},
    }
    report = agent.generate_drift_report(actual)
    assert isinstance(report, DriftReport)
    assert report.drift_count == 1
    assert report.high_impact_count == 0
    assert report.checked_at is not None


def test_run_convenience(agent: DriftDetectorAgent) -> None:
    actual = {
        "database": {"url": "postgres://localhost/db", "pool_size": 10},
        "llm": {"model": "gpt-4o", "timeout": 30},
        "security": {"api_key": "expected-key"},
    }
    report = agent.run(actual)
    assert isinstance(report, DriftReport)
    assert report.drift_count == 0


def test_empty_expected_config() -> None:
    agent = DriftDetectorAgent(expected_config={})
    drifts = agent.detect_config_drift({"anything": 1})
    assert drifts == []
