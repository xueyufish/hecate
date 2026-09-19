"""Conversation recall indexer — write side of the recall storage.

Consumes ``CHANNEL_WRITE`` events on the ``messages`` channel from the
persisted event log and projects user/assistant messages into the recall
layer (``recall_messages`` metadata rows + vectors in the Qdrant
``hecate_recall`` collection).

Delivery model: a single background poll loop (``run_forever``, started by
the composition root when ``RECALL_INDEXING_ENABLED``). Idempotency comes
from the ``(session_id, content_hash, seq)`` unique key with ``seq`` being
the event log version — a stable, per-session monotonic number — so rescans
converge and crashed batches are picked up on the next poll. Tool results,
system messages and non-message channels are never indexed.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import RecallMessageModel
from hecate_memory.rag.embedding import embedding_service
from hecate_memory.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

RECALL_COLLECTION = "hecate_recall"
_INDEXED_ROLES = ("user", "assistant")


def _content_hash(role: str, content: str) -> str:
    return hashlib.sha256(f"{role}\n{content}".encode()).hexdigest()


class RecallIndexerService:
    """Projects message-channel writes into the recall layer."""

    def __init__(self, vector_store: VectorStore | None = None) -> None:
        self._explicit_store = vector_store
        self._store: VectorStore | None | None = None if vector_store is None else vector_store
        self._store_resolved = vector_store is not None
        self._task: asyncio.Task | None = None

    # -- vector store ---------------------------------------------------------

    def _vs(self) -> VectorStore | None:
        if not self._store_resolved:
            self._store_resolved = True
            if self._explicit_store is None:
                try:
                    from hecate_memory.rag.factory import get_vector_store

                    self._store = get_vector_store()
                except Exception as e:
                    logger.warning("Recall indexer running without a vector store: %s", e)
                    self._store = None
            else:
                self._store = self._explicit_store
        return self._store

    async def _ensure_collection(self) -> bool:
        store = self._vs()
        if store is None:
            return False
        if not await store.collection_exists(RECALL_COLLECTION):
            await store.create_collection(RECALL_COLLECTION, vector_size=1024, with_sparse=True)
        return True

    # -- indexing ----------------------------------------------------------------

    async def poll_once(self, batch: int = 200) -> int:
        """One catch-up scan: index message writes newer than the per-session
        watermark. Returns the number of newly indexed messages."""
        from hecate.core.database import async_session_factory
        from hecate.models.session import SessionModel
        from hecate.studio.event_state.models import EventModel

        try:
            async with async_session_factory() as db:
                rows = (
                    (
                        await db.execute(
                            select(EventModel)
                            .where(
                                EventModel.event_type == "CHANNEL_WRITE",
                            )
                            .order_by(
                                EventModel.created_at.desc(), EventModel.session_id.desc(), EventModel.version.desc()
                            )
                            .limit(batch)
                        )
                    )
                    .scalars()
                    .all()
                )
                if not rows:
                    return 0

                by_session: dict[uuid.UUID, list[EventModel]] = {}
                for row in rows:
                    payload = row.payload or {}
                    if payload.get("channel") != "messages":
                        continue
                    value = payload.get("value")
                    if not isinstance(value, dict) or value.get("role") not in _INDEXED_ROLES:
                        continue
                    if not isinstance(value.get("content"), str) or not value["content"].strip():
                        continue
                    by_session.setdefault(row.session_id, []).append(row)

                indexed = 0
                for session_id, events in by_session.items():
                    session = await db.get(SessionModel, session_id)
                    if session is None:
                        continue
                    watermark = await db.scalar(
                        select(func.max(RecallMessageModel.event_version)).where(
                            RecallMessageModel.session_id == session_id,
                            ~RecallMessageModel.deleted,
                        )
                    )
                    watermark = int(watermark or 0)
                    fresh = [e for e in events if e.version > watermark]
                    if not fresh:
                        continue
                    triples = [
                        (e.version, str(e.payload["value"]["role"]), str(e.payload["value"]["content"]))
                        for e in sorted(fresh, key=lambda ev: ev.version)
                    ]
                    indexed += await self._index_session(
                        db,
                        workspace_id=session.workspace_id,
                        agent_id=session.agent_id,
                        session_id=session_id,
                        conversation_id=session.conversation_id,
                        user_id=getattr(session, "user_id", None),
                        messages=triples,
                    )
                await db.commit()
                return indexed
        except Exception:
            logger.exception("Recall poll failed")
            return 0

    async def _index_session(
        self,
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID,
        conversation_id: uuid.UUID | None,
        user_id: uuid.UUID | None,
        messages: list[tuple[int, str, str]],
    ) -> int:
        """Idempotently index (version, role, content) triples for a session."""
        if not messages or not await self._ensure_collection():
            return 0

        existing = {
            (h, s)
            for h, s in (
                await db.execute(
                    select(RecallMessageModel.content_hash, RecallMessageModel.seq).where(
                        RecallMessageModel.session_id == session_id,
                        RecallMessageModel.event_version.in_([v for v, _, _ in messages]),
                        ~RecallMessageModel.deleted,
                    )
                )
            ).all()
        }

        indexed = 0
        for version, role, content in messages:
            content_hash = _content_hash(role, content)
            if (content_hash, version) in existing:
                continue
            row = RecallMessageModel(
                workspace_id=workspace_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                session_id=session_id,
                user_id=user_id,
                role=role,
                content=content,
                content_hash=content_hash,
                seq=version,
                event_version=version,
            )
            db.add(row)
            await db.flush()
            await self._upsert_vector(row)
            indexed += 1
        return indexed

    async def index_messages(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID,
        conversation_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        messages: list[dict[str, Any]],
        start_seq: int = 0,
    ) -> int:
        """Direct indexing entry point (session identifier known upstream).

        ``messages`` are ``{"role", "content"}`` dicts; ``start_seq`` offsets
        the per-session sequence numbering.
        """
        from hecate.core.database import async_session_factory

        triples = [
            (start_seq + i, str(m["role"]), str(m["content"]))
            for i, m in enumerate(messages)
            if m.get("role") in _INDEXED_ROLES and isinstance(m.get("content"), str) and m["content"].strip()
        ]
        async with async_session_factory() as db:
            count = await self._index_session(
                db,
                workspace_id=workspace_id,
                agent_id=agent_id,
                session_id=session_id,
                conversation_id=conversation_id,
                user_id=user_id,
                messages=triples,
            )
            await db.commit()
            return count

    async def _upsert_vector(self, row: RecallMessageModel) -> None:
        store = self._vs()
        if store is None:
            return
        try:
            result = await embedding_service.encode_query(row.content)
            payload: dict[str, Any] = {
                "workspace_id": str(row.workspace_id),
                "agent_id": str(row.agent_id),
                "session_id": str(row.session_id),
                "conversation_id": str(row.conversation_id) if row.conversation_id else None,
                "role": row.role,
                "content": row.content,
                "timestamp": row.created_at.isoformat() if row.created_at else datetime.now(UTC).isoformat(),
            }
            sparse = [result.sparse] if result.sparse else None
            await store.upsert(RECALL_COLLECTION, [str(row.id)], [result.dense], [payload], sparse)
        except Exception as e:
            # Best-effort: the metadata row persists; the vector is repaired by
            # re-indexing (the unique key makes a rerun converge).
            logger.warning("Recall vector upsert failed for %s: %s", row.id, e)

    # -- lifecycle -----------------------------------------------------------------

    async def purge_conversation(self, conversation_id: uuid.UUID) -> int:
        """Cascade: drop recall rows (and their vectors) for a conversation."""
        from hecate.core.database import async_session_factory

        async with async_session_factory() as db:
            ids = (
                (
                    await db.execute(
                        select(RecallMessageModel.id).where(
                            RecallMessageModel.conversation_id == conversation_id,
                            ~RecallMessageModel.deleted,
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                return 0
            await db.execute(delete(RecallMessageModel).where(RecallMessageModel.id.in_(ids)))
            await db.commit()

        store = self._vs()
        if store is not None:
            try:
                await store.delete_by_ids(RECALL_COLLECTION, [str(i) for i in ids])
            except Exception as e:
                logger.warning("Recall vector cleanup failed for conversation %s: %s", conversation_id, e)
        return len(ids)

    async def stats(self) -> dict[str, Any]:
        """Observability surface: counts by workspace, last index time, backlog."""
        from hecate.core.database import async_session_factory

        async with async_session_factory() as db:
            total = await db.scalar(
                select(func.count()).select_from(RecallMessageModel).where(~RecallMessageModel.deleted)
            )
            last_at = await db.scalar(select(func.max(RecallMessageModel.created_at)))
            per_workspace = (
                await db.execute(
                    select(RecallMessageModel.workspace_id, func.count())
                    .where(~RecallMessageModel.deleted)
                    .group_by(RecallMessageModel.workspace_id)
                )
            ).all()
            return {
                "total": int(total or 0),
                "last_indexed_at": last_at.isoformat() if last_at else None,
                "by_workspace": {str(ws): int(n) for ws, n in per_workspace},
            }

    # -- background loop --------------------------------------------------------------

    async def run_forever(self) -> None:
        """Poll loop; ``RECALL_INDEX_POLL_INTERVAL_SECONDS=0`` disables polling."""
        from hecate.core.config import settings

        interval = max(int(settings.RECALL_INDEX_POLL_INTERVAL_SECONDS), 0)
        if interval == 0:
            logger.info("Recall indexer polling disabled (interval=0)")
            return
        logger.info("Recall indexer started (interval=%ds)", interval)
        while True:
            await asyncio.sleep(interval)
            try:
                await self.poll_once()
            except Exception:  # pragma: no cover — poll_once already guards
                logger.exception("Recall indexer poll crashed")

    def start(self) -> None:
        """Start the background poll task (idempotent)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


_wired_indexer: RecallIndexerService | None = None


def start_recall_indexer() -> RecallIndexerService | None:
    """Composition-root entry: start the poll loop when the flag is on."""
    global _wired_indexer
    from hecate.core.config import settings

    if not settings.RECALL_INDEXING_ENABLED:
        return None
    if _wired_indexer is None:
        _wired_indexer = RecallIndexerService()
    _wired_indexer.start()
    return _wired_indexer


async def stop_recall_indexer() -> None:
    global _wired_indexer
    if _wired_indexer is not None:
        await _wired_indexer.stop()
        _wired_indexer = None
