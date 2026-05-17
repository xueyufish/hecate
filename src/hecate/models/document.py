from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class DocumentModel(BaseModel):
    """ORM model for documents — tracks uploaded files and their parsing status."""

    __tablename__ = "documents"

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(nullable=False)
    file_size: Mapped[int] = mapped_column(default=0)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parsing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    parsing_error: Mapped[str | None] = mapped_column(nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index(
            "idx_documents_kb",
            "knowledge_base_id",
            postgresql_where=BaseModel.deleted_at.is_(None),
        ),
    )


class DocumentCreateSchema(PydanticBase):
    """Schema for creating a new document record."""

    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: uuid.UUID
    filename: str = Field(..., min_length=1, max_length=255)
    file_path: str
    file_size: int = 0
    content_type: str | None = None


class DocumentReadSchema(PydanticBase):
    """Schema for reading document data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    filename: str
    file_path: str
    file_size: int
    content_type: str | None
    parsing_status: str
    parsing_error: str | None
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
