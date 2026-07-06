"""Fine-tuning pipeline REST API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.model_hub.fine_tuning import FineTuningService
from hecate.models.dataset import DatasetReadSchema
from hecate.models.fine_tuning_job import FineTuningJobCreateSchema, FineTuningJobReadSchema

router = APIRouter(prefix="/api/fine-tuning", tags=["fine-tuning"])


@router.get("/datasets")
async def list_datasets(
    workspace_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    service = FineTuningService(db)
    datasets = await service.list_datasets(workspace_id)
    return [DatasetReadSchema.model_validate(d).model_dump() for d in datasets]


@router.get("/datasets/{dataset_id}")
async def get_dataset(
    dataset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = FineTuningService(db)
    dataset = await service.get_dataset(dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return DatasetReadSchema.model_validate(dataset).model_dump()


@router.post("/datasets/upload")
async def upload_dataset(
    file: UploadFile,
    name: str,
    description: str | None = None,
    workspace_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    content = await file.read()
    service = FineTuningService(db)
    dataset = await service.create_dataset(
        name=name,
        file_content=content,
        format="jsonl",
        description=description,
        workspace_id=workspace_id,
    )
    return DatasetReadSchema.model_validate(dataset).model_dump()


@router.get("/jobs")
async def list_jobs(
    workspace_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[dict[str, Any]]:
    from sqlalchemy import select as sa_select

    from hecate.models.fine_tuning_job import FineTuningJobModel

    ws_id = workspace_id or uuid.UUID("00000000-0000-0000-0000-000000000000")
    stmt = (
        sa_select(FineTuningJobModel)
        .where(FineTuningJobModel.workspace_id == ws_id, ~FineTuningJobModel.deleted)
        .order_by(FineTuningJobModel.created_at.desc())
    )
    result = await db.execute(stmt)
    jobs = result.scalars().all()
    return [FineTuningJobReadSchema.model_validate(j).model_dump() for j in jobs]


@router.post("/jobs")
async def submit_job(
    data: FineTuningJobCreateSchema,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = FineTuningService(db)
    job = await service.submit_job(
        dataset_id=data.dataset_id,
        base_model=data.base_model,
        config=data.config,
        provider=data.provider,
        workspace_id=data.workspace_id,
    )
    return FineTuningJobReadSchema.model_validate(job).model_dump()


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    from sqlalchemy import select as sa_select

    from hecate.models.fine_tuning_job import FineTuningJobModel

    stmt = sa_select(FineTuningJobModel).where(FineTuningJobModel.id == job_id, ~FineTuningJobModel.deleted)
    result = await db.execute(stmt)
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    service = FineTuningService(db)
    job = await service.poll_job_status(job_id)
    return FineTuningJobReadSchema.model_validate(job).model_dump()


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = FineTuningService(db)
    job = await service.cancel_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return FineTuningJobReadSchema.model_validate(job).model_dump()


@router.post("/jobs/{job_id}/deploy")
async def deploy_model(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    service = FineTuningService(db)
    model = await service.deploy_model(job_id)
    if model is None:
        raise HTTPException(status_code=400, detail="Job not ready for deployment")
    return {"model_id": model.model_id, "display_name": model.display_name}
