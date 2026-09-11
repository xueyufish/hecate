"""Evaluation task service — CRUD, lifecycle, run triggering, score queries.

A task is a persistent evaluation configuration (see ``EvaluationTaskModel``).
The service enforces cross-field configuration rules, the online-task
enable/disable lifecycle, and workspace isolation. Offline run execution
itself lives in :mod:`runner`; the online scoring loop in :mod:`online_worker`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationRunModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    RunStatus,
    TaskStatus,
    TaskType,
)
from hecate.ops.evaluation.engine import get_evaluator_class

logger = logging.getLogger(__name__)

_DEFAULT_MAX_TRACES_PER_CYCLE = 50


class EvaluationTaskNotFoundError(LookupError):
    """Task with the given id does not exist in the workspace."""


class EvaluationTaskValidationError(ValueError):
    """Task configuration violates a cross-field rule."""


def build_task_config(data: Any) -> dict:
    """Flatten a create/update schema into the task's ``config`` JSON.

    Only the fields relevant to the declared ``task_type`` are kept, so
    clients cannot smuggle online fields into offline tasks or vice versa.
    """
    config: dict = {}
    if data.task_type == TaskType.OFFLINE.value:
        if data.dataset_id is None:
            raise EvaluationTaskValidationError("offline tasks require dataset_id")
        answer_source = data.answer_source or "manual"
        config["dataset_id"] = str(data.dataset_id)
        config["answer_source"] = answer_source
        if data.threshold is not None:
            config["threshold"] = data.threshold
        if data.baseline_run_id is not None:
            config["baseline_run_id"] = str(data.baseline_run_id)
        if data.regression_threshold is not None:
            config["regression_threshold"] = data.regression_threshold
        if data.tags:
            config["tags"] = list(data.tags)
        if data.agent_id is not None:
            config["agent_id"] = str(data.agent_id)
        if answer_source == "agent" and data.agent_id is None:
            raise EvaluationTaskValidationError('answer_source="agent" requires agent_id (the agent under test)')
    else:
        if data.agent_id is None:
            raise EvaluationTaskValidationError("online tasks require agent_id")
        if data.sampling_rate is None or not (0.0 < data.sampling_rate <= 1.0):
            raise EvaluationTaskValidationError("online tasks require sampling_rate in (0, 1]")
        config["agent_id"] = str(data.agent_id)
        config["sampling_rate"] = data.sampling_rate
        config["max_traces_per_cycle"] = data.max_traces_per_cycle or _DEFAULT_MAX_TRACES_PER_CYCLE
    return config


def validate_task(task: EvaluationTaskModel) -> None:
    """Re-validate a persisted task against registry + config rules.

    Used on create, update, and enable — the enable path re-checks evaluator
    resolvability because the registry can change between create and enable
    (e.g. an optional dependency went missing at startup).
    """
    for name in task.evaluator_configs or []:
        if get_evaluator_class(name) is None:
            raise EvaluationTaskValidationError(f"Unknown evaluator: {name!r}")

    config = task.config or {}
    if task.task_type == TaskType.ONLINE.value:
        if not config.get("agent_id"):
            raise EvaluationTaskValidationError("online tasks require agent_id")
        rate = config.get("sampling_rate")
        if rate is None or not (0.0 < float(rate) <= 1.0):
            raise EvaluationTaskValidationError("online tasks require sampling_rate in (0, 1]")
    else:
        if not config.get("dataset_id"):
            raise EvaluationTaskValidationError("offline tasks require dataset_id")
        if config.get("answer_source") == "agent" and not config.get("agent_id"):
            raise EvaluationTaskValidationError('answer_source="agent" requires agent_id (the agent under test)')


class EvaluationTaskService:
    """CRUD and lifecycle operations for evaluation tasks."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_task(self, data, workspace_id: uuid.UUID) -> EvaluationTaskModel:
        """Persist a new task in ``active`` state."""
        config = build_task_config(data)
        task = EvaluationTaskModel(
            task_type=data.task_type,
            name=data.name,
            description=data.description,
            status=TaskStatus.ACTIVE.value,
            evaluator_configs=list(data.evaluators),
            config=config,
            workspace_id=workspace_id,
        )
        validate_task(task)
        self.db.add(task)
        await self.db.flush()
        await self.db.refresh(task)
        return task

    async def get_task(
        self,
        task_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> EvaluationTaskModel | None:
        """Fetch a non-deleted task scoped to the workspace."""
        result = await self.db.execute(
            select(EvaluationTaskModel).where(
                EvaluationTaskModel.id == task_id,
                EvaluationTaskModel.workspace_id == workspace_id,
                ~EvaluationTaskModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def _get_task_or_raise(self, task_id: uuid.UUID, workspace_id: uuid.UUID) -> EvaluationTaskModel:
        task = await self.get_task(task_id, workspace_id)
        if task is None:
            raise EvaluationTaskNotFoundError(str(task_id))
        return task

    async def list_tasks(
        self,
        workspace_id: uuid.UUID,
        task_type: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EvaluationTaskModel], int]:
        """List workspace tasks, newest first, with optional type/status filters."""
        conditions: list = [
            EvaluationTaskModel.workspace_id == workspace_id,
            ~EvaluationTaskModel.deleted,
        ]
        if task_type:
            conditions.append(EvaluationTaskModel.task_type == task_type)
        if status:
            conditions.append(EvaluationTaskModel.status == status)
        base_query = select(EvaluationTaskModel).where(*conditions)

        count_stmt = select(func.count()).select_from(base_query.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = base_query.order_by(EvaluationTaskModel.created_at.desc()).offset(offset).limit(page_size)
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total

    async def update_task(
        self,
        task_id: uuid.UUID,
        workspace_id: uuid.UUID,
        data,
    ) -> EvaluationTaskModel:
        """Apply a partial update, re-validating the merged configuration."""
        task = await self._get_task_or_raise(task_id, workspace_id)

        merged_data = {
            "task_type": task.task_type,
            "evaluators": data.evaluators if data.evaluators is not None else list(task.evaluator_configs or []),
            "dataset_id": data.dataset_id,
            "answer_source": data.answer_source,
            "threshold": data.threshold,
            "baseline_run_id": data.baseline_run_id,
            "regression_threshold": data.regression_threshold,
            "tags": data.tags,
            "agent_id": data.agent_id,
            "sampling_rate": data.sampling_rate,
            "max_traces_per_cycle": data.max_traces_per_cycle,
        }

        if data.name is not None:
            task.name = data.name
        if data.description is not None:
            task.description = data.description
        if data.status is not None:
            task.status = data.status

        config_changed = (
            any(
                merged_data[key] is not None
                for key in (
                    "dataset_id",
                    "answer_source",
                    "threshold",
                    "baseline_run_id",
                    "regression_threshold",
                    "tags",
                    "agent_id",
                    "sampling_rate",
                    "max_traces_per_cycle",
                )
            )
            or data.evaluators is not None
        )

        if config_changed:
            # Rebuild from the update payload layered over the stored config
            # so unset fields keep their stored values.
            stored = dict(task.config or {})
            merged = SimpleNamespace(
                task_type=task.task_type,
                evaluators=merged_data["evaluators"],
                dataset_id=_first_uuid(merged_data["dataset_id"], stored.get("dataset_id")),
                answer_source=merged_data["answer_source"] or stored.get("answer_source"),
                threshold=_first_value(merged_data["threshold"], stored.get("threshold")),
                baseline_run_id=_first_uuid(merged_data["baseline_run_id"], stored.get("baseline_run_id")),
                regression_threshold=_first_value(
                    merged_data["regression_threshold"], stored.get("regression_threshold")
                ),
                tags=_first_value(merged_data["tags"], stored.get("tags")),
                agent_id=_first_uuid(merged_data["agent_id"], stored.get("agent_id")),
                sampling_rate=_first_value(merged_data["sampling_rate"], stored.get("sampling_rate")),
                max_traces_per_cycle=_first_value(
                    merged_data["max_traces_per_cycle"], stored.get("max_traces_per_cycle")
                ),
            )

            task.evaluator_configs = list(merged.evaluators)
            task.config = build_task_config(merged)

        validate_task(task)
        await self.db.flush()
        await self.db.refresh(task)
        return task

    async def delete_task(self, task_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
        """Soft-delete a task. Produced runs and scores are retained."""
        task = await self._get_task_or_raise(task_id, workspace_id)
        task.deleted = True
        task.deleted_at = datetime.now(UTC)
        task.status = TaskStatus.DISABLED.value
        await self.db.flush()

    async def enable_task(self, task_id: uuid.UUID, workspace_id: uuid.UUID) -> EvaluationTaskModel:
        """Enable an online task after re-validating evaluators and config."""
        task = await self._get_task_or_raise(task_id, workspace_id)
        if task.task_type != TaskType.ONLINE.value:
            raise EvaluationTaskValidationError("enable/disable is only valid for online tasks")
        validate_task(task)
        task.status = TaskStatus.ACTIVE.value
        await self.db.flush()
        await self.db.refresh(task)
        return task

    async def disable_task(self, task_id: uuid.UUID, workspace_id: uuid.UUID) -> EvaluationTaskModel:
        """Disable an online task. Produced scores remain queryable."""
        task = await self._get_task_or_raise(task_id, workspace_id)
        if task.task_type != TaskType.ONLINE.value:
            raise EvaluationTaskValidationError("enable/disable is only valid for online tasks")
        task.status = TaskStatus.DISABLED.value
        await self.db.flush()
        await self.db.refresh(task)
        return task

    async def trigger_run(
        self,
        task_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> EvaluationRunModel:
        """Create a ``queued`` run for an offline task.

        The caller commits the request session and then hands the run to
        :meth:`runner.OfflineTaskRunner.run_in_background`, mirroring the
        synthesis-job flow.
        """
        task = await self._get_task_or_raise(task_id, workspace_id)
        if task.task_type != TaskType.OFFLINE.value:
            raise EvaluationTaskValidationError("runs can only be triggered for offline tasks")
        if task.status != TaskStatus.ACTIVE.value:
            raise EvaluationTaskValidationError("task is disabled")
        validate_task(task)

        run = EvaluationRunModel(
            dataset_id=uuid.UUID(str(task.config["dataset_id"])),
            task_id=task.id,
            evaluator_configs=list(task.evaluator_configs or []),
            status=RunStatus.PENDING.value,
            workspace_id=task.workspace_id,
        )
        self.db.add(run)
        await self.db.flush()
        return run

    async def list_task_runs(
        self,
        task_id: uuid.UUID,
        workspace_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EvaluationRunModel], int]:
        """List a task's runs, newest first. The task must be workspace-owned."""
        await self._get_task_or_raise(task_id, workspace_id)
        base_query = select(EvaluationRunModel).where(
            EvaluationRunModel.task_id == task_id,
            ~EvaluationRunModel.deleted,
        )
        count_stmt = select(func.count()).select_from(base_query.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = base_query.order_by(EvaluationRunModel.created_at.desc()).offset(offset).limit(page_size)
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total

    async def list_scores(
        self,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID | None = None,
        target_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        metric_name: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EvaluationTaskScoreModel], int]:
        """Query online scores with optional filters, oldest first."""
        conditions: list = [
            EvaluationTaskScoreModel.workspace_id == workspace_id,
            ~EvaluationTaskScoreModel.deleted,
        ]
        if task_id is not None:
            conditions.append(EvaluationTaskScoreModel.task_id == task_id)
        if target_id is not None:
            conditions.append(EvaluationTaskScoreModel.target_id == target_id)
        if session_id is not None:
            conditions.append(EvaluationTaskScoreModel.session_id == session_id)
        if metric_name is not None:
            conditions.append(EvaluationTaskScoreModel.metric_name == metric_name)
        if status is not None:
            conditions.append(EvaluationTaskScoreModel.status == status)
        base_query = select(EvaluationTaskScoreModel).where(*conditions)

        count_stmt = select(func.count()).select_from(base_query.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = base_query.order_by(EvaluationTaskScoreModel.created_at.asc()).offset(offset).limit(page_size)
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total


def _first_uuid(value: uuid.UUID | None, stored: str | None) -> uuid.UUID | None:
    """Prefer the incoming update value, fall back to the stored UUID string."""
    if value is not None:
        return value
    if stored:
        return uuid.UUID(str(stored))
    return None


def _first_value(value: Any, stored: Any) -> Any:
    """Prefer the incoming update value, fall back to the stored value."""
    return value if value is not None else stored
