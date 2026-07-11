"""Tests for ConversationTopicMatcher and ConversationClusterManager."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.conversation import ConversationModel
from hecate.models.conversation_cluster import ConversationClusterModel
from hecate.models.message import MessageModel
from hecate.services.ops_center.conversation_cluster_manager import (
    ConversationClusterManager,
    _compute_centroid,
)
from hecate.services.ops_center.conversation_topic_matcher import (
    ConversationTopicMatcher,
    _cosine_similarity,
)


async def _create_conversation(db: AsyncSession, cluster_id: uuid.UUID | None = None) -> ConversationModel:
    """Helper to create a conversation."""
    conv = ConversationModel(
        agent_id=uuid.uuid4(),
        title="Test Conversation",
        cluster_id=cluster_id,
    )
    db.add(conv)
    await db.flush()
    return conv


async def _create_message(
    db: AsyncSession, conversation_id: uuid.UUID, role: str = "user", content: str = "Hello"
) -> MessageModel:
    """Helper to create a message."""
    msg = MessageModel(
        conversation_id=conversation_id,
        role=role,
        content=content,
    )
    db.add(msg)
    await db.flush()
    return msg


async def _create_cluster(
    db: AsyncSession, label: str = "technical_support", embedding: list[float] | None = None
) -> ConversationClusterModel:
    """Helper to create a cluster."""
    cluster = ConversationClusterModel(
        label=label,
        centroid_embedding=embedding or [0.1] * 10,
        description="Test cluster",
        conversation_count=0,
    )
    db.add(cluster)
    await db.flush()
    return cluster


class TestCosineSimilarity:
    """Tests for _cosine_similarity()."""

    def test_identical_vectors(self) -> None:
        """Identical vectors have similarity 1.0."""
        a = [1.0, 0.0, 0.0]
        assert _cosine_similarity(a, a) == 1.0

    def test_orthogonal_vectors(self) -> None:
        """Orthogonal vectors have similarity 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_opposite_vectors(self) -> None:
        """Opposite vectors have similarity -1.0."""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_similarity(a, b) == -1.0

    def test_different_lengths(self) -> None:
        """Different length vectors return 0.0."""
        assert _cosine_similarity([1.0], [1.0, 0.0]) == 0.0


class TestComputeCentroid:
    """Tests for _compute_centroid()."""

    def test_single_embedding(self) -> None:
        """Single embedding returns itself."""
        emb = [1.0, 2.0, 3.0]
        assert _compute_centroid([emb]) == [1.0, 2.0, 3.0]

    def test_two_embeddings(self) -> None:
        """Two embeddings return their average."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        centroid = _compute_centroid([a, b])
        assert centroid == [0.5, 0.5, 0.0]

    def test_empty_list(self) -> None:
        """Empty list returns empty list."""
        assert _compute_centroid([]) == []


class TestMatchToCluster:
    """Tests for ConversationTopicMatcher.match_to_cluster()."""

    async def test_high_similarity_direct_match(self, db_session: AsyncSession) -> None:
        """Direct match when similarity > confirmation threshold."""
        cluster = await _create_cluster(db_session, embedding=[1.0] + [0.0] * 9)
        conv = await _create_conversation(db_session)
        await _create_message(db_session, conv.id, "user", "test")

        embedding = [0.99] + [0.01] * 9  # Very similar to cluster

        matcher = ConversationTopicMatcher(db_session)
        result = await matcher.match_to_cluster(conv.id, embedding)

        assert result is not None
        assert result.id == cluster.id

    async def test_low_similarity_no_match(self, db_session: AsyncSession) -> None:
        """No match when similarity < threshold."""
        await _create_cluster(db_session, embedding=[1.0] + [0.0] * 9)
        conv = await _create_conversation(db_session)
        await _create_message(db_session, conv.id, "user", "test")

        embedding = [0.0] * 9 + [1.0]  # Very different from cluster

        matcher = ConversationTopicMatcher(db_session)
        result = await matcher.match_to_cluster(conv.id, embedding)

        assert result is None

    async def test_no_clusters_returns_none(self, db_session: AsyncSession) -> None:
        """Returns None when no clusters exist."""
        conv = await _create_conversation(db_session)

        matcher = ConversationTopicMatcher(db_session)
        result = await matcher.match_to_cluster(conv.id, [0.5] * 10)

        assert result is None


class TestGenerateClusterLabel:
    """Tests for ConversationClusterManager.generate_cluster_label()."""

    async def test_generates_label(self, db_session: AsyncSession) -> None:
        """Generates label for cluster with conversations."""
        cluster = await _create_cluster(db_session, label="temp")
        conv = await _create_conversation(db_session, cluster_id=cluster.id)
        await _create_message(db_session, conv.id, "user", "How do I reset my password?")

        mock_response = AsyncMock()
        mock_response.content = json.dumps(
            {
                "label": "technical_support",
                "description": "Technical support questions",
            }
        )

        with patch(
            "hecate.services.ops_center.conversation_cluster_manager.llm_service.chat", return_value=mock_response
        ):
            manager = ConversationClusterManager(db_session)
            label = await manager.generate_cluster_label(cluster.id)

        assert label == "technical_support"

    async def test_empty_cluster_returns_none(self, db_session: AsyncSession) -> None:
        """Returns None for cluster with no conversations."""
        cluster = await _create_cluster(db_session)

        manager = ConversationClusterManager(db_session)
        label = await manager.generate_cluster_label(cluster.id)

        assert label is None


class TestRefineClusters:
    """Tests for ConversationClusterManager.refine_clusters()."""

    async def test_merges_similar_clusters(self, db_session: AsyncSession) -> None:
        """Merges clusters with centroid similarity > 0.9."""
        # Create two very similar clusters
        embedding = [0.5] * 10
        cluster_a = await _create_cluster(db_session, label="tech_support", embedding=embedding)
        cluster_b = await _create_cluster(db_session, label="technical_help", embedding=embedding)

        # Add conversations to cluster B
        conv = await _create_conversation(db_session, cluster_id=cluster_b.id)

        manager = ConversationClusterManager(db_session)
        await manager.refine_clusters()

        # Verify cluster B was soft-deleted
        await db_session.refresh(cluster_b)
        assert cluster_b.deleted is True

        # Verify conversation moved to cluster A
        await db_session.refresh(conv)
        assert conv.cluster_id == cluster_a.id
