"""Conversation embedding service.

Generates and retrieves embeddings for conversations using the existing
RAG embedding service. Stores embeddings in Qdrant for topic clustering.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.message import MessageModel
from hecate.services.rag.embedding import embedding_service

logger = logging.getLogger(__name__)

COLLECTION_NAME = "conversation_embeddings"


class ConversationEmbeddingService:
    """Service for generating and retrieving conversation embeddings.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def generate_embedding(
        self,
        conversation_id: uuid.UUID,
    ) -> list[float] | None:
        """Generate embedding for a conversation.

        Loads all user and assistant messages, concatenates their content,
        and generates an embedding using the RAG embedding service.

        Args:
            conversation_id: The conversation UUID.

        Returns:
            Embedding vector, or None if generation fails.
        """
        # Load messages
        msg_q = (
            select(MessageModel)
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role.in_(["user", "assistant"]),
                ~MessageModel.deleted,
            )
            .order_by(MessageModel.created_at)
        )
        result = (await self._db.execute(msg_q)).all()
        if not result:
            logger.warning("No messages found for conversation %s", conversation_id)
            return None

        # Concatenate message content
        texts = []
        for (msg,) in result:
            prefix = "User" if msg.role == "user" else "Assistant"
            texts.append(f"{prefix}: {msg.content}")

        combined_text = "\n\n".join(texts)

        # Generate embedding
        try:
            embeddings = await embedding_service.embed([combined_text])
            if not embeddings:
                logger.warning("Empty embedding returned for conversation %s", conversation_id)
                return None

            embedding = embeddings[0]

            # Store in Qdrant
            try:
                from hecate.core.database import get_qdrant_client

                client = get_qdrant_client()

                # Ensure collection exists
                try:
                    client.get_collection(COLLECTION_NAME)
                except Exception:
                    from qdrant_client.models import Distance, VectorParams

                    client.create_collection(
                        collection_name=COLLECTION_NAME,
                        vectors_config=VectorParams(
                            size=len(embedding),
                            distance=Distance.COSINE,
                        ),
                    )

                # Upsert embedding
                from qdrant_client.models import PointStruct

                client.upsert(
                    collection_name=COLLECTION_NAME,
                    points=[
                        PointStruct(
                            id=str(conversation_id),
                            vector=embedding,
                            payload={"conversation_id": str(conversation_id)},
                        )
                    ],
                )

                logger.info("Stored embedding for conversation %s", conversation_id)
            except Exception as e:
                logger.warning("Failed to store embedding in Qdrant: %s", e)

            return embedding

        except Exception as e:
            logger.warning("Failed to generate embedding for conversation %s: %s", conversation_id, e)
            return None

    async def get_embedding(
        self,
        conversation_id: uuid.UUID,
    ) -> list[float] | None:
        """Retrieve embedding for a conversation from Qdrant.

        Args:
            conversation_id: The conversation UUID.

        Returns:
            Embedding vector, or None if not found.
        """
        try:
            from hecate.core.database import get_qdrant_client

            client = get_qdrant_client()
            result = client.retrieve(
                collection_name=COLLECTION_NAME,
                ids=[str(conversation_id)],
            )
            if result and result[0].vector:
                return result[0].vector
            return None
        except Exception as e:
            logger.warning("Failed to retrieve embedding for conversation %s: %s", conversation_id, e)
            return None
