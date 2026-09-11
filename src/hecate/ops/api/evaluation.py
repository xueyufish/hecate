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
- ``POST /api/evaluation/runs/compare`` — Compare two runs (7.3 workflow
  evaluation; per-metric + token/latency/cost deltas, dataset drift,
  workflow versions, node drift)

Built-in evaluators (16 total, 9 LLM-as-Judge + 7 deterministic): correctness,
relevancy, completeness, contains, exact_match, is_json, regex_match,
tool_call_accuracy, task_completion, context_precision, context_recall,
faithfulness, answer_relevancy, refusal, harmfulness, pii_leakage. The
RAG four require the optional ``ragas`` dependency.

Evaluator class resolution goes through ``engine.get_evaluator_class``
which reads the module-private class index populated by
``register_evaluators`` at startup. There is no parallel dict in this
module — the PluginRegistry is the sole source of truth, and the class
index is a sidecar for callers that need ``class`` rather than
``instance``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
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
from hecate.ops.evaluation.dataset_service import EvaluationDatasetService
from hecate.ops.evaluation.engine import EvaluationEngine, get_evaluator_class
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.types import AnswerSource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


# Scope → source mapping for the 16 built-in evaluators. The class index
# (``engine._EVALUATOR_CLASS_REGISTRY``) and PluginRegistry manifests
# both report ``name``/``version``/``description``; this mapping is the
# single source of truth for the four-scope taxonomy that the spec
# exposes via ``GET /api/evaluation/evaluators?scope=<scope>``.
_EVALUATOR_SCOPE_META: dict[str, tuple[str, str]] = {
    # result (5 LLM-judge + 4 deterministic = 9 total but only the 7 in this scope tree)
    "correctness": ("result", "llm_judge"),
    "relevancy": ("result", "llm_judge"),
    "completeness": ("result", "llm_judge"),
    "contains": ("result", "deterministic"),
    "exact_match": ("result", "deterministic"),
    "is_json": ("result", "deterministic"),
    "regex_match": ("result", "deterministic"),
    # process
    "tool_call_accuracy": ("process", "llm_judge"),
    "task_completion": ("process", "llm_judge"),
    # rag (ragas optional)
    "context_precision": ("rag", "ragas"),
    "context_recall": ("rag", "ragas"),
    "faithfulness": ("rag", "ragas"),
    "answer_relevancy": ("rag", "ragas"),
    # safety
    "refusal": ("safety", "llm_judge"),
    "harmfulness": ("safety", "llm_judge"),
    "pii_leakage": ("safety", "deterministic"),
}


@router.get("/evaluators")
async def list_evaluators(
    request: Request,
    scope: Annotated[str | None, Query(pattern="^(result|process|rag|safety)$")] = None,
) -> dict:
    """List registered built-in evaluators.

    Grouped by ``scope`` (result / process / rag / safety) when no
    filter is supplied; otherwise only the requested scope is returned.
    Each entry includes ``name``, ``scope``, ``source``, ``version``,
    and ``description``. The total enumerator count is the count of
    names registered via ``register_evaluators`` at startup (typically
    16 minus rag evaluators skipped when ragas is missing).
    """
    plugin_registry = getattr(request.app.state, "plugin_registry", None)
    items: list[dict] = []
    for name, (eval_scope, source) in _EVALUATOR_SCOPE_META.items():
        if scope and eval_scope != scope:
            continue
        description = ""
        version = "1.0.0"
        if plugin_registry is not None:
            manifest = plugin_registry.get_manifest("evaluator", name)
            if manifest is not None:
                description = manifest.description
                version = manifest.version
        items.append(
            {
                "name": name,
                "scope": eval_scope,
                "source": source,
                "version": version,
                "description": description,
            }
        )
    return {"items": items, "total": len(items)}


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
    tags: Annotated[list[str] | None, Query()] = None,
) -> dict:
    """List items in an evaluation dataset with pagination.

    When ``tags`` is supplied (comma-separated or repeated query
    parameter), only items whose ``tags`` JSON array contains ANY of the
    specified values are returned (OR semantics).
    """
    await _get_dataset_or_404(dataset_id, db)
    svc = EvaluationDatasetService(db)
    items, total = await svc.list_items(dataset_id, page=page, page_size=page_size, tags=tags)
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


