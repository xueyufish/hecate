"""OpenAI-compatible models endpoint.

Implements ``GET /v1/models`` following the OpenAI Models API format.
Returns models discovered automatically from configured API keys via LiteLLM.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from hecate.core.deps import get_current_user_id

logger = logging.getLogger(__name__)

router = APIRouter()


class ModelObject(BaseModel):
    """A single model object."""

    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: 1700000000)
    owned_by: str = "hecate"


class ModelListResponse(BaseModel):
    """Response body for models list endpoint."""

    object: str = "list"
    data: list[ModelObject]


def _discover_models() -> list[str]:
    """Discover available models from configured API keys via LiteLLM."""
    try:
        from litellm import get_valid_models

        return get_valid_models()
    except Exception as e:
        logger.warning(f"Failed to discover models via LiteLLM: {e}")
        return []


@router.get("/models")
async def list_models(
    user_id: Annotated[uuid.UUID, Depends(get_current_user_id)],
) -> dict:
    """List available models.

    Args:
        user_id: The authenticated user ID.

    Returns:
        dict: Model list in OpenAI format.
    """
    models = _discover_models()
    return ModelListResponse(
        data=[ModelObject(id=m) for m in models],
    ).model_dump()
