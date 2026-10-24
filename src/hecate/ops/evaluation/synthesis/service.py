"""Dataset synthesis service — façade orchestrating strategy + filters + persistence.

Public surface:

- :class:`DatasetSynthesisService` — single entry point for synchronous
  synthesis (``count <= 20``). Builds the strategy from the request,
  runs it, applies the three-filter pipeline, then persists surviving
  items into a newly-created dataset. Returns a count summary.

Async-job execution lives in :mod:`hecate.ops.evaluation.synthesis.job`;
that module imports this service for the actual synthesis work.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.dataset_synthesis_job import DatasetSynthesisJobModel
from hecate.models.evaluation import EvaluationDatasetModel, EvaluationItemModel
from hecate.ops.evaluation.dataset_service import EvaluationDatasetService
from hecate.ops.evaluation.synthesis import CandidateItem
from hecate.ops.evaluation.synthesis.filters import (
    DLPFilter,
    EmbeddingDedupeFilter,
    QualityFilter,
)
from hecate.ops.evaluation.synthesis.strategies import (
    AdversarialStrategy,
    EvolutionStrategy,
    GenerationStrategy,
)
from hecate.ops.evaluation.types import LLMConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SeedDatasetUnavailableError(Exception):
    """Seed dataset is missing, soft-deleted, or outside the target workspace.

    Deliberately not a ``ValueError`` so the API layer's invalid-request
    (400) mapping cannot swallow it: the REST boundary translates it into
    the dataset endpoints' constant 404, and the async job runner into an
    explicit job failure.
    """


@dataclass
class SynthesisRequest:
    """Internal request object built from the API Pydantic schema."""

    seed_dataset_id: uuid.UUID | None
    topic: str | None
    strategy: str
    adversarial_intent: str | None
    count: int
    target_dataset_name: str
    target_dataset_description: str | None
    llm_config: LLMConfig | None
    quality_threshold: float | None
    workspace_id: uuid.UUID


@dataclass
class SynthesisResult:
    """Final outcome of a synthesis run."""

    target_dataset_id: uuid.UUID
    items_generated: int
    items_filtered: int
    duration_ms: int
    filter_breakdown: dict[str, int]


class DatasetSynthesisService:
    """Façade for AI dataset synthesis.

    Args:
        db: Async SQLAlchemy session — owned by the caller. The service
            does not manage the session's lifecycle (commit/rollback).
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def synthesize(
        self,
        request: SynthesisRequest,
    ) -> SynthesisResult:
        """Run the full pipeline: strategy → filter pipeline → persist.

        Validates request shape, builds the strategy, applies the three
        filters in order, then writes surviving candidates to a freshly
        created dataset.
        """
        start = time.perf_counter()
        if request.strategy == "adversarial" and not request.adversarial_intent:
            raise ValueError("adversarial strategy requires adversarial_intent")
        if not request.seed_dataset_id and not request.topic and request.strategy != "adversarial":
            raise ValueError("Provide at least one of: seed_dataset_id, topic (for non-adversarial strategies)")

        # Load and validate all seed material before creating a target:
        # the async failure handler commits the current transaction.
        seeds: list[Any] = []
        if request.seed_dataset_id:
            seeds = await self._load_seeds(request.seed_dataset_id, request.workspace_id)

        ds_svc = EvaluationDatasetService(self.db)
        target_ds = await ds_svc.create_dataset(
            name=request.target_dataset_name,
            description=request.target_dataset_description,
            workspace_id=request.workspace_id,
        )

        strategy = self._build_strategy(request.strategy, request.llm_config)
        candidates = await strategy.generate(
            seeds=seeds,
            count=request.count,
            topic=request.topic,
            intent=request.adversarial_intent,
        )
        items_generated = len(candidates)
        items_filtered_total = 0
        breakdown: dict[str, int] = {}

        # 4. Filter pipeline
        # 4a. Dedupe — load existing queries of target dataset (empty at this
        # point but the API allows future re-synthesis into the same dataset).
        dedupe = EmbeddingDedupeFilter()
        dedupe_result = await dedupe.filter(candidates, target_dataset_id=str(target_ds.id))
        items_filtered_total += len(dedupe_result.dropped)
        breakdown["dedupe"] = len(dedupe_result.dropped)
        candidates = dedupe_result.passed

        # 4b. Quality
        qf = QualityFilter(
            threshold=request.quality_threshold or QualityFilter.DEFAULT_THRESHOLD,
            llm_config=request.llm_config,
        )
        qf_result = await qf.filter(candidates)
        items_filtered_total += len(qf_result.dropped)
        breakdown["quality"] = len(qf_result.dropped)
        candidates = qf_result.passed

        # 4c. DLP
        dlp = DLPFilter(db=self.db)
        dlp_result = await dlp.filter(candidates)
        items_filtered_total += len(dlp_result.dropped)
        breakdown["dlp"] = len(dlp_result.dropped)
        candidates = dlp_result.passed

        # 5. Persist
        if candidates:
            item_dicts: list[dict] = [
                {
                    "query": c.query,
                    "expected_answer": c.expected_answer,
                    "context": c.context,
                    "metadata": {},
                    "tags": c.tags(),
                }
                for c in candidates
            ]
            await ds_svc.add_items(target_ds.id, item_dicts)

        await self.db.flush()
        duration_ms = int((time.perf_counter() - start) * 1000)
        return SynthesisResult(
            target_dataset_id=target_ds.id,
            items_generated=items_generated,
            items_filtered=items_filtered_total,
            duration_ms=duration_ms,
            filter_breakdown=breakdown,
        )

    def _build_strategy(self, name: str, llm_config: LLMConfig | None):
        if name == "generation":
            return GenerationStrategy(llm_config=llm_config)
        if name == "evolution":
            return EvolutionStrategy(llm_config=llm_config)
        if name == "adversarial":
            return AdversarialStrategy(llm_config=llm_config)
        raise ValueError(f"Unknown strategy: {name!r}")

    async def validate_seed(
        self,
        seed_dataset_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> EvaluationDatasetModel:
        """Resolve the seed dataset or raise :class:`SeedDatasetUnavailableError`.

        Shared by the REST boundary (seed visibility, checked before the
        role floor) and the pipeline (execution-time re-validation) so
        both enforce the same existence, soft-delete, and target-workspace
        membership conditions.
        """
        result = await self.db.execute(
            select(EvaluationDatasetModel).where(
                EvaluationDatasetModel.id == seed_dataset_id,
                ~EvaluationDatasetModel.deleted,
            )
        )
        ds = result.scalar_one_or_none()
        if ds is None or ds.workspace_id != workspace_id:
            raise SeedDatasetUnavailableError("seed dataset is missing, soft-deleted, or outside the target workspace")
        return ds

    async def _load_seeds(self, seed_dataset_id: uuid.UUID, workspace_id: uuid.UUID) -> list[Any]:
        """Read (query, expected_answer) pairs from a validated seed dataset.

        Eligibility is anchored to the parent dataset row — the items'
        own historical ``workspace_id`` values (including the zero UUID)
        never disqualify a legitimate seed.
        """
        await self.validate_seed(seed_dataset_id, workspace_id)
        result = await self.db.execute(
            EvaluationItemModel.__table__.select().where(
                EvaluationItemModel.dataset_id == seed_dataset_id,
                ~EvaluationItemModel.deleted,
            )
        )
        rows = result.fetchall()
        out: list[Any] = []
        for row in rows:
            out.append(
                CandidateItem(
                    query=row.query,
                    expected_answer=row.expected_answer,
                    seed_item_id=str(row.id),
                )
            )
        return out


# Re-export the model for convenience in the job module.
__all__ = [
    "CandidateItem",
    "DatasetSynthesisService",
    "SeedDatasetUnavailableError",
    "SynthesisRequest",
    "SynthesisResult",
    "DatasetSynthesisJobModel",
    "EvaluationDatasetModel",
]
