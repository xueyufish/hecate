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
)
from hecate.services.rag.embedding import embedding_service
from hecate.services.rag.searcher import HybridSearcher
from hecate.services.rag.vector_store import VectorStore

logger = logging.getLogger(__name__)

COLLECTION_NAME = "hecate_knowledge_memories"


@dataclass
class KnowledgeSearchResult:
    """Search result with relevance score."""

    memory: KnowledgeMemoryReadSchema
    score: float
    dense_score: float = 0.0
    sparse_score: float = 0.0


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
            if tags:
                result_tags = set(meta.get("tags", []))
                if not result_tags.intersection(tags):
                    continue

            memory = await self._get_by_id_raw(workspace_id, agent_id, uuid.UUID(r.id))
            if memory is None:
                continue

            memory.access_count += 1
            filtered.append(
                KnowledgeSearchResult(
                    memory=KnowledgeMemoryReadSchema.model_validate(memory),
                    score=r.score,
                    dense_score=r.dense_score,
                    sparse_score=r.sparse_score,
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
        await self.db.flush()

        if self._vector_store is not None:
            try:
                await self._vector_store.delete_by_ids(COLLECTION_NAME, [str(memory_id)])
            except Exception as e:
                logger.warning(f"Failed to delete knowledge from Qdrant: {e}")

        logger.info(f"Deleted knowledge memory {memory_id} for agent {agent_id}")

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
