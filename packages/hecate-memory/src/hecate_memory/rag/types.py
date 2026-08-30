"""Types for the RAG subsystem.

Provides shared data structures for vector store operations and
citation formatting across all RAG components.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field


@dataclass
class SearchResult:
    """A single search result from a vector store backend.

    Shared across all ``VectorStore`` implementations to provide a
    uniform return type for dense, sparse, and hybrid searches.
    """

    id: str
    score: float
    payload: dict[str, Any]


class Citation(PydanticBase):
    """A citation from a knowledge base retrieval.

    Represents a single source document chunk that was retrieved
    and used in generating the assistant's response.
    """

    model_config = ConfigDict(extra="forbid")

    position: int = Field(..., description="1-indexed position in the retrieved context")
    kb_id: uuid.UUID = Field(..., description="UUID of the knowledge base")
    kb_name: str = Field(..., description="Name of the knowledge base")
    document_name: str = Field(..., description="Name of the source document")
    chunk_id: str = Field(..., description="ID of the chunk in Qdrant")
    score: float = Field(..., description="Relevance score from hybrid search")
    content_snippet: str = Field(
        ...,
        max_length=150,
        description="First 150 characters of the chunk content",
    )

    def to_annotation(self) -> dict[str, Any]:
        """Convert to OpenAI-compatible annotation format.

        Returns:
            Dict with ``type: "kb_citation"`` and ``kb_citation`` payload.
        """
        return {
            "type": "kb_citation",
            "kb_citation": {
                "position": self.position,
                "kb_id": str(self.kb_id),
                "kb_name": self.kb_name,
                "document_name": self.document_name,
                "chunk_id": self.chunk_id,
                "score": self.score,
                "content_snippet": self.content_snippet,
            },
        }


class KbCitationAnnotation(PydanticBase):
    """OpenAI-compatible annotation wrapper for KB citations."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="kb_citation", description="Annotation type identifier")
    kb_citation: Citation = Field(..., description="The citation data")


class CitationResponse(PydanticBase):
    """Response schema for citation retrieval endpoint."""

    model_config = ConfigDict(extra="forbid")

    citations: list[Citation] = Field(default_factory=list, description="List of citations")
    message_id: uuid.UUID = Field(..., description="UUID of the message these citations belong to")
