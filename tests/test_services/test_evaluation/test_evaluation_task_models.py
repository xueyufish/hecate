"""Tests for evaluation task ORM models and schemas (7.2c).

Covers schema round-trips and the idempotency unique constraint on
``evaluation_task_scores`` — the DB-level backstop behind single-writer
dedupe in the online worker.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from hecate.models.evaluation import (
    EvaluationRunModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    EvaluationTaskScoreReadSchema,
    RunStatus,
    TaskScoreStatus,
    TaskStatus,
    TaskType,
)


async def _make_task(db_session, **overrides) -> EvaluationTaskModel:
    defaults: dict = {
        "task_type": TaskType.ONLINE.value,
        "name": "prod-relevancy",
        "evaluator_configs": ["relevancy"],
        "config": {"agent_id": str(uuid.uuid4()), "sampling_rate": 0.1, "max_traces_per_cycle": 50},
    }
    defaults.update(overrides)
    task = EvaluationTaskModel(**defaults)
    db_session.add(task)
    await db_session.flush()
    return task


async def _make_score(db_session, task_id: uuid.UUID, **overrides) -> EvaluationTaskScoreModel:
    defaults: dict = {
        "task_id": task_id,
        "target_id": uuid.uuid4(),
        "metric_name": "relevancy",
        "value": 0.9,
    }
    defaults.update(overrides)
    score = EvaluationTaskScoreModel(**defaults)
    db_session.add(score)
    await db_session.flush()
    return score


class TestEvaluationTaskModel:
    async def test_defaults_and_round_trip(self, db_session) -> None:
        task = await _make_task(db_session)

        assert task.status == TaskStatus.ACTIVE.value
        assert task.metrics == {}
        assert task.last_scanned_at is None
        assert task.config["sampling_rate"] == 0.1
        assert task.config["agent_id"]

    async def test_offline_task_config_shape(self, db_session) -> None:
        dataset_id = uuid.uuid4()
        task = await _make_task(
            db_session,
            task_type=TaskType.OFFLINE.value,
            config={
                "dataset_id": str(dataset_id),
                "answer_source": "agent",
                "threshold": 0.7,
                "regression_threshold": 0.05,
            },
        )
        assert task.config["dataset_id"] == str(dataset_id)
        assert task.config["answer_source"] == "agent"


class TestEvaluationTaskScoreModel:
    async def test_round_trip_with_schema(self, db_session) -> None:
        task = await _make_task(db_session)
        session_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        score = await _make_score(
            db_session,
            task.id,
            session_id=session_id,
            agent_id=agent_id,
        )

        read = EvaluationTaskScoreReadSchema.model_validate(score)
        assert read.target_type == "trace"
        assert read.session_id == session_id
        assert read.agent_id == agent_id
        assert read.status == TaskScoreStatus.COMPLETED.value

    async def test_idempotency_unique_constraint(self, db_session) -> None:
        task = await _make_task(db_session)
        target_id = uuid.uuid4()
        await _make_score(db_session, task.id, target_id=target_id)

        duplicate = EvaluationTaskScoreModel(
            task_id=task.id,
            target_id=target_id,
            metric_name="relevancy",
            value=0.5,
        )
        db_session.add(duplicate)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_same_target_different_metric_allowed(self, db_session) -> None:
        task = await _make_task(db_session)
        target_id = uuid.uuid4()
        await _make_score(db_session, task.id, target_id=target_id)
        await _make_score(db_session, task.id, target_id=target_id, metric_name="correctness")

        result = await db_session.execute(
            select(EvaluationTaskScoreModel).where(EvaluationTaskScoreModel.task_id == task.id)
        )
        assert len(result.scalars().all()) == 2


class TestRunModelTaskLinkage:
    async def test_task_run_carries_task_id_and_summary(self, db_session) -> None:
        task = await _make_task(db_session)
        run = EvaluationRunModel(
            dataset_id=uuid.uuid4(),
            task_id=task.id,
            status=RunStatus.PENDING.value,
            summary={"passed_items": 3, "failed_items": 1},
        )
        db_session.add(run)
        await db_session.flush()

        assert run.task_id == task.id
        assert run.summary["passed_items"] == 3

    async def test_request_run_task_id_null(self, db_session) -> None:
        run = EvaluationRunModel(dataset_id=uuid.uuid4())
        db_session.add(run)
        await db_session.flush()
        assert run.task_id is None
        assert run.summary is None
