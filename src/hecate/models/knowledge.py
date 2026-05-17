from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class KnowledgeBaseModel(BaseModel):
    """ORM model for knowledge bases — configures embedding and chunking for document retrieval."""

    __tablename__ = "knowledge_bases"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    embedding_model: Mapped[str] = mapped_column(
        String(100), nullable=False, default="BAAI/bge-m3"
    )
    chunk_strategy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="fixed"
    )
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    qdrant_collection: Mapped[str] = mapped_column(String(255), nullable=False)


class KnowledgeBaseCreateSchema(PydanticBase):
    """Schema for creating a new knowledge base."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    embedding_model: str = "BAAI/bge-m3"
    chunk_strategy: str = Field(default="fixed", pattern="^(auto|fixed|semantic)$")
    chunk_size: int = Field(default=512, ge=128, le=2048)
    chunk_overlap: int = Field(default=100, ge=0, le=512)


class KnowledgeBaseReadSchema(PydanticBase):
    """Schema for reading knowledge base data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    description: str | None
    embedding_model: str
    chunk_strategy: str
    chunk_size: int
    chunk_overlap: int
    qdrant_collection: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
