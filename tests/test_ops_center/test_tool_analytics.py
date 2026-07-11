"""Tests for ToolAnalyticsService and tool analytics API endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.trace import TraceModel
from hecate.services.ops_center.tool_analytics import ToolAnalyticsService


async def _insert_tool_span(
    db: AsyncSession,
    name: str = "tool:get_weather",
    status: str = "completed",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    agent_id: uuid.UUID | None = None,
    output_data: dict | None = None,
) -> TraceModel:
    """Helper to insert a tool span into TraceModel."""
    now = datetime.now(UTC)
    record = TraceModel(
        trace_id=uuid.uuid4(),
        parent_id=None,
        type="tool",
        name=name,
        session_id=uuid.uuid4(),
        agent_id=agent_id,
        status=status,
        level="DEFAULT",
        start_time=start_time or now,
        end_time=end_time or (now + timedelta(seconds=1)),
        output_data=output_data,
        metadata_={},
    )
    db.add(record)
    await db.flush()
    return record


class TestGetOverview:
    """Tests for ToolAnalyticsService.get_overview()."""

    async def test_with_data(self, db_session: AsyncSession) -> None:
        """Returns correct metrics with populated data."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        # Insert 3 completed + 1 error
        await _insert_tool_span(
            db_session,
            name="tool:a",
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )
        await _insert_tool_span(
            db_session,
            name="tool:a",
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=2),
        )
        await _insert_tool_span(
            db_session,
            name="tool:b",
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=3),
        )
        await _insert_tool_span(
            db_session,
            name="tool:b",
            status="error",
            start_time=now,
            end_time=now + timedelta(seconds=4),
            output_data={"error": "timeout"},
        )

        service = ToolAnalyticsService(db_session)
        result = await service.get_overview(start, end)

        assert result["total_executions"] == 4
        assert result["success_rate"] == 0.75
        assert result["error_count"] == 1
        assert result["unique_tools"] == 2
        assert result["avg_latency_ms"] > 0

    async def test_empty_data(self, db_session: AsyncSession) -> None:
        """Returns zeros for empty data."""
        now = datetime.now(UTC)
        service = ToolAnalyticsService(db_session)
        result = await service.get_overview(now - timedelta(days=1), now + timedelta(hours=1))

        assert result["total_executions"] == 0
        assert result["success_rate"] == 1.0
        assert result["error_count"] == 0
        assert result["unique_tools"] == 0
        assert result["avg_latency_ms"] == 0

    async def test_agent_filter(self, db_session: AsyncSession) -> None:
        """Filters by agent_id when provided."""
        now = datetime.now(UTC)
        agent_a = uuid.uuid4()
        agent_b = uuid.uuid4()

        await _insert_tool_span(
            db_session,
            name="tool:x",
            status="completed",
            agent_id=agent_a,
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )
        await _insert_tool_span(
            db_session,
            name="tool:y",
            status="completed",
            agent_id=agent_b,
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )

        service = ToolAnalyticsService(db_session)
        result = await service.get_overview(now - timedelta(days=1), now + timedelta(hours=1), agent_id=agent_a)

        assert result["total_executions"] == 1


class TestGetToolDetails:
    """Tests for ToolAnalyticsService.get_tool_details()."""

    async def test_returns_details(self, db_session: AsyncSession) -> None:
        """Returns per-tool metrics and top errors."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        await _insert_tool_span(
            db_session,
            name="tool:weather",
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )
        await _insert_tool_span(
            db_session,
            name="tool:weather",
            status="error",
            start_time=now,
            end_time=now + timedelta(seconds=2),
            output_data={"error": "timeout"},
        )

        service = ToolAnalyticsService(db_session)
        result = await service.get_tool_details("weather", start, end)

        assert result is not None
        assert result["tool_name"] == "weather"
        assert result["executions"] == 2
        assert result["success_rate"] == 0.5
        assert len(result["top_errors"]) == 1
        assert result["top_errors"][0]["message"] == "timeout"

    async def test_unknown_tool_returns_none(self, db_session: AsyncSession) -> None:
        """Returns None for tool with no records."""
        now = datetime.now(UTC)
        service = ToolAnalyticsService(db_session)
        result = await service.get_tool_details("nonexistent", now - timedelta(days=1), now + timedelta(hours=1))
        assert result is None


class TestGetTrends:
    """Tests for ToolAnalyticsService.get_trends()."""

    async def test_daily_trends(self, db_session: AsyncSession) -> None:
        """Returns correct number of data points for daily granularity."""
        now = datetime.now(UTC)

        # Insert spans on 3 different days
        for i in range(3):
            day = now - timedelta(days=i)
            await _insert_tool_span(
                db_session,
                name="tool:a",
                status="completed",
                start_time=day,
                end_time=day + timedelta(seconds=1),
            )

        service = ToolAnalyticsService(db_session)
        result = await service.get_trends("daily", days=7)

        assert len(result) >= 1
        for point in result:
            assert "date" in point
            assert "total" in point
            assert "errors" in point
            assert "avg_latency_ms" in point

    async def test_tool_filter(self, db_session: AsyncSession) -> None:
        """Filters by tool_name when provided."""
        now = datetime.now(UTC)

        await _insert_tool_span(
            db_session,
            name="tool:a",
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )
        await _insert_tool_span(
            db_session,
            name="tool:b",
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )

        service = ToolAnalyticsService(db_session)
        result = await service.get_trends("daily", days=7, tool_name="a")

        total = sum(p["total"] for p in result)
        assert total == 1


class TestGetTopErrors:
    """Tests for ToolAnalyticsService.get_top_errors()."""

    async def test_sorted_by_count(self, db_session: AsyncSession) -> None:
        """Returns errors sorted by occurrence count."""
        now = datetime.now(UTC)

        # Insert 3 errors of type A, 1 of type B
        for _ in range(3):
            await _insert_tool_span(
                db_session,
                name="tool:x",
                status="error",
                start_time=now,
                output_data={"error": "timeout"},
            )
        await _insert_tool_span(
            db_session,
            name="tool:x",
            status="error",
            start_time=now,
            output_data={"error": "auth"},
        )

        service = ToolAnalyticsService(db_session)
        result = await service.get_top_errors(limit=10)

        assert len(result) >= 2
        assert result[0]["count"] >= result[1]["count"]

    async def test_limit(self, db_session: AsyncSession) -> None:
        """Respects limit parameter."""
        now = datetime.now(UTC)

        for i in range(5):
            await _insert_tool_span(
                db_session,
                name="tool:x",
                status="error",
                start_time=now,
                output_data={"error": f"err_{i}"},
            )

        service = ToolAnalyticsService(db_session)
        result = await service.get_top_errors(limit=3)

        assert len(result) <= 3
