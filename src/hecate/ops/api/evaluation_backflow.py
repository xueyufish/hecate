"""Automated trace-backflow rule API endpoints (7.2d).

- ``POST /api/evaluation/backflow-rules`` — create a rule
- ``GET /api/evaluation/backflow-rules`` — list rules
- ``GET/PUT/DELETE /api/evaluation/backflow-rules/{rule_id}`` — manage one
- ``POST /api/evaluation/backflow-rules/{rule_id}/run`` — materialize now

Rules are the automated half of dataset backflow: they select scored
production traces of an online evaluation task by score-band filters and
materialize them into a dataset on explicit run requests. This router only
maps errors and shapes responses; selection, projection, and cross-path
idempotency live in the backflow service.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.evaluation import (
    EvaluationBackflowRuleCreateSchema,
    EvaluationBackflowRuleReadSchema,
    EvaluationBackflowRuleUpdateSchema,
)
from hecate.ops.evaluation.backflow import (
    BackflowRuleNotFoundError,
    BackflowService,
    BackflowValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


# ---------------------------------------------------------------------------
# Rule CRUD
# ---------------------------------------------------------------------------


@router.post("/backflow-rules", status_code=status.HTTP_201_CREATED)
async def create_rule(
    data: EvaluationBackflowRuleCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a backflow rule in the caller's workspace."""
    svc = BackflowService(db)
    try:
        rule = await svc.create_rule(data, workspace_id=ctx.workspace_id)
    except BackflowValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _rule_to_dict(rule)


@router.get("/backflow-rules")
async def list_rules(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List the workspace's backflow rules, newest first."""
    svc = BackflowService(db)
    rules, total = await svc.list_rules(workspace_id=ctx.workspace_id, page=page, page_size=page_size)
    return {"items": [_rule_to_dict(rule) for rule in rules], "total": total}


@router.get("/backflow-rules/{rule_id}")
async def get_rule(
    rule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a single backflow rule."""
    svc = BackflowService(db)
    rule = await svc.get_rule(rule_id, workspace_id=ctx.workspace_id)
    if rule is None:
        raise _not_found("Backflow rule not found")
    return _rule_to_dict(rule)


@router.put("/backflow-rules/{rule_id}")
async def update_rule(
    rule_id: uuid.UUID,
    data: EvaluationBackflowRuleUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update a backflow rule (partial; references and filters re-validate)."""
    svc = BackflowService(db)
    try:
        rule = await svc.update_rule(rule_id, workspace_id=ctx.workspace_id, data=data)
    except BackflowRuleNotFoundError as exc:
        raise _not_found("Backflow rule not found") from exc
    except BackflowValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _rule_to_dict(rule)


@router.delete("/backflow-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete a backflow rule; materialized dataset items are retained."""
    svc = BackflowService(db)
    try:
        await svc.delete_rule(rule_id, workspace_id=ctx.workspace_id)
    except BackflowRuleNotFoundError as exc:
        raise _not_found("Backflow rule not found") from exc


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@router.post("/backflow-rules/{rule_id}/run")
async def run_rule(
    rule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Materialize the rule's matching traces into its dataset (idempotent)."""
    svc = BackflowService(db)
    try:
        result = await svc.run_rule(rule_id, workspace_id=ctx.workspace_id)
    except BackflowRuleNotFoundError as exc:
        raise _not_found("Backflow rule not found") from exc
    except BackflowValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return {
        "created": result["created"],
        "skipped": result["skipped"],
        "dataset_id": str(result["dataset_id"]),
    }


# ---------------------------------------------------------------------------
# Response shaping + error mapping
# ---------------------------------------------------------------------------


def _rule_to_dict(rule) -> dict:
    return EvaluationBackflowRuleReadSchema.model_validate(rule).model_dump(mode="json")


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": message, "details": None}},
    )


def _validation_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error": {"code": "INVALID_REQUEST", "message": message, "details": None}},
    )
