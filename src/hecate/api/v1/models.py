"""OpenAI-compatible models endpoint.

Implements ``GET /v1/models`` following the OpenAI Models API format.
Returns a list of available LLM models.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from hecate.core.deps import verify_api_key

router = APIRouter()

AVAILABLE_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-3.5-turbo",
    "claude-3-5-sonnet-20241022",
    "claude-3-opus-20240229",
    "claude-3-haiku-20240307",
]


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


@router.get("/models")
async def list_models(
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """List available models.

    Args:
        api_key: The validated API key.

    Returns:
        dict: Model list in OpenAI format.
    """
    return ModelListResponse(
        data=[ModelObject(id=model) for model in AVAILABLE_MODELS],
    ).model_dump()
