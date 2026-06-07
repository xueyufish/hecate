"""Document ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for documents — uploaded files
that undergo parsing and chunking before being indexed into a knowledge base
for retrieval-augmented generation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class DocumentModel(BaseModel):
    """ORM model for documents — tracks uploaded files and their parsing status.

    Key fields:

    - **knowledge_base_id** — the knowledge base this document belongs to.
    - **file_path** — object storage path within MinIO where the original
      file is stored.
    - **parsing_status** — state machine tracking document processing:
      ``"pending"`` (uploaded, waiting) → ``"parsing"`` (in progress) →
      ``"completed"`` (successfully parsed and indexed) or ``"failed"``
      (error during parsing; see ``parsing_error`` for details).
    - **parsing_error** — human-readable error message when
      ``parsing_status`` is ``"failed"``; ``None`` otherwise.
    - **chunk_count** — number of text chunks produced after successful
      parsing. Set to ``0`` initially and updated once parsing completes.
    """

    __tablename__ = "documents"

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(nullable=False)
    file_size: Mapped[int] = mapped_column(default=0)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parsing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    parsing_error: Mapped[str | None] = mapped_column(nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (Index("idx_documents_kb", "knowledge_base_id", "deleted"),)


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
    deleted: bool | None = False
    deleted_at: datetime | None
