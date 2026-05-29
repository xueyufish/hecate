"""Model Provider management API endpoints.

Provides CRUD operations for model providers:
- ``POST /api/model-providers`` — Create a new provider
- ``GET /api/model-providers`` — List all providers
- ``PUT /api/model-providers/{id}`` — Update a provider
- ``DELETE /api/model-providers/{id}`` — Delete a provider
- ``POST /api/model-providers/{id}/test`` — Test connectivity
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.model_provider import (
    CustomModelCreateSchema,
    ModelProviderCreateSchema,
    ModelProviderModel,
    ModelProviderReadSchema,
    ModelProviderUpdateSchema,
    ModelRegistryModel,
    ModelRegistryReadSchema,
    ModelTestRequestSchema,
    ModelUpdateSchema,
)
from hecate.services.model_provider.crypto import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_CONFIG = {"timeout": 30, "max_retries": 3, "rate_limit_rpm": 60}


def _validate_config(config: dict) -> None:
    """Validate provider config values."""
    timeout = config.get("timeout", 30)
    if not (1 <= timeout <= 300):
        raise HTTPException(status_code=400, detail="timeout must be between 1 and 300")

    max_retries = config.get("max_retries", 3)
    if not (0 <= max_retries <= 10):
        raise HTTPException(status_code=400, detail="max_retries must be between 0 and 10")

    rate_limit = config.get("rate_limit_rpm", 60)
    if not (1 <= rate_limit <= 10000):
        raise HTTPException(status_code=400, detail="rate_limit_rpm must be between 1 and 10000")


async def _discover_models(provider_name: str, api_key: str, base_url: str | None = None) -> list[dict]:
    """Discover available models via LiteLLM."""
    try:
        from litellm import get_valid_models

        kwargs: dict = {}
        if base_url:
            kwargs["api_base"] = base_url

        model_ids = get_valid_models(
            custom_llm_provider=provider_name,
            api_key=api_key,
            **kwargs,
        )

        return [{"model_id": m, "display_name": m.split("/")[-1]} for m in model_ids]
    except ImportError:
        logger.warning("litellm not installed — skipping model discovery")
        return []
    except Exception as e:
        logger.warning(f"Model discovery failed for {provider_name}: {e}")
        return []


@router.post("/model-providers", status_code=status.HTTP_201_CREATED)
async def create_provider(
    data: ModelProviderCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Create a new model provider and discover available models."""
    existing = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.name == data.name,
            ModelProviderModel.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Provider '{data.name}' already exists")

    merged_config = {**DEFAULT_CONFIG, **data.config}
    _validate_config(merged_config)

    encrypted_key = encrypt_api_key(data.api_key)

    provider = ModelProviderModel(
        name=data.name,
        display_name=data.display_name,
        api_key_encrypted=encrypted_key,
        base_url=data.base_url,
        config=merged_config,
        is_enabled=data.is_enabled,
        status="pending",
    )
    db.add(provider)
    await db.flush()
    await db.refresh(provider)

    models = await _discover_models(data.name, data.api_key, data.base_url)
    for m in models:
        model = ModelRegistryModel(
            provider_id=provider.id,
            model_id=m["model_id"],
            display_name=m["display_name"],
            model_type="chat",
            capabilities={},
            is_custom=False,
            is_enabled=True,
        )
        db.add(model)

    await db.flush()

    return {
        **ModelProviderReadSchema.model_validate(provider).model_dump(),
        "model_count": len(models),
    }


