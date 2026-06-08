"""Memory ORM models and Pydantic schemas.

Defines the persistence layer (SQLAlchemy) and API schemas (Pydantic) for:

- **MemoryBlockModel** — L1 working memory: named regions in the context window
  that agents can read/write each turn (e.g., persona, user_profile).
- **MemoryModel** — L3 user memory: persistent facts extracted from conversations,
  stored with vector embeddings for semantic retrieval across sessions.
- **KnowledgeMemoryModel** — L4 knowledge memory: long-term agent knowledge archive,
  stored in PostgreSQL metadata + Qdrant vectors for hybrid search retrieval.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text  # noqa: I001
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


class MemoryBlockModel(BaseModel):
    """ORM model for L1 working memory blocks.

    Memory blocks are named regions in the context window that agents can
    read and write each turn. Examples: persona, user_profile, domain_context.

    Key fields:

    - **workspace_id** — tenant scope for multi-tenant isolation.
    - **agent_id** — the agent this block belongs to.
    - **label** — unique name within the agent (e.g., "persona").
    - **content** — the current content of the block.
    - **position** — ordering index for context assembly.
    - **limit** — maximum token count for this block.
    """

    __tablename__ = "memory_blocks"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limit: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)

    __table_args__ = (
        Index("idx_memory_blocks_workspace", "workspace_id", "deleted"),
        Index("idx_memory_blocks_agent", "agent_id"),
        Index("idx_memory_blocks_agent_label", "agent_id", "label", unique=True),
    )


class MemoryModel(BaseModel):
    """ORM model for L3 user memory — persistent facts across sessions.

    Stores facts extracted from conversations with vector embeddings for
    semantic retrieval. Supports multi-tenant isolation via workspace_id
    and finer-grained scoping via the scope JSONB field.

    Key fields:

    - **workspace_id** — tenant scope for multi-tenant isolation.
    - **content** — the extracted fact/preference/knowledge.
    - **scope** — JSONB with user_id, agent_id, session_id for fine-grained isolation.
    - **memory_type** — semantic (facts), procedural (methods), episodic (events).
    - **importance** — importance score (0.0 to 1.0).
    - **access_count** — number of times this memory has been retrieved.
    - **embedding** — vector embedding for semantic search (stored as JSON array).
    """

    __tablename__ = "memories"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False, default="semantic")
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False, default=list)

    __table_args__ = (
        Index("idx_memories_workspace", "workspace_id", "deleted"),
        Index("idx_memories_scope", "scope"),
        Index("idx_memories_type", "memory_type"),
        Index("idx_memories_importance", "importance"),
    )


class KnowledgeMemoryModel(BaseModel):
    """ORM model for L4 knowledge memory — agent's long-term knowledge archive.

    Stores atomic facts that agents actively choose to remember for
    long-term retrieval. Used for agent-scoped knowledge accumulation
    that persists across conversations and user sessions.

    Key fields:

    - **workspace_id** — tenant scope for multi-tenant isolation.
    - **agent_id** — the agent whose knowledge this belongs to.
    - **content** — the knowledge fact text.
    - **tags** — JSON array for categorization and filtering.
    - **importance** — importance score (0.0 to 1.0).
    - **access_count** — number of times this knowledge has been retrieved.
    - **source** — how the knowledge was created ("agent_tool" or "api").
    - **user_id** — optional user reference for user-specific knowledge.
    """

    __tablename__ = "knowledge_memories"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="agent_tool")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
        default=None,
    )

    __table_args__ = (
        Index("idx_knowledge_memories_workspace", "workspace_id", "deleted"),
        Index("idx_knowledge_memories_agent", "agent_id"),
        Index("idx_knowledge_memories_importance", "importance"),
    )


# --- Pydantic Schemas ---


class MemoryBlockCreateSchema(PydanticBase):
    """Schema for creating a new memory block."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=100)
    content: str = Field(default="", max_length=50000)
    position: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, gt=0)
    workspace_id: uuid.UUID = Field(default=_DEFAULT_WORKSPACE)


class MemoryBlockUpdateSchema(PydanticBase):
    """Schema for updating a memory block."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(None, max_length=50000)
    position: int | None = Field(None, ge=0)
    limit: int | None = Field(None, gt=0)


class MemoryBlockReadSchema(PydanticBase):
    """Schema for reading memory block data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    label: str
    content: str
    position: int
    limit: int
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None


class MemoryCreateSchema(PydanticBase):
    """Schema for creating a new memory."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=10000)
    scope: dict[str, Any] = Field(default_factory=dict)
    memory_type: str = Field(default="semantic", pattern="^(semantic|procedural|episodic)$")
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    workspace_id: uuid.UUID = Field(default=_DEFAULT_WORKSPACE)


class MemoryReadSchema(PydanticBase):
    """Schema for reading memory data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    content: str
    scope: dict[str, Any]
    memory_type: str
    importance: float
    access_count: int
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None


# --- L4 Knowledge Memory Schemas ---


class KnowledgeMemoryCreateSchema(PydanticBase):
    """Schema for creating a new knowledge memory."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=10000)
    tags: list[str] = Field(default_factory=list)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    user_id: uuid.UUID | None = Field(None)
    source: str = Field(default="agent_tool")


class KnowledgeMemoryUpdateSchema(PydanticBase):
    """Schema for updating a knowledge memory."""

    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(None, min_length=1, max_length=10000)
    tags: list[str] | None = Field(None)
    importance: float | None = Field(None, ge=0.0, le=1.0)


class KnowledgeMemoryReadSchema(PydanticBase):
    """Schema for reading knowledge memory data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    content: str
    tags: list[str]
    importance: float
    access_count: int
    source: str
    user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None


class KnowledgeMemorySearchSchema(PydanticBase):
    """Schema for searching knowledge memories."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    tags: list[str] | None = Field(None)
    user_id: uuid.UUID | None = Field(None)
    mode: str = Field(default="hybrid")
