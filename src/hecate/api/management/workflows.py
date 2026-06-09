"""Workflow management API endpoints.

Provides CRUD operations for workflows and version management:
- ``POST /api/workflows`` — Create a new workflow
- ``GET /api/workflows`` — List workflows (paginated)
- ``GET /api/workflows/{id}`` — Get workflow by ID
- ``PUT /api/workflows/{id}`` — Update workflow
- ``DELETE /api/workflows/{id}`` — Soft delete workflow
- ``GET /api/workflows/{id}/versions`` — List versions
- ``GET /api/workflows/{id}/versions/{version}`` — Get specific version
- ``POST /api/workflows/{id}/rollback/{version}`` — Rollback to version
- ``POST /api/workflows/{id}/validate`` — Validate graph DSL (dry-run)
- ``POST /api/workflows/{id}/test-run`` — Execute a test run
- ``GET /api/workflows/{id}/runs`` — List test run history
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel as PydanticBase
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.engine.graph_dsl import GraphValidationError
from hecate.models.workflow import (
    WorkflowCreateSchema,
    WorkflowRunReadSchema,
    WorkflowUpdateSchema,
)
from hecate.services.workflow.test_runner import WorkflowTestRunner
from hecate.services.workflow_service import WorkflowService

router = APIRouter()


@router.post("/workflows", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    data: WorkflowCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new workflow.

    Args:
        data: The workflow creation data.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The created workflow data with version details.

    Raises:
        HTTPException: 422 if graph DSL validation fails.
    """
    service = WorkflowService(db)
    try:
        result = await service.create_workflow(data, workspace_id=ctx.workspace_id or uuid.UUID(int=0))
        return result.model_dump()
    except GraphValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": {"field": e.field}}},
        ) from e


@router.get("/workflows")
async def list_workflows(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List workflows with pagination.

    Args:
        db: The async database session.
        ctx: The authenticated context.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with workflow list and total count.
    """
    service = WorkflowService(db)
    result = await service.list_workflows(
        workspace_id=ctx.workspace_id or uuid.UUID(int=0),
        page=page,
        page_size=page_size,
    )
    return {
        "items": [item.model_dump() for item in result["items"]],
        "total": result["total"],
    }


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a workflow by ID.

    Args:
        workflow_id: The UUID of the workflow to retrieve.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The workflow data with current version details.

    Raises:
        HTTPException: 404 if workflow not found.
    """
    service = WorkflowService(db)
    try:
        result = await service.get_workflow(workflow_id)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.put("/workflows/{workflow_id}")
async def update_workflow(
    workflow_id: uuid.UUID,
    data: WorkflowUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update an existing workflow.

    Args:
        workflow_id: The UUID of the workflow to update.
        data: The update data.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The updated workflow data.

    Raises:
        HTTPException: 404 if workflow not found, 422 if DSL validation fails.
    """
    service = WorkflowService(db)
    try:
        result = await service.update_workflow(workflow_id, data)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e
    except GraphValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": {"field": e.field}}},
        ) from e


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft delete a workflow.

    Args:
        workflow_id: The UUID of the workflow to delete.
        db: The async database session.
        ctx: The authenticated context.

    Raises:
        HTTPException: 404 if workflow not found or already deleted.
    """
    service = WorkflowService(db)
    try:
        await service.delete_workflow(workflow_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.get("/workflows/{workflow_id}/versions")
async def list_workflow_versions(
    workflow_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[dict]:
    """List all versions of a workflow.

    Args:
        workflow_id: The UUID of the workflow.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        list: List of version dicts ordered by version number.
    """
    service = WorkflowService(db)
    versions = await service.list_versions(workflow_id)
    return [v.model_dump() for v in versions]


@router.get("/workflows/{workflow_id}/versions/{version}")
async def get_workflow_version(
    workflow_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a specific version of a workflow.

    Args:
        workflow_id: The UUID of the workflow.
        version: The version number.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The version data.

    Raises:
        HTTPException: 404 if version not found.
    """
    service = WorkflowService(db)
    try:
        result = await service.get_version(workflow_id, version)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


class ValidateRequest(PydanticBase):
    graph_dsl: dict


@router.post("/workflows/{workflow_id}/validate")
async def validate_workflow(
    workflow_id: uuid.UUID,
    data: ValidateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Validate a graph DSL without persisting (dry-run compile).

    Args:
        workflow_id: The UUID of the workflow.
        data: Request body containing graph_dsl to validate.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: ``{"valid": bool, "errors": [...]}``
    """
    service = WorkflowService(db)
    return await service.validate_dsl(data.graph_dsl)


@router.post("/workflows/{workflow_id}/rollback/{version}")
async def rollback_workflow(
    workflow_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Rollback a workflow to a specific version.

    Args:
        workflow_id: The UUID of the workflow.
        version: The version number to rollback to.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: The updated workflow with new version.

    Raises:
        HTTPException: 404 if workflow or version not found.
    """
    service = WorkflowService(db)
    try:
        result = await service.rollback_to_version(workflow_id, version)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


class TestRunRequest(PydanticBase):
    """Request body for triggering a workflow test run."""

    input_data: dict = {}
    mock: bool = True


@router.post("/workflows/{workflow_id}/test-run")
async def test_run_workflow(
    workflow_id: uuid.UUID,
    data: TestRunRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Execute a workflow test run and return per-node results.

    Args:
        workflow_id: The UUID of the workflow to test.
        data: Request body with input_data and mock flag.
        db: The async database session.
        ctx: The authenticated context.

    Returns:
        dict: ``{"run_id": str, "status": str, "nodes": [...], "total_duration_ms": int}``

    Raises:
        HTTPException: 404 if workflow has no compiled version.
    """
    runner = WorkflowTestRunner(db)
    try:
        result = await runner.run_test(
            workflow_id=workflow_id,
            input_data=data.input_data,
            mock=data.mock,
        )
        return {
            "run_id": str(result.run_id),
            "status": result.status,
            "nodes": [
                {
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "status": n.status,
                    "output": n.output,
                    "error_message": n.error_message,
                    "duration_ms": n.duration_ms,
                }
                for n in result.nodes
            ],
            "total_duration_ms": result.total_duration_ms,
            "error": result.error,
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.get("/workflows/{workflow_id}/runs")
async def list_workflow_runs(
    workflow_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List test run history for a workflow with pagination.

    Args:
        workflow_id: The UUID of the workflow.
        db: The async database session.
        ctx: The authenticated context.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with run history list.
    """
    runner = WorkflowTestRunner(db)
    result = await runner.list_runs(workflow_id, page=page, page_size=page_size)
    return {
        "items": [WorkflowRunReadSchema.model_validate(item).model_dump() for item in result["items"]],
        "total": result["total"],
    }