@router.get("/model-providers")
async def list_providers(
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """List all model providers with status and model count."""
    stmt = (
        select(ModelProviderModel, func.count(ModelRegistryModel.id).label("model_count"))
        .outerjoin(
            ModelRegistryModel,
            (ModelRegistryModel.provider_id == ModelProviderModel.id) & (ModelRegistryModel.deleted_at.is_(None)),
        )
        .where(ModelProviderModel.deleted_at.is_(None))
        .group_by(ModelProviderModel.id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    items = []
    for provider, model_count in rows:
        items.append(
            {
                **ModelProviderReadSchema.model_validate(provider).model_dump(),
                "model_count": model_count or 0,
            }
        )

    return {"items": items, "total": len(items)}


@router.put("/model-providers/{provider_id}")
async def update_provider(
    provider_id: uuid.UUID,
    data: ModelProviderUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Update a model provider."""
    result = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.id == provider_id,
            ModelProviderModel.deleted_at.is_(None),
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    update_data = data.model_dump(exclude_unset=True)

    if "api_key" in update_data:
        update_data["api_key_encrypted"] = encrypt_api_key(update_data.pop("api_key"))

    if "config" in update_data:
        merged = {**provider.config, **update_data["config"]}
        _validate_config(merged)
        update_data["config"] = merged

    for field, value in update_data.items():
        setattr(provider, field, value)

    await db.flush()
    await db.refresh(provider)

    return ModelProviderReadSchema.model_validate(provider).model_dump()


@router.delete("/model-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> None:
    """Soft delete a provider and cascade soft-delete its models."""
    result = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.id == provider_id,
            ModelProviderModel.deleted_at.is_(None),
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    now = datetime.now(UTC)
    provider.deleted_at = now

    models_result = await db.execute(
        select(ModelRegistryModel).where(
            ModelRegistryModel.provider_id == provider_id,
            ModelRegistryModel.deleted_at.is_(None),
        )
    )
    for model in models_result.scalars().all():
        model.deleted_at = now

    await db.flush()


@router.post("/model-providers/{provider_id}/test")
async def test_provider(
    provider_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Test provider connectivity with a lightweight LLM call.

    Uses the provider's own decrypted API key and picks the first
    enabled model from the registry (instead of a hardcoded model).
    """
    result = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.id == provider_id,
            ModelProviderModel.deleted_at.is_(None),
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    decrypted_key = decrypt_api_key(provider.api_key_encrypted)

    first_model_result = await db.execute(
        select(ModelRegistryModel)
        .where(
            ModelRegistryModel.provider_id == provider.id,
            ModelRegistryModel.deleted_at.is_(None),
            ModelRegistryModel.is_enabled.is_(True),
        )
        .limit(1)
    )
    first_model = first_model_result.scalar_one_or_none()
    test_model_id = first_model.model_id if first_model else f"{provider.name}/default"

    try:
        start = time.monotonic()

        try:
            from litellm import acompletion as litellm_completion
        except ImportError as err:
            raise HTTPException(
                status_code=503,
                detail="litellm is required for provider testing",
            ) from err

        litellm_kwargs: dict = {
            "model": test_model_id,
            "messages": [{"role": "user", "content": "Hi"}],
            "api_key": decrypted_key,
            "max_tokens": 5,
        }
        if provider.base_url:
            litellm_kwargs["api_base"] = provider.base_url

        await litellm_completion(**litellm_kwargs)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        provider.status = "active"
        await db.flush()

        return {"status": "active", "response_time_ms": elapsed_ms}
    except HTTPException:
        raise
    except Exception as e:
        provider.status = "error"
        await db.flush()

        return {"status": "error", "error_message": str(e)}


@router.get("/models")
async def list_models(
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """List all registered models grouped by provider."""
    providers_result = await db.execute(select(ModelProviderModel).where(ModelProviderModel.deleted_at.is_(None)))
    providers = {p.id: p for p in providers_result.scalars().all()}

    models_result = await db.execute(select(ModelRegistryModel).where(ModelRegistryModel.deleted_at.is_(None)))
    models = models_result.scalars().all()

    grouped: dict[str, dict] = {}
    for m in models:
        provider = providers.get(m.provider_id)
        if provider is None:
            continue
        if provider.name not in grouped:
            grouped[provider.name] = {
                "provider_id": str(provider.id),
                "provider_name": provider.name,
                "provider_display_name": provider.display_name,
                "models": [],
            }
        grouped[provider.name]["models"].append(ModelRegistryReadSchema.model_validate(m).model_dump())

    return {"items": list(grouped.values()), "total": sum(len(g["models"]) for g in grouped.values())}


@router.put("/models/{model_id}")
async def update_model(
    model_id: uuid.UUID,
    data: ModelUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Update a registered model (enable/disable, display name)."""
    result = await db.execute(
        select(ModelRegistryModel).where(
            ModelRegistryModel.id == model_id,
            ModelRegistryModel.deleted_at.is_(None),
        )
    )
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")

    update_data = data.model_dump(exclude_unset=True)
    if "is_enabled" in update_data:
        model.is_enabled = update_data["is_enabled"]
    if "display_name" in update_data:
        model.display_name = update_data["display_name"]

    await db.flush()
    await db.refresh(model)

    return ModelRegistryReadSchema.model_validate(model).model_dump()


@router.post("/models", status_code=status.HTTP_201_CREATED)
async def add_custom_model(
    data: CustomModelCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Manually add a custom model to a provider."""
    provider_result = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.id == data.provider_id,
            ModelProviderModel.deleted_at.is_(None),
        )
    )
    if provider_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    model = ModelRegistryModel(
        provider_id=data.provider_id,
        model_id=data.model_id,
        display_name=data.display_name,
        model_type="chat",
        capabilities={},
        is_custom=True,
        is_enabled=True,
    )
    db.add(model)
    await db.flush()
    await db.refresh(model)

    return ModelRegistryReadSchema.model_validate(model).model_dump()


@router.post("/models/test")
async def test_model(
    data: ModelTestRequestSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Test a model with a custom prompt using its provider's credentials."""
    provider_result = await db.execute(
        select(ModelProviderModel)
        .join(ModelRegistryModel, ModelRegistryModel.provider_id == ModelProviderModel.id)
        .where(
            ModelRegistryModel.model_id == data.model_id,
            ModelRegistryModel.deleted_at.is_(None),
            ModelProviderModel.deleted_at.is_(None),
        )
    )
    provider = provider_result.scalar_one_or_none()

    try:
        try:
            from litellm import acompletion as litellm_completion
        except ImportError as err:
            raise HTTPException(
                status_code=503,
                detail="litellm is required for model testing",
            ) from err

        litellm_kwargs: dict = {
            "model": data.model_id,
            "messages": [{"role": "user", "content": data.prompt}],
            "temperature": data.temperature,
            "max_tokens": data.max_tokens,
        }

        if provider is not None:
            decrypted_key = decrypt_api_key(provider.api_key_encrypted)
            litellm_kwargs["api_key"] = decrypted_key
            if provider.base_url:
                litellm_kwargs["api_base"] = provider.base_url

        response = await litellm_completion(**litellm_kwargs)
        choice = response.choices[0]

        return {
            "content": choice.message.content,
            "model": response.model,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                "total_tokens": response.usage.total_tokens if response.usage else 0,
            },
            "finish_reason": choice.finish_reason,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
