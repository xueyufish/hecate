"""Tests for fine-tuning pipeline."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.model_hub.fine_tuning import (
    FineTuningBackendABC,
    FineTuningService,
    InMemoryFineTuningBackend,
)


def test_abc_not_instantiable() -> None:
    with pytest.raises(TypeError):
        FineTuningBackendABC()  # type: ignore[abstract]


async def test_in_memory_submit_job() -> None:
    backend = InMemoryFineTuningBackend(delay=0.01)
    job_id = await backend.submit_job(b"test data", "gpt-4o", {})
    assert job_id.startswith("ft-job-")


async def test_in_memory_poll_lifecycle() -> None:
    import asyncio

    backend = InMemoryFineTuningBackend(delay=0.01)
    job_id = await backend.submit_job(b"test data", "gpt-4o", {})

    status = await backend.poll_status(job_id)
    assert status["status"] in ("queued", "running", "succeeded")

    await asyncio.sleep(0.05)
    status = await backend.poll_status(job_id)
    assert status["status"] == "succeeded"
    assert status["result_model_id"] is not None


async def test_in_memory_cancel() -> None:
    backend = InMemoryFineTuningBackend()
    job_id = await backend.submit_job(b"test", "gpt-4o", {})
    cancelled = await backend.cancel_job(job_id)
    assert cancelled is True


async def test_in_memory_cancel_nonexistent() -> None:
    backend = InMemoryFineTuningBackend()
    cancelled = await backend.cancel_job("nonexistent")
    assert cancelled is False


async def test_fine_tuning_service_create_dataset(db_session: AsyncSession) -> None:
    service = FineTuningService(db_session)
    dataset = await service.create_dataset(
        name="test-dataset",
        file_content=b'{"messages": [{"role": "user", "content": "hi"}]}\n',
        format="jsonl",
    )
    assert dataset.name == "test-dataset"
    assert dataset.row_count >= 1
    assert dataset.format == "jsonl"


async def test_fine_tuning_service_list_datasets(db_session: AsyncSession) -> None:
    service = FineTuningService(db_session)
    await service.create_dataset(name="ds1", file_content=b"test\n")
    await service.create_dataset(name="ds2", file_content=b"test\n")
    datasets = await service.list_datasets()
    assert len(datasets) >= 2


async def test_fine_tuning_service_submit_job(db_session: AsyncSession) -> None:
    service = FineTuningService(db_session, backend=InMemoryFineTuningBackend(delay=0.01))
    dataset = await service.create_dataset(name="train", file_content=b"test\n")
    job = await service.submit_job(dataset.id, "gpt-4o", provider="openai")
    assert job.status == "running"
    assert job.dataset_id == dataset.id


async def test_fine_tuning_service_deploy(db_session: AsyncSession) -> None:
    import asyncio

    service = FineTuningService(db_session, backend=InMemoryFineTuningBackend(delay=0.01))
    dataset = await service.create_dataset(name="train", file_content=b"test\n")
    job = await service.submit_job(dataset.id, "gpt-4o")

    await asyncio.sleep(0.05)
    job = await service.poll_job_status(job.id)
    assert job is not None
    assert job.status == "succeeded"

    model = await service.deploy_model(job.id)
    assert model is not None
    assert "fine-tuned" in model.display_name
