"""AI dataset synthesis API endpoints.

- ``POST /api/evaluation/datasets/synthesize`` — kick off a synthesis job
- ``GET /api/evaluation/synthesis-jobs/{job_id}`` — poll job status

Sync vs async is decided by ``count``:

- ``count <= 20`` → synchronous, returns 200 with the result summary
- ``count > 20`` → asynchronous, returns 202 with ``job_id``
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.dataset_synthesis_job import (
    DatasetSynthesisAsyncResponseSchema,
    DatasetSynthesisJobCreateSchema,
    DatasetSynthesisJobReadSchema,
    DatasetSynthesisSyncResponseSchema,
)
from hecate.ops.evaluation.synthesis import (
    DatasetSynthesisJobService,
    DatasetSynthesisService,
)
from hecate.ops.evaluation.synthesis.service import SynthesisRequest
from hecate.ops.evaluation.types import LLMConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evaluation", tags=["evaluation"])

_SYNC_THRESHOLD = 20
_SYNC_TIMEOUT_SECONDS = 60


@router.post("/datasets/synthesize")
async def synthesize_dataset(
    data: DatasetSynthesisJobCreateSchema,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Kick off a synthesis task.

    See module docstring for sync/async decision logic.
    """
    workspace_id = ctx.workspace_id
    if data.workspace_id:
        workspace_id = data.workspace_id

    # Build internal request — shared between sync and async paths.
    workspace_id = ctx.workspace_id
    if data.workspace_id:
        workspace_id = data.workspace_id

    # Build internal request — shared between sync and async paths.
    request = SynthesisRequest(
        seed_dataset_id=data.seed_dataset_id,
        topic=data.topic,
        strategy=data.strategy,
        adversarial_intent=data.adversarial_intent,
        count=data.count,
        target_dataset_name=data.target_dataset_name,
        target_dataset_description=data.target_dataset_description,
        llm_config=_build_llm_config(data.llm_config),
        quality_threshold=data.quality_threshold,
        workspace_id=workspace_id,
    )

    if data.count <= _SYNC_THRESHOLD:
        return await _run_sync(request, db)
    # Async path: set 202 Accepted before returning the body
    response.status_code = status.HTTP_202_ACCEPTED
    return await _run_async(request, db, workspace_id)


async def _run_sync(request: SynthesisRequest, db: AsyncSession) -> dict:
    """Synchronous path: run pipeline inline, return result summary."""
    svc = DatasetSynthesisService(db)
    try:
        result = await asyncio.wait_for(svc.synthesize(request), timeout=_SYNC_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        logger.warning("Sync synthesis exceeded %ds — caller should retry as async", _SYNC_TIMEOUT_SECONDS)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={
                "error": {
                    "code": "SYNTHESIS_TIMEOUT",
                    "message": (
                        f"Synthesis of {request.count} items exceeded {_SYNC_TIMEOUT_SECONDS}s; resubmit as async"
                    ),
                    "details": {"hint": "use count>20 to force the async path"},
                }
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_REQUEST", "message": str(exc), "details": None}},
        ) from exc
    payload = DatasetSynthesisSyncResponseSchema(
        target_dataset_id=result.target_dataset_id,
        items_generated=result.items_generated,
        items_filtered=result.items_filtered,
        duration_ms=result.duration_ms,
    )
    return payload.model_dump()


async def _run_async(
    request: SynthesisRequest,
    db: AsyncSession,
    workspace_id: uuid.UUID,
) -> dict:
    """Asynchronous path: persist job row, spawn background task, return 202."""
    job_svc = DatasetSynthesisJobService(db)
    job = await job_svc.create_job(
        DatasetSynthesisJobCreateSchema(
            seed_dataset_id=request.seed_dataset_id,
            topic=request.topic,
            strategy=request.strategy,
            adversarial_intent=request.adversarial_intent,
            count=request.count,
            target_dataset_name=request.target_dataset_name,
            target_dataset_description=request.target_dataset_description,
            llm_config=request.llm_config.model_dump() if request.llm_config else None,
            quality_threshold=request.quality_threshold,
            workspace_id=workspace_id,
        ),
        workspace_id=workspace_id,
    )
    await db.commit()
    await job_svc.run_job_in_background(job.id)
    payload = DatasetSynthesisAsyncResponseSchema(job_id=job.id, status="queued")
    return payload.model_dump(mode="json")


@router.get("/synthesis-jobs/{job_id}")
async def get_synthesis_job(
    job_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Poll an async synthesis job."""
    job_svc = DatasetSynthesisJobService(db)
    job = await job_svc.get_job(job_id, workspace_id=ctx.workspace_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Synthesis job not found", "details": None}},
        )
    return DatasetSynthesisJobReadSchema.model_validate(job).model_dump(mode="json")


def _build_llm_config(raw: dict | None) -> LLMConfig | None:
    if not raw:
        return None
    return LLMConfig(
        model=raw.get("model", LLMConfig().model),
        temperature=float(raw.get("temperature", LLMConfig().temperature)),
        api_base=raw.get("api_base"),
    )


# Local import to keep the diff small; the standard-library asyncio is
# already imported via the engine module but explicit is safer here.
import asyncio  # noqa: E402
