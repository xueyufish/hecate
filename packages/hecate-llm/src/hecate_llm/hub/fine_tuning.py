"""Fine-tuning pipeline — ABC, adapters, and service.

Provides FineTuningBackendABC for pluggable fine-tuning providers,
OpenAIFineTuningBackend as the reference implementation, and
FineTuningService for job orchestration.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dataset import DatasetModel
from hecate.models.fine_tuning_job import FineTuningJobModel
from hecate.models.model_provider import ModelRegistryModel

logger = logging.getLogger(__name__)


class FineTuningBackendABC(ABC):
    """Abstract interface for fine-tuning provider backends."""

    @abstractmethod
    async def submit_job(
        self,
        dataset_content: bytes,
        base_model: str,
        config: dict[str, Any],
    ) -> str:
        """Submit a fine-tuning job.

        Args:
            dataset_content: The dataset file content.
            base_model: The base model to fine-tune.
            config: Hyperparameters and other config.

        Returns:
            Provider-specific job ID.
        """
        ...

    @abstractmethod
    async def poll_status(self, provider_job_id: str) -> dict[str, Any]:
        """Poll the status of a fine-tuning job.

        Args:
            provider_job_id: The provider-specific job ID.

        Returns:
            Dict with keys: status, progress, metrics, result_model_id, error.
        """
        ...

    @abstractmethod
    async def cancel_job(self, provider_job_id: str) -> bool:
        """Cancel a running fine-tuning job.

        Args:
            provider_job_id: The provider-specific job ID.

        Returns:
            True if cancelled successfully.
        """
        ...


class OpenAIFineTuningBackend(FineTuningBackendABC):
    """Fine-tuning backend using OpenAI's API."""

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def submit_job(
        self,
        dataset_content: bytes,
        base_model: str,
        config: dict[str, Any],
    ) -> str:
        async with httpx.AsyncClient() as client:
            file_resp = await client.post(
                f"{self._base_url}/files",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": ("training.jsonl", dataset_content, "application/jsonl")},
                data={"purpose": "fine-tune"},
                timeout=120.0,
            )
            file_resp.raise_for_status()
            file_id = file_resp.json()["id"]

            job_resp = await client.post(
                f"{self._base_url}/fine_tuning/jobs",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "training_file": file_id,
                    "model": base_model,
                    "hyperparameters": config.get("hyperparameters", {}),
                    "suffix": config.get("suffix"),
                },
                timeout=30.0,
            )
            job_resp.raise_for_status()
            return job_resp.json()["id"]

    async def poll_status(self, provider_job_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/fine_tuning/jobs/{provider_job_id}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

            status_map = {
                "queued": "queued",
                "running": "running",
                "succeeded": "succeeded",
                "failed": "failed",
                "cancelled": "cancelled",
            }
            status = status_map.get(data.get("status", ""), "queued")
            result_model = data.get("fine_tuned_model")
            error = data.get("error")

            return {
                "status": status,
                "result_model_id": result_model,
                "error": error.get("message") if isinstance(error, dict) else str(error) if error else None,
                "metrics": data.get("result_files", []),
            }

    async def cancel_job(self, provider_job_id: str) -> bool:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._base_url}/fine_tuning/jobs/{provider_job_id}/cancel",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=10.0,
            )
            return resp.status_code == 200


class InMemoryFineTuningBackend(FineTuningBackendABC):
    """Test stub that simulates fine-tuning job lifecycle."""

    def __init__(self, delay: float = 0.1) -> None:
        self._delay = delay
        self._jobs: dict[str, dict[str, Any]] = {}

    async def submit_job(
        self,
        dataset_content: bytes,
        base_model: str,
        config: dict[str, Any],
    ) -> str:
        job_id = f"ft-job-{uuid.uuid4().hex[:8]}"
        self._jobs[job_id] = {
            "status": "queued",
            "base_model": base_model,
            "config": config,
            "created_at": datetime.now(UTC),
        }
        return job_id

    async def poll_status(self, provider_job_id: str) -> dict[str, Any]:
        job = self._jobs.get(provider_job_id)
        if job is None:
            return {"status": "failed", "error": "Job not found"}

        elapsed = (datetime.now(UTC) - job["created_at"]).total_seconds()
        if elapsed < self._delay:
            return {"status": "queued"}
        if elapsed < self._delay * 3:
            return {"status": "running", "progress": 50}

        return {
            "status": "succeeded",
            "result_model_id": f"ft:{job['base_model']}:custom:{provider_job_id}",
            "metrics": {"training_loss": 0.15, "validation_loss": 0.18},
        }

    async def cancel_job(self, provider_job_id: str) -> bool:
        if provider_job_id in self._jobs:
            self._jobs[provider_job_id]["status"] = "cancelled"
            return True
        return False


