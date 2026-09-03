"""Pydantic schemas for the suggestion service.

Defines the structured result returned by LLM-based suggestion generation,
including the generated questions, the model used, and token usage metrics.
"""

from __future__ import annotations

from pydantic import BaseModel as PydanticBase
from pydantic import Field


class SuggestionResult(PydanticBase):
    """Structured result from suggestion generation.

    Attributes:
        questions: List of suggested follow-up questions for the user.
        model: The LLM model identifier used for generation.
        usage: Token usage metrics (prompt_tokens, completion_tokens, total_tokens).
    """

    questions: list[str] = Field(default_factory=list)
    model: str = ""
    usage: dict = Field(default_factory=dict)
