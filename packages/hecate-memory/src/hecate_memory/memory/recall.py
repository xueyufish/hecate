"""Conversation recall storage — search over the transcript-level index.

The write side (indexing worker, watermark catch-up) lives in
``recall_indexer.py``; this module owns the read side backing
``conversation_search`` and the provider contract's ``search_recall``.

Retrieval is hybrid-ready but vector-led: the Qdrant ``hecate_recall``
collection holds one point per indexed message with a payload mirror of the
scope columns (workspace/agent/session/conversation), and the metadata table
supplies authoritiative rows. Scope filtering happens at the Qdrant payload
filter AND is re-verified against the metadata rows before returning.
"""

from __future__ import annotations

import base64
import binascii
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.composition.memory_provider import RecallHit, RecallPage
from hecate.models.memory import RecallMessageModel
from hecate_memory.rag.searcher import HybridSearcher
from hecate_memory.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

RECALL_COLLECTION = "hecate_recall"

_ALLOWED_ROLES = ("user", "assistant")


class RecallService:
    """Read side of the conversation recall storage."""

    def __init__(self, db: AsyncSession, vector_store: VectorStore | None = None) -> None:
        self.db = db
        self._vector_store = vector_store
        self._searcher = HybridSearcher(vector_store) if vector_store else None

    async def search(
        self,
        *,
        query: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        limit: int = 5,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        roles: list[str] | None = None,
        cursor: str | None = None,
        exclude_session_ids: list[uuid.UUID] | None = None,
    ) -> RecallPage:
        """Semantic search over indexed conversation messages.

        Scope: workspace + calling agent (cross-agent retrieval is a separate
        future feature). ``cursor`` is an opaque continuation token;
        ``exclude_session_ids`` removes already-inspected sessions so agentic
        re-search can iterate. ``low_signal`` marks pages the escalation hint
        should react to.
        """
        if self._searcher is None:
            return RecallPage(hits=[], low_signal=True)

        wanted_roles = [r for r in (roles or []) if r in _ALLOWED_ROLES] or list(_ALLOWED_ROLES)
        excluded = {str(s) for s in (exclude_session_ids or [])}
        after_score, after_id = _decode_cursor(cursor)

        await _ensure_collection(self._searcher)
        try:
            results = await self._searcher.search(
                RECALL_COLLECTION,
                query,
                limit=limit * 4,
                mode="hybrid",
                workspace_id=str(workspace_id),
            )
        except Exception as e:
            logger.warning("Recall vector search failed, returning low-signal page: %s", e)
            return RecallPage(hits=[], low_signal=True)

        hits: list[RecallHit] = []
        for r in results:
            meta = r.metadata
            if str(meta.get("workspace_id", "")) != str(workspace_id):
                continue
            if str(meta.get("agent_id", "")) != str(agent_id):
                continue
            if str(meta.get("role", "")) not in wanted_roles:
                continue
            if str(meta.get("session_id", "")) in excluded:
                continue
            # Strict (score, id) ordering continuation — no repeats.
            if after_score is not None and (
                r.score < after_score or (r.score == after_score and str(r.id) <= after_id)
            ):
                continue
            hits.append(
                RecallHit(
                    recall_id=uuid.UUID(r.id),
                    session_id=uuid.UUID(str(meta.get("session_id"))),
                    conversation_id=(uuid.UUID(str(meta["conversation_id"])) if meta.get("conversation_id") else None),
                    role=str(meta.get("role", "")),
                    content=str(r.content),
                    timestamp=_parse_ts(meta.get("timestamp")),
                    score=float(r.score),
                    metadata={},
                )
            )
            if len(hits) >= limit + 1:
                break

        # Re-verify scope against the metadata table (authoritative rows).
        hits = await self._hydrate_and_filter(hits, start_date, end_date)

        next_cursor: str | None = None
        if len(hits) > limit:
            hits = hits[:limit]
            last = hits[-1]
            next_cursor = _encode_cursor(last.score, str(last.recall_id))

        low_signal = not hits or all(h.score < _low_signal_threshold() for h in hits)
        return RecallPage(hits=hits, next_cursor=next_cursor, low_signal=low_signal)

    async def _hydrate_and_filter(
        self,
        hits: list[RecallHit],
        start_date: datetime | None,
        end_date: datetime | None,
    ) -> list[RecallHit]:
        """Drop hits whose metadata row is gone or outside the time window."""
        if not hits:
            return hits
        ids = [h.recall_id for h in hits]
        stmt = select(RecallMessageModel).where(
            RecallMessageModel.id.in_(ids),
            RecallMessageModel.workspace_id.isnot(None),
            ~RecallMessageModel.deleted,
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        valid = {r.id: r for r in rows}

        kept: list[RecallHit] = []
        for h in hits:
            row = valid.get(h.recall_id)
            if row is None:
                continue
            if start_date is not None and row.created_at < start_date:
                continue
            if end_date is not None and row.created_at > end_date:
                continue
            kept.append(h)
        return kept


# -- helpers --------------------------------------------------------------------


def _low_signal_threshold() -> float:
    from hecate.core.config import settings

    return float(getattr(settings, "RECALL_LOW_SIGNAL_THRESHOLD", 0.35))


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value))
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.fromtimestamp(0)


def _encode_cursor(score: float, last_id: str) -> str:
    raw = f"{score!r}|{last_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str | None) -> tuple[float | None, str | None]:
    if not cursor:
        return None, None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        score_text, last_id = raw.split("|", 1)
        return float(score_text), last_id
    except (ValueError, binascii.Error):
        logger.warning("Invalid recall cursor ignored: %r", cursor)
        return None, None


async def _ensure_collection(searcher: HybridSearcher) -> None:
    """Lazily create the recall collection; searcher stores the vector store."""
    store = getattr(searcher, "_store", None) or getattr(searcher, "store", None)
    if store is None:
        return
    if not await store.collection_exists(RECALL_COLLECTION):
        await store.create_collection(RECALL_COLLECTION, vector_size=1024, with_sparse=True)
