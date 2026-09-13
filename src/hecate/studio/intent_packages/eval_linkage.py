"""Intent package ↔ evaluation linkage (6.49, reuses the 7.2 family).

Thin integration between intent package versions and the 7.2c evaluation
machinery:

- **generate_eval_dataset** — turn a published version's frozen samples
  into an evaluation dataset (``query`` = utterance, ``expected_answer`` =
  category name, per-item ``metadata.category``). ``heldout_ratio``
  selects a deterministic per-category fraction of samples as eval items.
  Note the honest v1 limitation documented on the method: the recognizer's
  evidence still carries the sampled utterances, so v1 accuracy is a
  self-consistency measure — true held-out scoring needs evidence
  exclusion at runtime (follow-up).
- **trigger_recognition_run** — create (or reuse) the offline evaluation
  task with ``answer_source='intent'`` and start a background run.
- **latest_recognition_result** — read back the newest completed run for a
  version as the deterministic ``linked_result`` the publish gate
  consumes: ``{"run_id", "pass_rate", "per_category"}``. Only
  deterministic scores participate; LLM-judge/human scores are invisible
  to the gate by construction.
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    EvaluationTaskModel,
    RunStatus,
    TaskType,
)
from hecate.models.intent_package import IntentPackageVersionModel

logger = logging.getLogger(__name__)

INTENT_TAG = "intent-package"


class IntentEvalLinkageService:
    """Link intent package versions to recognition-accuracy evaluation.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # 7.1 — dataset generation
    # ------------------------------------------------------------------

    async def generate_eval_dataset(
        self,
        package_id: UUID,
        version_id: UUID,
        workspace_id: UUID,
        heldout_ratio: float = 1.0,
        created_by: UUID | None = None,
    ) -> EvaluationDatasetModel:
        """Create an evaluation dataset from a published version's samples.

        One item per selected sample: ``query`` = utterance,
        ``expected_answer`` = category name. The per-category sample
        selection is deterministic (positional stride) so the same version
        yields the same dataset content.

        Args:
            heldout_ratio: Fraction of each category's samples to include
                (0 < ratio <= 1; default 1.0 = all).
        """
        if not 0 < heldout_ratio <= 1.0:
            raise ValueError("heldout_ratio must be in (0, 1]")

        version = await self._get_published_version(package_id, version_id, workspace_id)
        dataset = EvaluationDatasetModel(
            name=f"intent:{version.name}:{version.content_hash[:12]}",
            description=f"Recognition-accuracy dataset for intent package version "
            f"{version.name} (package {package_id})",
            workspace_id=workspace_id,
        )
        self.db.add(dataset)
        await self.db.flush()

        categories = (version.content or {}).get("categories", [])
        items_added = 0
        for category in categories:
            name = str(category.get("name"))
            samples = category.get("samples") or []
            take = max(1, round(len(samples) * heldout_ratio)) if samples else 0
            # Deterministic stride selection.
            stride = len(samples) / take if take else 0
            for index in range(take):
                sample = samples[round(index * stride)] if stride else None
                if sample is None:
                    continue
                self.db.add(
                    EvaluationItemModel(
                        dataset_id=dataset.id,
                        query=str(sample.get("utterance") or ""),
                        expected_answer=name,
                        metadata_={
                            "intent_package_id": str(package_id),
                            "intent_package_version_id": str(version.id),
                            "category": name,
                        },
                        tags=[INTENT_TAG],
                    )
                )
                items_added += 1
        await self.db.flush()
        logger.info(
            "Generated recognition dataset %s from intent version %s (%d items)",
            dataset.id,
            version.id,
            items_added,
        )
        return dataset

    # ------------------------------------------------------------------
    # 7.2 — run trigger (7.2c task machinery)
    # ------------------------------------------------------------------

    async def trigger_recognition_run(
        self,
        package_id: UUID,
        version_id: UUID,
        workspace_id: UUID,
        created_by: UUID | None = None,
        heldout_ratio: float = 1.0,
    ) -> EvaluationRunModel:
        """Create the offline task (if absent) and start a background run.

        The task carries ``answer_source='intent'`` with the package
        reference in its config; the dataset is generated on demand from
        the version's frozen samples.
        """
        task = await self._get_or_create_task(package_id, version_id, workspace_id, created_by)
        dataset = await self.generate_eval_dataset(
            package_id, version_id, workspace_id, heldout_ratio=heldout_ratio, created_by=created_by
        )

        run = EvaluationRunModel(
            dataset_id=dataset.id,
            task_id=task.id,
            status=RunStatus.PENDING.value,
            evaluator_configs=["exact_match"],
        )
        self.db.add(run)
        await self.db.flush()

        from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner

        await OfflineTaskRunner(self.db).run_in_background(run.id, task.id)
        logger.info("Triggered recognition run %s for intent version %s", run.id, version_id)
        return run

    # ------------------------------------------------------------------
    # 7.3 — gate result read-back
    # ------------------------------------------------------------------

    async def latest_recognition_result(
        self,
        package_id: UUID,
        version_id: UUID,
        workspace_id: UUID,
    ) -> dict | None:
        """The newest completed recognition run for a version, gate-shaped.

        Returns ``{"run_id", "pass_rate", "per_category"}`` computed from
        deterministic exact-match scores only, or ``None`` when no
        completed run exists.
        """
        task = await self._find_task(package_id, version_id, workspace_id)
        if task is None:
            return None
        rows = await self.db.execute(
            select(EvaluationRunModel)
            .where(
                EvaluationRunModel.task_id == task.id,
                EvaluationRunModel.status == RunStatus.COMPLETED.value,
                ~EvaluationRunModel.deleted,
            )
            .order_by(EvaluationRunModel.created_at.desc(), EvaluationRunModel.id.desc())
            .limit(1)
        )
        run = rows.scalar_one_or_none()
        if run is None:
            return None

        stmt = (
            select(
                EvaluationItemModel.metadata_,
                EvaluationScoreModel.value,
            )
            .join(
                EvaluationScoreModel,
                EvaluationScoreModel.item_id == EvaluationItemModel.id,
            )
            .where(
                EvaluationScoreModel.run_id == run.id,
                EvaluationScoreModel.source == "deterministic",
                EvaluationScoreModel.value >= 0,
            )
        )
        rows = (await self.db.execute(stmt)).all()
        if not rows:
            return None

        per_category: dict[str, list[float]] = {}
        for metadata, value in rows:
            category = str((metadata or {}).get("category") or "unknown")
            per_category.setdefault(category, []).append(float(value))
        all_values = [value for values in per_category.values() for value in values]
        return {
            "run_id": str(run.id),
            "pass_rate": sum(1 for v in all_values if v >= 1.0) / len(all_values),
            "per_category": {
                name: sum(1 for v in values if v >= 1.0) / len(values) for name, values in sorted(per_category.items())
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_published_version(
        self,
        package_id: UUID,
        version_id: UUID,
        workspace_id: UUID,
    ) -> IntentPackageVersionModel:
        rows = await self.db.execute(
            select(IntentPackageVersionModel).where(
                IntentPackageVersionModel.id == version_id,
                IntentPackageVersionModel.package_id == package_id,
                IntentPackageVersionModel.workspace_id == workspace_id,
                IntentPackageVersionModel.published_at.is_not(None),
                ~IntentPackageVersionModel.deleted,
            )
        )
        version = rows.scalar_one_or_none()
        if version is None:
            raise ValueError(f"Published version {version_id} not found on package {package_id}")
        return version

    async def _get_or_create_task(
        self,
        package_id: UUID,
        version_id: UUID,
        workspace_id: UUID,
        created_by: UUID | None,
    ) -> EvaluationTaskModel:
        """One reusable offline task per (package, version) pair."""
        existing = await self._find_task(package_id, version_id, workspace_id)
        if existing is not None:
            return existing
        task = EvaluationTaskModel(
            name=f"intent-recognition:{package_id}:{version_id}",
            task_type=TaskType.OFFLINE.value,
            evaluator_configs=["exact_match"],
            config={
                "answer_source": "intent",
                "intent_package": {
                    "package_id": str(package_id),
                    "version_id": str(version_id),
                },
                "max_total_executions": 1000,
            },
            workspace_id=workspace_id,
        )
        self.db.add(task)
        await self.db.flush()
        return task

    async def _find_task(
        self,
        package_id: UUID,
        version_id: UUID,
        workspace_id: UUID,
    ) -> EvaluationTaskModel | None:
        stmt = select(EvaluationTaskModel).where(
            EvaluationTaskModel.name == f"intent-recognition:{package_id}:{version_id}",
            EvaluationTaskModel.workspace_id == workspace_id,
            ~EvaluationTaskModel.deleted,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()


__all__ = ["IntentEvalLinkageService", "INTENT_TAG"]
