"""Tests for DatasetSynthesisJobService — async job lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dataset_synthesis_job import (
    DatasetSynthesisJobCreateSchema,
    DatasetSynthesisJobModel,
)
from hecate.models.evaluation import EvaluationDatasetModel
from hecate.ops.evaluation.synthesis import DatasetSynthesisJobService
from hecate.ops.evaluation.synthesis.service import SeedDatasetUnavailableError


def _job_config(seed_dataset_id: uuid.UUID | None) -> dict:
    return {
        "seed_dataset_id": str(seed_dataset_id) if seed_dataset_id else None,
        "topic": None,
        "adversarial_intent": None,
        "count": 5,
        "target_dataset_name": "queued-target",
        "target_dataset_description": None,
        "llm_config": None,
        "quality_threshold": None,
    }


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


class TestSeedUnavailablePropagation:
    """A seed invalidated after acceptance fails the job explicitly."""

    async def _seed_deleted_dataset(self, db_session: AsyncSession, ws: uuid.UUID) -> EvaluationDatasetModel:
        ds = EvaluationDatasetModel(name="queued-seed", workspace_id=ws, deleted=True, deleted_at=datetime.now(UTC))
        db_session.add(ds)
        await db_session.commit()
        return ds

    async def _seed_queued_job(
        self, db_session: AsyncSession, ws: uuid.UUID, seed_id: uuid.UUID
    ) -> DatasetSynthesisJobModel:
        job = DatasetSynthesisJobModel(
            strategy="generation", status="queued", config=_job_config(seed_id), workspace_id=ws
        )
        db_session.add(job)
        await db_session.commit()
        return job

    async def _target_dataset_count(self, db_session: AsyncSession) -> int:
        return (
            await db_session.execute(
                select(func.count())
                .select_from(EvaluationDatasetModel)
                .where(EvaluationDatasetModel.name == "queued-target")
            )
        ).scalar_one()

    async def test_run_pipeline_raises_before_target_or_model(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hecate_llm.service

        calls = {"n": 0}

        async def _chat(*_a: object, **_kw: object) -> object:
            calls["n"] += 1
            raise AssertionError("model must not be called for an unavailable seed")

        monkeypatch.setattr(hecate_llm.service.llm_service, "chat", _chat)
        ws = uuid.uuid4()
        seed = await self._seed_deleted_dataset(db_session, ws)
        job = await self._seed_queued_job(db_session, ws, seed.id)
        svc = DatasetSynthesisJobService(db_session)
        with pytest.raises(SeedDatasetUnavailableError):
            await svc._run_pipeline(db_session, job)
        assert await self._target_dataset_count(db_session) == 0
        assert calls["n"] == 0

    async def test_execute_job_marks_failed_when_seed_unavailable(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import hecate.core.database as database_mod

        ws = uuid.uuid4()
        seed = await self._seed_deleted_dataset(db_session, ws)
        job = await self._seed_queued_job(db_session, ws, seed.id)
        monkeypatch.setattr(database_mod, "async_session_factory", lambda: db_session)

        await DatasetSynthesisJobService(db_session)._execute_job(job.id)

        refreshed = (
            await db_session.execute(select(DatasetSynthesisJobModel).where(DatasetSynthesisJobModel.id == job.id))
        ).scalar_one()
        assert refreshed.status == "failed"
        assert refreshed.completed_at is not None
        assert "seed" in (refreshed.error_message or "").lower()
        assert refreshed.target_dataset_id is None
        assert await self._target_dataset_count(db_session) == 0

    async def test_seed_read_failure_does_not_commit_an_orphan_target(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A seed rejected during loading leaves only the failed job, never a target."""
        import hecate_llm.service

        import hecate.core.database as database_mod
        from hecate.models.evaluation import EvaluationItemModel
        from hecate.ops.evaluation.synthesis import DatasetSynthesisService

        ws = uuid.uuid4()
        seed = EvaluationDatasetModel(name="live-seed", workspace_id=ws)
        db_session.add(seed)
        await db_session.commit()
        job = await self._seed_queued_job(db_session, ws, seed.id)
        calls = {"seed": 0, "model": 0}

        async def unavailable_during_load(
            self: DatasetSynthesisService, seed_dataset_id: uuid.UUID, workspace_id: uuid.UUID
        ) -> list:
            calls["seed"] += 1
            raise SeedDatasetUnavailableError("Seed dataset unavailable during loading")

        async def unexpected_model(*args: object, **kwargs: object) -> None:
            calls["model"] += 1
            raise AssertionError("An unavailable seed must not reach the model")

        monkeypatch.setattr(DatasetSynthesisService, "_load_seeds", unavailable_during_load)
        monkeypatch.setattr(hecate_llm.service.llm_service, "chat", unexpected_model)
        monkeypatch.setattr(database_mod, "async_session_factory", lambda: db_session)
        await DatasetSynthesisJobService(db_session)._execute_job(job.id)

        job_table = DatasetSynthesisJobModel.__table__
        row = (await db_session.execute(select(job_table).where(job_table.c.id == job.id))).mappings().one()
        assert row["status"] == "failed" and row["completed_at"] is not None
        assert row["target_dataset_id"] is None and "unavailable" in row["error_message"]
        assert calls == {"seed": 1, "model": 0}
        assert await self._target_dataset_count(db_session) == 0
        assert (await db_session.execute(select(func.count()).select_from(EvaluationItemModel))).scalar_one() == 0


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
