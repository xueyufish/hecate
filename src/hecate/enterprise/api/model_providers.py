"""Model Provider management API endpoints.

Provides CRUD operations for model providers and the model registry:
- ``POST /api/model-providers`` — Create a new provider
- ``GET /api/model-providers`` — List all providers (search, call counts)
- ``PUT /api/model-providers/{id}`` — Update a provider
- ``DELETE /api/model-providers/{id}`` — Delete a provider
- ``POST /api/model-providers/{id}/test`` — Test connectivity
- ``GET /api/models`` — List registered models (search, publish-state filter)
- ``PUT /api/models/{model_id}`` — Update a registered model
- ``DELETE /api/models/{model_id}`` — Delete (refused while referenced)
- ``POST /api/models`` — Add a custom model
- ``POST /api/models/test`` — Test a model (records pass evidence)
- ``POST /api/models/{model_id}/publish`` — Publish (requires test evidence)
- ``POST /api/models/{model_id}/unpublish`` — Unpublish (freely reversible)
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context, require_platform_admin
from hecate.enterprise.auth.crypto import decrypt_api_key, encrypt_api_key
from hecate.models.agent import AgentModel
from hecate.models.audit import AuditAction, AuditLogModel
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
from hecate.models.trace import TraceModel
from hecate.models.workflow import WorkflowModel, WorkflowVersionModel

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_CONFIG = {"timeout": 30, "max_retries": 3, "rate_limit_rpm": 60}

#: Rolling window for the provider call-count card (6.48).
CALL_COUNT_WINDOW_DAYS = 30


def _generate_provider_name(display_name: str) -> str:
    """Generate a URL-safe provider name from display_name."""
    import re
    import unicodedata

    slug = unicodedata.normalize("NFKD", display_name).lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug).strip("-")
    return slug[:100] if slug else str(uuid.uuid4())[:8]


def _resolve_litellm_model_id(model_id: str, base_url: str | None) -> str:
    """Resolve the LiteLLM-compatible model ID from a raw model name.

    If the model_id already contains a '/' prefix (e.g. 'openai/gpt-4'),
    return as-is. If base_url is set, prepend 'openai/' since the endpoint
    is OpenAI-compatible. Otherwise return the raw model_id.
    """
    if "/" in model_id:
        return model_id
    if base_url:
        return f"openai/{model_id}"
    return model_id


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


async def _get_registry_model(db: AsyncSession, model_id: uuid.UUID) -> ModelRegistryModel:
    """Fetch one non-deleted registry row or raise 404."""
    result = await db.execute(
        select(ModelRegistryModel).where(
            ModelRegistryModel.id == model_id,
            ~ModelRegistryModel.deleted,
        )
    )
    model = result.scalar_one_or_none()
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Model not found", "details": None}},
        )
    return model


async def _model_references(db: AsyncSession, model: ModelRegistryModel) -> list[dict]:
    """Collect agents and workflows that reference the model (delete guard).

    Agents match exactly on ``model_config->>'model'``; workflows match on
    the model id appearing as a complete JSON string in any non-deleted
    version's graph DSL (quoted-token match avoids prefix false positives).
    """
    references: list[dict] = []

    agents_result = await db.execute(
        select(AgentModel.id, AgentModel.name).where(
            AgentModel.model_config_db["model"].as_string() == model.model_id,
            ~AgentModel.deleted,
        )
    )
    for agent_id, name in agents_result.all():
        references.append({"type": "agent", "id": str(agent_id), "name": name})

    workflow_result = await db.execute(
        select(WorkflowModel.id, WorkflowModel.name)
        .join(WorkflowVersionModel, WorkflowVersionModel.workflow_id == WorkflowModel.id)
        .where(
            cast(WorkflowVersionModel.graph_dsl, String).contains(f'"{model.model_id}"'),
            ~WorkflowVersionModel.deleted,
            ~WorkflowModel.deleted,
        )
        .distinct()
    )
    for workflow_id, name in workflow_result.all():
        references.append({"type": "workflow", "id": str(workflow_id), "name": name})

    return references


async def _audit_model_action(
    db: AsyncSession,
    ctx: AuthContext,
    action: AuditAction,
    model: ModelRegistryModel,
) -> None:
    """Persist a publish/unpublish audit event (6.47)."""
    db.add(
        AuditLogModel(
            org_id=ctx.org_id or uuid.UUID(int=0),
            workspace_id=ctx.workspace_id,
            user_id=ctx.user_id,
            action=action.value,
            resource_type="model",
            resource_id=model.id,
            success=True,
            metadata_={"model_id": model.model_id},
        )
    )


async def _provider_call_counts(db: AsyncSession) -> tuple[dict[uuid.UUID, dict[str, int]], dict[str, int]]:
    """Aggregate invocation counts per provider from generation traces (6.48).

    One grouped query over ``traces.metadata->>'model'`` carries both the
    30-day rolling window and the all-time total; model names that map to no
    registry row land in the unmatched bucket instead of being dropped.
    """
    model_expr = TraceModel.metadata_["model"].as_string()
    cutoff = datetime.now(UTC) - timedelta(days=CALL_COUNT_WINDOW_DAYS)
    counts_result = await db.execute(
        select(
            model_expr.label("model_name"),
            func.count().label("total"),
            func.count().filter(TraceModel.start_time >= cutoff).label("recent"),
        )
        .where(
            TraceModel.type == "generation",
            ~TraceModel.deleted,
            model_expr.is_not(None),
        )
        .group_by(model_expr)
    )

    registry_result = await db.execute(
        select(ModelRegistryModel.model_id, ModelRegistryModel.provider_id).where(~ModelRegistryModel.deleted)
    )
    model_to_providers: dict[str, set[uuid.UUID]] = defaultdict(set)
    for model_name, provider_id in registry_result.all():
        model_to_providers[model_name].add(provider_id)

    per_provider: dict[uuid.UUID, dict[str, int]] = defaultdict(lambda: {"call_count_30d": 0, "call_count_total": 0})
    unmatched = {"unmatched_call_count_30d": 0, "unmatched_call_count_total": 0}
    for model_name, total, recent in counts_result.all():
        provider_ids = model_to_providers.get(model_name)
        if not provider_ids:
            unmatched["unmatched_call_count_total"] += total
            unmatched["unmatched_call_count_30d"] += recent
            continue
        for provider_id in provider_ids:
            per_provider[provider_id]["call_count_total"] += total
            per_provider[provider_id]["call_count_30d"] += recent
    return per_provider, unmatched


async def _discover_models(provider_name: str, api_key: str, base_url: str | None = None) -> list[dict]:
    """Discover available models via the LLM gateway (single litellm access point)."""
    from hecate_llm.service import llm_service

    try:
        kwargs: dict = {}
        if base_url:
            kwargs["api_base"] = base_url

        model_ids = llm_service.list_models(
            provider=provider_name,
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
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> dict:
    """Create a new model provider and discover available models."""
    provider_name = _generate_provider_name(data.display_name)

    existing = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.name == provider_name,
            ~ModelProviderModel.deleted,
        )
    )
    if existing.scalar_one_or_none():
        suffix = str(uuid.uuid4())[:4]
        provider_name = f"{provider_name}-{suffix}"

    merged_config = {**DEFAULT_CONFIG, **data.config}
    _validate_config(merged_config)

    encrypted_key = encrypt_api_key(data.api_key)

    provider = ModelProviderModel(
        name=provider_name,
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

    models = await _discover_models(provider_name, data.api_key, data.base_url)
    for m in models:
        model = ModelRegistryModel(
            provider_id=provider.id,
            model_id=m["model_id"],
            display_name=m["display_name"],
            model_type="chat",
            capabilities={},
            is_custom=False,
            is_enabled=True,
            is_published=False,
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
    _ctx: Annotated[AuthContext, Depends(get_auth_context)],
    search: str | None = None,
) -> dict:
    """List all model providers with status, model count, and call counts."""
    stmt = (
        select(ModelProviderModel, func.count(ModelRegistryModel.id).label("model_count"))
        .outerjoin(
            ModelRegistryModel,
            (ModelRegistryModel.provider_id == ModelProviderModel.id) & (~ModelRegistryModel.deleted),
        )
        .where(~ModelProviderModel.deleted)
        .group_by(ModelProviderModel.id)
    )
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                ModelProviderModel.name.ilike(like),
                ModelProviderModel.display_name.ilike(like),
            )
        )
    result = await db.execute(stmt)
    rows = result.all()

    per_provider_counts, unmatched = await _provider_call_counts(db)

    items = []
    for provider, model_count in rows:
        counts = per_provider_counts.get(provider.id, {"call_count_30d": 0, "call_count_total": 0})
        items.append(
            {
                **ModelProviderReadSchema.model_validate(provider).model_dump(),
                "model_count": model_count or 0,
                **counts,
            }
        )

    return {"items": items, "total": len(items), **unmatched}


@router.put("/model-providers/{provider_id}")
async def update_provider(
    provider_id: uuid.UUID,
    data: ModelProviderUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> dict:
    """Update a model provider."""
    result = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.id == provider_id,
            ~ModelProviderModel.deleted,
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
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> None:
    """Soft delete a provider and cascade soft-delete its models."""
    result = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.id == provider_id,
            ~ModelProviderModel.deleted,
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    now = datetime.now(UTC)
    provider.deleted = True
    provider.deleted_at = now

    models_result = await db.execute(
        select(ModelRegistryModel).where(
            ModelRegistryModel.provider_id == provider_id,
            ~ModelRegistryModel.deleted,
        )
    )
    for model in models_result.scalars().all():
        model.deleted = True
        model.deleted_at = now

    await db.flush()


@router.post("/model-providers/{provider_id}/test")
async def test_provider(
    provider_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> dict:
    """Test provider connectivity by verifying API key and endpoint reachability.

    Validates that the provider's API key is accepted by calling the
    OpenAI-compatible ``/models`` endpoint. Does not require any registered
    models — this only checks authentication and network connectivity.
    """
    result = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.id == provider_id,
            ~ModelProviderModel.deleted,
        )
    )
    provider = result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    decrypted_key = decrypt_api_key(provider.api_key_encrypted)

    try:
        import httpx

        start = time.monotonic()

        base = (provider.base_url or "").rstrip("/")
        models_url = f"{base}/models" if base else "https://api.openai.com/v1/models"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                models_url,
                headers={"Authorization": f"Bearer {decrypted_key}"},
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code in (200, 401, 403):
            if resp.status_code == 200:
                provider.status = "active"
                await db.flush()
                return {"status": "active", "response_time_ms": elapsed_ms}
            provider.status = "error"
            await db.flush()
            return {
                "status": "error",
                "error_message": "Authentication failed — API key rejected",
                "response_time_ms": elapsed_ms,
            }

        provider.status = "error"
        await db.flush()
        return {
            "status": "error",
            "error_message": f"Unexpected response: HTTP {resp.status_code}",
            "response_time_ms": elapsed_ms,
        }
    except httpx.ConnectError as e:
        provider.status = "error"
        await db.flush()
        return {"status": "error", "error_message": f"Connection failed: {e}"}
    except httpx.TimeoutException:
        provider.status = "error"
        await db.flush()
        return {"status": "error", "error_message": "Connection timed out (15s)"}
    except Exception as e:
        provider.status = "error"
        await db.flush()
        return {"status": "error", "error_message": str(e)}


@router.get("/models")
async def list_models(
    db: Annotated[AsyncSession, Depends(get_db)],
    _ctx: Annotated[AuthContext, Depends(get_auth_context)],
    search: str | None = None,
    publish_state: str | None = None,
) -> dict:
    """List registered models grouped by provider.

    ``search`` matches model_id/display_name; ``publish_state`` filters on
    the publish lifecycle ("published" / "unpublished" — the unpublished
    bucket includes models whose test passed but were never published).
    """
    model_conditions = [~ModelRegistryModel.deleted]
    if search:
        like = f"%{search}%"
        model_conditions.append(
            or_(
                ModelRegistryModel.model_id.ilike(like),
                ModelRegistryModel.display_name.ilike(like),
            )
        )
    if publish_state == "published":
        model_conditions.append(ModelRegistryModel.is_published.is_(True))
    elif publish_state == "unpublished":
        model_conditions.append(ModelRegistryModel.is_published.is_(False))

    providers_result = await db.execute(select(ModelProviderModel).where(~ModelProviderModel.deleted))
    providers = {p.id: p for p in providers_result.scalars().all()}

    models_result = await db.execute(select(ModelRegistryModel).where(*model_conditions))
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
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> dict:
    """Update a registered model (enable/disable, display name)."""
    result = await db.execute(
        select(ModelRegistryModel).where(
            ModelRegistryModel.id == model_id,
            ~ModelRegistryModel.deleted,
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
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> dict:
    """Manually add a custom model to a provider."""
    provider_result = await db.execute(
        select(ModelProviderModel).where(
            ModelProviderModel.id == data.provider_id,
            ~ModelProviderModel.deleted,
        )
    )
    provider = provider_result.scalar_one_or_none()
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    resolved_model_id = _resolve_litellm_model_id(data.model_id, provider.base_url)

    model = ModelRegistryModel(
        provider_id=data.provider_id,
        model_id=resolved_model_id,
        display_name=data.display_name,
        model_type="chat",
        capabilities={},
        is_custom=True,
        is_enabled=True,
        is_published=False,
    )
    db.add(model)
    await db.flush()
    await db.refresh(model)

    return ModelRegistryReadSchema.model_validate(model).model_dump()


@router.post("/models/test")
async def test_model(
    data: ModelTestRequestSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> dict:
    """Test a model with a custom prompt using its provider's credentials."""
    provider_result = await db.execute(
        select(ModelProviderModel)
        .join(ModelRegistryModel, ModelRegistryModel.provider_id == ModelProviderModel.id)
        .where(
            ModelRegistryModel.model_id == data.model_id,
            ~ModelRegistryModel.deleted,
            ~ModelProviderModel.deleted,
        )
    )
    provider = provider_result.scalar_one_or_none()

    from hecate_llm.service import llm_service

    test_kwargs: dict = {
        "model_id": data.model_id,
        "prompt": data.prompt,
        "temperature": data.temperature,
        "max_tokens": data.max_tokens,
    }
    if provider is not None:
        test_kwargs["api_key"] = decrypt_api_key(provider.api_key_encrypted)
        if provider.base_url:
            test_kwargs["api_base"] = provider.base_url

    response = await llm_service.test_connection(**test_kwargs)
    if response.finish_reason == "error":
        error_msg = response.usage.get("error", "unknown") if isinstance(response.usage, dict) else "unknown"
        raise HTTPException(status_code=400, detail=error_msg) from None

    # Record the pass evidence that gates publishing (6.47). Dynamic models
    # resolved through no registry row stay evidence-less.
    if provider is not None:
        rows_result = await db.execute(
            select(ModelRegistryModel).where(
                ModelRegistryModel.model_id == data.model_id,
                ModelRegistryModel.provider_id == provider.id,
                ~ModelRegistryModel.deleted,
            )
        )
        passed_at = datetime.now(UTC)
        for row in rows_result.scalars().all():
            row.last_test_passed_at = passed_at
        await db.flush()

    return {
        "content": response.content,
        "model": response.model,
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "finish_reason": response.finish_reason,
    }


