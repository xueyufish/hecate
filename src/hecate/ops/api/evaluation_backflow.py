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

Rule creation and listing sit behind the evaluation access boundary: an
active workspace is required for every identity — system scope included
(403 ``NO_WORKSPACE``) — and non-system callers need a viewer floor to
list or an editor floor to create. System scope lists only its explicit
workspace and has no global listing.

By-id reads, updates, deletes, and runs resolve the rule first (existence,
soft-delete state, workspace ownership before the role check); a
workspace-less member is denied before any query, and system scope keeps
its role exemption, operates by explicit id, and runs in the rule's real
workspace.

Rule references are validated like resources: create validates the
requested task and dataset, update the merged-patch effective pair, and
run the rule's currently bound pair — each must be live in the target
workspace before the role check and any business validation, for system
scope too. Invisible rules or references on create, update, and run share
the combined identifier-free ``Backflow rule or task or dataset not
found`` 404, while detail and delete keep the rule-only message so failed
references can still be cleaned up. The service rechecks the rule and its
references before any write or run.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import (
    WorkspaceRole,
    WorkspaceRoleRequiredError,
    ensure_workspace_role,
    get_auth_context,
)
from hecate.models.evaluation import (
    EvaluationBackflowRuleCreateSchema,
    EvaluationBackflowRuleModel,
    EvaluationBackflowRuleReadSchema,
    EvaluationBackflowRuleUpdateSchema,
    EvaluationDatasetModel,
    EvaluationTaskModel,
)
from hecate.ops.api.evaluation import _forbidden, _no_workspace
from hecate.ops.evaluation.backflow import (
    BackflowReferenceNotFoundError,
    BackflowRuleNotFoundError,
    BackflowService,
    BackflowValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])

_COMBINED_NOT_FOUND = "Backflow rule or task or dataset not found"
_RULE_NOT_FOUND = "Backflow rule not found"


# ---------------------------------------------------------------------------
# Rule CRUD
# ---------------------------------------------------------------------------


@router.post("/backflow-rules", status_code=status.HTTP_201_CREATED)
async def create_rule(
    data: EvaluationBackflowRuleCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a backflow rule in the caller's workspace.

    Scoped behind the evaluation write boundary: an active workspace is
    required for every identity — system scope included — and the
    requested task and dataset must be live in that workspace before the
    editor floor and the service's business validation.
    """
    if ctx.workspace_id is None:
        raise _no_workspace()
    await _require_live_reference(db, EvaluationTaskModel, data.task_id, ctx.workspace_id)
    await _require_live_reference(db, EvaluationDatasetModel, data.dataset_id, ctx.workspace_id)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.EDITOR)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    svc = BackflowService(db)
    try:
        rule = await svc.create_rule(data, workspace_id=ctx.workspace_id)
    except BackflowReferenceNotFoundError as exc:
        raise _not_found(_COMBINED_NOT_FOUND) from exc
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
    """List the workspace's backflow rules, newest first.

    Scoped behind the evaluation read boundary: an active workspace and a
    viewer role floor are required before any query — system scope lists
    only its explicit workspace and has no global listing.
    """
    if ctx.workspace_id is None:
        raise _no_workspace()
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    svc = BackflowService(db)
    rules, total = await svc.list_rules(workspace_id=ctx.workspace_id, page=page, page_size=page_size)
    return {"items": [_rule_to_dict(rule) for rule in rules], "total": total}


@router.get("/backflow-rules/{rule_id}")
async def get_rule(
    rule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a single backflow rule.

    Scoped behind the evaluation read boundary: the rule resolves through
    the rule read boundary (ownership before role), then a viewer floor
    applies.
    """
    rule = await _get_rule_for_read(ctx, rule_id, db)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.VIEWER)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    return _rule_to_dict(rule)


