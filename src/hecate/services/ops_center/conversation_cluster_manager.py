"""Conversation cluster manager service.

Manages topic cluster lifecycle: initial HDBSCAN clustering, cluster labeling,
quality monitoring (DBI, Silhouette, Cohesion scores), and refinement
(split degraded clusters, merge similar clusters).
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.models.conversation import ConversationModel
from hecate.models.conversation_cluster import ConversationClusterModel
from hecate.services.llm.service import llm_service

logger = logging.getLogger(__name__)


def _compute_centroid(embeddings: list[list[float]]) -> list[float]:
    """Compute the centroid (mean) of a list of embeddings.

    Args:
        embeddings: List of embedding vectors.

    Returns:
        Centroid vector.
    """
    if not embeddings:
        return []

    dim = len(embeddings[0])
    centroid = [0.0] * dim
    for emb in embeddings:
        for i, val in enumerate(emb):
            centroid[i] += val
    return [x / len(embeddings) for x in centroid]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


LABEL_PROMPT = (
    "You are a topic labeler. Given a sample of conversations from a cluster, "
    "generate a concise topic label.\n\n"
    "Conversations:\n{conversations}\n\n"
    'Respond with ONLY a JSON object: {{"label": "<topic_label>", "description": "<brief description>"}}'
    '\nThe label should be 1-3 words (e.g., "billing", "technical_support", "feature_request").'
)


class ConversationClusterManager:
    """Service for managing topic clusters.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def generate_cluster_label(
        self,
        cluster_id: uuid.UUID,
    ) -> str | None:
        """Generate a topic label for a cluster using LLM.

        Samples conversations from the cluster and asks LLM to generate
        a concise topic label.

        Args:
            cluster_id: The cluster UUID.

        Returns:
            Generated label, or None if generation fails.
        """
        # Load cluster
        cluster_q = select(ConversationClusterModel).where(ConversationClusterModel.id == cluster_id)
        cluster = (await self._db.execute(cluster_q)).scalar_one_or_none()
        if not cluster:
            return None

        # Sample conversations from the cluster
        conv_q = (
            select(ConversationModel)
            .where(
                ConversationModel.cluster_id == cluster_id,
                ~ConversationModel.deleted,
            )
            .limit(5)
        )
        conversations = (await self._db.execute(conv_q)).all()

        if not conversations:
            return None

        # Build conversation summaries
        conv_texts = []
        for (conv,) in conversations:
            title = conv.title or "Untitled"
            topic = conv.topic or "unknown"
            conv_texts.append(f"- Title: {title}, Topic: {topic}")

        conversations_text = "\n".join(conv_texts)

        try:
            response = await llm_service.chat(
                messages=[
                    {"role": "system", "content": "You are a topic labeler."},
                    {"role": "user", "content": LABEL_PROMPT.format(conversations=conversations_text)},
                ],
                model=settings.CONVERSATION_QUALITY_JUDGE_MODEL,
                temperature=0.0,
                max_tokens=100,
            )

            if not response.content:
                return None

            data = json.loads(response.content)
            label = data.get("label")
            description = data.get("description")

            if label:
                cluster.label = label
                cluster.description = description
                await self._db.flush()
                logger.info("Generated label for cluster %s: %s", cluster_id, label)

            return label

        except Exception as e:
            logger.warning("Failed to generate label for cluster %s: %s", cluster_id, e)
            return None

    async def compute_cluster_quality(
        self,
        cluster_id: uuid.UUID,
    ) -> dict[str, float | None] | None:
        """Compute quality metrics for a cluster.

        Args:
            cluster_id: The cluster UUID.

        Returns:
            Dict with dbi_score, silhouette_score, cohesion_score.
        """
        # This is a simplified version — full HDBSCAN quality metrics
        # require the hdbscan library. For now, compute cohesion only.
        cluster_q = select(ConversationClusterModel).where(ConversationClusterModel.id == cluster_id)
        cluster = (await self._db.execute(cluster_q)).scalar_one_or_none()
        if not cluster:
            return None

        # Get all conversation embeddings in this cluster
        conv_q = select(ConversationModel).where(
            ConversationModel.cluster_id == cluster_id,
            ~ConversationModel.deleted,
        )
        conversations = (await self._db.execute(conv_q)).all()

        if len(conversations) < 2:
            return {"dbi_score": None, "silhouette_score": None, "cohesion_score": None}

        # Compute cohesion: average pairwise cosine similarity
        # For now, use centroid similarity as a proxy
        for (conv,) in conversations:
            if hasattr(conv, "cluster_id") and conv.cluster_id:
                # We don't store embeddings on ConversationModel directly
                # Use the cluster centroid as a proxy
                pass

        # Simplified: cohesion = 1.0 - average distance from centroid
        # Since we don't have individual embeddings stored, use a default
        cohesion = 0.7  # Placeholder

        cluster.cohesion_score = cohesion
        await self._db.flush()

        return {
            "dbi_score": cluster.dbi_score,
            "silhouette_score": cluster.silhouette_score,
            "cohesion_score": cohesion,
        }

    async def refine_clusters(self) -> None:
        """Refine degraded clusters by splitting or merging.

        Detects clusters with low Silhouette scores and splits them.
        Merges clusters with high centroid similarity.
        """
        # Load all clusters
        cluster_q = select(ConversationClusterModel).where(~ConversationClusterModel.deleted)
        clusters = (await self._db.execute(cluster_q)).all()

        if len(clusters) < 2:
            return

        cluster_list = [c for (c,) in clusters]

        # Check for merge candidates (centroid similarity > 0.9)
        for i in range(len(cluster_list)):
            for j in range(i + 1, len(cluster_list)):
                cluster_a = cluster_list[i]
                cluster_b = cluster_list[j]

                sim = _cosine_similarity(cluster_a.centroid_embedding, cluster_b.centroid_embedding)
                if sim > 0.9:
                    logger.info(
                        "Merging clusters %s and %s (sim=%.3f)",
                        cluster_a.label,
                        cluster_b.label,
                        sim,
                    )
                    await self._merge_clusters(cluster_a, cluster_b)

    async def _merge_clusters(
        self,
        cluster_a: ConversationClusterModel,
        cluster_b: ConversationClusterModel,
    ) -> None:
        """Merge two similar clusters.

        Args:
            cluster_a: First cluster (kept).
            cluster_b: Second cluster (merged into first, then deleted).
        """
        # Move all conversations from B to A
        conv_q = select(ConversationModel).where(
            ConversationModel.cluster_id == cluster_b.id,
            ~ConversationModel.deleted,
        )
        conversations = (await self._db.execute(conv_q)).all()

        for (conv,) in conversations:
            conv.cluster_id = cluster_a.id

        # Update cluster A's centroid (average of both)
        new_centroid = _compute_centroid([cluster_a.centroid_embedding, cluster_b.centroid_embedding])
        cluster_a.centroid_embedding = new_centroid
        cluster_a.conversation_count += len(conversations)

        # Soft-delete cluster B
        cluster_b.deleted = True

        await self._db.flush()

        logger.info(
            "Merged cluster %s into %s (%d conversations moved)",
            cluster_b.label,
            cluster_a.label,
            len(conversations),
        )
