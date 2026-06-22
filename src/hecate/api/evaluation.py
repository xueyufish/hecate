"""Evaluation management API endpoints.

Provides CRUD operations for evaluation datasets, items, and runs:

- ``POST /api/evaluation/datasets`` — Create a new evaluation dataset
- ``GET /api/evaluation/datasets`` — List evaluation datasets (paginated)
- ``GET /api/evaluation/datasets/{dataset_id}`` — Get a single dataset
- ``PUT /api/evaluation/datasets/{dataset_id}`` — Update a dataset
- ``DELETE /api/evaluation/datasets/{dataset_id}`` — Delete a dataset
- ``POST /api/evaluation/datasets/{dataset_id}/items`` — Add items to dataset
- ``GET /api/evaluation/datasets/{dataset_id}/items`` — List items (paginated)
- ``DELETE /api/evaluation/datasets/{dataset_id}/items/{item_id}`` — Delete item
- ``POST /api/evaluation/runs`` — Create and execute evaluation run
- ``GET /api/evaluation/runs`` — List evaluation runs
- ``GET /api/evaluation/runs/{run_id}`` — Get run with summary stats
- ``GET /api/evaluation/runs/{run_id}/scores`` — Get scores for a run

Built-in evaluators: correctness, relevancy, completeness,
tool_call_accuracy, task_completion, context_precision, context_recall,
faithfulness, answer_relevancy (RAG evaluators require ragas).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.evaluation import (
    EvaluationDatasetCreateSchema,
    EvaluationDatasetModel,
    EvaluationDatasetReadSchema,
    EvaluationDatasetUpdateSchema,
    EvaluationItemCreateSchema,
    EvaluationItemReadSchema,
    EvaluationRunCreateSchema,
    EvaluationRunModel,
    EvaluationRunReadSchema,
    EvaluationScoreModel,
    EvaluationScoreReadSchema,
)

# Import evaluators package to trigger registration
from hecate.services.evaluation import evaluators as _evaluators_pkg  # noqa: F401
from hecate.services.evaluation.dataset_service import EvaluationDatasetService
from hecate.services.evaluation.engine import EvaluationEngine
from hecate.services.evaluation.evaluator import Evaluator
from hecate.services.evaluation.registry import (
    get_evaluator as _get_evaluator_cls,
)
from hecate.services.evaluation.registry import (
    list_evaluator_names as _list_evaluator_names,
)
from hecate.services.evaluation.registry import (
    list_evaluators as _list_evaluators,
)
from hecate.services.evaluation.types import AnswerSource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


async def _get_dataset_or_404(
    dataset_id: uuid.UUID,
    db: AsyncSession,
) -> EvaluationDatasetModel:
    """Look up an evaluation dataset or raise 404."""
    result = await db.execute(
        select(EvaluationDatasetModel).where(
            EvaluationDatasetModel.id == dataset_id,
            ~EvaluationDatasetModel.deleted,
        )
    )
    ds = result.scalar_one_or_none()
    if ds is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Dataset not found", "details": None}},
        )
    return ds


# ---------------------------------------------------------------------------
# Dataset endpoints
# ---------------------------------------------------------------------------


@router.post("/datasets", status_code=status.HTTP_201_CREATED)
async def create_dataset(
    data: EvaluationDatasetCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new evaluation dataset."""
    svc = EvaluationDatasetService(db)
    ds = await svc.create_dataset(
        name=data.name,
        description=data.description,
        metadata=data.metadata,
        workspace_id=ctx.workspace_id,
        version=data.version,
        default_threshold=data.default_threshold,
    )
    return EvaluationDatasetReadSchema.model_validate(ds).model_dump(by_alias=True)


