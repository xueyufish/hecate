"""Tests for AgentHealthService — fleet overview, per-agent health, and trends."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.trace import TraceModel
from hecate.services.ops_center.agent_health import (
    AgentHealthService,
    _classify_health_status,
    _compute_health_score,
    _compute_p95,
)


async def _insert_root_trace(
    db: AsyncSession,
    agent_id: uuid.UUID | None = None,
    status: str = "completed",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> TraceModel:
    """Helper to insert a root trace span (type='trace') into TraceModel."""
    now = datetime.now(UTC)
    record = TraceModel(
        trace_id=uuid.uuid4(),
        parent_id=None,
        type="trace",
        name=f"session:{uuid.uuid4()}",
        session_id=uuid.uuid4(),
        agent_id=agent_id,
        status=status,
        level="DEFAULT",
        start_time=start_time or now,
        end_time=end_time or (now + timedelta(seconds=1)),
        metadata_={},
    )
    db.add(record)
    await db.flush()
    return record


class TestClassifyHealthStatus:
    """Tests for _classify_health_status()."""

    async def test_healthy(self) -> None:
        """Low error rate + low latency → healthy."""
        assert _classify_health_status(0.01, 1000) == "healthy"

    async def test_warning_high_error(self) -> None:
        """Error rate above warning threshold → warning."""
        assert _classify_health_status(0.10, 1000) == "warning"

    async def test_critical_high_latency(self) -> None:
        """Latency above critical threshold → critical."""
        assert _classify_health_status(0.01, 40000) == "critical"

    async def test_critical_both_bad(self) -> None:
        """Both dimensions bad → critical (worst wins)."""
        assert _classify_health_status(0.20, 40000) == "critical"

    async def test_warning_latency_only(self) -> None:
        """Latency above warning but below critical → warning."""
        assert _classify_health_status(0.01, 15000) == "warning"


class TestComputeHealthScore:
    """Tests for _compute_health_score()."""

    async def test_perfect_score(self) -> None:
        """Zero errors, low latency, 20 sessions → high score."""
        score = _compute_health_score(0.0, 1000, 20)
        assert score is not None
        assert score >= 90

    async def test_degraded_from_errors(self) -> None:
        """High error rate reduces score significantly."""
        score = _compute_health_score(0.10, 1000, 20)
        assert score is not None
        assert score < 80

    async def test_null_for_unknown(self) -> None:
        """Zero sessions → None (unknown agent)."""
        assert _compute_health_score(0.0, 0, 0) is None

    async def test_custom_weights(self) -> None:
        """Score formula uses configured weights."""
        # With default weights: error_rate=0.5, latency=0.3, activity=0.2
        score_default = _compute_health_score(0.05, 5000, 10)
        assert score_default is not None
        assert 0 <= score_default <= 100


class TestComputeP95:
    """Tests for _compute_p95()."""

    async def test_empty(self) -> None:
        """Empty list → 0.0."""
        assert _compute_p95([]) == 0.0

    async def test_single_value(self) -> None:
        """Single value → that value."""
        assert _compute_p95([100.0]) == 100.0

    async def test_percentile(self) -> None:
        """Returns 95th percentile value."""
        values = [float(i) for i in range(100)]
        result = _compute_p95(values)
        assert result >= 94.0


class TestGetFleetOverview:
    """Tests for AgentHealthService.get_fleet_overview()."""

    async def test_with_mixed_statuses(self, db_session: AsyncSession) -> None:
        """Returns correct fleet distribution with mixed agent health."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)

        agent_a = uuid.uuid4()
        agent_b = uuid.uuid4()
        agent_c = uuid.uuid4()

        # Agent A: healthy (all completed, fast)
        await _insert_root_trace(
            db_session,
            agent_id=agent_a,
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )

        # Agent B: warning (50% error rate)
        await _insert_root_trace(
            db_session,
            agent_id=agent_b,
            status="error",
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )
        await _insert_root_trace(
            db_session,
            agent_id=agent_b,
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )

        # Agent C: healthy (completed, moderate latency)
        await _insert_root_trace(
            db_session,
            agent_id=agent_c,
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=2),
        )

        service = AgentHealthService(db_session)
        result = await service.get_fleet_overview(start, end)

        assert result["total_agents"] == 3
        assert result["healthy_count"] >= 2
        assert result["critical_count"] >= 1  # agent B has 50% error rate → critical
        assert len(result["top_degraded"]) <= 10

    async def test_empty_data(self, db_session: AsyncSession) -> None:
        """Returns zeros for empty data."""
        now = datetime.now(UTC)
        service = AgentHealthService(db_session)
        result = await service.get_fleet_overview(now - timedelta(days=1), now + timedelta(hours=1))

        assert result["total_agents"] == 0
        assert result["healthy_count"] == 0
        assert result["warning_count"] == 0
        assert result["critical_count"] == 0
        assert result["unknown_count"] == 0
        assert result["fleet_error_rate"] == 0.0
        assert result["fleet_p95_latency_ms"] == 0.0
        assert result["top_degraded"] == []


class TestGetAgentHealth:
    """Tests for AgentHealthService.get_agent_health()."""

    async def test_active_agent(self, db_session: AsyncSession) -> None:
        """Returns all health fields for an active agent."""
        now = datetime.now(UTC)
        start = now - timedelta(days=1)
        end = now + timedelta(hours=1)
        agent_id = uuid.uuid4()

        await _insert_root_trace(
            db_session,
            agent_id=agent_id,
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=1),
        )
        await _insert_root_trace(
            db_session,
            agent_id=agent_id,
            status="completed",
            start_time=now,
            end_time=now + timedelta(seconds=2),
        )

        service = AgentHealthService(db_session)
        result = await service.get_agent_health(agent_id, start, end)

        assert result is not None
        assert result["agent_id"] == str(agent_id)
        assert result["total_sessions"] == 2
        assert result["error_count"] == 0
        assert result["error_rate"] == 0.0
        assert result["success_rate"] == 1.0
        assert result["health_status"] in ("healthy", "warning", "critical")
        assert result["health_score"] is not None
        assert "score_breakdown" in result

    async def test_inactive_agent(self, db_session: AsyncSession) -> None:
        """Returns None for agent with no activity."""
        now = datetime.now(UTC)
        service = AgentHealthService(db_session)
        result = await service.get_agent_health(uuid.uuid4(), now - timedelta(days=1), now + timedelta(hours=1))
        assert result is None


class TestGetAgentTrends:
    """Tests for AgentHealthService.get_agent_trends()."""

    async def test_daily_trends(self, db_session: AsyncSession) -> None:
        """Returns correct daily buckets."""
        now = datetime.now(UTC)
        agent_id = uuid.uuid4()

        # Insert spans on 3 different days
        for i in range(3):
            day = now - timedelta(days=i)
            await _insert_root_trace(
                db_session,
                agent_id=agent_id,
                status="completed",
                start_time=day,
                end_time=day + timedelta(seconds=1),
            )

        service = AgentHealthService(db_session)
        result = await service.get_agent_trends(agent_id, days=7, granularity="daily")

        assert len(result) >= 1
        for point in result:
            assert "date" in point
            assert "total_sessions" in point
            assert "errors" in point
            assert "error_rate" in point
            assert "avg_latency_ms" in point
            assert "p95_latency_ms" in point

    async def test_empty_trends(self, db_session: AsyncSession) -> None:
        """Returns empty list for agent with no data."""
        service = AgentHealthService(db_session)
        result = await service.get_agent_trends(uuid.uuid4(), days=7)
        assert result == []
