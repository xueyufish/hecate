"""Tests for ScheduleManager — cron validation."""

from __future__ import annotations

from hecate.services.scheduling.manager import ScheduleManager


class TestCronValidation:
    def test_valid_cron_if_croniter_available(self) -> None:
        try:
            import croniter  # noqa: F401
        except ImportError:
            return

        manager = ScheduleManager()
        assert manager.validate_cron("*/5 * * * *")
        assert manager.validate_cron("0 9 * * 1-5")
        assert manager.validate_cron("30 */2 * * *")

    def test_invalid_cron_if_croniter_available(self) -> None:
        try:
            import croniter  # noqa: F401
        except ImportError:
            return

        manager = ScheduleManager()
        assert not manager.validate_cron("")
        assert not manager.validate_cron("not-a-cron")
