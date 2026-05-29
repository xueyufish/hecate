"""OpenAI-compatible models endpoint.

Implements ``GET /v1/models`` following the OpenAI Models API format.
Returns models from the database (model_registry) grouped by provider,
with fallback to LiteLLM discovery when no providers are configured.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.core.deps import get_current_user_id
from hecate.models.model_provider import ModelProviderModel, ModelRegistryModel

logger = logging.getLogger(__name__)

router = APIRouter()


class ModelObject(BaseModel):
    """A single model object."""

    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: 1700000000)
    owned_by: str = "hecate"
    provider: str | None = None
    provider_display_name: str | None = None


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
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """List available models.

    Priority: database providers → LiteLLM fallback.

    Args:
        user_id: The authenticated user ID.
        db: The async database session.

    Returns:
        dict: Model list in OpenAI format with provider grouping.
    """
    providers_result = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.deleted_at.is_(None),
            ModelProviderModel.is_enabled.is_(True),
        )
    )
    providers = {p.id: p for p in providers_result.scalars().all()}

    if providers:
        models_result = await db.execute(
            select(ModelRegistryModel).where(
                ModelRegistryModel.deleted_at.is_(None),
                ModelRegistryModel.is_enabled.is_(True),
                ModelRegistryModel.model_type == "chat",
            )
        )
        models = models_result.scalars().all()

        data = []
        for m in models:
            provider = providers.get(m.provider_id)
            if provider is None:
                continue
            data.append(
                ModelObject(
                    id=m.model_id,
                    owned_by=provider.name,
                    provider=provider.name,
                    provider_display_name=provider.display_name,
                )
            )

        return ModelListResponse(data=data).model_dump()

    model_ids = _discover_models()
    return ModelListResponse(
        data=[ModelObject(id=m) for m in model_ids],
    ).model_dump()
