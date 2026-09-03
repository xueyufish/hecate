"""Tests for FeatureFlagEvaluator (Tier 2 evaluation logic)."""

from __future__ import annotations

import pytest

from hecate.core.feature_flags.evaluator import (
    FLAG_ACTIVE,
    FLAG_DEPRECATED,
    FLAG_DRAFT,
    FLAG_RETIRED,
    FeatureFlagEvaluator,
)


@pytest.fixture
def evaluator() -> FeatureFlagEvaluator:
    return FeatureFlagEvaluator()


class TestStatusLogic:
    def test_draft_returns_false(self, evaluator):
        assert evaluator.evaluate({"status": FLAG_DRAFT, "enabled": True}) is False

    def test_retired_returns_false(self, evaluator):
        assert evaluator.evaluate({"status": FLAG_RETIRED, "enabled": True}) is False

    def test_unknown_status_returns_false(self, evaluator):
        assert evaluator.evaluate({"status": "weird", "enabled": True}) is False

    def test_active_disabled_returns_false(self, evaluator):
        assert evaluator.evaluate({"status": FLAG_ACTIVE, "enabled": False}) is False

    def test_none_flag_dict_returns_false(self, evaluator):
        assert evaluator.evaluate(None) is False

    def test_empty_dict_returns_false(self, evaluator):
        assert evaluator.evaluate({}) is False

    def test_active_enabled_no_rules_returns_true(self, evaluator):
        assert evaluator.evaluate({"status": FLAG_ACTIVE, "enabled": True}) is True

    def test_deprecated_behaves_like_active(self, evaluator):
        """Deprecated still works; flag-audit tool warns separately."""
        assert evaluator.evaluate({"status": FLAG_DEPRECATED, "enabled": True}) is True


class TestTargetingRules:
    def test_empty_rules_returns_true(self, evaluator):
        assert evaluator.evaluate({"status": FLAG_ACTIVE, "enabled": True, "key": "x"}) is True

    def test_tenant_allowlist_match(self, evaluator):
        rules = {"tenant_allowlist": ["t1", "t2"]}
        assert (
            evaluator.evaluate(
                {"status": FLAG_ACTIVE, "enabled": True, "key": "x", "targeting_rules": rules},
                tenant_id="t1",
            )
            is True
        )

    def test_tenant_allowlist_mismatch(self, evaluator):
        rules = {"tenant_allowlist": ["t1"]}
        assert (
            evaluator.evaluate(
                {"status": FLAG_ACTIVE, "enabled": True, "key": "x", "targeting_rules": rules},
                tenant_id="t2",
            )
            is False
        )

    def test_user_allowlist_match(self, evaluator):
        rules = {"user_allowlist": ["u1"]}
        assert (
            evaluator.evaluate(
                {"status": FLAG_ACTIVE, "enabled": True, "key": "x", "targeting_rules": rules},
                user_id="u1",
            )
            is True
        )

    def test_percentage_always_returns_true_at_100(self, evaluator):
        rules = {"percentage": 100}
        for i in range(20):
            assert (
                evaluator.evaluate(
                    {"status": FLAG_ACTIVE, "enabled": True, "key": f"k{i}", "targeting_rules": rules},
                    user_id=f"u{i}",
                )
                is True
            )

    def test_percentage_always_returns_false_at_0(self, evaluator):
        rules = {"percentage": 0}
        for i in range(20):
            assert (
                evaluator.evaluate(
                    {"status": FLAG_ACTIVE, "enabled": True, "key": f"k{i}", "targeting_rules": rules},
                    user_id=f"u{i}",
                )
                is False
            )

    def test_percentage_is_stable_for_same_user(self, evaluator):
        """Same user_id + flag_key yields consistent result across calls."""
        rules = {"percentage": 50}
        flag = {"status": FLAG_ACTIVE, "enabled": True, "key": "stable_test", "targeting_rules": rules}
        results = {evaluator.evaluate(flag, user_id="u1") for _ in range(20)}
        assert len(results) == 1, "percentage should be stable for same user"

    def test_percentage_distribution_reasonable(self, evaluator):
        rules = {"percentage": 50}
        true_count = 0
        n = 1000
        for i in range(n):
            if evaluator.evaluate(
                {"status": FLAG_ACTIVE, "enabled": True, "key": "dist", "targeting_rules": rules},
                user_id=f"u{i}",
            ):
                true_count += 1
        # Allow 10% tolerance around 50%
        assert 400 <= true_count <= 600, f"got {true_count}/{n}"
