"""Evidence tracker for tool execution results.

Captures, normalizes, and persists tool execution results as structured
evidence records with provenance tracking and importance scoring.
Provides query interface for evidence retrieval.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evidence import EvidenceModel

logger = logging.getLogger(__name__)

# Importance scoring constants
_DEFAULT_IMPORTANCE = 0.5
_ERROR_IMPORTANCE = 0.0
_RE_REFERENCE_BOOST = 0.1
_MAX_IMPORTANCE = 1.0
_MAX_CONTENT_SIZE = 10_240  # 10KB per evidence record


class EvidenceTracker:
    """Tracks and persists tool execution results as evidence.

    Features:
    - Captures tool results with full provenance chain
    - Normalizes JSON and text outputs
    - Assigns importance scores (0.0-1.0)
    - Boosts importance when evidence is re-referenced
    - Provides query interface for evidence retrieval
    """

    def __init__(self, db_session: AsyncSession) -> None:
        """Initialize the evidence tracker.

        Args:
            db_session: Async SQLAlchemy session for database operations.
        """
        self.db = db_session

    async def capture(
        self,
        tool_name: str,
        tool_arguments: dict[str, Any],
        result: Any,
        session_id: UUID,
        conversation_id: UUID | None = None,
        message_id: UUID | None = None,
        turn_index: int | None = None,
        is_error: bool = False,
    ) -> EvidenceModel:
        """Capture a tool execution result as evidence.

        Args:
            tool_name: Name of the tool that was executed.
            tool_arguments: Arguments passed to the tool.
            result: Raw result from the tool.
            session_id: Session that triggered the tool call.
            conversation_id: Optional conversation context.
            message_id: Optional message that triggered the call.
            turn_index: Optional turn number in the conversation.
            is_error: Whether this result represents an error.

        Returns:
            The created EvidenceModel.
        """
        # Normalize the result
        normalized_content, raw_content = self._normalize_result(result)

        # Calculate importance
        importance = _ERROR_IMPORTANCE if is_error else _DEFAULT_IMPORTANCE

        # Build provenance chain
        provenance = {
            "tool_name": tool_name,
            "tool_arguments": tool_arguments,
            "session_id": str(session_id),
            "conversation_id": str(conversation_id) if conversation_id else None,
            "message_id": str(message_id) if message_id else None,
            "turn_index": turn_index,
            "captured_at": datetime.now(tz=UTC).isoformat(),
        }

        # Truncate content if too large
        if raw_content and len(raw_content) > _MAX_CONTENT_SIZE:
            raw_content = raw_content[:_MAX_CONTENT_SIZE] + "... [truncated]"

        # Create evidence record
        evidence = EvidenceModel(
            session_id=session_id,
            conversation_id=conversation_id,
            message_id=message_id,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            raw_content=raw_content,
            normalized_content=normalized_content,
            is_error=is_error,
            importance=importance,
            source_type="tool",
            provenance=provenance,
        )

        self.db.add(evidence)
        await self.db.flush()

        logger.debug(f"Captured evidence for tool '{tool_name}': importance={importance}, is_error={is_error}")
        return evidence

    async def boost_importance(
        self,
        evidence_id: UUID,
    ) -> float:
        """Boost importance of an evidence record when re-referenced.

        Args:
            evidence_id: ID of the evidence to boost.

        Returns:
            New importance score after boost.
        """
        stmt = select(EvidenceModel).where(
            EvidenceModel.id == evidence_id,
            EvidenceModel.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        evidence = result.scalar_one_or_none()

        if not evidence:
            logger.warning(f"Evidence {evidence_id} not found for importance boost")
            return _DEFAULT_IMPORTANCE

        # Boost importance, capped at max
        new_importance = min(
            _MAX_IMPORTANCE,
            evidence.importance + _RE_REFERENCE_BOOST,
        )
        evidence.importance = new_importance
        await self.db.flush()

        logger.debug(f"Boosted evidence {evidence_id} importance to {new_importance}")
        return new_importance

    async def query(
        self,
        session_id: UUID | None = None,
        tool_name: str | None = None,
        min_importance: float | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[EvidenceModel]:
        """Query evidence records with optional filters.

        Args:
            session_id: Filter by session.
            tool_name: Filter by tool name.
            min_importance: Filter by minimum importance score.
            since: Filter by creation time (after this timestamp).
            limit: Maximum results to return.

        Returns:
            List of matching EvidenceModel records.
        """
        conditions: list[Any] = [EvidenceModel.deleted_at.is_(None)]

        if session_id is not None:
            conditions.append(EvidenceModel.session_id == session_id)
        if tool_name is not None:
            conditions.append(EvidenceModel.tool_name == tool_name)
        if min_importance is not None:
            conditions.append(EvidenceModel.importance >= min_importance)
        if since is not None:
            conditions.append(EvidenceModel.created_at >= since)

        stmt = select(EvidenceModel).where(and_(*conditions)).order_by(EvidenceModel.created_at.desc()).limit(limit)

        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, evidence_id: UUID) -> EvidenceModel | None:
        """Get a specific evidence record by ID.

        Args:
            evidence_id: UUID of the evidence record.

        Returns:
            EvidenceModel if found, None otherwise.
        """
        stmt = select(EvidenceModel).where(
            EvidenceModel.id == evidence_id,
            EvidenceModel.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    def _normalize_result(
        self,
        result: Any,
    ) -> tuple[dict[str, Any], str | None]:
        """Normalize a tool result into structured content.

        Args:
            result: Raw tool result.

        Returns:
            Tuple of (normalized_content dict, raw_content string).
        """
        if result is None:
            return {"format": "null", "value": None}, None

        # Handle dict results
        if isinstance(result, dict):
            return {"format": "json", "value": result}, json.dumps(result, default=str)

        # Handle list results
        if isinstance(result, list):
            return {"format": "json", "value": result}, json.dumps(result, default=str)

        # Handle string results
        if isinstance(result, str):
            # Try to parse as JSON
            try:
                parsed = json.loads(result)
                return {"format": "json", "value": parsed}, result
            except (json.JSONDecodeError, TypeError):
                pass
            return {"format": "text", "value": result}, result

        # Handle other types
        raw = str(result)
        return {"format": "text", "value": raw}, raw
