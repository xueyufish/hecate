"""MCP session manager — maps MCP tool calls to Hecate sessions."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.session import SessionModel

logger = logging.getLogger(__name__)


class MCPSessionManager:
    """Create and look up Hecate sessions for MCP tool calls."""

    def __init__(self) -> None:
        self._sessions: dict[str, uuid.UUID] = {}

    async def create_session(self, agent_id: str, db: AsyncSession) -> dict:
        """Create a new Hecate session for an agent.

        Args:
            agent_id: UUID string of the agent.
            db: Async database session.

        Returns:
            Dict with ``session_id`` and ``status``.
        """
        agent_uuid = uuid.UUID(agent_id)
        session = SessionModel(agent_id=agent_uuid, status="active")
        db.add(session)
        await db.flush()
        await db.refresh(session)

        session_id = str(session.id)
        self._sessions[session_id] = session.id
        logger.info("Created MCP session %s for agent %s", session_id, agent_id)
        return {"session_id": session_id, "status": "active"}

    async def get_session(self, session_id: str, db: AsyncSession) -> SessionModel | None:
        """Look up a session by UUID.

        Args:
            session_id: UUID string of the session.
            db: Async database session.

        Returns:
            The ``SessionModel`` if found, else ``None``.
        """
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return None
        result = await db.execute(select(SessionModel).where(SessionModel.id == sid, ~SessionModel.deleted))
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        agent_id: str | None,
        db: AsyncSession,
    ) -> list[dict]:
        """List active sessions, optionally filtered by agent.

        Args:
            agent_id: Optional UUID string to filter by agent.
            db: Async database session.

        Returns:
            List of session summary dicts.
        """
        query = select(SessionModel).where(
            ~SessionModel.deleted,
            SessionModel.status == "active",
        )
        if agent_id:
            query = query.where(SessionModel.agent_id == uuid.UUID(agent_id))

        result = await db.execute(query.order_by(SessionModel.created_at.desc()).limit(50))
        sessions = result.scalars().all()
        return [
            {
                "session_id": str(s.id),
                "agent_id": str(s.agent_id),
                "status": s.status,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sessions
        ]
