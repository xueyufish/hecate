"""REST API for DLP policy management.

Endpoints follow the existing ``src/hecate/api/management/`` convention
and are mounted by ``src/hecate/main.py`` with the ``/api`` prefix.
Resource paths use ``/dlp/...``.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel as _PydanticBase
from pydantic import Field as _Field
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.dlp import (
    DLPCustomRegexCreateSchema,
    DLPCustomRegexModel,
    DLPCustomRegexReadSchema,
    DLPDictionaryCreateSchema,
    DLPDictionaryModel,
    DLPDictionaryReadSchema,
    DLPPolicyCreateSchema,
    DLPPolicyModel,
    DLPPolicyReadSchema,
    DLPPolicyUpdateSchema,
)
from hecate.services.security.dlp.service import (
    DLPService,
    known_entity_types,
    supported_directions,
)


class DLPScanTestSchema(_PydanticBase):
    """Body for ``POST /dlp/scan/test``."""

    text: str = _Field(min_length=0)
    direction: str = _Field(default="llm_output", min_length=1, max_length=50)
    org_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None


class DLPResultDict(_PydanticBase):
    """Response for ``POST /dlp/scan/test`` (passthrough shape)."""

    findings: list[dict]
    action: str
    text: str | None
    audit_data: list[dict]


policies_router = APIRouter(tags=["dlp"])
custom_regex_router = APIRouter(tags=["dlp"])
dictionaries_router = APIRouter(tags=["dlp"])
scan_router = APIRouter(tags=["dlp"])
meta_router = APIRouter(tags=["dlp"])


def _to_policy_read(policy: DLPPolicyModel) -> DLPPolicyReadSchema:
    return DLPPolicyReadSchema.model_validate(policy)


def _to_regex_read(rec: DLPCustomRegexModel) -> DLPCustomRegexReadSchema:
    return DLPCustomRegexReadSchema.model_validate(rec)


def _to_dict_read(rec: DLPDictionaryModel) -> DLPDictionaryReadSchema:
    return DLPDictionaryReadSchema.model_validate(rec)


# --- Policies ---


@policies_router.post("/dlp/policies", response_model=DLPPolicyReadSchema, status_code=201)
async def create_policy(
    body: DLPPolicyCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> DLPPolicyReadSchema:
    service = DLPService(db)
    policy = await service.create_policy(
        org_id=body.org_id or ctx.org_id,
        workspace_id=body.workspace_id,
        agent_id=body.agent_id,
        entity_type=body.entity_type,
        direction=body.direction,
        action=body.action,
        mask_format=body.mask_format,
        is_locked=body.is_locked,
        enabled=body.enabled,
    )
    await db.commit()
    return _to_policy_read(policy)


@policies_router.get("/dlp/policies", response_model=list[DLPPolicyReadSchema])
async def list_policies(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    org_id: Annotated[uuid.UUID | None, Query()] = None,
    workspace_id: Annotated[uuid.UUID | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    direction: Annotated[str | None, Query()] = None,
    enabled_only: Annotated[bool, Query()] = True,
) -> list[DLPPolicyReadSchema]:
    service = DLPService(db)
    policies = await service.list_policies(
        org_id=org_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        direction=direction,
        enabled_only=enabled_only,
    )
    return [_to_policy_read(p) for p in policies]


@policies_router.get("/dlp/policies/{policy_id}", response_model=DLPPolicyReadSchema)
async def get_policy(
    policy_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> DLPPolicyReadSchema:
    service = DLPService(db)
    policy = await service.get_policy(str(policy_id))
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return _to_policy_read(policy)


@policies_router.put("/dlp/policies/{policy_id}", response_model=DLPPolicyReadSchema)
async def update_policy(
    policy_id: uuid.UUID,
    body: DLPPolicyUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> DLPPolicyReadSchema:
    service = DLPService(db)
    policy = await service.update_policy(
        str(policy_id),
        action=body.action,
        mask_format=body.mask_format,
        is_locked=body.is_locked,
        enabled=body.enabled,
    )
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.commit()
    return _to_policy_read(policy)


@policies_router.delete("/dlp/policies/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    service = DLPService(db)
    deleted = await service.delete_policy(str(policy_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.commit()


# --- Custom regex ---


@custom_regex_router.post("/dlp/custom-regex", response_model=DLPCustomRegexReadSchema, status_code=201)
async def create_custom_regex(
    body: DLPCustomRegexCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> DLPCustomRegexReadSchema:
    service = DLPService(db)
    rec = await service.create_custom_regex(
        org_id=body.org_id or ctx.org_id,
        workspace_id=body.workspace_id,
        name=body.name,
        pattern=body.pattern,
        entity_type=body.entity_type,
        enabled=body.enabled,
    )
    await db.commit()
    return _to_regex_read(rec)


@custom_regex_router.get("/dlp/custom-regex", response_model=list[DLPCustomRegexReadSchema])
async def list_custom_regex(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    org_id: Annotated[uuid.UUID | None, Query()] = None,
    workspace_id: Annotated[uuid.UUID | None, Query()] = None,
    enabled_only: Annotated[bool, Query()] = True,
) -> list[DLPCustomRegexReadSchema]:
    service = DLPService(db)
    recs = await service.list_custom_regex(org_id=org_id, workspace_id=workspace_id, enabled_only=enabled_only)
    return [_to_regex_read(r) for r in recs]


@custom_regex_router.delete("/dlp/custom-regex/{regex_id}", status_code=204)
async def delete_custom_regex(
    regex_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    service = DLPService(db)
    if not await service.delete_custom_regex(str(regex_id)):
        raise HTTPException(status_code=404, detail="Custom regex not found")
    await db.commit()


# --- Dictionaries ---


@dictionaries_router.post("/dlp/dictionaries", response_model=DLPDictionaryReadSchema, status_code=201)
async def create_dictionary(
    body: DLPDictionaryCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> DLPDictionaryReadSchema:
    service = DLPService(db)
    rec = await service.create_dictionary(
        org_id=body.org_id or ctx.org_id,
        workspace_id=body.workspace_id,
        name=body.name,
        entity_type=body.entity_type,
        terms=body.terms,
        case_sensitive=body.case_sensitive,
        enabled=body.enabled,
    )
    await db.commit()
    return _to_dict_read(rec)


@dictionaries_router.get("/dlp/dictionaries", response_model=list[DLPDictionaryReadSchema])
async def list_dictionaries(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    org_id: Annotated[uuid.UUID | None, Query()] = None,
    workspace_id: Annotated[uuid.UUID | None, Query()] = None,
    enabled_only: Annotated[bool, Query()] = True,
) -> list[DLPDictionaryReadSchema]:
    service = DLPService(db)
    recs = await service.list_dictionaries(org_id=org_id, workspace_id=workspace_id, enabled_only=enabled_only)
    return [_to_dict_read(r) for r in recs]


@dictionaries_router.delete("/dlp/dictionaries/{dictionary_id}", status_code=204)
async def delete_dictionary(
    dictionary_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    service = DLPService(db)
    if not await service.delete_dictionary(str(dictionary_id)):
        raise HTTPException(status_code=404, detail="Dictionary not found")
    await db.commit()


# --- Dry-run scan ---


@scan_router.post("/dlp/scan/test", response_model=DLPResultDict)
async def dry_run_scan(
    body: DLPScanTestSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    service = DLPService(db)
    result = await service.dry_run_scan(
        body.text,
        body.direction,
        org_id=body.org_id,
        workspace_id=body.workspace_id,
        agent_id=body.agent_id,
    )
    return {
        "findings": [
            {
                "entity_type": f.entity_type,
                "value": f.value,
                "start": f.start,
                "end": f.end,
                "score": f.score,
                "recognizer": f.recognizer,
            }
            for f in result.findings
        ],
        "action": result.action.value,
        "text": result.text,
        "audit_data": result.audit_data,
    }


# --- Metadata ---


@meta_router.get("/dlp/entities", response_model=list[str])
async def list_entities() -> list[str]:
    return known_entity_types()


@meta_router.get("/dlp/directions", response_model=list[str])
async def list_directions() -> list[str]:
    return supported_directions()
