"""Conversation topic matcher service.

Matches conversations to topic clusters using embedding cosine similarity
and LLM semantic confirmation. Implements incremental matching without
full re-clustering.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.models.conversation_cluster import ConversationClusterModel
from hecate.models.message import MessageModel
from hecate.services.llm.service import llm_service

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity (0.0-1.0).
    """
    if len(a) != len(b):
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (norm_a * norm_b)


CONFIRM_PROMPT = (
    "You are a topic classifier. Given a conversation and a list of candidate topic clusters, "
    "select the most appropriate cluster.\n\n"
    "Conversation:\n{conversation}\n\n"
    "Candidate clusters:\n{clusters}\n\n"
    'Respond with ONLY a JSON object: {{"selected_cluster_id": "<id or null>", "reasoning": "<brief explanation>"}}'
    "\nIf none of the clusters match well, set selected_cluster_id to null."
)


class ConversationTopicMatcher:
    """Service for matching conversations to topic clusters.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def match_to_cluster(
        self,
        conversation_id: uuid.UUID,
        embedding: list[float],
    ) -> ConversationClusterModel | None:
        """Match a conversation to an existing cluster.

        Uses cosine similarity for initial filtering, then LLM confirmation
        for ambiguous matches.

        Args:
            conversation_id: The conversation UUID.
            embedding: The conversation's embedding vector.

        Returns:
            Matched ConversationClusterModel, or None if no match.
        """
        # Load all clusters
        cluster_q = select(ConversationClusterModel).where(~ConversationClusterModel.deleted)
        clusters = (await self._db.execute(cluster_q)).all()

        if not clusters:
            logger.info("No clusters exist yet, skipping matching")
            return None

        # Compute cosine similarity to each cluster
        similarities: list[tuple[ConversationClusterModel, float]] = []
        for (cluster,) in clusters:
            sim = _cosine_similarity(embedding, cluster.centroid_embedding)
            similarities.append((cluster, sim))

        # Sort by similarity descending
        similarities.sort(key=lambda x: x[1], reverse=True)

        top_cluster, top_sim = similarities[0]

        # Direct assignment if above confirmation threshold
        if top_sim >= settings.CONVERSATION_CLUSTERING_CONFIRMATION_THRESHOLD:
            logger.info(
                "Direct match: conversation %s → cluster %s (sim=%.3f)",
                conversation_id,
                top_cluster.label,
                top_sim,
            )
            return top_cluster

        # Below similarity threshold → no match
        if top_sim < settings.CONVERSATION_CLUSTERING_SIMILARITY_THRESHOLD:
            logger.info(
                "No match: conversation %s has max similarity %.3f < threshold %.3f",
                conversation_id,
                top_sim,
                settings.CONVERSATION_CLUSTERING_SIMILARITY_THRESHOLD,
            )
            return None

        # Ambiguous range → LLM confirmation
        candidates = [
            (cluster, sim)
            for cluster, sim in similarities[:5]
            if sim >= settings.CONVERSATION_CLUSTERING_SIMILARITY_THRESHOLD
        ]

        if not candidates:
            return None

        return await self._llm_confirm_match(conversation_id, candidates)

    async def _llm_confirm_match(
        self,
        conversation_id: uuid.UUID,
        candidates: list[tuple[ConversationClusterModel, float]],
    ) -> ConversationClusterModel | None:
        """Use LLM to confirm which cluster best matches the conversation.

        Args:
            conversation_id: The conversation UUID.
            candidates: List of (cluster, similarity) tuples.

        Returns:
            Confirmed cluster, or None if no good match.
        """
        # Load conversation messages for context
        msg_q = (
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role.in_(["user", "assistant"]),
                ~MessageModel.deleted,
            )
            .order_by(MessageModel.created_at)
            .limit(10)
        )
        result = (await self._db.execute(msg_q)).all()
        if not result:
            return None

        conversation_text = "\n".join(
            f"{'User' if msg.role == 'user' else 'Assistant'}: {msg.content[:200]}" for (msg,) in result
        )

        # Build cluster descriptions
        cluster_descriptions = []
        for cluster, sim in candidates:
            desc = f"- ID: {cluster.id}, Label: {cluster.label}, Similarity: {sim:.3f}"
            if cluster.description:
                desc += f", Description: {cluster.description[:100]}"
            cluster_descriptions.append(desc)

        clusters_text = "\n".join(cluster_descriptions)

        prompt = CONFIRM_PROMPT.format(
            conversation=conversation_text,
            clusters=clusters_text,
        )

        try:
            response = await llm_service.chat(
                messages=[
                    {"role": "system", "content": "You are a topic classifier."},
                    {"role": "user", "content": prompt},
                ],
                model=settings.CONVERSATION_QUALITY_JUDGE_MODEL,
                temperature=0.0,
                max_tokens=200,
            )

            if not response.content:
                return None

            data = json.loads(response.content)
            selected_id = data.get("selected_cluster_id")

            if not selected_id or selected_id == "null":
                return None

            # Find the selected cluster
            for cluster, _sim in candidates:
                if str(cluster.id) == str(selected_id):
                    logger.info(
                        "LLM confirmed: conversation %s → cluster %s",
                        conversation_id,
                        cluster.label,
                    )
                    return cluster

            return None

        except Exception as e:
            logger.warning("LLM confirmation failed: %s", e)
            return None