def _list_evaluator_names() -> list[str]:
    """Enumerate registered evaluator names for error messages.

    Imports the class index lazily to avoid a circular import at module
    load time. Returns an empty list when registration has not yet run.
    """
    from hecate.ops.evaluation.engine import (
        _EVALUATOR_CLASS_REGISTRY,  # noqa: PLC0415  (intentional lazy)
    )

    return list(_EVALUATOR_CLASS_REGISTRY.keys())


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(
    data: EvaluationRunCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create and execute an evaluation run."""
    # Validate dataset exists
    await _get_dataset_or_404(data.dataset_id, db)

    # Resolve evaluators from names via the engine's class index
    evaluators: list[Evaluator] = []
    for name in data.evaluators:
        cls = get_evaluator_class(name)
        if cls is None:
            available = sorted(_list_evaluator_names())
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": {
                        "code": "INVALID_EVALUATOR",
                        "message": f"Unknown evaluator: {name!r}",
                        "details": {"available": available},
                    }
                },
            )
        evaluators.append(cls())

    engine = EvaluationEngine(db)
    source = AnswerSource(data.answer_source)
    result = await engine.run(evaluators, data.dataset_id, answer_source=source, tags=data.tags)

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
    task_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List evaluation runs with optional dataset/task filters."""
    base_query = select(EvaluationRunModel).where(~EvaluationRunModel.deleted)
    if dataset_id:
        base_query = base_query.where(EvaluationRunModel.dataset_id == dataset_id)
    if task_id:
        base_query = base_query.where(EvaluationRunModel.task_id == task_id)

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


class RunCompareRequest(BaseModel):
    """Request payload for ``POST /api/evaluation/runs/compare``."""

    model_config = ConfigDict(extra="forbid")

    baseline_run_id: uuid.UUID
    candidate_run_id: uuid.UUID
    regression_threshold: float | None = Field(default=None, gt=0.0, le=1.0)


class RunCompareResponse(BaseModel):
    """Response payload for ``POST /api/evaluation/runs/compare``."""

    model_config = ConfigDict(extra="forbid")

    baseline_run_id: uuid.UUID
    candidate_run_id: uuid.UUID
    baseline_workflow_version: int | None
    candidate_workflow_version: int | None
    metrics: list[dict]
    token_usage_delta: int
    latency_delta_ms: int
    cost_delta: float | None
    dataset_drift: dict | None
    node_drift: list[dict] | None
    overall_regressed: bool


@router.post("/runs/compare", response_model=RunCompareResponse)
async def compare_runs(
    body: RunCompareRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> RunCompareResponse:
    """Compare two evaluation runs.

    Returns per-metric deltas, token / latency / cost paired deltas, a
    dataset drift block (when the run snapshots differ), and per-node
    drift when both runs share the same workflow graph. The metric-level
    ``is_regression`` flag uses the candidate run's stored
    ``regression_threshold`` when configured, otherwise the request's
    ``regression_threshold`` (defaulting to the existing 7.2c default
    of 5%).

    Returns ``422`` when the two runs reference different datasets or
    disjoint evaluator sets — a misleading diff is worse than no diff.
    """
    from hecate.models.evaluation import EvaluationScoreModel

    threshold = body.regression_threshold if body.regression_threshold is not None else 0.05

    baseline = await db.get(EvaluationRunModel, body.baseline_run_id)
    candidate = await db.get(EvaluationRunModel, body.candidate_run_id)
    if baseline is None or candidate is None:
        missing = body.baseline_run_id if baseline is None else body.candidate_run_id
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"run {missing} not found",
        )
    if baseline.workspace_id != ctx.workspace_id or candidate.workspace_id != ctx.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="run not found",
        )
    if baseline.dataset_id != candidate.dataset_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="runs reference different datasets; comparison not meaningful",
        )

    base_scores_stmt = select(EvaluationScoreModel.metric_name, EvaluationScoreModel.value).where(
        EvaluationScoreModel.run_id == baseline.id,
        EvaluationScoreModel.value >= 0,
    )
    cand_scores_stmt = select(EvaluationScoreModel.metric_name, EvaluationScoreModel.value).where(
        EvaluationScoreModel.run_id == candidate.id,
        EvaluationScoreModel.value >= 0,
    )
    base_rows = (await db.execute(base_scores_stmt)).all()
    cand_rows = (await db.execute(cand_scores_stmt)).all()

    def _averages(rows: list) -> dict[str, float]:
        out: dict[str, list[float]] = {}
        for metric_name, value in rows:
            out.setdefault(metric_name, []).append(float(value))
        return {k: sum(v) / len(v) for k, v in out.items() if v}

    base_avg = _averages(base_rows)
    cand_avg = _averages(cand_rows)

    if base_avg.keys() != cand_avg.keys():
        only_base = sorted(set(base_avg) - set(cand_avg))
        only_cand = sorted(set(cand_avg) - set(base_avg))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"evaluator sets differ (only in baseline: {only_base}, only in candidate: {only_cand})",
        )

    metrics: list[dict] = []
    overall_regressed = False
    for metric_name, base_value in base_avg.items():
        cand_value = cand_avg[metric_name]
        delta_value = cand_value - base_value
        is_regression = cand_value < base_value * (1.0 - threshold)
        if is_regression:
            overall_regressed = True
        metrics.append(
            {
                "metric": metric_name,
                "baseline_avg": base_value,
                "candidate_avg": cand_value,
                "delta": delta_value,
                "is_regression": is_regression,
            }
        )

    token_usage_delta = 0
    latency_delta_ms = 0
    if baseline.started_at and candidate.completed_at and baseline.completed_at and candidate.started_at:
        latency_delta_ms = int(
            (candidate.completed_at - candidate.started_at).total_seconds() * 1000
            - (baseline.completed_at - baseline.started_at).total_seconds() * 1000
        )

    dataset_drift: dict | None = None
    snapshot = baseline.dataset_snapshot or candidate.dataset_snapshot
    if snapshot and snapshot.get("hash"):
        snapshot_hash = snapshot.get("hash")
        candidate_hash = (candidate.dataset_snapshot or {}).get("hash") or snapshot_hash
        if candidate_hash != snapshot_hash:
            snapshot_items = {row.get("id") for row in snapshot.get("items", [])}
            candidate_items = {row.get("id") for row in (candidate.dataset_snapshot or {}).get("items", [])}
            changed = sorted(snapshot_items ^ candidate_items)
            dataset_drift = {
                "old_hash": snapshot_hash,
                "new_hash": candidate_hash,
                "changed_item_ids": changed,
            }

    node_drift: list[dict] | None = None
    base_traj = baseline.trajectory or []
    cand_traj = candidate.trajectory or []
    if base_traj and cand_traj:
        base_keys = {row.get("session_id") for row in base_traj if row.get("session_id")}
        cand_keys = {row.get("session_id") for row in cand_traj if row.get("session_id")}
        if base_keys and base_keys == cand_keys:
            node_drift = [
                {
                    "session_id": k,
                    "baseline_repetition": next(
                        (row.get("repetition") for row in base_traj if row.get("session_id") == k),
                        None,
                    ),
                    "candidate_repetition": next(
                        (row.get("repetition") for row in cand_traj if row.get("session_id") == k),
                        None,
                    ),
                    "baseline_content_length": next(
                        (row.get("content_length") for row in base_traj if row.get("session_id") == k),
                        None,
                    ),
                    "candidate_content_length": next(
                        (row.get("content_length") for row in cand_traj if row.get("session_id") == k),
                        None,
                    ),
                }
                for k in sorted(base_keys)
            ]

    return RunCompareResponse(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        baseline_workflow_version=baseline.workflow_version,
        candidate_workflow_version=candidate.workflow_version,
        metrics=metrics,
        token_usage_delta=token_usage_delta,
        latency_delta_ms=latency_delta_ms,
        cost_delta=None,
        dataset_drift=dataset_drift,
        node_drift=node_drift,
        overall_regressed=overall_regressed,
    )
