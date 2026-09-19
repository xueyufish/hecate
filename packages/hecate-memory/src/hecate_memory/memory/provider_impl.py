"""Builtin full-contract memory provider.

Implements the tiered ``MemoryProvider`` contract (search / fact retrieval /
fact CRUD / lifecycle hooks) on top of the shipped hecate-memory services, and
is registered as the ``builtin`` entry under the ``hecate.memory_providers``
group. With the tiered contract in place, the builtin backend is just the
default implementation — third-party packages (mem0/zep adapters) register
their own entries and can take over the whole memory tool surface.

Design notes:

- Sessions: the provider is a process-wide singleton (the resolver caches it),
  so tier-2/3 methods open short-lived sessions from
  ``hecate.core.database.async_session_factory`` and commit on success.
  ``search`` keeps delegating to the ``KnowledgeBaseService`` singleton,
  preserving the pre-existing knowledge-query behavior byte for byte.
- L3 ranking: ``UserMemoryService.retrieve_memories`` is importance-ranked
  today (its embedding column is not yet wired to a real encoder); hits are
  merged with L4 hybrid-relevance scores after normalizing both onto [0, 1].
  This reuses the production retrieval quality baseline as-is (design D10).
- ``sync_turn`` is a deliberate no-op for the builtin backend: L3 extraction
  already runs in its own post-turn pipeline; a third-party backend would do
  its real write-back here.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.composition.memory_provider import (
    MemoryFactHit,
    MemoryWriteResult,
    PrefetchEntry,
    RecallPage,
)
from hecate.models.memory import KnowledgeMemoryModel, MemoryModel
from hecate_memory.rag.service import knowledge_base_service

logger = logging.getLogger(__name__)

# L4/L3 patch fields accepted by update_memory, per target layer.
_L4_PATCH_FIELDS = {"content", "tags", "importance"}
_L3_PATCH_FIELDS = {"content", "importance", "memory_type", "scope"}


class BuiltinMemoryProvider:
    """Default in-process memory backend implementing the full contract."""

    def __init__(self, vector_store: Any | None = None) -> None:
        self._explicit_vector_store = vector_store
        self._resolved_vector_store: Any | None = None
        self._vs_resolved = vector_store is not None

    # -- capability declaration ------------------------------------------------

    def capabilities(self) -> frozenset[str]:
        from hecate.core.composition.memory_provider import (
            CAP_ADD_MEMORY,
            CAP_FORGET_MEMORY,
            CAP_PREFETCH,
            CAP_SEARCH,
            CAP_SEARCH_MEMORIES,
            CAP_SEARCH_RECALL,
            CAP_SYNC_TURN,
            CAP_UPDATE_MEMORY,
        )

        return frozenset(
            {
                CAP_SEARCH,
                CAP_SEARCH_MEMORIES,
                CAP_SEARCH_RECALL,
                CAP_ADD_MEMORY,
                CAP_UPDATE_MEMORY,
                CAP_FORGET_MEMORY,
                CAP_PREFETCH,
                CAP_SYNC_TURN,
            }
        )

    # -- session / vector-store plumbing ---------------------------------------

    @asynccontextmanager
    async def _session(self):
        """Short-lived session with commit-on-success semantics."""
        from hecate.core.database import async_session_factory

        async with async_session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    def _vs(self) -> Any | None:
        """Lazily resolve the configured vector store (None on failure)."""
        if not self._vs_resolved:
            self._vs_resolved = True
            if self._explicit_vector_store is not None:
                self._resolved_vector_store = self._explicit_vector_store
            else:
                try:
                    from hecate_memory.rag.factory import get_vector_store

                    self._resolved_vector_store = get_vector_store()
                except Exception as e:
                    logger.warning("Vector store unavailable for memory provider: %s", e)
                    self._resolved_vector_store = None
        return self._resolved_vector_store

    # -- tier-1: generic search -------------------------------------------------

    async def search(
        self,
        collection_name: str,
        query: str,
        *,
        limit: int = 10,
        mode: str = "hybrid",
        workspace_id: str | None = None,
    ) -> list[Any]:
        return await knowledge_base_service.search(
            collection_name,
            query,
            limit=limit,
            mode=mode,
            workspace_id=workspace_id,
        )

    # -- tier-2: fact & recall retrieval ----------------------------------------

    async def search_memories(
        self,
        *,
        query: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        top_k: int = 5,
        tags: list[str] | None = None,
    ) -> list[MemoryFactHit]:
        """Merge L3 (importance-ranked) and L4 (hybrid relevance) fact hits."""
        hits: list[MemoryFactHit] = []

        # L3 is user-scoped (agent_id is L4's scope dimension) — an agent
        # filter must not hide unscoped L3 memories.
        l3_scope: dict[str, Any] = {}
        if user_id is not None:
            l3_scope["user_id"] = str(user_id)

        async with self._session() as db:
            l3_rows = await _load_l3_hits(db, workspace_id, query, l3_scope or None, top_k)
            for m in l3_rows:
                hits.append(
                    MemoryFactHit(
                        memory_id=m.id,
                        source_layer="user_memory",
                        content=m.content,
                        score=float(m.importance),
                        revision=m.revision,
                        importance=float(m.importance),
                        metadata={"scope": dict(m.scope or {})},
                    )
                )

            if agent_id is not None:
                from hecate_memory.memory.knowledge_memory import KnowledgeMemoryService

                svc = KnowledgeMemoryService(db, self._vs())
                results = await svc.search_knowledge(
                    agent_id,
                    workspace_id,
                    query,
                    top_k=top_k,
                    tags=tags,
                    user_id=user_id,
                )
                for r in results:
                    hits.append(
                        MemoryFactHit(
                            memory_id=r.memory.id,
                            source_layer="knowledge_memory",
                            content=r.memory.content,
                            score=float(r.score),
                            revision=r.memory.revision,
                            tags=list(r.memory.tags),
                            importance=float(r.memory.importance),
                        )
                    )

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    async def search_recall(
        self,
        *,
        query: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        limit: int = 5,
        start_date: Any | None = None,
        end_date: Any | None = None,
        roles: list[str] | None = None,
        cursor: str | None = None,
        exclude_session_ids: list[uuid.UUID] | None = None,
    ) -> RecallPage:
        from hecate_memory.memory.recall import RecallService

        async with self._session() as db:
            svc = RecallService(db, self._vs())
            return await svc.search(
                query=query,
                workspace_id=workspace_id,
                agent_id=agent_id,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                roles=roles,
                cursor=cursor,
                exclude_session_ids=exclude_session_ids,
            )

    # -- tier-3: fact CRUD -------------------------------------------------------

    async def add_memory(
        self,
        *,
        content: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> MemoryWriteResult:
        if agent_id is None:
            return MemoryWriteResult(
                ok=False,
                error="missing_agent",
                metadata={"reason": "L4 knowledge memory is agent-scoped"},
            )
        from hecate_memory.memory.knowledge_memory import KnowledgeMemoryService

        async with self._session() as db:
            svc = KnowledgeMemoryService(db, self._vs())
            schema = await svc.insert_knowledge(
                agent_id=agent_id,
                workspace_id=workspace_id,
                content=content,
                tags=tags,
                importance=importance,
                user_id=user_id,
                source="agent_tool",
            )
            return MemoryWriteResult(
                ok=True,
                memory_id=schema.id,
                revision=schema.revision,
                metadata={"deduplicated": schema.access_count >= 1},
            )

    async def update_memory(
        self,
        *,
        memory_id: uuid.UUID,
        workspace_id: uuid.UUID,
        patch: dict[str, Any],
        expected_revision: int | None = None,
        agent_id: uuid.UUID | None = None,
    ) -> MemoryWriteResult:
        async with self._session() as db:
            row = await _load_l4(db, memory_id, workspace_id, agent_id)
            layer_fields = _L4_PATCH_FIELDS
            if row is None:
                row = await _load_l3(db, memory_id, workspace_id)
                layer_fields = _L3_PATCH_FIELDS
            if row is None:
                return MemoryWriteResult(ok=False, error="not_found")

            unknown = set(patch) - layer_fields
            if unknown:
                return MemoryWriteResult(
                    ok=False,
                    error="invalid_patch",
                    metadata={"reason": f"unsupported fields for this memory layer: {sorted(unknown)}"},
                )
            if expected_revision is not None and row.revision != expected_revision:
                return MemoryWriteResult(
                    ok=False,
                    error="revision_conflict",
                    metadata={"current_revision": row.revision},
                )

            content_changed = "content" in patch and patch["content"] != row.content
            for key, value in patch.items():
                setattr(row, key, value)
            row.revision += 1
            new_revision = row.revision
            layer = "knowledge_memory" if layer_fields is _L4_PATCH_FIELDS else "user_memory"
            if layer == "knowledge_memory" and content_changed:
                from hecate_memory.memory.knowledge_memory import KnowledgeMemoryService

                await KnowledgeMemoryService(db, self._vs()).reindex(row)
            return MemoryWriteResult(ok=True, memory_id=row.id, revision=new_revision, metadata={"layer": layer})

    async def forget_memory(
        self,
        *,
        memory_id: uuid.UUID,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        expected_revision: int | None = None,
    ) -> MemoryWriteResult:
        from datetime import UTC, datetime

        async with self._session() as db:
            row = await _load_l4(db, memory_id, workspace_id, agent_id)
            layer: str | None = None
            if row is not None:
                layer = "knowledge_memory"
            else:
                row = await _load_l3(db, memory_id, workspace_id)
                if row is not None:
                    layer = "user_memory"
            if row is None or layer is None:
                return MemoryWriteResult(ok=False, error="not_found")

            if expected_revision is not None and row.revision != expected_revision:
                return MemoryWriteResult(
                    ok=False,
                    error="revision_conflict",
                    metadata={"current_revision": row.revision},
                )

            row.deleted = True
            row.deleted_at = datetime.now(UTC)
            row.revision += 1
            if layer == "knowledge_memory":
                vs = self._vs()
                if vs is not None:
                    try:
                        await vs.delete_by_ids("hecate_knowledge_memories", [str(memory_id)])
                    except Exception as e:
                        logger.warning("Failed to delete knowledge vector for %s: %s", memory_id, e)
            return MemoryWriteResult(ok=True, memory_id=row.id, revision=row.revision, metadata={"layer": layer})

    # -- lifecycle hooks ----------------------------------------------------------

    async def prefetch(
        self,
        *,
        query_text: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        max_entries: int = 5,
        max_tokens: int = 500,
    ) -> list[PrefetchEntry]:
        hits = await self.search_memories(
            query=query_text,
            workspace_id=workspace_id,
            agent_id=agent_id,
            user_id=user_id,
            top_k=max_entries,
        )
        entries: list[PrefetchEntry] = []
        budget = max_tokens
        for h in hits:
            tokens = _approx_tokens(h.content)
            if tokens > budget and entries:
                break
            entries.append(
                PrefetchEntry(
                    content=h.content,
                    source=h.source_layer,
                    score=h.score,
                    metadata={"memory_id": str(h.memory_id)},
                )
            )
            budget -= tokens
            if budget <= 0:
                break
        return entries

    async def sync_turn(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID,
        messages: list[dict[str, Any]],
    ) -> None:
        # No-op by design: builtin L3 extraction runs in its own post-turn
        # pipeline; injecting the same messages here would double-extract.
        logger.debug(
            "sync_turn no-op for builtin provider (workspace=%s agent=%s session=%s, %d messages)",
            workspace_id,
            agent_id,
            session_id,
            len(messages),
        )


# -- module-level helpers (DB accessors shared by the CRUD methods) -------------


async def _load_l4(
    db: AsyncSession,
    memory_id: uuid.UUID,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
) -> KnowledgeMemoryModel | None:
    stmt = select(KnowledgeMemoryModel).where(
        KnowledgeMemoryModel.id == memory_id,
        KnowledgeMemoryModel.workspace_id == workspace_id,
        ~KnowledgeMemoryModel.deleted,
    )
    if agent_id is not None:
        stmt = stmt.where(KnowledgeMemoryModel.agent_id == agent_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _load_l3(db: AsyncSession, memory_id: uuid.UUID, workspace_id: uuid.UUID) -> MemoryModel | None:
    stmt = select(MemoryModel).where(
        MemoryModel.id == memory_id,
        MemoryModel.workspace_id == workspace_id,
        ~MemoryModel.deleted,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _load_l3_hits(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    query: str,
    scope: dict[str, Any] | None,
    top_k: int,
) -> list[MemoryModel]:
    from hecate_memory.memory.user_memory import UserMemoryService

    rows = await UserMemoryService(db).retrieve_memories(workspace_id, query, scope=scope, top_k=top_k)
    # retrieve_memories returns read schemas; reload raw models so callers see
    # revision columns without a second query round-trip per row.
    ids = [m.id for m in rows]
    if not ids:
        return []
    stmt = select(MemoryModel).where(MemoryModel.id.in_(ids), ~MemoryModel.deleted)
    found = (await db.execute(stmt)).scalars().all()
    by_id = {m.id: m for m in found}
    return [by_id[m.id] for m in rows if m.id in by_id]


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
