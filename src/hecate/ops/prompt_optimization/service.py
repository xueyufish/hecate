"""Run-level service for prompt self-optimization (6.19).

Owns run creation (config validation + snapshot persistence) and the
query/cancel surface. Execution lives in :mod:`runner`, review decisions
in :mod:`review` — the split mirrors the evaluation task family
(``ops/evaluation/tasks``): the API commits a validated row, then hands
off to a background runner on a fresh session.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.models.agent import AgentModel
from hecate.models.evaluation import EvaluationDatasetModel, EvaluationDatasetVersionModel
from hecate.models.prompt import PromptModel, PromptVersionModel
from hecate.models.prompt_optimization import (
    BUDGET_PRESETS,
    DEFAULT_REFLECTION_MODEL,
    DEFAULT_SPLIT_RATIO,
    PromptOptimizationBudgetPreset,
    PromptOptimizationRunCreateSchema,
    PromptOptimizationRunModel,
    PromptOptimizationRunStatus,
    PromptOptimizationStopReason,
)
from hecate.ops.evaluation.engine import get_evaluator_class

logger = logging.getLogger(__name__)

ACTIVE_RUN_STATUSES = {
    PromptOptimizationRunStatus.CREATED.value,
    PromptOptimizationRunStatus.RUNNING.value,
}


class RunConflictError(Exception):
    """Another active run already exists for the target prompt."""


class PromptOptimizationService:
    """Create and query prompt optimization runs."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_run(
        self,
        data: PromptOptimizationRunCreateSchema,
        workspace_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
    ) -> PromptOptimizationRunModel:
        """Validate the run config and persist a ``created`` run row.

        Raises:
            LookupError: Prompt, agent, or dataset version not found in the
                workspace.
            ValueError: Config mismatch (dataset/version pair, evaluators,
                primary metric, split feasibility).
            RunConflictError: An active run already exists for the prompt.
        """
        prompt = await self._get_prompt(data.prompt_id, workspace_id)
        if prompt is None:
            msg = f"Prompt {data.prompt_id} not found"
            raise LookupError(msg)
        base_version = data.base_version or prompt.current_version
        base_version_row = await self._get_version(prompt.id, base_version, workspace_id)
        if base_version_row is None:
            msg = f"Prompt version {base_version} not found"
            raise LookupError(msg)

        agent = await self._get_agent(data.agent_id, workspace_id)
        if agent is None:
            msg = f"Agent {data.agent_id} not found"
            raise LookupError(msg)

        # Full input binding: the pinned dataset and its frozen version must
        # both be live in the workspace and belong to each other — a bare
        # version id alone never proves the samples are this workspace's.
        dataset = await self._get_dataset(data.dataset_id, workspace_id)
        if dataset is None:
            msg = f"Dataset {data.dataset_id} not found"
            raise LookupError(msg)
        version = await self._get_dataset_version(data.dataset_version_id, workspace_id)
        if version is None:
            msg = f"Dataset version {data.dataset_version_id} not found"
            raise LookupError(msg)
        if version.dataset_id != data.dataset_id:
            msg = "dataset_id does not match the pinned dataset version"
            raise ValueError(msg)
        if len(version.items or []) < 2:
            msg = "dataset version needs at least 2 items for a train/validation split"
            raise ValueError(msg)

        evaluator_names: list[str] = []
        for name in data.evaluator_configs:
            cls = get_evaluator_class(name)
            if cls is None:
                msg = f"unknown evaluator: {name}"
                raise ValueError(msg)
            evaluator_names.append(cls().name)
        if data.primary_metric not in evaluator_names:
            msg = (
                f"primary_metric {data.primary_metric!r} is not produced by the "
                f"configured evaluators ({evaluator_names})"
            )
            raise ValueError(msg)

        existing = await self._find_active_run(data.prompt_id, workspace_id)
        if existing is not None:
            raise RunConflictError(f"prompt {data.prompt_id} already has an active run {existing.id}")

        preset_value = (
            data.budget_preset.value
            if isinstance(data.budget_preset, PromptOptimizationBudgetPreset)
            else str(data.budget_preset)
        )
        preset = BUDGET_PRESETS[preset_value]
        max_rounds = data.max_rounds or preset["max_rounds"]
        mutation_call_limit = data.mutation_call_limit or preset["mutation_call_limit"]
        rollout_item_limit = data.rollout_item_limit or preset["rollout_item_limit"]

        run = PromptOptimizationRunModel(
            prompt_id=prompt.id,
            base_version=base_version,
            agent_id=agent.id,
            dataset_id=data.dataset_id,
            dataset_version_id=version.id,
            dataset_version_hash=version.content_hash,
            split_ratio=data.split_ratio if data.split_ratio is not None else DEFAULT_SPLIT_RATIO,
            evaluator_configs=data.evaluator_configs,
            primary_metric=data.primary_metric,
            min_improvement=data.min_improvement,
            max_regression=data.max_regression,
            budget_preset=str(data.budget_preset),
            max_rounds=max_rounds,
            mutation_call_limit=mutation_call_limit,
            rollout_item_limit=rollout_item_limit,
            strategy=data.strategy,
            strategy_params=data.strategy_params,
            reflection_model=data.reflection_model or DEFAULT_REFLECTION_MODEL,
            status=PromptOptimizationRunStatus.CREATED.value,
            created_by=created_by,
            workspace_id=workspace_id,
        )
        self.db.add(run)
        await self.db.flush()
        logger.info(
            "Created prompt optimization run %s (prompt %s v%s, agent %s, dataset version %s)",
            run.id,
            prompt.id,
            base_version,
            agent.id,
            version.id,
        )
        return run

    async def is_run_visible(self, run_id: uuid.UUID, workspace_id: uuid.UUID) -> bool:
        """Check ownership before authorization without reading run payloads."""
        result = await self.db.execute(
            select(PromptOptimizationRunModel.id).where(
                PromptOptimizationRunModel.id == run_id,
                PromptOptimizationRunModel.workspace_id == workspace_id,
                ~PromptOptimizationRunModel.deleted,
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_run(
        self, run_id: uuid.UUID, workspace_id: uuid.UUID, *, for_update: bool = False
    ) -> PromptOptimizationRunModel | None:
        """Resolve run visibility with a fresh read.

        ``populate_existing`` keeps the API precheck and the write paths
        from authorizing against an identity-map row another session has
        since deleted or moved.
        """
        stmt = (
            select(PromptOptimizationRunModel)
            .where(
                PromptOptimizationRunModel.id == run_id,
                PromptOptimizationRunModel.workspace_id == workspace_id,
                ~PromptOptimizationRunModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_runs(
        self,
        workspace_id: uuid.UUID,
        limit: int = 50,
        prompt_id: uuid.UUID | None = None,
    ) -> list[PromptOptimizationRunModel]:
        stmt = (
            select(PromptOptimizationRunModel)
            .where(
                PromptOptimizationRunModel.workspace_id == workspace_id,
                ~PromptOptimizationRunModel.deleted,
            )
            .order_by(PromptOptimizationRunModel.created_at.desc())
            .limit(limit)
            .execution_options(populate_existing=True)
        )
        if prompt_id is not None:
            stmt = stmt.where(PromptOptimizationRunModel.prompt_id == prompt_id)
        return list((await self.db.execute(stmt)).scalars().all())

    async def cancel_run(self, run_id: uuid.UUID, workspace_id: uuid.UUID) -> PromptOptimizationRunModel | None:
        """Request cancellation; the runner honors it at the next round boundary.

        A run that has not started yet is concluded immediately. A running
        run keeps executing its current round; the runner observes
        ``stop_reason`` on a fresh session read and finalizes.
        """
        run = await self.get_run(run_id, workspace_id, for_update=True)
        if run is None:
            return None
        if run.status == PromptOptimizationRunStatus.CREATED.value:
            run.status = PromptOptimizationRunStatus.CONCLUDED.value
            run.stop_reason = PromptOptimizationStopReason.CANCELLED.value
            run.completed_at = run.updated_at
        elif run.status == PromptOptimizationRunStatus.RUNNING.value:
            run.stop_reason = PromptOptimizationStopReason.CANCELLED.value
        else:
            return run
        await self.db.flush()
        return run

    async def _get_prompt(self, prompt_id: uuid.UUID, workspace_id: uuid.UUID) -> PromptModel | None:
        result = await self.db.execute(
            select(PromptModel)
            .where(
                PromptModel.id == prompt_id,
                PromptModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _get_version(
        self,
        prompt_id: uuid.UUID,
        version: int,
        workspace_id: uuid.UUID,
    ) -> PromptVersionModel | None:
        """A live base version bound to this workspace's prompt."""
        result = await self.db.execute(
            select(PromptVersionModel)
            .where(
                PromptVersionModel.prompt_id == prompt_id,
                PromptVersionModel.version == version,
                PromptVersionModel.workspace_id == workspace_id,
                ~PromptVersionModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _get_dataset(self, dataset_id: uuid.UUID, workspace_id: uuid.UUID) -> EvaluationDatasetModel | None:
        result = await self.db.execute(
            select(EvaluationDatasetModel)
            .where(
                EvaluationDatasetModel.id == dataset_id,
                EvaluationDatasetModel.workspace_id == workspace_id,
                ~EvaluationDatasetModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _get_dataset_version(
        self,
        version_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> EvaluationDatasetVersionModel | None:
        result = await self.db.execute(
            select(EvaluationDatasetVersionModel)
            .where(
                EvaluationDatasetVersionModel.id == version_id,
                EvaluationDatasetVersionModel.workspace_id == workspace_id,
                ~EvaluationDatasetVersionModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _get_agent(self, agent_id: uuid.UUID, workspace_id: uuid.UUID) -> AgentModel | None:
        result = await self.db.execute(
            select(AgentModel)
            .where(
                AgentModel.id == agent_id,
                AgentModel.workspace_id == workspace_id,
                ~AgentModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def _find_active_run(
        self,
        prompt_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> PromptOptimizationRunModel | None:
        stmt = select(PromptOptimizationRunModel).where(
            PromptOptimizationRunModel.prompt_id == prompt_id,
            PromptOptimizationRunModel.workspace_id == workspace_id,
            PromptOptimizationRunModel.status.in_(ACTIVE_RUN_STATUSES),
            ~PromptOptimizationRunModel.deleted,
        )
        return (await self.db.execute(stmt)).scalars().first()


def feature_enabled() -> bool:
    """Flag gate for API surfaces (design: deployments opt in explicitly)."""
    return bool(settings.PROMPT_OPTIMIZATION_ENABLED)
