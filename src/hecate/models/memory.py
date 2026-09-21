"""Memory ORM models and Pydantic schemas.

Defines the persistence layer (SQLAlchemy) and API schemas (Pydantic) for:

- **MemoryBlockModel** — L1 working memory: named regions in the context window
  that agents can read/write each turn (e.g., persona, user_profile).
- **MemoryModel** — L3 user memory: persistent facts extracted from conversations,
  stored with vector embeddings for semantic retrieval across sessions.
- **KnowledgeMemoryModel** — L4 knowledge memory: long-term agent knowledge archive,
  stored in PostgreSQL metadata + Qdrant vectors for hybrid search retrieval.
- **RecallMessageModel** — conversation recall storage: transcript-level index
  rows (metadata + scope) backing semantic search over past conversations;
  vectors live in the Qdrant ``hecate_recall`` collection.
- **MemoryEditLogModel** — append-only audit trail for memory mutations made
  through agent memory tools (L1 edits, L3/L4 update/forget).
- **ConsolidationRunModel** — run-level audit trail for sleep-time memory
  consolidation (trigger, review window, per-operation results); doubles as
  the per-unit consolidation watermark source (latest SUCCESS ``window_end``).
- **ConsolidationPressureFlagModel** — pending pressure markers written by the
  memory pressure alert and consumed by the consolidation trigger bus.
- **MemoryAccessSessionModel** — distinct-session retrieval access markers
  backing the deduplicated access-frequency signal (fusion ranking inputs).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import (  # noqa: I001
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
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
    # Optimistic-concurrency counter, bumped on every successful mutation.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

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
    scope: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    memory_type: Mapped[str] = mapped_column(String(50), nullable=False, default="semantic")
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding: Mapped[list[float]] = mapped_column(JSON, nullable=False, default=list)
    # Optimistic-concurrency counter, bumped on every successful mutation.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Set when a consolidation SUPERSEDE replaces this memory: pointer to the
    # successor row. The row is soft-deleted but retained for audit/lineage.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    # Exogenous decay anchor for retrieval-time time-decay ranking: set at
    # insert, refreshed only by consolidation UPDATE / SUPERSEDE (successor).
    # Deliberately NOT ``updated_at`` — that column is bumped by access-count
    # increments, which would turn the anchor into "last retrieved".
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    # True once ``embedding`` holds a real model embedding. Legacy rows carry
    # deterministic mock vectors and must be excluded from cosine scoring.
    embedding_real: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    # Last retrieval hit (access-frequency heat clock for the offline value
    # score). Never used as the decay anchor.
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    # Offline consolidation value score and its named components (observability
    # only — never drives deletion, never enters online ranking).
    value_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    value_components: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True, default=None
    )

    __table_args__ = (
        Index("idx_memories_workspace", "workspace_id", "deleted"),
        Index("idx_memories_scope", "scope", postgresql_using="gin"),
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
    # Optimistic-concurrency counter, bumped on every successful mutation.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Consolidation SUPERSEDE pointer — see MemoryModel.superseded_by.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    # Confirmation anchor / real-vector marker / value-score fields — see
    # MemoryModel for the field semantics.
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    embedding_real: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    value_score: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    value_components: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True, default=None
    )
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


class RecallMessageModel(BaseModel):
    """ORM model for conversation recall storage (transcript-level index).

    One row per indexed user/assistant message. Metadata and scope live here;
    the vector embedding lives in the Qdrant ``hecate_recall`` collection with
    a payload mirror of the scope columns (memory-isolation pattern). The
    recall layer outlives event retention — pruning the ``events`` table never
    touches these rows; deleting a conversation cascades to them.

    Key fields:

    - **workspace_id** — tenant scope (first-class isolation).
    - **agent_id** — owning agent; recall defaults to the calling agent's scope.
    - **conversation_id / session_id** — provenance pointers returned by
      ``conversation_search`` so the agent can page into the source dialogue.
    - **role / content** — the indexed message itself (user or assistant only).
    - **content_hash / seq** — sha256 of the content and its position in the
      session stream; together with ``session_id`` they form the idempotency
      key that makes re-indexing converge.
    - **event_version** — watermark of the last event log version covered for
      this message's session (catch-up scan bookkeeping).
    """

    __tablename__ = "recall_messages"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "content_hash",
            "seq",
            name="uq_recall_messages_session_hash_seq",
        ),
        Index("idx_recall_messages_workspace", "workspace_id", "deleted"),
        Index("idx_recall_messages_agent", "workspace_id", "agent_id"),
        Index("idx_recall_messages_session", "session_id", "seq"),
        Index("idx_recall_messages_conversation", "conversation_id"),
    )


class MemoryEditLogModel(BaseModel):
    """Append-only audit trail for agent-driven memory mutations.

    One row per memory tool mutation (L1 block edit, L3/L4 update or forget).
    Written by the tool execution path; no tool can modify or delete audit
    rows. Content is stored as truncated summaries (≤200 chars each), never
    full bodies, to bound growth and PII exposure.
    """

    __tablename__ = "memory_edit_log"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    tool_name: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    revision_before: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    revision_after: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    before_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    after_summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    __table_args__ = (
        Index("idx_memory_edit_log_workspace", "workspace_id", "created_at"),
        Index("idx_memory_edit_log_target", "target_type", "target_id"),
        Index("idx_memory_edit_log_agent", "workspace_id", "agent_id"),
    )


class ConsolidationRunModel(BaseModel):
    """ORM model for sleep-time memory consolidation run audit.

    One row per consolidation attempt on a single consolidation unit
    ``(workspace_id, agent_id, user_id)`` — user_id NULL means the
    agent-level unit. Append-only: rows are never rewritten by later runs.

    Doubles as the per-unit watermark source: the latest ``SUCCESS`` run's
    ``window_end`` is the review-window lower bound for the next run.

    Key fields:

    - **workspace_id / agent_id / user_id** — the consolidated unit.
    - **trigger** — what scheduled this run: ``cron`` / ``idle`` /
      ``pressure`` / ``manual``.
    - **window_start / window_end** — review window bounds (transcript
      created_at range) covered by this run.
    - **candidate_count / adopted_count / rejected_count / failed_count** —
      pipeline tallies (rejected = security/dedupe/allowlist refusals).
    - **llm_calls** — LLM invocations spent (budget observability).
    - **status** — ``success`` (all planned operations applied, watermark
      advances) / ``partial`` (some operations applied, watermark does not
      advance — unit is retried) / ``failed`` (pipeline error before or
      during application).
    - **degraded** — True when similarity scoring ran in exact-match
      fallback mode (embedding unavailable).
    - **operations** — JSON list of per-operation results
      (op / target / outcome / detail).
    - **error** — pipeline error detail when status is ``failed``.
    """

    __tablename__ = "consolidation_runs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    adopted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    __table_args__ = (
        Index("idx_consolidation_runs_workspace", "workspace_id", "deleted"),
        Index("idx_consolidation_runs_unit", "workspace_id", "agent_id", "status", "created_at"),
    )


class ConsolidationPressureFlagModel(BaseModel):
    """Pending memory-pressure marker for a consolidation unit.

    Written (best-effort) by the memory pressure alert when a session's
    context usage first crosses the pressure threshold; consumed (deleted)
    by the consolidation trigger bus, which schedules flagged units first.

    ``user_id`` uses the zero-UUID sentinel for the agent-level unit so the
    unique constraint keeps at most one flag per unit (SQL NULLs would be
    treated as distinct).
    """

    __tablename__ = "consolidation_pressure_flags"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "agent_id",
            "user_id",
            name="uq_consolidation_pressure_flags_unit",
        ),
        Index("idx_consolidation_pressure_flags_unit", "workspace_id", "agent_id"),
    )


class MemoryAccessSessionModel(BaseModel):
    """Distinct-session access marker for memory retrieval hits.

    One row per ``(target_type, memory_id, session_id)`` pair, written
    best-effort when the ``memory_search`` tool returns a hit. The count of
    these rows is the deduplicated access-frequency signal consumed by the
    consolidation value score — repeated hits inside one session never
    inflate it, which keeps the frequency signal from reinforcing itself.
    """

    __tablename__ = "memory_access_sessions"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    memory_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "target_type",
            "memory_id",
            "session_id",
            name="uq_memory_access_sessions_hit",
        ),
        Index("idx_memory_access_sessions_memory", "target_type", "memory_id"),
        Index("idx_memory_access_sessions_workspace", "workspace_id", "deleted"),
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
    revision: int
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
    revision: int
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
    revision: int
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


# --- Recall & Audit Schemas ---


class RecallMessageReadSchema(PydanticBase):
    """Schema for reading a recall index row (observability surfaces)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    conversation_id: uuid.UUID | None
    session_id: uuid.UUID
    user_id: uuid.UUID | None
    role: str
    content: str
    content_hash: str
    seq: int
    event_version: int
    created_at: datetime


