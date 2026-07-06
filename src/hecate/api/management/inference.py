"""Inference endpoint management REST API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.model_hub.inference_manager import InferenceManager
from hecate.models.inference_endpoint import InferenceEndpointCreateSchema, InferenceEndpointReadSchema

router = APIRouter(prefix="/api/inference", tags=["inference"])


@router.get("/endpoints")
async def list_endpoints(
    workspace_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    manager = InferenceManager(db)
    endpoints = await manager.list_endpoints(workspace_id)
    return [InferenceEndpointReadSchema.model_validate(e).model_dump() for e in endpoints]


@router.post("/endpoints")
async def create_endpoint(
    data: InferenceEndpointCreateSchema,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    manager = InferenceManager(db)
    endpoint = await manager.create_endpoint(
        url=data.url,
        model_id=data.model_id,
        backend_type=data.backend_type,
        auth_config=data.auth_config,
        workspace_id=data.workspace_id,
    )
    return InferenceEndpointReadSchema.model_validate(endpoint).model_dump()


@router.delete("/endpoints/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    manager = InferenceManager(db)
    deleted = await manager.delete_endpoint(endpoint_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return {"status": "deleted"}


@router.get("/endpoints/{endpoint_id}/health")
async def check_endpoint_health(
    endpoint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    manager = InferenceManager(db)
    status = await manager.check_health(endpoint_id)
    return {"endpoint_id": str(endpoint_id), "status": status}
