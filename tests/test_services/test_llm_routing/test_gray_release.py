"""Unit tests for GrayReleaseManager."""

from __future__ import annotations

from hecate.services.llm.gray_release import (
    GrayReleaseConfig,
    GrayReleaseManager,
    RolloutSchedule,
)


def _make_config(
    name: str = "release-1",
    models: dict[str, float] | None = None,
) -> GrayReleaseConfig:
    return GrayReleaseConfig(
        release_name=name,
        models=models or {"gpt-4o": 0.9, "gpt-4o-mini": 0.1},
    )


class TestGrayReleaseManager:
    def test_create_and_list(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(_make_config("r1"))
        mgr.create_release(_make_config("r2"))

        assert sorted(mgr.list_releases()) == ["r1", "r2"]

    def test_get_release(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(_make_config("r1"))

        assert mgr.get_release("r1") is not None
        assert mgr.get_release("missing") is None

    def test_remove_release(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(_make_config("r1"))
        mgr.remove_release("r1")

        assert mgr.list_releases() == []

    def test_weight_normalization(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(GrayReleaseConfig("r1", models={"a": 3, "b": 7}))

        release = mgr.get_release("r1")
        assert release is not None
        assert abs(release.models["a"] - 0.3) < 0.001
        assert abs(release.models["b"] - 0.7) < 0.001

    def test_select_model_deterministic(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(_make_config("r1"))

        m1 = mgr.select_model("r1", context_key="user-123")
        m2 = mgr.select_model("r1", context_key="user-123")

        assert m1 == m2

    def test_select_model_distribution(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(GrayReleaseConfig("r1", models={"a": 0.5, "b": 0.5}))

        counts: dict[str, int] = {"a": 0, "b": 0}
        for i in range(200):
            model = mgr.select_model("r1", context_key=f"user-{i}")
            counts[model] += 1

        assert counts["a"] > 50
        assert counts["b"] > 50

    def test_select_model_not_found(self) -> None:
        mgr = GrayReleaseManager()
        try:
            mgr.select_model("missing")
            raise AssertionError("Expected KeyError")
        except KeyError:
            pass

    def test_select_model_disabled(self) -> None:
        mgr = GrayReleaseManager()
        cfg = _make_config("r1")
        cfg.enabled = False
        mgr.create_release(cfg)

        model = mgr.select_model("r1")
        assert model in ("gpt-4o", "gpt-4o-mini")

    def test_update_weights(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(_make_config("r1"))
        mgr.update_weights("r1", {"gpt-4o": 0.2, "gpt-4o-mini": 0.8})

        release = mgr.get_release("r1")
        assert release is not None
        assert abs(release.models["gpt-4o-mini"] - 0.8) < 0.001

    def test_update_weights_nonexistent(self) -> None:
        mgr = GrayReleaseManager()
        mgr.update_weights("missing", {"a": 1.0})

    def test_set_rollout_schedule(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(GrayReleaseConfig("r1", models={"old": 1.0}))

        schedule = RolloutSchedule(
            release_name="r1",
            stages=[
                {"model_name": "old", "target_weight": 1.0, "trigger_at_minutes": 0},
                {"model_name": "new", "target_weight": 1.0, "trigger_at_minutes": 60},
            ],
        )
        mgr.set_rollout_schedule("r1", schedule)
        mgr.advance_rollout("r1", 30)

        release = mgr.get_release("r1")
        assert release is not None
        assert "old" in release.models

    def test_advance_rollout_to_second_stage(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(GrayReleaseConfig("r1", models={"old": 1.0}))

        schedule = RolloutSchedule(
            release_name="r1",
            stages=[
                {"model_name": "old", "target_weight": 1.0, "trigger_at_minutes": 0},
                {"model_name": "new", "target_weight": 1.0, "trigger_at_minutes": 60},
            ],
        )
        mgr.set_rollout_schedule("r1", schedule)
        mgr.advance_rollout("r1", 120)

        release = mgr.get_release("r1")
        assert release is not None
        assert abs(release.models.get("new", 0.0) - 1.0) < 0.001

    def test_get_current_stage(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(_make_config("r1"))

        schedule = RolloutSchedule(
            release_name="r1",
            stages=[
                {"model_name": "old", "target_weight": 0.8, "trigger_at_minutes": 0},
                {"model_name": "new", "target_weight": 0.5, "trigger_at_minutes": 30},
                {"model_name": "new", "target_weight": 1.0, "trigger_at_minutes": 60},
            ],
        )
        mgr.set_rollout_schedule("r1", schedule)

        assert mgr.get_current_stage("r1", 15)["trigger_at_minutes"] == 0
        assert mgr.get_current_stage("r1", 45)["trigger_at_minutes"] == 30
        assert mgr.get_current_stage("r1", 90)["trigger_at_minutes"] == 60

    def test_get_current_stage_no_schedule(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(_make_config("r1"))

        assert mgr.get_current_stage("r1", 100) is None

    def test_zero_weights_get_equal_distribution(self) -> None:
        mgr = GrayReleaseManager()
        mgr.create_release(GrayReleaseConfig("r1", models={"a": 0.0, "b": 0.0}))

        release = mgr.get_release("r1")
        assert release is not None
        assert abs(release.models["a"] - 0.5) < 0.001
        assert abs(release.models["b"] - 0.5) < 0.001
