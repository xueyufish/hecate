"""Tests for DatasetSynthesisJobService — async job lifecycle."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dataset_synthesis_job import (
    DatasetSynthesisJobCreateSchema,
    DatasetSynthesisJobModel,
)
from hecate.ops.evaluation.synthesis import DatasetSynthesisJobService


class TestCreateJob:
    @pytest.mark.asyncio
    async def test_create_persists_queued_state(self, db_session: AsyncSession) -> None:
        svc = DatasetSynthesisJobService(db_session)
        data = DatasetSynthesisJobCreateSchema(
            seed_dataset_id=None,
            topic="test topic",
            strategy="generation",
            adversarial_intent=None,
            count=10,
            target_dataset_name="gen-test",
            target_dataset_description=None,
            llm_config=None,
            quality_threshold=0.5,
        )
        job = await svc.create_job(data, workspace_id=uuid.UUID(int=0))
        assert job.id is not None
        assert job.status == "queued"
        assert job.strategy == "generation"
        assert job.config["topic"] == "test topic"
        assert job.config["count"] == 10

    @pytest.mark.asyncio
    async def test_create_persists_adversarial_intent(self, db_session: AsyncSession) -> None:
        svc = DatasetSynthesisJobService(db_session)
        data = DatasetSynthesisJobCreateSchema(
            seed_dataset_id=None,
            topic=None,
            strategy="adversarial",
            adversarial_intent="prompt_injection_basic",
            count=5,
            target_dataset_name="adv-test",
            target_dataset_description=None,
            llm_config=None,
            quality_threshold=None,
        )
        job = await svc.create_job(data, workspace_id=uuid.UUID(int=0))
        assert job.config["adversarial_intent"] == "prompt_injection_basic"


class TestGetJob:
    @pytest.mark.asyncio
    async def test_get_existing(self, db_session: AsyncSession) -> None:
        svc = DatasetSynthesisJobService(db_session)
        data = DatasetSynthesisJobCreateSchema(
            seed_dataset_id=None,
            topic=None,
            strategy="generation",
            adversarial_intent=None,
            count=1,
            target_dataset_name="get-test",
            target_dataset_description=None,
            llm_config=None,
            quality_threshold=None,
        )
        job = await svc.create_job(data, workspace_id=uuid.UUID(int=0))
        await db_session.flush()

        fetched = await svc.get_job(job.id)
        assert fetched is not None
        assert fetched.id == job.id
        assert fetched.status == "queued"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, db_session: AsyncSession) -> None:
        svc = DatasetSynthesisJobService(db_session)
        fetched = await svc.get_job(uuid.uuid4())
        assert fetched is None

    @pytest.mark.asyncio
    async def test_get_filters_by_workspace(self, db_session: AsyncSession) -> None:
        svc = DatasetSynthesisJobService(db_session)
        data = DatasetSynthesisJobCreateSchema(
            seed_dataset_id=None,
            topic=None,
            strategy="generation",
            adversarial_intent=None,
            count=1,
            target_dataset_name="ws-test",
            target_dataset_description=None,
            llm_config=None,
            quality_threshold=None,
        )
        job = await svc.create_job(data, workspace_id=uuid.UUID(int=0))
        await db_session.flush()

        # Tenant isolation: another workspace must not see this job
        other = await svc.get_job(job.id, workspace_id=uuid.uuid4())
        assert other is None


class TestStatusTransitions:
    @pytest.mark.asyncio
    async def test_status_enum_values(self, db_session: AsyncSession) -> None:
        """Spec invariant: status set is {queued, running, completed, failed}."""
        svc = DatasetSynthesisJobService(db_session)
        data = DatasetSynthesisJobCreateSchema(
            seed_dataset_id=None,
            topic=None,
            strategy="generation",
            adversarial_intent=None,
            count=1,
            target_dataset_name="enum-test",
            target_dataset_description=None,
            llm_config=None,
            quality_threshold=None,
        )
        job = await svc.create_job(data, workspace_id=uuid.UUID(int=0))
        await db_session.flush()
        valid_states = {"queued", "running", "completed", "failed"}
        # Default new-job state is "queued"
        assert job.status in valid_states

        # A direct UPDATE is acceptable for the test — the service uses
        # the same valid value set in its lifecycle methods.
        job.status = "running"
        await db_session.flush()
        stmt = select(DatasetSynthesisJobModel).where(DatasetSynthesisJobModel.id == job.id)
        refreshed = (await db_session.execute(stmt)).scalar_one()
        assert refreshed.status == "running"

        job.status = "completed"
        job.metrics = {"items_generated": 1, "items_filtered": 0}
        await db_session.flush()
        stmt = select(DatasetSynthesisJobModel).where(DatasetSynthesisJobModel.id == job.id)
        refreshed = (await db_session.execute(stmt)).scalar_one()
        assert refreshed.status == "completed"
        assert refreshed.metrics["items_generated"] == 1