class FineTuningService:
    """Service for fine-tuning job orchestration."""

    def __init__(self, db: AsyncSession, backend: FineTuningBackendABC | None = None) -> None:
        self._db = db
        self._backend = backend or InMemoryFineTuningBackend()

    async def create_dataset(
        self,
        name: str,
        file_content: bytes,
        format: str = "jsonl",
        description: str | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> DatasetModel:
        ws_id = workspace_id or uuid.UUID("00000000-0000-0000-0000-000000000000")
        row_count = file_content.count(b"\n")
        schema_preview = {}
        if format == "jsonl" and file_content:
            try:
                first_line = file_content.split(b"\n")[0]
                import json

                schema_preview = json.loads(first_line)
            except (json.JSONDecodeError, IndexError):
                pass

        dataset = DatasetModel(
            name=name,
            description=description,
            format=format,
            row_count=row_count,
            schema_preview=schema_preview,
            file_storage_url=f"datasets/{uuid.uuid4().hex}/{name}.{format}",
            workspace_id=ws_id,
        )
        self._db.add(dataset)
        await self._db.flush()
        return dataset

    async def list_datasets(
        self,
        workspace_id: uuid.UUID | None = None,
    ) -> list[DatasetModel]:
        ws_id = workspace_id or uuid.UUID("00000000-0000-0000-0000-000000000000")
        stmt = (
            select(DatasetModel)
            .where(DatasetModel.workspace_id == ws_id, ~DatasetModel.deleted)
            .order_by(DatasetModel.created_at.desc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_dataset(self, dataset_id: uuid.UUID) -> DatasetModel | None:
        stmt = select(DatasetModel).where(DatasetModel.id == dataset_id, ~DatasetModel.deleted)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def submit_job(
        self,
        dataset_id: uuid.UUID,
        base_model: str,
        config: dict[str, Any] | None = None,
        provider: str = "openai",
        workspace_id: uuid.UUID | None = None,
    ) -> FineTuningJobModel:
        ws_id = workspace_id or uuid.UUID("00000000-0000-0000-0000-000000000000")
        job = FineTuningJobModel(
            dataset_id=dataset_id,
            base_model=base_model,
            provider=provider,
            config=config or {},
            workspace_id=ws_id,
        )
        self._db.add(job)
        await self._db.flush()

        dataset = await self.get_dataset(dataset_id)
        if dataset is None:
            job.status = "failed"
            job.error_message = "Dataset not found"
            await self._db.flush()
            return job

        try:
            content = dataset.name.encode()
            provider_job_id = await self._backend.submit_job(content, base_model, config or {})
            job.provider_job_id = provider_job_id
            job.status = "running"
            await self._db.flush()
        except Exception as e:
            job.status = "failed"
            job.error_message = str(e)
            await self._db.flush()

        return job

    async def poll_job_status(self, job_id: uuid.UUID) -> FineTuningJobModel | None:
        stmt = select(FineTuningJobModel).where(FineTuningJobModel.id == job_id, ~FineTuningJobModel.deleted)
        result = await self._db.execute(stmt)
        job = result.scalar_one_or_none()
        if job is None or job.provider_job_id is None:
            return job

        if job.status in ("succeeded", "failed", "cancelled"):
            return job

        try:
            status = await self._backend.poll_status(job.provider_job_id)
            job.status = status.get("status", job.status)
            if status.get("result_model_id"):
                job.result_model_id = status["result_model_id"]
            if status.get("metrics"):
                job.metrics = status["metrics"]
            if status.get("error"):
                job.error_message = status["error"]
            await self._db.flush()
        except Exception as e:
            logger.exception("Poll error for job %s", job_id)
            job.error_message = str(e)

        return job

    async def cancel_job(self, job_id: uuid.UUID) -> FineTuningJobModel | None:
        stmt = select(FineTuningJobModel).where(FineTuningJobModel.id == job_id, ~FineTuningJobModel.deleted)
        result = await self._db.execute(stmt)
        job = result.scalar_one_or_none()
        if job is None:
            return None

        if job.provider_job_id:
            await self._backend.cancel_job(job.provider_job_id)
        job.status = "cancelled"
        await self._db.flush()
        return job

    async def deploy_model(self, job_id: uuid.UUID) -> ModelRegistryModel | None:
        stmt = select(FineTuningJobModel).where(FineTuningJobModel.id == job_id, ~FineTuningJobModel.deleted)
        result = await self._db.execute(stmt)
        job = result.scalar_one_or_none()
        if job is None or job.status != "succeeded" or job.result_model_id is None:
            return None

        model = ModelRegistryModel(
            provider_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            model_id=job.result_model_id,
            display_name=f"{job.base_model} (fine-tuned)",
            model_type="chat",
            model_metadata={
                "modalities": {"input": ["text"], "output": ["text"]},
                "capabilities": {"fine_tuned": True},
                "limits": {},
                "base_model": job.base_model,
                "dataset_id": str(job.dataset_id),
            },
            is_custom=True,
        )
        self._db.add(model)
        await self._db.flush()
        return model
