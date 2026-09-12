"""Human annotation queue API endpoints (7.4/7.4a).

- ``POST /api/evaluation/annotation-queues`` — create a queue
- ``GET /api/evaluation/annotation-queues`` — list queues (with item counts)
- ``GET/PUT/DELETE /api/evaluation/annotation-queues/{queue_id}`` — manage one
- ``GET/POST /api/evaluation/annotation-queues/{queue_id}/items`` — list / enqueue
- ``POST .../items/from-task`` — enqueue from online-task score samples
- ``GET .../items/{item_id}`` — item detail (trace projection + suggestions)
- ``POST .../items/{item_id}/claim|skip|submit`` — review workflow
- ``DELETE .../items/{item_id}`` — remove a pending/skipped item
- ``POST /api/evaluation/annotation-queues/{queue_id}/push-dataset`` — backflow
- ``GET /api/evaluation/calibration`` — machine-vs-human agreement stats

Human scores land in ``evaluation_task_scores`` (``source="human"``) via the
annotation service; this router only maps errors and shapes responses.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.evaluation import (
    AnnotationQueueCreateSchema,
    AnnotationQueueItemReadSchema,
    AnnotationQueueReadSchema,
    AnnotationQueueUpdateSchema,
)
from hecate.ops.evaluation.annotation import (
    AnnotationItemStateError,
    AnnotationQueueItemNotFoundError,
    AnnotationQueueNotFoundError,
    AnnotationService,
    AnnotationValidationError,
    CalibrationService,
    CalibrationValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class AnnotationEntry(BaseModel):
    """One annotation for one queue metric."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str = Field(..., min_length=1, max_length=100)
    value: float | None = None
    value_label: str | None = Field(None, max_length=255)
    overrides_score_id: uuid.UUID | None = None
    reason_code: str | None = Field(None, max_length=50)
    justification: str | None = Field(None, max_length=5000)


class SubmitAnnotationsRequest(BaseModel):
    """Request payload for submitting an item's annotations."""

    model_config = ConfigDict(extra="forbid")

    annotations: list[AnnotationEntry] = Field(..., min_length=1)


class AddItemsRequest(BaseModel):
    """Request payload for enqueueing traces (single or bulk, cap 100)."""

    model_config = ConfigDict(extra="forbid")

    target_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=100)


class FromTaskRequest(BaseModel):
    """Request payload for enqueueing from online-task score samples."""

    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID
    metric_name: str | None = Field(None, max_length=100)
    min_score: float | None = Field(None, ge=-1.0, le=1.0)
    max_score: float | None = Field(None, ge=-1.0, le=1.0)
    limit: int | None = Field(None, ge=1, le=500)


class PushDatasetRequest(BaseModel):
    """Request payload for materializing completed annotations into a dataset."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: uuid.UUID | None = None
    dataset_name: str | None = Field(None, min_length=1, max_length=255)
    item_ids: list[uuid.UUID] | None = None


# ---------------------------------------------------------------------------
# Queue CRUD
# ---------------------------------------------------------------------------


@router.post("/annotation-queues", status_code=status.HTTP_201_CREATED)
async def create_queue(
    data: AnnotationQueueCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create an annotation queue in the caller's workspace."""
    svc = AnnotationService(db)
    try:
        queue = await svc.create_queue(data, workspace_id=ctx.workspace_id)
    except AnnotationValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _queue_to_dict(queue)


