"""Tests for OpsCenterOverviewService — unified aggregation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.conversation import ConversationModel
from hecate.models.trace import TraceModel
from hecate.services.ops_center.agent_health import AgentHealthService
from hecate.services.ops_center.overview import OpsCenterOverviewService


async def _insert_trace(
    db: AsyncSession,
    agent_id: uuid.UUID | None = None,
    type: str = "trace",
    status: str = "completed",
    name: str = "session:test",
) -> None:
    """Helper to insert a trace record."""
    record = TraceModel(
        trace_id=uuid.uuid4(),
        type=type,
        name=name,
        agent_id=agent_id,
        status=status,
        start_time=datetime.now(UTC),
        end_time=datetime.now(UTC) + timedelta(seconds=1),
        metadata_={},
    )
    db.add(record)
    await db.flush()


async def _insert_conversation(
    db: AsyncSession,
    quality_score: float | None = None,
) -> ConversationModel:
    """Helper to insert a conversation."""
    conv = ConversationModel(
        agent_id=uuid.uuid4(),
        title="Test",
        quality_score=quality_score,
    )
    db.add(conv)
    await db.flush()
    return conv


async def _insert_agent(
    db: AsyncSession,
    name: str = "test-agent",
) -> AgentModel:
    """Helper to insert an agent."""
    agent = AgentModel(
        name=name,
        model_config_db={"model": "gpt-4o"},
    )
    db.add(agent)
    await db.flush()
    return agent


class TestGetOverview:
    """Tests for OpsCenterOverviewService.get_overview()."""

    async def test_all_sources_available(self, db_session: AsyncSession) -> None:
        """Returns all three sections when all sources work."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        # Insert test data
        await _insert_trace(db_session, agent_id=uuid.uuid4(), status="completed")

        service = OpsCenterOverviewService(db_session)
        result = await service.get_overview(start, end)

        assert result["agent_health"] is not None
        assert result["tool_analytics"] is not None
        assert result["conversation_analytics"] is not None
        assert result["errors"] == []

    async def test_one_source_fails(self, db_session: AsyncSession) -> None:
        """Returns null for failed source with error message."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        service = OpsCenterOverviewService(db_session)

        # Mock one service to fail
        with patch.object(
            AgentHealthService,
            "get_fleet_overview",
            side_effect=Exception("DB error"),
        ):
            result = await service.get_overview(start, end)

        assert result["agent_health"] is None
        assert result["tool_analytics"] is not None
        assert result["conversation_analytics"] is not None
        assert len(result["errors"]) == 1
        assert "agent_health" in result["errors"][0]

    async def test_all_sources_fail(self, db_session: AsyncSession) -> None:
        """Returns null for all sources when all fail."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        service = OpsCenterOverviewService(db_session)

        with (
            patch.object(
                AgentHealthService,
                "get_fleet_overview",
                side_effect=Exception("error1"),
            ),
            patch.object(
                AgentHealthService,
                "get_fleet_overview",
                side_effect=Exception("error1"),
            ),
        ):
            result = await service.get_overview(start, end)

        assert result["errors"]


class TestGetRecentActivity:
    """Tests for OpsCenterOverviewService.get_recent_activity()."""

    async def test_with_critical_agents(self, db_session: AsyncSession) -> None:
        """Returns agent error events for agents with error traces."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        agent = await _insert_agent(db_session, name="my-agent")
        await _insert_trace(db_session, agent_id=agent.id, status="error")

        service = OpsCenterOverviewService(db_session)
        result = await service.get_recent_activity(start, end)

        assert len(result) >= 1
        assert result[0]["source"] == "agent_health"
        assert result[0]["severity"] == "critical"

    async def test_with_tool_errors(self, db_session: AsyncSession) -> None:
        """Returns tool error events."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        await _insert_trace(
            db_session,
            type="tool",
            status="error",
            name="tool:web_search",
        )

        service = OpsCenterOverviewService(db_session)
        result = await service.get_recent_activity(start, end)

        assert len(result) >= 1
        assert any(r["source"] == "tool_analytics" for r in result)

    async def test_with_low_quality_conversations(self, db_session: AsyncSession) -> None:
        """Returns low quality conversation events."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        await _insert_conversation(db_session, quality_score=0.3)

        service = OpsCenterOverviewService(db_session)
        result = await service.get_recent_activity(start, end)

        assert len(result) >= 1
        assert any(r["source"] == "conversation_analytics" for r in result)

    async def test_sorted_by_timestamp_desc(self, db_session: AsyncSession) -> None:
        """Events are sorted by timestamp descending."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        service = OpsCenterOverviewService(db_session)
        result = await service.get_recent_activity(start, end)

        timestamps = [r["timestamp"] for r in result if r["timestamp"]]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_max_20_items(self, db_session: AsyncSession) -> None:
        """Returns at most 20 items."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        service = OpsCenterOverviewService(db_session)
        result = await service.get_recent_activity(start, end, limit=20)

        assert len(result) <= 20

    async def test_empty_when_no_events(self, db_session: AsyncSession) -> None:
        """Returns empty list when no events."""
        now = datetime.now(UTC)
        service = OpsCenterOverviewService(db_session)
        result = await service.get_recent_activity(now - timedelta(days=1), now + timedelta(hours=1))

        assert result == []
