"""Async job lifecycle for synthesis tasks >20 items.

Mirrors the ``FineTuningJobModel`` lifecycle pattern: persisted row in
the database tracks ``queued → running → completed/failed`` with
``started_at`` / ``completed_at`` / ``error_message``. The actual
synthesis work runs in an :func:`asyncio.create_task` spawned by the
service; the API returns 202 immediately.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dataset_synthesis_job import (
    DatasetSynthesisJobCreateSchema,
    DatasetSynthesisJobModel,
    DatasetSynthesisJobReadSchema,
)
from hecate.ops.evaluation.synthesis.service import (
    DatasetSynthesisService,
    SynthesisRequest,
)
from hecate.ops.evaluation.types import LLMConfig

logger = logging.getLogger(__name__)


class DatasetSynthesisJobService:
    """Async job tracking and execution for synthesis tasks.

    Args:
        db: Async SQLAlchemy session. A new session is opened per
            background task (via ``asyncio.create_task``) to avoid
            sharing a connection across the event loop.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_job(
        self,
        data: DatasetSynthesisJobCreateSchema,
        workspace_id: uuid.UUID,
    ) -> DatasetSynthesisJobModel:
        """Persist a new job row in ``queued`` state.

        The actual synthesis work is scheduled separately via
        :meth:`run_job_in_background` — the caller is expected to invoke
        that immediately after the job row is committed.
        """
        job = DatasetSynthesisJobModel(
            strategy=data.strategy,
            status="queued",
            config={
                "seed_dataset_id": str(data.seed_dataset_id) if data.seed_dataset_id else None,
                "topic": data.topic,
                "adversarial_intent": data.adversarial_intent,
                "count": data.count,
                "target_dataset_name": data.target_dataset_name,
                "target_dataset_description": data.target_dataset_description,
                "llm_config": data.llm_config,
                "quality_threshold": data.quality_threshold,
            },
            workspace_id=workspace_id,
        )
        self.db.add(job)
        await self.db.flush()
        await self.db.refresh(job)
        return job

    async def run_job_in_background(self, job_id: uuid.UUID) -> None:
        """Spawn an asyncio task to run the synthesis pipeline.

        The task opens its own AsyncSession so the request's session
        can close cleanly while the pipeline runs.
        """
        asyncio.create_task(self._execute_job(job_id))

    async def _execute_job(self, job_id: uuid.UUID) -> None:
        """Open a fresh session, transition status, run synthesis, write metrics."""
        from hecate.core.database import async_session_factory  # local import — avoid circular

        if async_session_factory is None:
            logger.error("async_session_factory unavailable — cannot run synthesis job %s", job_id)
            return

        async with async_session_factory() as session:
            job = await self._get_job(session, job_id)
            if job is None:
                logger.error("Job %s not found", job_id)
                return
            await self._mark_running(session, job)
            try:
                result = await self._run_pipeline(session, job)
                await self._mark_completed(session, job, result)
            except Exception as exc:  # noqa: BLE001 — surface error_message
                logger.exception("Synthesis job %s failed", job_id)
                await self._mark_failed(session, job, str(exc))

    async def _run_pipeline(self, session: AsyncSession, job: DatasetSynthesisJobModel):
        config = job.config
        request = SynthesisRequest(
            seed_dataset_id=uuid.UUID(config["seed_dataset_id"]) if config.get("seed_dataset_id") else None,
            topic=config.get("topic"),
            strategy=job.strategy,
            adversarial_intent=config.get("adversarial_intent"),
            count=int(config["count"]),
            target_dataset_name=config["target_dataset_name"],
            target_dataset_description=config.get("target_dataset_description"),
            llm_config=_build_llm_config(config.get("llm_config")),
            quality_threshold=config.get("quality_threshold"),
            workspace_id=job.workspace_id,
        )
        svc = DatasetSynthesisService(session)
        return await svc.synthesize(request)

    async def _mark_running(self, session: AsyncSession, job: DatasetSynthesisJobModel) -> None:
        job.status = "running"
        job.started_at = datetime.now(UTC)
        job.target_dataset_id = None  # populated on completion
        await session.flush()

    async def _mark_completed(
        self,
        session: AsyncSession,
        job: DatasetSynthesisJobModel,
        result,
    ) -> None:
        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        job.target_dataset_id = result.target_dataset_id
        job.metrics = {
            "items_generated": result.items_generated,
            "items_filtered": result.items_filtered,
            "duration_ms": result.duration_ms,
            "filter_breakdown": result.filter_breakdown,
        }
        await session.commit()

    async def _mark_failed(
        self,
        session: AsyncSession,
        job: DatasetSynthesisJobModel,
        error_message: str,
    ) -> None:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
        job.error_message = error_message[:2000]
        await session.commit()

    async def _get_job(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
    ) -> DatasetSynthesisJobModel | None:
        result = await session.execute(
            select(DatasetSynthesisJobModel).where(
                DatasetSynthesisJobModel.id == job_id,
                ~DatasetSynthesisJobModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def get_job(
        self,
        job_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
    ) -> DatasetSynthesisJobModel | None:
        """Public read accessor for ``GET /evaluation/synthesis-jobs/{job_id}``."""
        conditions = [
            DatasetSynthesisJobModel.id == job_id,
            ~DatasetSynthesisJobModel.deleted,
        ]
        if workspace_id is not None:
            conditions.append(DatasetSynthesisJobModel.workspace_id == workspace_id)
        result = await self.db.execute(select(DatasetSynthesisJobModel).where(*conditions))
        return result.scalar_one_or_none()


def _build_llm_config(raw: dict | None) -> LLMConfig | None:
    if not raw:
        return None
    return LLMConfig(
        model=raw.get("model", LLMConfig().model),
        temperature=float(raw.get("temperature", LLMConfig().temperature)),
        api_base=raw.get("api_base"),
    )


__all__ = [
    "DatasetSynthesisJobService",
    "DatasetSynthesisJobReadSchema",
]
