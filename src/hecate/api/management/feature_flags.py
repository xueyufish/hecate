"""Feature flag management REST API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from hecate.core.deps_feature_flags import get_feature_flag_service
from hecate.core.feature_flags import FeatureFlagService

router = APIRouter(prefix="/api/feature-flags", tags=["feature-flags"])


class FeatureFlagCreate(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    description: str | None = None


class FeatureFlagUpdate(BaseModel):
    enabled: bool | None = None
    targeting_rules: dict | None = None
    description: str | None = None
    target_removal_version: str | None = None


class FeatureFlagTransition(BaseModel):
    status: str


class FeatureFlagResponse(BaseModel):
    key: str
    status: str
    enabled: bool
    targeting_rules: dict | None
    description: str | None
    target_removal_version: str | None
    evaluation_count: int
    last_true_count: int


@router.get("")
async def list_flags(
    service: Annotated[FeatureFlagService, Depends(get_feature_flag_service)],
) -> list[FeatureFlagResponse]:
    flags = await service.list()
    return [
        FeatureFlagResponse(
            key=f.key,
            status=f.status,
            enabled=f.enabled,
            targeting_rules=f.targeting_rules,
            description=f.description,
            target_removal_version=f.target_removal_version,
            evaluation_count=f.evaluation_count,
            last_true_count=f.last_true_count,
        )
        for f in flags
    ]


@router.get("/{key}")
async def get_flag(
    key: str,
    service: Annotated[FeatureFlagService, Depends(get_feature_flag_service)],
) -> FeatureFlagResponse:
    flag = await service.get(key)
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"flag {key!r} not found")
    return FeatureFlagResponse(
        key=flag.key,
        status=flag.status,
        enabled=flag.enabled,
        targeting_rules=flag.targeting_rules,
        description=flag.description,
        target_removal_version=flag.target_removal_version,
        evaluation_count=flag.evaluation_count,
        last_true_count=flag.last_true_count,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_flag(
    body: FeatureFlagCreate,
    service: Annotated[FeatureFlagService, Depends(get_feature_flag_service)],
) -> FeatureFlagResponse:
    try:
        flag = await service.create(key=body.key, description=body.description)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return FeatureFlagResponse(
        key=flag.key,
        status=flag.status,
        enabled=flag.enabled,
        targeting_rules=flag.targeting_rules,
        description=flag.description,
        target_removal_version=flag.target_removal_version,
        evaluation_count=flag.evaluation_count,
        last_true_count=flag.last_true_count,
    )


@router.patch("/{key}")
async def update_flag(
    key: str,
    body: FeatureFlagUpdate,
    service: Annotated[FeatureFlagService, Depends(get_feature_flag_service)],
) -> FeatureFlagResponse:
    try:
        flag = await service.update(
            key,
            enabled=body.enabled,
            targeting_rules=body.targeting_rules,
            description=body.description,
            target_removal_version=body.target_removal_version,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return FeatureFlagResponse(
        key=flag.key,
        status=flag.status,
        enabled=flag.enabled,
        targeting_rules=flag.targeting_rules,
        description=flag.description,
        target_removal_version=flag.target_removal_version,
        evaluation_count=flag.evaluation_count,
        last_true_count=flag.last_true_count,
    )


@router.post("/{key}/transition")
async def transition_flag(
    key: str,
    body: FeatureFlagTransition,
    service: Annotated[FeatureFlagService, Depends(get_feature_flag_service)],
) -> FeatureFlagResponse:
    try:
        flag = await service.transition(key, body.status)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return FeatureFlagResponse(
        key=flag.key,
        status=flag.status,
        enabled=flag.enabled,
        targeting_rules=flag.targeting_rules,
        description=flag.description,
        target_removal_version=flag.target_removal_version,
        evaluation_count=flag.evaluation_count,
        last_true_count=flag.last_true_count,
    )


@router.delete("/{key}")
async def delete_flag(
    key: str,
    service: Annotated[FeatureFlagService, Depends(get_feature_flag_service)],
):
    try:
        await service.delete(key)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return {"status": "deleted"}
