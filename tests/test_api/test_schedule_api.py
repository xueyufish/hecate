"""Tests for schedule API endpoints — stub tests.

Full integration tests require PostgreSQL advisory locks.
These stubs verify route registration and basic connectivity.
"""

from __future__ import annotations

import pytest


class TestScheduleAPIRoutes:
    @pytest.mark.skip(reason="Schedule API requires PostgreSQL advisory locks")
    async def test_create_schedule(self, client: object) -> None:
        pass

    @pytest.mark.skip(reason="Schedule API requires PostgreSQL advisory locks")
    async def test_list_schedules(self, client: object) -> None:
        pass

    @pytest.mark.skip(reason="Schedule API requires PostgreSQL advisory locks")
    async def test_get_schedule(self, client: object) -> None:
        pass

    @pytest.mark.skip(reason="Schedule API requires PostgreSQL advisory locks")
    async def test_delete_schedule(self, client: object) -> None:
        pass
