"""Distinct-session access tracking for memory retrieval hits.

Backs the deduplicated access-frequency signal: one
``memory_access_sessions`` row per ``(target_type, memory_id,
session_id)`` — repeated hits inside one session never inflate the
count, which keeps frequency from reinforcing itself once ranking
becomes access-aware. Also maintains the rows' ``last_accessed_at``
heat clock for the offline consolidation value score.

Recording is best-effort by contract: any failure is logged and
swallowed, never fails the search that produced the hits.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import KnowledgeMemoryModel, MemoryAccessSessionModel, MemoryModel

logger = logging.getLogger(__name__)

_HEAT_MODELS = {"user_memory": MemoryModel, "knowledge_memory": KnowledgeMemoryModel}


async def record_access(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    hits: list[tuple[str, uuid.UUID]],
    session_id: uuid.UUID | None,
) -> None:
    """Record retrieval hits as distinct-session access markers.

    ``hits`` are ``(target_type, memory_id)`` pairs as returned by
    ``search_memories`` (``source_layer``, memory id). Deduplicated by
    the unique constraint semantics: existing pairs for the same session
    are skipped, so the row count per memory is its distinct-session hit
    count. ``session_id=None`` (context without a session) still
    refreshes the heat clock but cannot create a marker.
    """
    if not hits:
        return
    try:
        deduped = list(dict.fromkeys(hits))
        now = datetime.now(UTC)

        if session_id is not None:
            memory_ids = [m for _, m in deduped]
            known: set[tuple[str, uuid.UUID]] = set()
            if memory_ids:
                rows = (
                    await db.execute(
                        select(MemoryAccessSessionModel.target_type, MemoryAccessSessionModel.memory_id).where(
                            MemoryAccessSessionModel.session_id == session_id,
                            MemoryAccessSessionModel.memory_id.in_(memory_ids),
                            ~MemoryAccessSessionModel.deleted,
                        )
                    )
                ).all()
                known = {(t, m) for t, m in rows}
            for target_type, memory_id in deduped:
                if (target_type, memory_id) in known:
                    continue
                db.add(
                    MemoryAccessSessionModel(
                        workspace_id=workspace_id,
                        target_type=target_type,
                        memory_id=memory_id,
                        session_id=session_id,
                    )
                )

        for model_type, ids in ((name, [m for t, m in deduped if t == name]) for name in _HEAT_MODELS):
            if ids:
                await db.execute(
                    update(model_type).where(model_type.id.in_(ids), ~model_type.deleted).values(last_accessed_at=now)
                )
        await db.flush()
    except Exception as e:
        logger.warning("Memory access tracking failed (best-effort): %s", e)


async def distinct_session_counts(
    db: AsyncSession,
    *,
    target_type: str,
    memory_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Distinct-session hit count per memory (offline value-score input)."""
    if not memory_ids:
        return {}
    rows = (
        await db.execute(
            select(MemoryAccessSessionModel.memory_id, func.count())
            .where(
                MemoryAccessSessionModel.target_type == target_type,
                MemoryAccessSessionModel.memory_id.in_(memory_ids),
                ~MemoryAccessSessionModel.deleted,
            )
            .group_by(MemoryAccessSessionModel.memory_id)
        )
    ).all()
    return {memory_id: count for memory_id, count in rows}