@router.get("/datasets")
async def list_datasets(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List evaluation datasets with pagination."""
    svc = EvaluationDatasetService(db)
    items, total = await svc.list_datasets(page=page, page_size=page_size)
    return {
        "items": [EvaluationDatasetReadSchema.model_validate(ds).model_dump(by_alias=True) for ds in items],
        "total": total,
    }


@router.get("/datasets/{dataset_id}")
async def get_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a single evaluation dataset."""
    ds = await _get_dataset_or_404(dataset_id, db)
    return EvaluationDatasetReadSchema.model_validate(ds).model_dump(by_alias=True)


@router.put("/datasets/{dataset_id}")
async def update_dataset(
    dataset_id: uuid.UUID,
    data: EvaluationDatasetUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update an evaluation dataset."""
    await _get_dataset_or_404(dataset_id, db)
    svc = EvaluationDatasetService(db)
    ds = await svc.update_dataset(
        dataset_id,
        name=data.name,
        description=data.description,
        metadata=data.metadata,
    )
    return EvaluationDatasetReadSchema.model_validate(ds).model_dump(by_alias=True)


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete an evaluation dataset (soft delete)."""
    await _get_dataset_or_404(dataset_id, db)
    svc = EvaluationDatasetService(db)
    await svc.delete_dataset(dataset_id)


# ---------------------------------------------------------------------------
# Dataset item endpoints
# ---------------------------------------------------------------------------


@router.post("/datasets/{dataset_id}/items", status_code=status.HTTP_201_CREATED)
async def add_items(
    dataset_id: uuid.UUID,
    items: list[EvaluationItemCreateSchema],
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Add items to an evaluation dataset."""
    await _get_dataset_or_404(dataset_id, db)
    svc = EvaluationDatasetService(db)
    raw_items = [item.model_dump() for item in items]
    count = await svc.add_items(dataset_id, raw_items)
    return {"added": count}


@router.get("/datasets/{dataset_id}/items")
async def list_items(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List items in an evaluation dataset with pagination."""
    await _get_dataset_or_404(dataset_id, db)
    svc = EvaluationDatasetService(db)
    items, total = await svc.list_items(dataset_id, page=page, page_size=page_size)
    return {
        "items": [EvaluationItemReadSchema.model_validate(item).model_dump(by_alias=True) for item in items],
        "total": total,
    }


@router.delete(
    "/datasets/{dataset_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_item(
    dataset_id: uuid.UUID,
    item_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete an item from an evaluation dataset."""
    await _get_dataset_or_404(dataset_id, db)
    svc = EvaluationDatasetService(db)
    await svc.delete_item(item_id)


# ---------------------------------------------------------------------------
# Evaluation run endpoints
# ---------------------------------------------------------------------------


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(
    data: EvaluationRunCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create and execute an evaluation run."""
    # Validate dataset exists
    await _get_dataset_or_404(data.dataset_id, db)

    # Resolve evaluators from names
    evaluators: list[Evaluator] = []
    for name in data.evaluators:
        cls = _get_evaluator_cls(name)
        if cls is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": {
                        "code": "INVALID_EVALUATOR",
                        "message": f"Unknown evaluator: {name!r}",
                        "details": {"available": _list_evaluator_names()},
                    }
                },
            )
        evaluators.append(cls())

    engine = EvaluationEngine(db)
    source = AnswerSource(data.answer_source)
    result = await engine.run(evaluators, data.dataset_id, answer_source=source)

    # Convert result to API response
    return {
        "run_id": str(result.run_id),
        "dataset_id": str(result.dataset_id) if result.dataset_id else None,
        "total_items": result.total_items,
        "metric_averages": result.metric_averages,
        "total_duration_ms": result.total_duration_ms,
        "item_scores": {
            item_id: [
                {"metric_name": s.metric_name, "value": s.value, "reasoning": s.reasoning, "source": s.source}
                for s in scores
            ]
            for item_id, scores in result.item_scores.items()
        },
    }


@router.get("/runs")
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    dataset_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List evaluation runs with optional dataset filter."""
    base_query = select(EvaluationRunModel).where(~EvaluationRunModel.deleted)
    if dataset_id:
        base_query = base_query.where(EvaluationRunModel.dataset_id == dataset_id)

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = base_query.order_by(EvaluationRunModel.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    runs = result.scalars().all()

    return {
        "items": [EvaluationRunReadSchema.model_validate(r).model_dump() for r in runs],
        "total": total,
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get an evaluation run with summary statistics."""
    result = await db.execute(
        select(EvaluationRunModel).where(
            EvaluationRunModel.id == run_id,
            ~EvaluationRunModel.deleted,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Run not found", "details": None}},
        )

    # Get score summary
    scores_stmt = select(EvaluationScoreModel).where(EvaluationScoreModel.run_id == run_id)
    scores_result = await db.execute(scores_stmt)
    scores = scores_result.scalars().all()

    # Compute metric averages
    metric_values: dict[str, list[float]] = {}
    for s in scores:
        if s.value >= 0:
            metric_values.setdefault(s.metric_name, []).append(s.value)

    metric_averages = {k: sum(v) / len(v) for k, v in metric_values.items()}

    run_data = EvaluationRunReadSchema.model_validate(run).model_dump()
    run_data["metric_averages"] = metric_averages
    run_data["total_scores"] = len(scores)
    return run_data


@router.get("/runs/{run_id}/scores")
async def get_run_scores(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """Get all scores for a specific evaluation run."""
    base_query = select(EvaluationScoreModel).where(EvaluationScoreModel.run_id == run_id)

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    offset = (page - 1) * page_size
    stmt = base_query.order_by(EvaluationScoreModel.created_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    scores = result.scalars().all()

    return {
        "items": [EvaluationScoreReadSchema.model_validate(s).model_dump() for s in scores],
        "total": total,
    }


# ---------------------------------------------------------------------------
# Evaluator listing API
# ---------------------------------------------------------------------------


@router.get("/evaluators")
async def list_evaluators(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    category: str | None = None,
) -> dict:
    """List all registered evaluators with metadata."""
    evaluators = _list_evaluators(category)
    return {
        "evaluators": [
            {
                "name": name,
                "category": getattr(cls, "category", "generic"),
                "description": cls().description,
            }
            for name, cls in sorted(evaluators.items())
        ],
        "total": len(evaluators),
    }


# ---------------------------------------------------------------------------
# Run comparison API
# ---------------------------------------------------------------------------


@router.post("/runs/compare")
async def compare_runs(
    data: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Compare two evaluation runs and detect regressions."""
    from hecate.services.regression_service import RegressionService

    baseline_id = uuid.UUID(data["baseline_run_id"])
    candidate_id = uuid.UUID(data["candidate_run_id"])
    threshold = data.get("threshold", 0.05)

    svc = RegressionService(db)
    return await svc.compare_runs(baseline_id, candidate_id, threshold=float(threshold))


# ---------------------------------------------------------------------------
# Regression trigger API
# ---------------------------------------------------------------------------


@router.post("/regression/run")
async def regression_run(
    data: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Execute an evaluation run and compare against baseline."""
    from hecate.services.regression_service import RegressionService

    dataset_id = uuid.UUID(data["dataset_id"])
    evaluators_list: list[str] = data["evaluators"]
    tags: list[str] | None = data.get("tags")
    threshold: float = float(data.get("threshold", 0.05))
    baseline_run_id = uuid.UUID(data["baseline_run_id"]) if data.get("baseline_run_id") else None

    svc = RegressionService(db)
    return await svc.run_regression(
        dataset_id=dataset_id,
        evaluator_names=evaluators_list,
        tags=tags,
        threshold=threshold,
        baseline_run_id=baseline_run_id,
    )


# ---------------------------------------------------------------------------
# Dataset lock/unlock/baseline API
# ---------------------------------------------------------------------------


@router.put("/datasets/{dataset_id}/lock")
async def lock_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Lock a dataset to prevent item modifications."""
    svc = EvaluationDatasetService(db)
    ds = await svc.lock_dataset(dataset_id)
    return EvaluationDatasetReadSchema.model_validate(ds).model_dump(by_alias=True)


@router.put("/datasets/{dataset_id}/unlock")
async def unlock_dataset(
    dataset_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Unlock a dataset to allow item modifications."""
    svc = EvaluationDatasetService(db)
    ds = await svc.unlock_dataset(dataset_id)
    return EvaluationDatasetReadSchema.model_validate(ds).model_dump(by_alias=True)


@router.put("/datasets/{dataset_id}/baseline")
async def set_baseline(
    dataset_id: uuid.UUID,
    data: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Set the baseline run for regression comparison."""
    svc = EvaluationDatasetService(db)
    run_id = uuid.UUID(data["baseline_run_id"])
    ds = await svc.set_baseline_run(dataset_id, run_id)
    return EvaluationDatasetReadSchema.model_validate(ds).model_dump(by_alias=True)
