"""Partial unique index semantics on evaluation_task_scores (7.4/7.4a).

The idempotency index only covers automated rows (``task_id IS NOT NULL``);
human annotation rows are exempt and may repeat the same
``(target_type, target_id, metric_name)``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import EvaluationTaskScoreModel

_WS = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


async def test_duplicate_automated_score_rejected(db_session: AsyncSession) -> None:
    """Same (task, target, metric) twice → unique index violation."""
    task_id, target_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        EvaluationTaskScoreModel(
            task_id=task_id, target_id=target_id, metric_name="helpfulness", value=0.5, workspace_id=_WS
        )
    )
    await db_session.flush()
    db_session.add(
        EvaluationTaskScoreModel(
            task_id=task_id, target_id=target_id, metric_name="helpfulness", value=0.9, workspace_id=_WS
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_human_rows_exempt_from_task_idempotency(db_session: AsyncSession) -> None:
    """Multiple human rows (task_id NULL) for one target+metric coexist."""
    target_id = uuid.uuid4()
    for value in (0.5, 0.9):
        db_session.add(
            EvaluationTaskScoreModel(
                task_id=None,
                target_id=target_id,
                metric_name="helpfulness",
                value=value,
                source="human",
                workspace_id=_WS,
            )
        )
    await db_session.flush()  # no integrity error
    from sqlalchemy import select

    rows = (
        (
            await db_session.execute(
                select(EvaluationTaskScoreModel).where(EvaluationTaskScoreModel.target_id == target_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
