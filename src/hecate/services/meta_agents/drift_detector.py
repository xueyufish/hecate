"""Drift detector agent for comparing actual vs expected configuration.

Recursively compares configuration dictionaries, categorises drifts by
impact and domain, and produces a report — without applying any fixes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_HIGH_IMPACT_KEYWORDS = {"security", "guard", "api_key", "secret", "password", "token"}
_MEDIUM_IMPACT_KEYWORDS = {"timeout", "retry", "limit", "pool", "concurrency", "batch"}

_CATEGORY_MAP: list[tuple[set[str], str]] = [
    ({"database", "db", "postgres", "sqlalchemy"}, "database"),
    ({"llm", "model", "openai", "anthropic", "litellm"}, "llm"),
    ({"security", "guard", "api_key", "rate", "secret"}, "security"),
    ({"timeout", "pool", "worker", "concurrency", "batch"}, "performance"),
]


def _classify_impact(key: str) -> str:
    key_lower = key.lower()
    for kw in _HIGH_IMPACT_KEYWORDS:
        if kw in key_lower:
            return "high"
    for kw in _MEDIUM_IMPACT_KEYWORDS:
        if kw in key_lower:
            return "medium"
    return "low"


def _classify_category(key: str) -> str:
    key_lower = key.lower()
    for keywords, category in _CATEGORY_MAP:
        if any(kw in key_lower for kw in keywords):
            return category
    return "general"


@dataclass
class DriftItem:
    """A single configuration drift."""

    config_key: str
    expected_value: str
    actual_value: str
    impact: str  # "high" | "medium" | "low"
    category: str


@dataclass
class DriftReport:
    """Aggregated drift report."""

    drift_count: int = 0
    high_impact_count: int = 0
    drifts: list[DriftItem] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class DriftDetectorAgent:
    """Detects configuration drift by comparing expected vs actual values."""

    def __init__(self, expected_config: dict[str, Any] | None = None) -> None:
        self._expected = expected_config or {}

    def detect_config_drift(self, actual_config: dict[str, Any]) -> list[DriftItem]:
        """Recursively compare expected and actual configuration."""
        drifts: list[DriftItem] = []
        self._compare_dicts(self._expected, actual_config, prefix="", drifts=drifts)
        logger.info("Detected %d configuration drifts", len(drifts))
        return drifts

    def generate_drift_report(self, actual_config: dict[str, Any]) -> DriftReport:
        """Detect drift and build an aggregated report."""
        drifts = self.detect_config_drift(actual_config)
        report = DriftReport(
            drift_count=len(drifts),
            high_impact_count=sum(1 for d in drifts if d.impact == "high"),
            drifts=drifts,
        )
        logger.info(
            "Drift report: %d drifts (%d high impact)",
            report.drift_count,
            report.high_impact_count,
        )
        return report

    def run(self, actual_config: dict[str, Any]) -> DriftReport:
        """Convenience method to detect drift and return a report."""
        return self.generate_drift_report(actual_config)

    def _compare_dicts(
        self,
        expected: dict[str, Any],
        actual: dict[str, Any],
        prefix: str,
        drifts: list[DriftItem],
    ) -> None:
        for key, expected_val in expected.items():
            full_key = f"{prefix}.{key}" if prefix else key
            actual_val = actual.get(key)

            if actual_val is None and key not in actual:
                drifts.append(
                    DriftItem(
                        config_key=full_key,
                        expected_value=str(expected_val),
                        actual_value="<missing>",
                        impact=_classify_impact(full_key),
                        category=_classify_category(full_key),
                    )
                )
                continue

            if isinstance(expected_val, dict) and isinstance(actual_val, dict):
                self._compare_dicts(expected_val, actual_val, full_key, drifts)
            elif expected_val != actual_val:
                drifts.append(
                    DriftItem(
                        config_key=full_key,
                        expected_value=str(expected_val),
                        actual_value=str(actual_val),
                        impact=_classify_impact(full_key),
                        category=_classify_category(full_key),
                    )
                )