@router.post("/models/{model_id}/publish")
async def publish_model(
    model_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> dict:
    """Publish a registered model to the application reference surface.

    Gated on model-test evidence: a model that never passed the inline test
    cannot be published (409 ``MODEL_TEST_REQUIRED``). Publishing is the
    explicit lifecycle action from the AgentArts-style 调测通过后发布 flow.
    """
    model = await _get_registry_model(db, model_id)
    if model.last_test_passed_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "MODEL_TEST_REQUIRED",
                    "message": f"Model '{model.model_id}' has not passed a test — run the model test before publishing",
                    "details": None,
                }
            },
        )

    model.is_published = True
    await _audit_model_action(db, ctx, AuditAction.SYSTEM_MODEL_PUBLISH, model)
    await db.flush()
    await db.refresh(model)
    return ModelRegistryReadSchema.model_validate(model).model_dump()


@router.post("/models/{model_id}/unpublish")
async def unpublish_model(
    model_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> dict:
    """Remove a model from the application reference surface.

    Freely reversible and unconditional: existing ``llm_config`` bindings
    keep working (reference-only gating), and the model stays manageable
    and testable on the settings surface.
    """
    model = await _get_registry_model(db, model_id)
    model.is_published = False
    await _audit_model_action(db, ctx, AuditAction.SYSTEM_MODEL_UNPUBLISH, model)
    await db.flush()
    await db.refresh(model)
    return ModelRegistryReadSchema.model_validate(model).model_dump()


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    _ctx: Annotated[AuthContext, Depends(require_platform_admin)],
) -> None:
    """Soft-delete a registered model, refused while still referenced.

    The in-use guard returns 409 ``MODEL_IN_USE`` with the referencing
    agents/workflows so the caller can resolve bindings first instead of
    silently breaking them at runtime.
    """
    model = await _get_registry_model(db, model_id)
    references = await _model_references(db, model)
    if references:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "MODEL_IN_USE",
                    "message": f"Model '{model.model_id}' is referenced by {len(references)} agent(s)/workflow(s)",
                    "details": {"references": references},
                }
            },
        )

    model.deleted = True
    model.deleted_at = datetime.now(UTC)
    await db.flush()
