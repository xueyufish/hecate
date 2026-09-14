"""Tests for the intent package ↔ evaluation linkage (6.49 × 7.2c)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    RunStatus,
)
from hecate.models.intent_package import (
    IntentCategoryCreateSchema,
    IntentPackageVersionCreateSchema,
)
from hecate.studio.intent_packages.eval_linkage import IntentEvalLinkageService
from hecate.studio.intent_packages.service import IntentPackageService

WS = uuid.UUID("00000000-0000-0000-0000-000000000010")


async def _seed_published(db_session, samples_per_category: int = 4):
    """A published package with two categories and a known content hash."""
    service = IntentPackageService(db_session)
    package = await service.create_package(name="pkg", description=None, workspace_id=WS)
    for name in ("billing", "tech"):
        category = await service.add_category(
            package.id, WS, IntentCategoryCreateSchema(name=name, description=f"{name} desc")
        )
        for i in range(samples_per_category):
            await service.add_sample(package.id, category.id, WS, f"{name} sample {i}")
    version = await service.create_version(package.id, WS, IntentPackageVersionCreateSchema(name="v1"), created_by=None)
    await service.publish_version(package.id, version.id, WS, None, linked_result=None)
    return service, package, version


async def test_generate_eval_dataset_from_published_version(db_session):
    _service, package, version = await _seed_published(db_session)
    linkage = IntentEvalLinkageService(db_session)

    dataset = await linkage.generate_eval_dataset(package.id, version.id, WS, heldout_ratio=1.0)

    items = (
        (await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset.id)))
        .scalars()
        .all()
    )
    assert len(items) == 8  # 2 categories × 4 samples
    assert all(item.expected_answer in ("billing", "tech") for item in items)
    assert all(item.metadata_.get("intent_package_version_id") == str(version.id) for item in items)
    assert all(item.tags == ["intent-package"] for item in items)


async def test_heldout_ratio_selects_deterministic_subset(db_session):
    _service, package, version = await _seed_published(db_session, samples_per_category=4)
    linkage = IntentEvalLinkageService(db_session)

    dataset_a = await linkage.generate_eval_dataset(package.id, version.id, WS, heldout_ratio=0.5)
    dataset_b = await linkage.generate_eval_dataset(package.id, version.id, WS, heldout_ratio=0.5)

    async def _count(dataset: EvaluationDatasetModel) -> int:
        rows = await db_session.execute(
            select(func.count()).select_from(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset.id)
        )
        return rows.scalar_one()

    assert await _count(dataset_a) == await _count(dataset_b) == 4


async def test_generate_rejects_unpublished_version(db_session):
    service, package, _version = await _seed_published(db_session)
    await service.add_sample(
        package.id,
        (await service.list_categories(package.id, WS))[0].id,
        WS,
        "another utterance",
    )
    v2 = await service.create_version(package.id, WS, IntentPackageVersionCreateSchema(name="v2"), created_by=None)
    linkage = IntentEvalLinkageService(db_session)
    with pytest.raises(ValueError, match="Published version"):
        await linkage.generate_eval_dataset(package.id, v2.id, WS)


async def test_latest_recognition_result_gate_shape(db_session: AsyncSession):
    """Scores recorded for a run read back as the gate's linked_result."""
    _service, package, version = await _seed_published(db_session)
    linkage = IntentEvalLinkageService(db_session)
    dataset = await linkage.generate_eval_dataset(package.id, version.id, WS)

    task = await linkage._get_or_create_task(package.id, version.id, WS, None)
    run = EvaluationRunModel(
        dataset_id=dataset.id,
        task_id=task.id,
        status=RunStatus.PENDING.value,
        evaluator_configs=["exact_match"],
    )
    db_session.add(run)
    await db_session.flush()

    items = (
        (await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset.id)))
        .scalars()
        .all()
    )
    for index, item in enumerate(items):
        db_session.add(
            EvaluationScoreModel(
                run_id=run.id,
                item_id=item.id,
                metric_name="exact_match",
                value=1.0 if index % 4 else 0.0,  # 6/8 pass
                source="deterministic",
            )
        )
    run.status = RunStatus.COMPLETED.value
    run.completed_at = datetime.now(UTC)
    await db_session.flush()

    result = await linkage.latest_recognition_result(package.id, version.id, WS)
    assert result is not None
    assert result["run_id"] == str(run.id)
    assert result["pass_rate"] == pytest.approx(0.75)
    assert set(result["per_category"]) == {"billing", "tech"}


async def test_latest_recognition_result_none_without_runs(db_session):
    _service, package, version = await _seed_published(db_session)
    linkage = IntentEvalLinkageService(db_session)
    assert await linkage.latest_recognition_result(package.id, version.id, WS) is None
