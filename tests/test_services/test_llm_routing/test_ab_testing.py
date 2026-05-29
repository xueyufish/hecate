"""Unit tests for ABTestManager."""

from __future__ import annotations

from hecate.services.llm.ab_testing import ABTestConfig, ABTestManager, ABTestResult


def _make_config(
    test_name: str = "test-1",
    model_a: str = "gpt-4o",
    model_b: str = "claude-3",
    split: float = 0.5,
) -> ABTestConfig:
    return ABTestConfig(
        test_name=test_name,
        model_a=model_a,
        model_b=model_b,
        traffic_split=split,
    )


class TestABTestManager:
    def test_create_and_list(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1"))
        mgr.create_test(_make_config("t2"))

        assert sorted(mgr.list_tests()) == ["t1", "t2"]

    def test_get_test(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1"))

        assert mgr.get_test("t1") is not None
        assert mgr.get_test("missing") is None

    def test_remove_test(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1"))
        mgr.remove_test("t1")

        assert mgr.list_tests() == []

    def test_select_model_deterministic(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1", split=0.5))

        m1 = mgr.select_model("t1", context_key="user-123")
        m2 = mgr.select_model("t1", context_key="user-123")

        assert m1 == m2

    def test_select_model_different_users(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1", split=0.5))

        models = {mgr.select_model("t1", context_key=f"user-{i}") for i in range(100)}

        assert len(models) == 2

    def test_select_model_disabled(self) -> None:
        mgr = ABTestManager()
        cfg = _make_config("t1")
        cfg.enabled = False
        mgr.create_test(cfg)

        assert mgr.select_model("t1") == "gpt-4o"

    def test_select_model_not_found(self) -> None:
        mgr = ABTestManager()
        try:
            mgr.select_model("missing")
            raise AssertionError("Expected KeyError")
        except KeyError:
            pass

    def test_record_and_get_results(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1"))

        mgr.record_result(ABTestResult("t1", "gpt-4o", success=True, latency_ms=100.0, token_usage={"total": 50}))
        mgr.record_result(ABTestResult("t1", "gpt-4o", success=True, latency_ms=150.0, token_usage={"total": 60}))
        mgr.record_result(ABTestResult("t1", "claude-3", success=False, latency_ms=200.0, token_usage={"total": 40}))

        results = mgr.get_results("t1")

        assert results["gpt-4o"]["success_rate"] == 1.0
        assert results["gpt-4o"]["avg_latency_ms"] == 125.0
        assert results["gpt-4o"]["sample_count"] == 2
        assert results["claude-3"]["success_rate"] == 0.0
        assert results["claude-3"]["sample_count"] == 1

    def test_get_results_empty(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1"))

        assert mgr.get_results("t1") == {}

    def test_calculate_significance_clear_winner(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1"))

        for _ in range(50):
            mgr.record_result(ABTestResult("t1", "gpt-4o", success=True, latency_ms=100.0))
        for _ in range(50):
            mgr.record_result(ABTestResult("t1", "claude-3", success=False, latency_ms=200.0))

        sig = mgr.calculate_significance("t1")

        assert sig["is_significant"] is True
        assert sig["winner"] == "gpt-4o"
        assert sig["z_score"] > 0
        assert sig["p_value"] < 0.05

    def test_calculate_significance_no_difference(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1"))

        for _ in range(50):
            mgr.record_result(ABTestResult("t1", "gpt-4o", success=True, latency_ms=100.0))
            mgr.record_result(ABTestResult("t1", "claude-3", success=True, latency_ms=100.0))

        sig = mgr.calculate_significance("t1")

        assert sig["is_significant"] is False
        assert sig["winner"] is None

    def test_calculate_significance_insufficient_data(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1"))

        mgr.record_result(ABTestResult("t1", "gpt-4o", success=True, latency_ms=100.0))

        sig = mgr.calculate_significance("t1")
        assert sig["is_significant"] is False

    def test_calculate_significance_test_not_found(self) -> None:
        mgr = ABTestManager()
        sig = mgr.calculate_significance("missing")

        assert sig["is_significant"] is False
        assert sig["winner"] is None

    def test_traffic_split_100_percent(self) -> None:
        mgr = ABTestManager()
        mgr.create_test(_make_config("t1", split=1.0))

        models = {mgr.select_model("t1", context_key=f"user-{i}") for i in range(50)}

        assert models == {"gpt-4o"}
