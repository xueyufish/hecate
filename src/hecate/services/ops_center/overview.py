"""Ops Center overview service — unified aggregation across all subsystems.

Fans out to AgentHealthService, ToolAnalyticsService, and
ConversationAnalyticsService in parallel. Handles partial failures
gracefully (returns null for failed sources).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.conversation import ConversationModel
from hecate.models.trace import TraceModel
from hecate.services.ops_center.agent_health import AgentHealthService
from hecate.services.ops_center.conversation_analytics import ConversationAnalyticsService
from hecate.services.ops_center.tool_analytics import ToolAnalyticsService

logger = logging.getLogger(__name__)


class OpsCenterOverviewService:
    """Service for aggregating Ops Center data from all subsystems.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_overview(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict[str, Any]:
        """Aggregate overview from all three subsystems in parallel.

        Returns:
            Dict with agent_health, tool_analytics, conversation_analytics
            (null on failure), and errors list.
        """
        agent_svc = AgentHealthService(self._db)
        tool_svc = ToolAnalyticsService(self._db)
        conv_svc = ConversationAnalyticsService(self._db)

        results = await asyncio.gather(
            agent_svc.get_fleet_overview(start_date, end_date),
            tool_svc.get_overview(start_date, end_date),
            conv_svc.get_overview(start_date, end_date),
            return_exceptions=True,
        )

        errors: list[str] = []
        agent_health: dict[str, Any] | None = None
        tool_analytics: dict[str, Any] | None = None
        conversation_analytics: dict[str, Any] | None = None

        if isinstance(results[0], Exception):
            errors.append(f"agent_health: {results[0]}")
            logger.warning("Agent health aggregation failed: %s", results[0])
        else:
            agent_health = results[0]

        if isinstance(results[1], Exception):
            errors.append(f"tool_analytics: {results[1]}")
            logger.warning("Tool analytics aggregation failed: %s", results[1])
        else:
            tool_analytics = results[1]

        if isinstance(results[2], Exception):
            errors.append(f"conversation_analytics: {results[2]}")
            logger.warning("Conversation analytics aggregation failed: %s", results[2])
        else:
            conversation_analytics = results[2]

        return {
            "agent_health": agent_health,
            "tool_analytics": tool_analytics,
            "conversation_analytics": conversation_analytics,
            "errors": errors,
        }

    async def get_recent_activity(
        self,
        start_date: datetime,
        end_date: datetime,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get recent notable events across all subsystems.

        Queries critical/warning agents, recent tool errors, and
        low-quality conversations. Merges and sorts by timestamp.

        Returns:
            List of activity items with source, severity, title,
            timestamp, link.
        """
        items: list[dict[str, Any]] = []

        # 1. Critical/warning agents from root trace spans
        agent_q = (
            select(
                TraceModel.agent_id,
            )
            .where(
                TraceModel.type == "trace",
                TraceModel.start_time >= start_date,
                TraceModel.start_time <= end_date,
                TraceModel.status == "error",
                TraceModel.agent_id.isnot(None),
                ~TraceModel.deleted,
            )
            .group_by(TraceModel.agent_id)
        )
        agent_errors = (await self._db.execute(agent_q)).all()

        for row in agent_errors:
            agent_id = row.agent_id
            # Get agent name
            agent_q = select(AgentModel.name).where(AgentModel.id == agent_id)
            agent_name = (await self._db.execute(agent_q)).scalar() or str(agent_id)[:8]

            # Get last error time
            last_q = (
                select(TraceModel.start_time)
                .where(
                    TraceModel.type == "trace",
                    TraceModel.agent_id == agent_id,
                    TraceModel.status == "error",
                    ~TraceModel.deleted,
                )
                .order_by(TraceModel.start_time.desc())
                .limit(1)
            )
            last_time = (await self._db.execute(last_q)).scalar()

            items.append(
                {
                    "source": "agent_health",
                    "severity": "critical",
                    "title": f'Agent "{agent_name}" has errors',
                    "timestamp": last_time.isoformat() if last_time else None,
                    "link": "/ops-center/agents",
                }
            )

        # 2. Recent tool errors
        tool_q = (
            select(
                TraceModel.name,
                TraceModel.end_time,
            )
            .where(
                TraceModel.type == "tool",
                TraceModel.status == "error",
                TraceModel.start_time >= start_date,
                TraceModel.start_time <= end_date,
                ~TraceModel.deleted,
            )
            .order_by(TraceModel.end_time.desc())
            .limit(10)
        )
        tool_errors = (await self._db.execute(tool_q)).all()

        for row in tool_errors:
            tool_name = row.name.replace("tool:", "") if row.name else "unknown"
            items.append(
                {
                    "source": "tool_analytics",
                    "severity": "warning",
                    "title": f'Tool "{tool_name}" execution error',
                    "timestamp": row.end_time.isoformat() if row.end_time else None,
                    "link": "/ops-center/tools",
                }
            )

        # 3. Low-quality conversations
        conv_q = (
            select(
                ConversationModel.id,
                ConversationModel.title,
                ConversationModel.quality_score,
                ConversationModel.created_at,
            )
            .where(
                ConversationModel.quality_score < 0.5,
                ConversationModel.quality_score.isnot(None),
                ConversationModel.created_at >= start_date,
                ConversationModel.created_at <= end_date,
                ~ConversationModel.deleted,
            )
            .order_by(ConversationModel.created_at.desc())
            .limit(10)
        )
        low_convs = (await self._db.execute(conv_q)).all()

        for row in low_convs:
            title = row.title or str(row.id)[:8]
            items.append(
                {
                    "source": "conversation_analytics",
                    "severity": "warning",
                    "title": f'Low quality conversation "{title}" (score: {row.quality_score:.2f})',
                    "timestamp": row.created_at.isoformat() if row.created_at else None,
                    "link": "/ops-center/conversations",
                }
            )

        # Sort by timestamp descending, limit
        items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return items[:limit]
