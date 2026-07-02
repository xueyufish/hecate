"""A/B testing manager for LLM model comparison.

Supports traffic splitting between two models, metrics collection,
and statistical significance calculation using a two-proportion z-test.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ABTestConfig:
    """Configuration for an A/B test."""

    test_name: str
    model_a: str
    model_b: str
    traffic_split: float = 0.5
    enabled: bool = True
    created_at: float = field(default_factory=time.time)


@dataclass
class ABTestResult:
    """Result from a single A/B test invocation."""

    test_name: str
    model: str
    success: bool
    latency_ms: float
    token_usage: dict[str, int] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class ABTestManager:
    """Manages A/B tests for comparing LLM model performance.

    Supports:
    - Deterministic traffic splitting via hash-based assignment
    - Metrics collection (success rate, latency, token usage)
    - Statistical significance calculation via two-proportion z-test
    """

    def __init__(self) -> None:
        self._tests: dict[str, ABTestConfig] = {}
        self._results: dict[str, list[ABTestResult]] = {}

    def create_test(self, config: ABTestConfig) -> None:
        """Register an A/B test.

        Args:
            config: Test configuration.
        """
        self._tests[config.test_name] = config
        self._results[config.test_name] = []
        logger.info(f"Created A/B test '{config.test_name}': {config.model_a} vs {config.model_b}")

    def get_test(self, test_name: str) -> ABTestConfig | None:
        """Retrieve a test configuration.

        Args:
            test_name: Name of the test.

        Returns:
            ABTestConfig or None if not found.
        """
        return self._tests.get(test_name)

    def remove_test(self, test_name: str) -> None:
        """Remove a test and its results.

        Args:
            test_name: Name of the test to remove.
        """
        self._tests.pop(test_name, None)
        self._results.pop(test_name, None)

    def list_tests(self) -> list[str]:
        """List all test names.

        Returns:
            List of test names.
        """
        return list(self._tests.keys())

    def select_model(self, test_name: str, context_key: str | None = None) -> str:
        """Select a model for the given test using traffic splitting.

        Uses deterministic hash-based assignment when context_key is provided,
        otherwise falls back to random assignment.

        Args:
            test_name: Name of the test.
            context_key: Optional key for deterministic assignment (e.g., session_id).

        Returns:
            Selected model name.

        Raises:
            KeyError: If test not found.
        """
        config = self._tests.get(test_name)
        if not config:
            raise KeyError(f"Test '{test_name}' not found")

        if not config.enabled:
            return config.model_a

        if context_key:
            hash_input = f"{test_name}:{context_key}".encode()
            hash_val = int(hashlib.md5(hash_input).hexdigest(), 16)  # noqa: S324
            ratio = (hash_val % 10000) / 10000.0
        else:
            import random

            ratio = random.random()  # noqa: S311

        return config.model_a if ratio < config.traffic_split else config.model_b

    def record_result(self, result: ABTestResult) -> None:
        """Record an A/B test result.

        Args:
            result: The test result to record.
        """
        if result.test_name not in self._results:
            self._results[result.test_name] = []
        self._results[result.test_name].append(result)

    def get_results(self, test_name: str) -> dict[str, dict]:
        """Get aggregated results for a test.

        Args:
            test_name: Name of the test.

        Returns:
            Dict mapping model name to aggregated metrics:
            {model: {success_rate, avg_latency_ms, total_tokens, sample_count}}
        """
        results = self._results.get(test_name, [])
        grouped: dict[str, list[ABTestResult]] = {}

        for r in results:
            grouped.setdefault(r.model, []).append(r)

        aggregated: dict[str, dict] = {}
        for model, model_results in grouped.items():
            count = len(model_results)
            successes = sum(1 for r in model_results if r.success)
            total_latency = sum(r.latency_ms for r in model_results)
            total_tokens = sum(sum(r.token_usage.values()) for r in model_results)

            aggregated[model] = {
                "success_rate": successes / count if count > 0 else 0.0,
                "avg_latency_ms": total_latency / count if count > 0 else 0.0,
                "total_tokens": total_tokens,
                "sample_count": count,
            }

        return aggregated

    def calculate_significance(self, test_name: str) -> dict:
        """Calculate statistical significance using a two-proportion z-test.

        Compares success rates between model_a and model_b.

        Args:
            test_name: Name of the test.

        Returns:
            Dict with z_score, p_value, is_significant, and winner.
        """
        config = self._tests.get(test_name)
        if not config:
            return {"z_score": 0.0, "p_value": 1.0, "is_significant": False, "winner": None}

        results = self.get_results(test_name)
        a_data = results.get(config.model_a)
        b_data = results.get(config.model_b)

        if not a_data or not b_data:
            return {"z_score": 0.0, "p_value": 1.0, "is_significant": False, "winner": None}

        n_a = a_data["sample_count"]
        n_b = b_data["sample_count"]
        p_a = a_data["success_rate"]
        p_b = b_data["success_rate"]

        # Need sufficient samples for z-test validity
        if n_a < 2 or n_b < 2:
            return {"z_score": 0.0, "p_value": 1.0, "is_significant": False, "winner": None}

        # Pooled proportion
        p_pool = (p_a * n_a + p_b * n_b) / (n_a + n_b)

        if p_pool == 0.0 or p_pool == 1.0:
            return {"z_score": 0.0, "p_value": 1.0, "is_significant": False, "winner": None}

        # Standard error
        se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))

        if se == 0.0:
            return {"z_score": 0.0, "p_value": 1.0, "is_significant": False, "winner": None}

        z_score = (p_a - p_b) / se

        # Two-tailed p-value using normal CDF approximation
        p_value = 2 * (1 - _normal_cdf(abs(z_score)))

        is_significant = p_value < 0.05
        winner = None
        if is_significant:
            winner = config.model_a if p_a > p_b else config.model_b

        return {
            "z_score": z_score,
            "p_value": p_value,
            "is_significant": is_significant,
            "winner": winner,
        }


def _normal_cdf(x: float) -> float:
    """Approximate the standard normal CDF using the error function.

    Args:
        x: Input value.

    Returns:
        Approximate cumulative probability.
    """
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