@router.put("/backflow-rules/{rule_id}")
async def update_rule(
    rule_id: uuid.UUID,
    data: EvaluationBackflowRuleUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update a backflow rule (partial; references and filters re-validate).

    Scoped behind the evaluation write boundary: the rule and the
    merged-patch effective task/dataset pair resolve first, then an
    editor floor applies and the update runs in the rule's real
    workspace with the service rechecking rule and references.
    """
    rule = await _get_rule_for_combined_read(ctx, rule_id, db)
    await _require_live_reference(
        db, EvaluationTaskModel, data.task_id if data.task_id is not None else rule.task_id, rule.workspace_id
    )
    await _require_live_reference(
        db,
        EvaluationDatasetModel,
        data.dataset_id if data.dataset_id is not None else rule.dataset_id,
        rule.workspace_id,
    )
    try:
        ensure_workspace_role(ctx, WorkspaceRole.EDITOR)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    svc = BackflowService(db)
    try:
        updated = await svc.update_rule(
            rule_id,
            workspace_id=rule.workspace_id,
            data=data,
            expected_references=(rule.task_id, rule.dataset_id),
        )
    except (BackflowRuleNotFoundError, BackflowReferenceNotFoundError) as exc:
        raise _not_found(_COMBINED_NOT_FOUND) from exc
    except BackflowValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _rule_to_dict(updated)


@router.delete("/backflow-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete a backflow rule; materialized dataset items are retained.

    Scoped behind the evaluation write boundary: the rule resolves through
    the rule read boundary (ownership before role), then an editor floor
    applies. Deletion never depends on the rule's references still being
    alive, so rules pointing at removed objects stay cleanable.
    """
    rule = await _get_rule_for_read(ctx, rule_id, db)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.EDITOR)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    svc = BackflowService(db)
    try:
        await svc.delete_rule(rule_id, workspace_id=rule.workspace_id)
    except BackflowRuleNotFoundError as exc:
        raise _not_found(_RULE_NOT_FOUND) from exc


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@router.post("/backflow-rules/{rule_id}/run")
async def run_rule(
    rule_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Materialize the rule's matching traces into its dataset (idempotent).

    Scoped behind the evaluation write boundary: the rule and its
    currently bound task and dataset resolve first — all must be live in
    the rule's real workspace before the editor floor — and the service
    rechecks the rule and references before selecting any candidate.
    """
    rule = await _get_rule_for_combined_read(ctx, rule_id, db)
    await _require_live_reference(db, EvaluationTaskModel, rule.task_id, rule.workspace_id)
    await _require_live_reference(db, EvaluationDatasetModel, rule.dataset_id, rule.workspace_id)
    try:
        ensure_workspace_role(ctx, WorkspaceRole.EDITOR)
    except WorkspaceRoleRequiredError as e:
        raise _forbidden(e) from e
    svc = BackflowService(db)
    try:
        result = await svc.run_rule(
            rule_id,
            workspace_id=rule.workspace_id,
            expected_references=(rule.task_id, rule.dataset_id),
        )
    except (BackflowRuleNotFoundError, BackflowReferenceNotFoundError) as exc:
        raise _not_found(_COMBINED_NOT_FOUND) from exc
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


async def _get_rule_for_read(
    ctx: AuthContext,
    rule_id: uuid.UUID,
    db: AsyncSession,
) -> EvaluationBackflowRuleModel:
    """Resolve rule visibility before callers enforce the workspace role.

    Missing, soft-deleted, and cross-workspace rules surface as the same
    identifier-free not-found. Keeping role checks in callers lets each
    endpoint decide ownership before returning 403.
    """
    if not ctx.is_system_scope and ctx.workspace_id is None:
        raise _not_found(_RULE_NOT_FOUND)
    rule = (
        await db.execute(
            select(EvaluationBackflowRuleModel).where(
                EvaluationBackflowRuleModel.id == rule_id,
                ~EvaluationBackflowRuleModel.deleted,
            )
        )
    ).scalar_one_or_none()
    if rule is None or (not ctx.is_system_scope and rule.workspace_id != ctx.workspace_id):
        raise _not_found(_RULE_NOT_FOUND)
    return rule


async def _get_rule_for_combined_read(
    ctx: AuthContext,
    rule_id: uuid.UUID,
    db: AsyncSession,
) -> EvaluationBackflowRuleModel:
    """Rule visibility for endpoints whose references share the 404 message.

    Create, update, and run surface an invisible rule and an invisible
    reference with the same combined identifier-free not-found, so the
    rule resolution here reuses that message.
    """
    try:
        return await _get_rule_for_read(ctx, rule_id, db)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
        raise _not_found(_COMBINED_NOT_FOUND) from exc


async def _require_live_reference(
    db: AsyncSession,
    model: type,
    reference_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> None:
    """Raise the combined 404 unless the reference is live in the workspace.

    Projection-only on purpose: the precheck must not plant entities in
    the identity map, so the service re-resolves them fresh.
    """
    found = (
        await db.execute(
            select(model.id).where(
                model.id == reference_id,
                model.workspace_id == workspace_id,
                ~model.deleted,
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise _not_found(_COMBINED_NOT_FOUND)
