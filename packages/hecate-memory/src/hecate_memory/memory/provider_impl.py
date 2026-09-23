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
- L3 ranking: ``UserMemoryService.retrieve_memories_scored`` cosine-scores
  scope-filtered candidates against the query vector; rows without a real
  embedding carry no semantic signal. The merge below normalizes L3 cosine
  and L4 hybrid relevance onto one [0, 1] scale and applies the bounded
  metadata bias (``MEMORY_FUSION_BIAS_ENABLED``) across the union, so
  ``MemoryFactHit.score`` has one documented meaning: the fused score
  (relevance alone when the bias is off).
- ``sync_turn`` is a deliberate no-op for the builtin backend: L3 extraction
  already runs in its own post-turn pipeline; a third-party backend would do
  its real write-back here.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
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
from hecate_memory.memory import ranking
from hecate_memory.rag.service import knowledge_base_service

logger = logging.getLogger(__name__)

# L4/L3 patch fields accepted by update_memory, per target layer.
_L4_PATCH_FIELDS = {"content", "tags", "importance"}
_L3_PATCH_FIELDS = {"content", "importance", "memory_type", "scope"}


@dataclass
class _MergedCandidate:
    """One fact-memory candidate awaiting cross-layer normalization.

    ``raw_relevance`` is the layer's raw relevance (L3 cosine, L4 hybrid
    score); ``None`` marks a candidate with no semantic signal, which is
    ranked after the scored ones in metadata order.
    """

    source_layer: str
    memory_id: uuid.UUID
    content: str
    revision: int
    importance: float
    raw_relevance: float | None
    last_confirmed_at: datetime | None
    created_at: datetime
    memory_type: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


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
            CAP_CROSS_THREAD,
            CAP_END_EPISODE,
            CAP_ESCALATE_FAILURE,
            CAP_FORGET_MEMORY,
            CAP_PREFETCH,
            CAP_SEARCH,
            CAP_SEARCH_MEMORIES,
            CAP_SEARCH_RECALL,
            CAP_SYNC_TURN,
            CAP_TASK_MEMORY,
            CAP_UPDATE_MEMORY,
        )
        from hecate.core.config import settings

        # Base set — always declared when the builtin provider is active.
        caps = {
            CAP_SEARCH,
            CAP_SEARCH_MEMORIES,
            CAP_SEARCH_RECALL,
            CAP_ADD_MEMORY,
            CAP_UPDATE_MEMORY,
            CAP_FORGET_MEMORY,
            CAP_PREFETCH,
            CAP_SYNC_TURN,
        }
        # 4.21 / 4.23 surface. The four caps are *opt-in*: only added when
        # ``settings.REFLECTION_ENABLED`` is true. Off paths must be
        # byte-identical to pre-change behavior, which is what callers see
        # when these four caps are absent — ``provider_supports`` returns
        # False and the structured-error path takes over.
        if settings.REFLECTION_ENABLED:
            caps.update(
                {
                    CAP_TASK_MEMORY,
                    CAP_CROSS_THREAD,
                    CAP_END_EPISODE,
                    CAP_ESCALATE_FAILURE,
                }
            )
        return frozenset(caps)

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
        """Merge L3 (semantic cosine) and L4 (hybrid relevance) fact hits.

        Both layers' relevance scores are normalized onto one [0, 1] scale
        and the bounded metadata bias (decay x importance multipliers) is
        applied across the union, so the merged order is a single-scale
        ranking rather than a mix of importance values and similarity
        scores. Candidates without a semantic signal (no real vector)
        rank after the scored ones in metadata order.
        """
        merged: list[_MergedCandidate] = []
        unscored: list[_MergedCandidate] = []

        # L3 is user-scoped (agent_id is L4's scope dimension) — an agent
        # filter must not hide unscoped L3 memories.
        l3_scope: dict[str, Any] = {}
        if user_id is not None:
            l3_scope["user_id"] = str(user_id)

        async with self._session() as db:
            from hecate_memory.memory.user_memory import UserMemoryService

            l3_scored = await UserMemoryService(db).retrieve_memories_scored(
                workspace_id, query, scope=l3_scope or None, top_k=top_k
            )
            for s in l3_scored:
                candidate = _MergedCandidate(
                    source_layer="user_memory",
                    memory_id=s.memory.id,
                    content=s.memory.content,
                    revision=s.revision,
                    importance=float(s.memory.importance),
                    raw_relevance=s.raw_relevance,
                    last_confirmed_at=s.last_confirmed_at,
                    created_at=s.memory.created_at,
                    memory_type=s.memory.memory_type,
                    metadata={"scope": dict(s.memory.scope or {})},
                )
                if candidate.raw_relevance is None:
                    unscored.append(candidate)
                else:
                    merged.append(candidate)

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
                    merged.append(
                        _MergedCandidate(
                            source_layer="knowledge_memory",
                            memory_id=r.memory.id,
                            content=r.memory.content,
                            revision=r.memory.revision,
                            importance=float(r.memory.importance),
                            raw_relevance=float(r.score),
                            last_confirmed_at=r.last_confirmed_at,
                            created_at=r.memory.created_at,
                            memory_type="semantic",
                            tags=list(r.memory.tags),
                        )
                    )

        return self._rank_merged(merged, unscored, top_k)

    @staticmethod
    def _rank_merged(
        merged: list[_MergedCandidate],
        unscored: list[_MergedCandidate],
        top_k: int,
    ) -> list[MemoryFactHit]:
        """Normalize relevance across the union, apply bias, build hits."""
        normalized = ranking.normalize_scores([c.raw_relevance for c in merged])
        ranked: list[tuple[float, _MergedCandidate, dict[str, Any]]] = []
        for candidate, rel in zip(merged, normalized, strict=True):
            breakdown = ranking.breakdown_for_hit(
                relevance=rel,
                last_confirmed_at=candidate.last_confirmed_at,
                created_at=candidate.created_at,
                importance=candidate.importance,
                knowledge_layer=candidate.source_layer == "knowledge_memory",
                memory_type=candidate.memory_type,
            )
            fused, breakdown = ranking.fuse(rel, breakdown["decay_mult"], breakdown["importance_mult"])
            ranked.append((fused, candidate, breakdown))
        ranked.sort(key=lambda item: item[0], reverse=True)

        hits = [
            MemoryFactHit(
                memory_id=c.memory_id,
                source_layer=c.source_layer,
                content=c.content,
                score=round(fused, 4),
                revision=c.revision,
                tags=c.tags,
                importance=c.importance,
                metadata=c.metadata | {"breakdown": breakdown},
            )
            for fused, c, breakdown in ranked
        ]
        hits.extend(
            MemoryFactHit(
                memory_id=c.memory_id,
                source_layer=c.source_layer,
                content=c.content,
                score=0.0,
                revision=c.revision,
                tags=c.tags,
                importance=c.importance,
                metadata=c.metadata
                | {
                    "breakdown": {
                        "relevance": 0.0,
                        "decay_mult": 1.0,
                        "importance_mult": 1.0,
                        "semantic": False,
                    }
                },
            )
            for c in unscored
        )
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
        # 4.21 sub-action: when REFLECTION_ENABLED is on and the provider
        # declared ``CAP_TASK_MEMORY``, append any TOOL events from the
        # just-completed turn to the active episode for this
        # (workspace, agent, session). No active episode → silent skip;
        # tool events present but flag off → byte-identical to the
        # pre-change path (the helper short-circuits).
        try:
            await self._episode_record_sub_action(
                workspace_id=workspace_id,
                agent_id=agent_id,
                session_id=session_id,
                messages=messages,
            )
        except Exception:
            # Per the task-memory capability: sub-action failure MUST NOT
            # affect the turn result. Log and move on.
            logger.warning(
                "sync_turn episode_record sub-action failed (workspace=%s agent=%s session=%s)",
                workspace_id,
                agent_id,
                session_id,
                exc_info=True,
            )

        # Original sync_turn is a no-op for L3 extraction (the builtin
        # post-turn pipeline handles L3 separately to avoid double-extract).
        # Kept verbatim below — behavior unchanged for callers.
        logger.debug(
            "sync_turn completed (workspace=%s agent=%s session=%s, %d messages)",
            workspace_id,
            agent_id,
            session_id,
            len(messages),
        )

    # -- tier-4 + tier-5 + lifecycle hooks (4.21 / 4.23) --------------------
    #
    # These methods are *opt-in*: when ``REFLECTION_ENABLED`` is false the
    # builtin provider omits the corresponding CAP_* declarations from
    # ``capabilities()``, so callers short-circuit on
    # ``provider_supports`` and never reach these implementations. The
    # bodies therefore only need to be correct when REFLECTION_ENABLED
    # is on; they default to a structured no-op result so an accidental
    # direct call (or a legacy caller that bypassed the capability check)
    # gets a clean ``error='none'`` / empty result instead of an
    # exception.
    async def _episode_record_sub_action(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID,
        messages: list[dict[str, Any]],
    ) -> None:
        """Sync-turn sub-action: append TOOL events to the active episode.

        No-op unless ``settings.REFLECTION_ENABLED`` is true AND the
        provider declared ``CAP_TASK_MEMORY`` (i.e. the caller already
        passed the capability check). The actual write path is owned by
        ``TaskMemoryService.append_tool_events`` (added in Group 3 of
        the memory-storage-family change) — this method is the hook
        point that the runtime invokes from ``sync_turn``.
        """
        from hecate.core.config import settings

        if not settings.REFLECTION_ENABLED:
            return
        from hecate.core.composition.memory_provider import (
            CAP_TASK_MEMORY,
            provider_supports,
        )

        if not provider_supports(self, CAP_TASK_MEMORY):
            return
        from hecate_memory.memory.task_memory import TaskMemoryService

        await TaskMemoryService.append_turn_tool_events(
            workspace_id=workspace_id,
            agent_id=agent_id,
            session_id=session_id,
            messages=messages,
        )

    async def add_episode(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        task_type: str = "",
        situation: str | None = None,
        intent: str | None = None,
    ):
        from hecate.core.composition.memory_provider import EpisodeWriteResult
        from hecate.core.config import settings

        if not settings.REFLECTION_ENABLED:
            return EpisodeWriteResult(ok=False, error="none")
        from hecate_memory.memory.task_memory import TaskMemoryService

        return await TaskMemoryService.add_episode(
            workspace_id=workspace_id,
            agent_id=agent_id,
            actor_id=actor_id,
            session_id=session_id,
            task_type=task_type,
            situation=situation,
            intent=intent,
        )

    async def record_tool_event(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID,
        tool_name: str,
        args: dict[str, Any],
        result_ref: str | None = None,
        ts: datetime | None = None,
    ):
        from hecate.core.composition.memory_provider import EpisodeWriteResult
        from hecate.core.config import settings

        if not settings.REFLECTION_ENABLED:
            return EpisodeWriteResult(ok=False, error="none")
        from hecate_memory.memory.task_memory import TaskMemoryService

        return await TaskMemoryService.record_tool_event(
            workspace_id=workspace_id,
            agent_id=agent_id,
            session_id=session_id,
            tool_name=tool_name,
            args=args,
            result_ref=result_ref,
            ts=ts,
        )

    async def close_episode(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        episode_id: uuid.UUID,
    ):
        from hecate.core.composition.memory_provider import EpisodeWriteResult
        from hecate.core.config import settings

        if not settings.REFLECTION_ENABLED:
            return EpisodeWriteResult(ok=False, error="none")
        from hecate_memory.memory.task_memory import TaskMemoryService

        return await TaskMemoryService.close_episode(
            workspace_id=workspace_id,
            agent_id=agent_id,
            episode_id=episode_id,
        )

    async def search_task_memory(
        self,
        *,
        query: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        top_k: int = 5,
        task_type: str | None = None,
    ) -> list[Any]:
        from hecate.core.config import settings

        if not settings.REFLECTION_ENABLED:
            return []
        from hecate_memory.memory.task_memory import TaskMemoryService

        return await TaskMemoryService.search_reflections(
            query=query,
            workspace_id=workspace_id,
            agent_id=agent_id,
            actor_id=actor_id,
            top_k=top_k,
            task_type=task_type,
        )

    async def search_cross_thread(
        self,
        *,
        query: str,
        workspace_id: uuid.UUID,
        team_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        top_k: int = 5,
        tags: list[str] | None = None,
    ) -> list[Any]:
        from hecate.core.config import settings

        if not settings.REFLECTION_ENABLED:
            return []
        from hecate_memory.memory.task_memory import TaskMemoryService

        return await TaskMemoryService.search_cross_thread(
            query=query,
            workspace_id=workspace_id,
            team_id=team_id,
            actor_id=actor_id,
            top_k=top_k,
            tags=tags,
        )

    async def end_episode(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        episode_id: uuid.UUID,
    ) -> None:
        from hecate.core.config import settings

        if not settings.REFLECTION_ENABLED:
            return
        from hecate_memory.memory.task_memory import TaskMemoryService

        await TaskMemoryService.end_episode(
            workspace_id=workspace_id,
            agent_id=agent_id,
            episode_id=episode_id,
        )

    async def escalate_failure(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        task_type: str,
        confidence: float,
    ) -> list[Any]:
        from hecate.core.config import settings

        if not settings.REFLECTION_ENABLED:
            return []
        from hecate_memory.memory.task_memory import TaskMemoryService

        return await TaskMemoryService.escalate_failure_recall(
            workspace_id=workspace_id,
            agent_id=agent_id,
            task_type=task_type,
            confidence=confidence,
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


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)
