"""Conversation turn score ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for per-turn quality scores
and user feedback. Each row represents one assistant turn's evaluation
from LLM-as-Judge and/or user feedback.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class UserRating(StrEnum):
    """User feedback rating values."""

    POSITIVE = "positive"
    NEGATIVE = "negative"


class ConversationTurnScoreModel(BaseModel):
    """ORM model for per-turn quality scores and user feedback.

    Each row represents one assistant turn's evaluation. Stores both
    automated LLM-as-Judge scores and user feedback in a single record,
    following Langfuse's unified Score model pattern.

    Key fields:

    - **conversation_id** — the conversation this score belongs to.
    - **message_id** — the assistant message being scored.
    - **turn_index** — 0-based index of the turn within the conversation.
    - **helpfulness** — LLM score (0.0–1.0): does the response address
      the user's need?
    - **coherence** — LLM score (0.0–1.0): is the response logically
      consistent and well-structured?
    - **instruction_adherence** — LLM score (0.0–1.0): does the response
      follow system prompt constraints?
    - **overall_score** — weighted average of the three dimensions.
    - **reasoning** — LLM explanation for the scores.
    - **user_rating** — user feedback: "positive" or "negative".
    - **user_comment** — optional user comment.
    - **user_id** — who submitted the feedback.
    - **feedback_at** — when feedback was submitted.
    - **scored_at** — when the LLM-as-Judge evaluation was performed.
    """

    __tablename__ = "conversation_turn_scores"

    conversation_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    turn_index: Mapped[int] = mapped_column(nullable=False)

    # LLM-as-Judge scores
    helpfulness: Mapped[float | None] = mapped_column(Float, nullable=True)
    coherence: Mapped[float | None] = mapped_column(Float, nullable=True)
    instruction_adherence: Mapped[float | None] = mapped_column(Float, nullable=True)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    topic: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # User feedback
    user_rating: Mapped[str | None] = mapped_column(String(20), nullable=True)
    user_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    feedback_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Scoring timestamp
    scored_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        Index("idx_turn_scores_conversation", "conversation_id", "turn_index"),
        Index("idx_turn_scores_message", "message_id"),
        Index("idx_turn_scores_scored", "scored_at"),
    )


class ConversationTurnScoreCreateSchema(PydanticBase):
    """Schema for creating a turn score record."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: uuid.UUID
    message_id: uuid.UUID
    turn_index: int
    helpfulness: float | None = None
    coherence: float | None = None
    instruction_adherence: float | None = None
    overall_score: float | None = None
    reasoning: str | None = None
    topic: str | None = None


class ConversationTurnScoreReadSchema(PydanticBase):
    """Schema for reading a turn score record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    turn_index: int
    helpfulness: float | None
    coherence: float | None
    instruction_adherence: float | None
    overall_score: float | None
    reasoning: str | None
    topic: str | None
    user_rating: str | None
    user_comment: str | None
    user_id: uuid.UUID | None
    feedback_at: datetime | None
    scored_at: datetime | None
    created_at: datetime


class FeedbackSubmitSchema(PydanticBase):
    """Schema for submitting user feedback on a turn."""

    model_config = ConfigDict(extra="forbid")

    rating: str = Field(..., pattern="^(positive|negative)$")
    comment: str | None = None
