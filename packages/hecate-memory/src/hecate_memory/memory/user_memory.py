"""User memory service for L3 persistent memory.

Manages persistent facts extracted from conversations, stored with
vector embeddings for semantic retrieval across sessions.

Retrieval is semantically ranked (memory-importance-fusion change):
scope-filtered candidates are scored in-process with cosine similarity
against the query vector, normalized, and — when
``MEMORY_FUSION_BIAS_ENABLED`` — biased by bounded time-decay and
importance multipliers. Rows without a real embedding (legacy mock
vectors) carry no semantic signal: they are excluded from cosine
scoring and returned after the scored candidates in metadata order.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import (
    MemoryCreateSchema,
    MemoryEditLogModel,
    MemoryModel,
    MemoryReadSchema,
)
from hecate_memory.memory import ranking

logger = logging.getLogger(__name__)


@dataclass
class ScoredMemory:
    """One ranked L3 candidate with its per-signal breakdown.

    ``relevance`` is the candidate-window-normalized score (the fusion
    input); ``raw_relevance`` is the raw cosine similarity. Either is
    ``None`` when the row was not semantically scored (no real vector,
    or the query could not be embedded); such rows are ordered after
    the scored candidates by importance. ``last_confirmed_at`` rides
    along so the merge layer can re-run the bias across L3+L4.
    """

    memory: MemoryReadSchema
    relevance: float | None
    fused: float
    revision: int
    breakdown: dict[str, Any]
    raw_relevance: float | None = None
    last_confirmed_at: datetime | None = None


class UserMemoryService:
    """Service for managing L3 user memory.

    Provides operations for storing, retrieving, and managing persistent
    facts extracted from conversations.
    """

    def __init__(self, db: AsyncSession) -> None:
        """Initialize with a database session.

        Args:
            db: Async SQLAlchemy session for database operations.
        """
        self.db = db

    async def store_memory(
        self,
        workspace_id: uuid.UUID,
        data: MemoryCreateSchema,
        embedding: list[float] | None = None,
    ) -> MemoryReadSchema:
        """Store a new memory with optional embedding.

        Args:
            workspace_id: The workspace for tenant isolation.
            data: Memory creation data.
            embedding: Optional precomputed embedding (real vectors from
                consolidation). When omitted, a real embedding is
                attempted; if the embedding service is unavailable the
                row falls back to a placeholder vector and is marked for
                backfill (``embedding_real=False``).

        Returns:
            The created memory.
        """
        embedding_real = embedding is not None
        if embedding is None:
            embedding, embedding_real = await self._try_real_embedding(data.content)
        if embedding is None:
            embedding = self._generate_mock_embedding(data.content)

        now = datetime.now(UTC)
        memory = MemoryModel(
            workspace_id=workspace_id,
            content=data.content,
            scope=data.scope,
            memory_type=data.memory_type,
            importance=data.importance,
            embedding=embedding,
            embedding_real=embedding_real,
            last_confirmed_at=now,
        )
        self.db.add(memory)
        await self.db.flush()

        logger.info(f"Stored memory: {data.content[:50]}...")
        return MemoryReadSchema.model_validate(memory)

    async def retrieve_memories(
        self,
        workspace_id: uuid.UUID,
        query: str,
        scope: dict[str, Any] | None = None,
        top_k: int = 5,
        min_importance: float = 0.0,
    ) -> list[MemoryReadSchema]:
        """Retrieve relevant memories by semantic similarity.

        Thin wrapper over :meth:`retrieve_memories_scored` for callers
        that only want the rows.

        Args:
            workspace_id: The workspace for tenant isolation.
            query: The query to search for.
            scope: Optional scope filter (user_id, agent_id, session_id).
            top_k: Maximum number of results.
            min_importance: Minimum importance threshold.

        Returns:
            List of relevant memories in ranked order.
        """
        scored = await self.retrieve_memories_scored(
            workspace_id, query, scope=scope, top_k=top_k, min_importance=min_importance
        )
        return [s.memory for s in scored]

    async def retrieve_memories_scored(
        self,
        workspace_id: uuid.UUID,
        query: str,
        scope: dict[str, Any] | None = None,
        top_k: int = 5,
        min_importance: float = 0.0,
    ) -> list[ScoredMemory]:
        """Semantic retrieval with per-signal breakdown.

        Candidate pool = scope-filtered rows up to the configured cap,
        pre-ordered by importance then confirmation time. Rows with a
        real embedding are cosine-scored against the query vector and
        min-max normalized; rows without one are returned after the
        scored candidates in metadata order (they carry no semantic
        signal). When the query cannot be embedded, the whole pool
        degrades to the metadata order with a warning — retrieval never
        fails.

        Args:
            workspace_id: The workspace for tenant isolation.
            query: The query to search for.
            scope: Optional scope filter (user_id, agent_id, session_id).
            top_k: Maximum number of results.
            min_importance: Minimum importance threshold.

        Returns:
            Ranked scored memories (at most ``top_k``).
        """
        conditions = [
            ~MemoryModel.deleted,
            MemoryModel.archived_at.is_(None),  # lifecycle archive: out of every retrieval path
            MemoryModel.workspace_id == workspace_id,
        ]

        if min_importance > 0:
            conditions.append(MemoryModel.importance >= min_importance)

        if scope:
            if "user_id" in scope:
                conditions.append(MemoryModel.scope["user_id"].as_string() == str(scope["user_id"]))
            if "agent_id" in scope:
                conditions.append(MemoryModel.scope["agent_id"].as_string() == str(scope["agent_id"]))

        cap = int(ranking_cap())
        stmt = (
            select(MemoryModel)
            .where(*conditions)
            .order_by(MemoryModel.importance.desc(), MemoryModel.last_confirmed_at.desc())
            .limit(cap)
        )

        rows = (await self.db.execute(stmt)).scalars().all()
        if not rows:
            return []

        real_rows = [r for r in rows if r.embedding_real]
        unscored_rows = [r for r in rows if not r.embedding_real]

        query_dense: list[float] | None = None
        if real_rows:
            query_dense = await self._try_query_embedding(query)
            if query_dense is None:
                # Degraded: no semantic signal available — metadata order
                # for the whole pool (never fail the search).
                logger.warning("L3 retrieval degraded to metadata order: query embedding unavailable")
                real_rows, unscored_rows = [], list(rows)

        scored: list[ScoredMemory] = []
        if real_rows and query_dense is not None:
            raw = [ranking.cosine_similarity(query_dense, list(r.embedding)) for r in real_rows]
            normalized = ranking.normalize_scores(raw)
            for r, raw_rel, rel in zip(real_rows, raw, normalized, strict=True):
                breakdown = ranking.breakdown_for_hit(
                    relevance=rel,
                    last_confirmed_at=r.last_confirmed_at,
                    created_at=r.created_at,
                    importance=r.importance,
                    memory_type=r.memory_type,
                )
                fused, breakdown = ranking.fuse(rel, breakdown["decay_mult"], breakdown["importance_mult"])
                scored.append(
                    ScoredMemory(
                        memory=MemoryReadSchema.model_validate(r),
                        relevance=rel,
                        fused=fused,
                        revision=r.revision,
                        breakdown=breakdown,
                        raw_relevance=raw_rel,
                        last_confirmed_at=r.last_confirmed_at,
                    )
                )
            scored.sort(key=lambda s: s.fused, reverse=True)

        for r in unscored_rows:
            scored.append(
                ScoredMemory(
                    memory=MemoryReadSchema.model_validate(r),
                    relevance=None,
                    fused=0.0,
                    revision=r.revision,
                    breakdown={"relevance": 0.0, "decay_mult": 1.0, "importance_mult": 1.0, "semantic": False},
                    raw_relevance=None,
                    last_confirmed_at=r.last_confirmed_at,
                )
            )

        top = scored[:top_k]
        # Access-frequency accounting: only rows actually returned count.
        for s in top:
            s.memory.access_count += 1
        await self._bump_access_counts([s.memory.id for s in top])
        return top

    async def update_importance(
        self,
        workspace_id: uuid.UUID,
        memory_id: uuid.UUID,
        boost: float = 0.1,
    ) -> float:
        """Update memory importance score.

        Args:
            workspace_id: The workspace for tenant isolation.
            memory_id: The memory to update.
            boost: Amount to boost importance (can be negative).

        Returns:
            New importance score.

        Raises:
            ValueError: If memory not found.
        """
        memory = await self._get_by_id(workspace_id, memory_id)
        if memory is None:
            raise ValueError(f"Memory {memory_id} not found")

        new_importance = max(0.0, min(1.0, memory.importance + boost))
        memory.importance = new_importance
        memory.revision += 1
        await self.db.flush()

        logger.debug(f"Updated memory {memory_id} importance to {new_importance}")
        return new_importance

    async def delete_memory(
        self,
        workspace_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> None:
        """Soft delete a memory.

        Args:
            workspace_id: The workspace for tenant isolation.
            memory_id: The memory to delete.

        Raises:
            ValueError: If memory not found.
        """
        memory = await self._get_by_id(workspace_id, memory_id)
        if memory is None:
            raise ValueError(f"Memory {memory_id} not found")

        memory.deleted = True
        memory.deleted_at = datetime.now(UTC)
        memory.revision += 1
        await self.db.flush()
        logger.info(f"Deleted memory {memory_id}")

    async def supersede_memory(
        self,
        workspace_id: uuid.UUID,
        memory_id: uuid.UUID,
        superseded_by: uuid.UUID,
        expected_revision: int | None = None,
        *,
        agent_id: uuid.UUID | None = None,
    ) -> MemoryModel | None:
        """Supersede a memory: point it at its successor and soft-delete it.

        Consolidation replaces conflicting facts instead of overwriting or
        deleting them — the old row stays queryable with a ``superseded_by``
        lineage pointer. Writes one ``memory_edit_log`` audit row
        (``tool_name="consolidation"``) like every other memory mutation.

        Args:
            workspace_id: The workspace for tenant isolation.
            memory_id: The memory being replaced.
            superseded_by: ID of the successor memory.
            expected_revision: Optional optimistic-concurrency guard.
            agent_id: Owning agent, for the audit row attribution.

        Returns:
            The superseded memory, or None if not found.

        Raises:
            ValueError: If the expected revision does not match.
        """
        memory = await self._get_by_id(workspace_id, memory_id)
        if memory is None:
            return None
        if expected_revision is not None and memory.revision != expected_revision:
            raise ValueError(
                f"Revision conflict for memory {memory_id}: expected {expected_revision}, got {memory.revision}"
            )

        revision_after = memory.revision + 1
        memory.superseded_by = superseded_by
        memory.deleted = True
        memory.deleted_at = datetime.now(UTC)
        memory.revision = revision_after
        await self.db.flush()

        if agent_id is not None:
            self.db.add(
                MemoryEditLogModel(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    tool_name="consolidation",
                    target_type="user_memory",
                    target_id=memory_id,
                    revision_before=revision_after - 1,
                    revision_after=revision_after,
                    after_summary=f"superseded_by {superseded_by}",
                )
            )
            await self.db.flush()
        logger.info(f"Superseded memory {memory_id} -> {superseded_by}")
        return memory

    async def list_memories(
        self,
        workspace_id: uuid.UUID,
        scope: dict[str, Any] | None = None,
        memory_type: str | None = None,
        min_importance: float = 0.0,
        limit: int = 50,
    ) -> list[MemoryReadSchema]:
        """List memories with optional filters.

        Args:
            workspace_id: The workspace for tenant isolation.
            scope: Optional scope filter.
            memory_type: Optional type filter.
            min_importance: Minimum importance threshold.
            limit: Maximum results.

        Returns:
            List of memories.
        """
        conditions = [
            ~MemoryModel.deleted,
            MemoryModel.archived_at.is_(None),  # lifecycle archive filter
            MemoryModel.workspace_id == workspace_id,
        ]

        if memory_type:
            conditions.append(MemoryModel.memory_type == memory_type)
        if min_importance > 0:
            conditions.append(MemoryModel.importance >= min_importance)

        stmt = (
            select(MemoryModel)
            .where(*conditions)
            .order_by(MemoryModel.importance.desc(), MemoryModel.created_at.desc())
            .limit(limit)
        )

        result = await self.db.execute(stmt)
        memories = result.scalars().all()
        return [MemoryReadSchema.model_validate(m) for m in memories]

    async def count_memories(
        self,
        workspace_id: uuid.UUID,
    ) -> int:
        """Count total non-deleted memories in a workspace.

        Args:
            workspace_id: The workspace for tenant isolation.

        Returns:
            Total count.
        """
        stmt = (
            select(func.count())
            .select_from(MemoryModel)
            .where(
                ~MemoryModel.deleted,
                MemoryModel.workspace_id == workspace_id,
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def extract_facts(
        self,
        messages: list[dict[str, Any]],
    ) -> list[str]:
        """Extract facts from conversation messages.

        Simple heuristic extraction - identifies statements that look like
        facts or preferences.

        Args:
            messages: Conversation messages.

        Returns:
            List of extracted fact strings.
        """
        facts: list[str] = []

        for msg in messages:
            if msg.get("role") != "user":
                continue

            content = msg.get("content", "")
            if not isinstance(content, str):
                continue

            content_lower = content.lower()
            if any(
                indicator in content_lower
                for indicator in [
                    "i prefer",
                    "i like",
                    "i want",
                    "i need",
                    "my favorite",
                    "i always",
                    "i never",
                    "i work",
                    "i am",
                    "i use",
                ]
            ):
                facts.append(content)

        return facts

    async def _try_real_embedding(self, text: str) -> tuple[list[float] | None, bool]:
        """Attempt a real model embedding; (None, False) when unavailable."""
        try:
            from hecate_memory.rag.embedding import embedding_service
        except ImportError:
            return None, False
        try:
            if embedding_service.is_mock:
                return None, False
            result = await embedding_service.encode_query(text)
            return result.dense, True
        except Exception as e:
            logger.warning("Real embedding unavailable, memory stored pending backfill: %s", e)
            return None, False

    async def _try_query_embedding(self, query: str) -> list[float] | None:
        """Embed a retrieval query; None (degraded) when unavailable."""
        try:
            from hecate_memory.rag.embedding import embedding_service
        except ImportError:
            return None
        try:
            if embedding_service.is_mock:
                return None
            result = await embedding_service.encode_query(query)
            return result.dense
        except Exception as e:
            logger.warning("Query embedding failed: %s", e)
            return None

    async def _bump_access_counts(self, memory_ids: list[uuid.UUID]) -> None:
        """Increment access_count for the returned rows (existing semantics)."""
        if not memory_ids:
            return
        try:
            rows = (await self.db.execute(select(MemoryModel).where(MemoryModel.id.in_(memory_ids)))).scalars().all()
            now = datetime.now(UTC)
            for row in rows:
                row.access_count += 1
                row.last_accessed_at = now
            await self.db.flush()
        except Exception as e:
            logger.warning("Access-count bump failed (best-effort): %s", e)

    def _generate_mock_embedding(self, text: str) -> list[float]:
        """Generate a deterministic mock embedding for testing.

        Args:
            text: Text to generate embedding for.

        Returns:
            1024-dimensional float vector.
        """
        import hashlib

        hash_bytes = hashlib.md5(text.encode()).digest()  # noqa: S324
        dense = [b / 255.0 for b in hash_bytes]
        dense = dense + [0.0] * (1024 - len(dense))
        return dense[:1024]

    async def _get_by_id(self, workspace_id: uuid.UUID, memory_id: uuid.UUID) -> MemoryModel | None:
        """Get memory by ID with workspace check."""
        stmt = select(MemoryModel).where(
            MemoryModel.id == memory_id,
            MemoryModel.workspace_id == workspace_id,
            ~MemoryModel.deleted,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()


def ranking_cap() -> int:
    """Candidate-pool cap for in-process L3 semantic scoring."""
    try:
        from hecate.core.config import settings

        return max(1, int(getattr(settings, "MEMORY_FUSION_L3_CANDIDATE_CAP", 200)))
    except Exception:
        return 200
