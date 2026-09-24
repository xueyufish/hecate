"""Knowledge memory service for L4 long-term agent knowledge.

Provides storage, retrieval, and management of agent-scoped knowledge
facts with hybrid search over Qdrant and metadata in PostgreSQL.
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
    KnowledgeMemoryModel,
    KnowledgeMemoryReadSchema,
    MemoryEditLogModel,
)
from hecate_memory.rag.embedding import embedding_service
from hecate_memory.rag.searcher import HybridSearcher
from hecate_memory.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

COLLECTION_NAME = "hecate_knowledge_memories"


def _namespace_visible(
    *,
    row_team_id: uuid.UUID | None,
    row_actor_id: uuid.UUID | None,
    caller_team_id: uuid.UUID | None,
    caller_actor_id: uuid.UUID | None,
    include_shared: bool,
) -> bool:
    """Check whether a memory row is visible to the caller under the
    four-layer namespace (workspace_id / team_id / actor_id / session_id).

    Rules:

    - ``actor_id == caller_actor_id`` is always visible (actor's own row).
    - Workspace-shared rows (``team_id IS NULL AND actor_id IS NULL``)
      are visible iff ``include_shared``.
    - Team rows (``team_id != NULL``): visible iff ``caller_team_id ==
      row_team_id``. ``actor_id`` on the row may be NULL (team-wide) or
      match the caller's actor_id (intra-team actor scoping).
    - Otherwise: hidden.

    Symmetric for read paths: the row is "in" the caller's scope iff
    every dimension matches what the caller has access to.
    """
    if row_actor_id is not None and caller_actor_id is not None and row_actor_id == caller_actor_id:
        return True
    if row_team_id is None and row_actor_id is None:
        return include_shared
    return bool(row_team_id is not None and caller_team_id is not None and row_team_id == caller_team_id)


@dataclass
class KnowledgeSearchResult:
    """Search result with relevance score."""

    memory: KnowledgeMemoryReadSchema
    score: float
    dense_score: float = 0.0
    sparse_score: float = 0.0
    # Exogenous decay anchor of the backing row — rides along so the
    # merge layer can apply the metadata bias without reloading the row.
    last_confirmed_at: datetime | None = None


class KnowledgeMemoryService:
    """Service for managing L4 knowledge memory.

    Provides CRUD operations and hybrid search for agent knowledge
    with multi-tenant isolation via workspace_id.
    """

    def __init__(self, db: AsyncSession, vector_store: VectorStore | None = None) -> None:
        """Initialize with database session and optional vector store.

        Args:
            db: Async SQLAlchemy session for database operations.
            vector_store: Optional vector store for Qdrant operations.
                If None, search and insert will use mock embeddings.
        """
        self.db = db
        self._vector_store = vector_store
        self._searcher = HybridSearcher(vector_store) if vector_store else None
        self._collection_created = False

    async def insert_knowledge(
        self,
        agent_id: uuid.UUID,
        workspace_id: uuid.UUID,
        content: str,
        tags: list[str] | None = None,
        importance: float = 0.5,
        user_id: uuid.UUID | None = None,
        source: str = "agent_tool",
    ) -> KnowledgeMemoryReadSchema:
        """Store a new knowledge memory.

        Args:
            agent_id: The agent whose knowledge this belongs to.
            workspace_id: The workspace for tenant isolation.
            content: The knowledge fact text.
            tags: Optional tags for categorization.
            importance: Importance score (0.0 to 1.0).
            user_id: Optional user reference for user-specific knowledge.
            source: How the knowledge was created.

        Returns:
            The created (or updated) knowledge memory.
        """
        existing = await self._check_duplicate(agent_id, workspace_id, content)
        if existing is not None:
            existing.access_count += 1
            await self.db.flush()
            await self._upsert_to_qdrant(existing)
            await self.db.refresh(existing)
            return KnowledgeMemoryReadSchema.model_validate(existing)

        memory = KnowledgeMemoryModel(
            workspace_id=workspace_id,
            agent_id=agent_id,
            content=content,
            tags=tags or [],
            importance=importance,
            source=source,
            user_id=user_id,
        )
        self.db.add(memory)
        await self.db.flush()

        await self._upsert_to_qdrant(memory)

        logger.info(f"Inserted knowledge memory for agent {agent_id}: {content[:50]}...")
        return KnowledgeMemoryReadSchema.model_validate(memory)

    async def search_knowledge(
        self,
        agent_id: uuid.UUID,
        workspace_id: uuid.UUID,
        query: str,
        top_k: int = 5,
        tags: list[str] | None = None,
        user_id: uuid.UUID | None = None,
        mode: str = "hybrid",
        *,
        team_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        include_shared: bool = True,
    ) -> list[KnowledgeSearchResult]:
        """Search knowledge memories using hybrid search.

        Args:
            agent_id: The agent whose knowledge to search.
            workspace_id: The workspace for tenant isolation.
            query: The search query.
            top_k: Maximum number of results.
            tags: Optional tag filter.
            user_id: Optional user filter for user-specific knowledge.
            mode: Search mode ("hybrid", "dense", or "sparse").
            team_id: 4.23 namespace dimension. When ``None``, returns
                rows whose ``team_id IS NULL`` or whose ``team_id``
                matches the caller's. When set, narrows to that team.
            actor_id: 4.23 namespace dimension. Same shape as
                ``team_id``: ``None`` matches ``actor_id IS NULL`` rows.
            include_shared: When ``True`` (default), rows with
                ``actor_id IS NULL AND team_id IS NULL`` (workspace-shared)
                are also returned. When ``False``, only the caller's
                exact actor/team scope is queried.

        Returns:
            List of scored knowledge search results.
        """
        if self._searcher is None:
            return []

        await self._ensure_collection()

        results = await self._searcher.search(
            COLLECTION_NAME,
            query,
            limit=top_k * 3,
            mode=mode,
            workspace_id=str(workspace_id),
        )

        filtered: list[KnowledgeSearchResult] = []
        for r in results:
            meta = r.metadata
            if str(meta.get("workspace_id", "")) != str(workspace_id):
                continue
            if str(meta.get("agent_id", "")) != str(agent_id):
                continue
            if user_id and str(meta.get("user_id", "")) != str(user_id):
                continue
            # 4.23 namespace filtering on metadata: the Qdrant vector
            # payload mirrors the SQL columns for retrieval-time filter;
            # absent keys are treated as NULL (actor-only or workspace-
            # shared rows). The structured filter below ensures the
            # SQL-backed path returns the same scope.
            row_team_id = meta.get("team_id")
            row_actor_id = meta.get("actor_id")
            if not _namespace_visible(
                row_team_id=uuid.UUID(row_team_id) if row_team_id else None,
                row_actor_id=uuid.UUID(row_actor_id) if row_actor_id else None,
                caller_team_id=team_id,
                caller_actor_id=actor_id,
                include_shared=include_shared,
            ):
                continue
            if tags:
                result_tags = set(meta.get("tags", []))
                if not result_tags.intersection(tags):
                    continue

            memory = await self._get_by_id_raw(workspace_id, agent_id, uuid.UUID(r.id))
            if memory is None:
                continue
            # Lifecycle archive: archived rows stay in Qdrant but leave
            # every retrieval path (soft delete; restore rejoins them).
            if memory.archived_at is not None:
                continue

            memory.access_count += 1
            filtered.append(
                KnowledgeSearchResult(
                    memory=KnowledgeMemoryReadSchema.model_validate(memory),
                    score=r.score,
                    dense_score=r.dense_score,
                    sparse_score=r.sparse_score,
                    last_confirmed_at=memory.last_confirmed_at,
                )
            )

            if len(filtered) >= top_k:
                break

        await self.db.flush()
        return filtered

    async def get_knowledge(
        self,
        agent_id: uuid.UUID,
        workspace_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> KnowledgeMemoryReadSchema:
        """Get a single knowledge memory by ID.

        Args:
            agent_id: The agent that owns the knowledge.
            workspace_id: The workspace for tenant isolation.
            memory_id: The knowledge memory ID.

        Returns:
            The knowledge memory data.

        Raises:
            ValueError: If not found.
        """
        memory = await self._get_by_id_raw(workspace_id, agent_id, memory_id)
        if memory is None:
            raise ValueError(f"Knowledge memory {memory_id} not found")
        return KnowledgeMemoryReadSchema.model_validate(memory)

    async def list_knowledge(
        self,
        agent_id: uuid.UUID,
        workspace_id: uuid.UUID,
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[KnowledgeMemoryReadSchema], int]:
        """List knowledge memories with pagination.

        Args:
            agent_id: The agent whose knowledge to list.
            workspace_id: The workspace for tenant isolation.
            tags: Optional tag filter.
            limit: Maximum results per page.
            offset: Pagination offset.

        Returns:
            Tuple of (results, total_count).
        """
        conditions = [
            KnowledgeMemoryModel.workspace_id == workspace_id,
            KnowledgeMemoryModel.agent_id == agent_id,
            ~KnowledgeMemoryModel.deleted,
            KnowledgeMemoryModel.archived_at.is_(None),  # lifecycle archive filter
        ]

        count_stmt = select(func.count()).select_from(KnowledgeMemoryModel).where(*conditions)
        count_result = await self.db.execute(count_stmt)
        total = count_result.scalar_one()

        data_stmt = (
            select(KnowledgeMemoryModel)
            .where(*conditions)
            .order_by(KnowledgeMemoryModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(data_stmt)
        memories = result.scalars().all()

        if tags:
            memories = [m for m in memories if set(m.tags).intersection(tags)]
            total = len(memories)

        return [KnowledgeMemoryReadSchema.model_validate(m) for m in memories], total

    async def delete_knowledge(
        self,
        agent_id: uuid.UUID,
        workspace_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> None:
        """Delete a knowledge memory.

        Args:
            agent_id: The agent that owns the knowledge.
            workspace_id: The workspace for tenant isolation.
            memory_id: The knowledge memory ID.

        Raises:
            ValueError: If not found.
        """
        memory = await self._get_by_id_raw(workspace_id, agent_id, memory_id)
        if memory is None:
            raise ValueError(f"Knowledge memory {memory_id} not found")

        memory.deleted = True
        memory.deleted_at = datetime.now(UTC)
        memory.revision += 1
        await self.db.flush()

        if self._vector_store is not None:
            try:
                await self._vector_store.delete_by_ids(COLLECTION_NAME, [str(memory_id)])
            except Exception as e:
                logger.warning(f"Failed to delete knowledge from Qdrant: {e}")

        logger.info(f"Deleted knowledge memory {memory_id} for agent {agent_id}")

    async def supersede_knowledge(
        self,
        agent_id: uuid.UUID,
        workspace_id: uuid.UUID,
        memory_id: uuid.UUID,
        superseded_by: uuid.UUID,
        expected_revision: int | None = None,
    ) -> KnowledgeMemoryModel | None:
        """Supersede a knowledge memory: lineage pointer + soft delete.

        Consolidation replaces conflicting facts instead of overwriting or
        deleting them — the old row stays queryable with a ``superseded_by``
        lineage pointer. The Qdrant vector is removed since the row no
        longer participates in retrieval. Writes one ``memory_edit_log``
        audit row (``tool_name="consolidation"``).

        Args:
            agent_id: The agent that owns the knowledge.
            workspace_id: The workspace for tenant isolation.
            memory_id: The knowledge memory being replaced.
            superseded_by: ID of the successor memory.
            expected_revision: Optional optimistic-concurrency guard.

        Returns:
            The superseded memory, or None if not found.

        Raises:
            ValueError: If the expected revision does not match.
        """
        memory = await self._get_by_id_raw(workspace_id, agent_id, memory_id)
        if memory is None:
            return None
        if expected_revision is not None and memory.revision != expected_revision:
            raise ValueError(
                f"Revision conflict for knowledge memory {memory_id}: "
                f"expected {expected_revision}, got {memory.revision}"
            )

        revision_after = memory.revision + 1
        memory.superseded_by = superseded_by
        memory.deleted = True
        memory.deleted_at = datetime.now(UTC)
        memory.revision = revision_after
        await self.db.flush()

        self.db.add(
            MemoryEditLogModel(
                workspace_id=workspace_id,
                agent_id=agent_id,
                tool_name="consolidation",
                target_type="knowledge_memory",
                target_id=memory_id,
                revision_before=revision_after - 1,
                revision_after=revision_after,
                after_summary=f"superseded_by {superseded_by}",
            )
        )
        await self.db.flush()

        if self._vector_store is not None:
            try:
                await self._vector_store.delete_by_ids(COLLECTION_NAME, [str(memory_id)])
            except Exception as e:
                logger.warning(f"Failed to delete superseded knowledge vector from Qdrant: {e}")

        logger.info(f"Superseded knowledge memory {memory_id} -> {superseded_by}")
        return memory

    async def reindex(self, memory: KnowledgeMemoryModel) -> None:
        """Regenerate the Qdrant vector for a knowledge memory.

        Call after an in-place content change so the stored embedding stays in
        sync with the new text.
        """
        await self._upsert_to_qdrant(memory)

    async def _ensure_collection(self) -> None:
        """Lazily create the Qdrant collection on first use."""
        if self._collection_created or self._vector_store is None:
            return

        exists = await self._vector_store.collection_exists(COLLECTION_NAME)
        if not exists:
            await self._vector_store.create_collection(
                COLLECTION_NAME,
                vector_size=1024,
                with_sparse=True,
            )
        self._collection_created = True

    async def _upsert_to_qdrant(self, memory: KnowledgeMemoryModel) -> None:
        """Generate embedding and upsert to Qdrant.

        Args:
            memory: The knowledge memory model to index.
        """
        if self._vector_store is None:
            return

        await self._ensure_collection()

        result = await embedding_service.encode_query(memory.content)
        # Honest marker: rows indexed from mock vectors are flagged so the
        # real-embedding backfill can find them.
        memory.embedding_real = not embedding_service.is_mock

        payload: dict[str, Any] = {
            "workspace_id": str(memory.workspace_id),
            "agent_id": str(memory.agent_id),
            "tags": memory.tags,
            "importance": memory.importance,
            "text": memory.content,
        }
        if memory.user_id:
            payload["user_id"] = str(memory.user_id)

        sparse_vectors: list[dict[int, float]] | None = None
        if result.sparse:
            sparse_vectors = [result.sparse]

        await self._vector_store.upsert(
            COLLECTION_NAME,
            [str(memory.id)],
            [result.dense],
            [payload],
            sparse_vectors,
        )

    async def _check_duplicate(
        self,
        agent_id: uuid.UUID,
        workspace_id: uuid.UUID,
        content: str,
    ) -> KnowledgeMemoryModel | None:
        """Check for existing knowledge with identical content.

        Args:
            agent_id: The agent scope.
            workspace_id: The workspace scope.
            content: The content to check.

        Returns:
            Existing model if found, None otherwise.
        """
        stmt = select(KnowledgeMemoryModel).where(
            KnowledgeMemoryModel.agent_id == agent_id,
            KnowledgeMemoryModel.workspace_id == workspace_id,
            KnowledgeMemoryModel.content == content,
            ~KnowledgeMemoryModel.deleted,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_by_id_raw(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> KnowledgeMemoryModel | None:
        """Get raw model by ID with ownership checks."""
        stmt = select(KnowledgeMemoryModel).where(
            KnowledgeMemoryModel.id == memory_id,
            KnowledgeMemoryModel.workspace_id == workspace_id,
            KnowledgeMemoryModel.agent_id == agent_id,
            ~KnowledgeMemoryModel.deleted,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