class MemoryEditLogReadSchema(PydanticBase):
    """Schema for reading a memory edit audit record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    session_id: uuid.UUID | None
    trace_id: str | None
    tool_name: str
    target_type: str
    target_id: uuid.UUID
    revision_before: int | None
    revision_after: int | None
    before_summary: str | None
    after_summary: str | None


# --- Consolidation Schemas ---


class ConsolidationOperationRecord(PydanticBase):
    """One entry of a consolidation run's per-operation result list."""

    model_config = ConfigDict(extra="forbid")

    op: str = Field(..., pattern="^(ADD|UPDATE|SUPERSEDE|NOOP|UPDATE_BLOCK)$")
    target_type: str | None = Field(None, pattern="^(user_memory|knowledge_memory|memory_block)$")
    target_id: uuid.UUID | None = None
    content_summary: str | None = None
    outcome: str = Field(..., pattern="^(applied|rejected|skipped|failed)$")
    detail: str | None = None


class ConsolidationRunReadSchema(PydanticBase):
    """Schema for reading a consolidation run audit record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    user_id: uuid.UUID | None
    trigger: str
    window_start: datetime
    window_end: datetime
    candidate_count: int
    adopted_count: int
    rejected_count: int
    failed_count: int
    llm_calls: int
    status: str
    degraded: bool
    operations: list[dict[str, Any]]
    error: str | None
    created_at: datetime
    updated_at: datetime