@router.get("/annotation-queues")
async def list_queues(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List the workspace's annotation queues, newest first, with item counts."""
    svc = AnnotationService(db)
    queues, total = await svc.list_queues(workspace_id=ctx.workspace_id, page=page, page_size=page_size)
    counts = await svc.queue_item_counts([q.id for q in queues])
    return {
        "items": [{**_queue_to_dict(q), "counts": counts.get(str(q.id), {})} for q in queues],
        "total": total,
    }


@router.get("/annotation-queues/{queue_id}")
async def get_queue(
    queue_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a single annotation queue."""
    svc = AnnotationService(db)
    queue = await svc.get_queue(queue_id, workspace_id=ctx.workspace_id)
    if queue is None:
        raise _not_found("Annotation queue not found")
    return _queue_to_dict(queue)


@router.put("/annotation-queues/{queue_id}")
async def update_queue(
    queue_id: uuid.UUID,
    data: AnnotationQueueUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update an annotation queue (partial; metric defs re-validated)."""
    svc = AnnotationService(db)
    try:
        queue = await svc.update_queue(queue_id, workspace_id=ctx.workspace_id, data=data)
    except AnnotationQueueNotFoundError as exc:
        raise _not_found("Annotation queue not found") from exc
    except AnnotationValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _queue_to_dict(queue)


@router.delete("/annotation-queues/{queue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_queue(
    queue_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete an annotation queue; items and scores are retained."""
    svc = AnnotationService(db)
    try:
        await svc.delete_queue(queue_id, workspace_id=ctx.workspace_id)
    except AnnotationQueueNotFoundError as exc:
        raise _not_found("Annotation queue not found") from exc


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


@router.post("/annotation-queues/{queue_id}/items")
async def add_items(
    queue_id: uuid.UUID,
    data: AddItemsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Enqueue completed root traces (duplicates in the queue are no-ops)."""
    svc = AnnotationService(db)
    try:
        result = await svc.add_items(
            queue_id,
            workspace_id=ctx.workspace_id,
            target_ids=data.target_ids,
            added_by=ctx.user_id,
        )
    except AnnotationQueueNotFoundError as exc:
        raise _not_found("Annotation queue not found") from exc
    except AnnotationValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return {
        "created": result["created"],
        "already_present": [str(t) for t in result["already_present"]],
        "rejected": result["rejected"],
    }


@router.post("/annotation-queues/{queue_id}/items/from-task")
async def add_items_from_task(
    queue_id: uuid.UUID,
    data: FromTaskRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Enqueue target traces of matching online-task score samples."""
    svc = AnnotationService(db)
    try:
        result = await svc.add_items_from_task(
            queue_id,
            workspace_id=ctx.workspace_id,
            task_id=data.task_id,
            metric_name=data.metric_name,
            min_score=data.min_score,
            max_score=data.max_score,
            limit=data.limit or 50,
            added_by=ctx.user_id,
        )
    except AnnotationQueueNotFoundError as exc:
        raise _not_found("Annotation queue or task not found") from exc
    except AnnotationValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return {key: value for key, value in result.items()}


# ---------------------------------------------------------------------------
# Item workflow
# ---------------------------------------------------------------------------


@router.get("/annotation-queues/{queue_id}/items")
async def list_items(
    queue_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    item_status: Annotated[str | None, Query(alias="status", pattern="^(pending|claimed|completed|skipped)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List a queue's items in enqueue order."""
    svc = AnnotationService(db)
    try:
        items, total = await svc.list_items(
            queue_id,
            workspace_id=ctx.workspace_id,
            status=item_status,
            page=page,
            page_size=page_size,
        )
    except AnnotationQueueNotFoundError as exc:
        raise _not_found("Annotation queue not found") from exc
    return {
        "items": [_item_to_dict(i) for i in items],
        "total": total,
    }


@router.get("/annotation-queues/{queue_id}/items/{item_id}")
async def get_item_detail(
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Item state plus review context: trace projection and machine-score suggestions."""
    svc = AnnotationService(db)
    try:
        detail = await svc.get_item_detail(item_id, workspace_id=ctx.workspace_id)
    except AnnotationQueueItemNotFoundError as exc:
        raise _not_found("Annotation queue item not found") from exc

    item = detail["item"]
    if item.queue_id != queue_id:
        raise _not_found("Annotation queue item not found")
    trace = detail["trace"]
    return {
        "item": _item_to_dict(item),
        "queue": _queue_to_dict(detail["queue"]),
        "trace": _trace_summary(trace),
        "projection": detail["projection"],
        "suggestions": detail["suggestions"],
    }


@router.post("/annotation-queues/{queue_id}/items/{item_id}/claim")
async def claim_item(
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Claim a pending item for review (assignees only when the queue has them)."""
    svc = AnnotationService(db)
    try:
        item = await svc.claim_item(item_id, workspace_id=ctx.workspace_id, user_id=ctx.user_id)
    except AnnotationQueueItemNotFoundError as exc:
        raise _not_found("Annotation queue item not found") from exc
    except AnnotationItemStateError as exc:
        raise _conflict(str(exc)) from exc
    except AnnotationValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _item_to_dict(item)


@router.post("/annotation-queues/{queue_id}/items/{item_id}/skip")
async def skip_item(
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Skip a pending or claimed item without scoring it."""
    svc = AnnotationService(db)
    try:
        item = await svc.skip_item(item_id, workspace_id=ctx.workspace_id, user_id=ctx.user_id)
    except AnnotationQueueItemNotFoundError as exc:
        raise _not_found("Annotation queue item not found") from exc
    except AnnotationItemStateError as exc:
        raise _conflict(str(exc)) from exc
    return _item_to_dict(item)


@router.post("/annotation-queues/{queue_id}/items/{item_id}/submit")
async def submit_item(
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    data: SubmitAnnotationsRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Submit annotations; human score rows land in the shared score ledger."""
    svc = AnnotationService(db)
    try:
        item = await svc.submit_item(
            item_id,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user_id,
            annotations=[a.model_dump() for a in data.annotations],
        )
    except AnnotationQueueItemNotFoundError as exc:
        raise _not_found("Annotation queue item not found") from exc
    except AnnotationItemStateError as exc:
        raise _conflict(str(exc)) from exc
    except AnnotationValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return _item_to_dict(item)


@router.delete("/annotation-queues/{queue_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Remove a pending or skipped item from the queue."""
    svc = AnnotationService(db)
    try:
        await svc.delete_item(item_id, workspace_id=ctx.workspace_id)
    except AnnotationQueueItemNotFoundError as exc:
        raise _not_found("Annotation queue item not found") from exc
    except AnnotationItemStateError as exc:
        raise _conflict(str(exc)) from exc


# ---------------------------------------------------------------------------
# Backflow + calibration
# ---------------------------------------------------------------------------


@router.post("/annotation-queues/{queue_id}/push-dataset")
async def push_to_dataset(
    queue_id: uuid.UUID,
    data: PushDatasetRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Materialize completed annotated items into an evaluation dataset."""
    svc = AnnotationService(db)
    try:
        result = await svc.push_to_dataset(
            queue_id,
            workspace_id=ctx.workspace_id,
            dataset_id=data.dataset_id,
            dataset_name=data.dataset_name,
            item_ids=data.item_ids,
        )
    except AnnotationQueueNotFoundError as exc:
        raise _not_found("Annotation queue not found") from exc
    except AnnotationValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return {
        "created": result["created"],
        "skipped": result["skipped"],
        "dataset_id": str(result["dataset_id"]),
    }


@router.get("/calibration")
async def calibration(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    start_date: Annotated[str | None, Query()] = None,
    end_date: Annotated[str | None, Query()] = None,
    agent_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    metric_name: str | None = None,
) -> dict:
    """Machine-vs-human agreement statistics per metric."""
    from datetime import datetime

    svc = CalibrationService(db)

    def _parse(name: str, raw: str | None) -> datetime | None:
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _validation_error(f"invalid {name}: {raw!r}") from exc

    try:
        report = await svc.calibration(
            workspace_id=ctx.workspace_id,
            start_date=_parse("start_date", start_date),
            end_date=_parse("end_date", end_date),
            agent_id=agent_id,
            task_id=task_id,
            metric_name=metric_name,
        )
    except CalibrationValidationError as exc:
        raise _validation_error(str(exc)) from exc
    return report.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Response shaping + error mapping
# ---------------------------------------------------------------------------


def _queue_to_dict(queue) -> dict:
    return AnnotationQueueReadSchema.model_validate(queue).model_dump(mode="json")


def _item_to_dict(item) -> dict:
    return AnnotationQueueItemReadSchema.model_validate(item).model_dump(mode="json")


def _trace_summary(trace) -> dict | None:
    if trace is None:
        return None
    return {
        "id": str(trace.id),
        "trace_id": str(trace.trace_id),
        "session_id": str(trace.session_id) if trace.session_id else None,
        "agent_id": str(trace.agent_id) if trace.agent_id else None,
        "status": trace.status,
        "start_time": trace.start_time.isoformat(),
        "end_time": trace.end_time.isoformat() if trace.end_time else None,
    }


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


def _conflict(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": {"code": "CONFLICT", "message": message, "details": None}},
    )
