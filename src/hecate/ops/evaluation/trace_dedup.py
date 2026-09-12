"""Cross-path trace attribution for dataset materialization.

Both dataset backflow paths attribute materialized items to their source
trace via a ``metadata_`` key: the automated rule path writes
``metadata_.backflow.trace_id`` (7.2d), the human-annotation path writes
``metadata_.annotation.trace_id`` (7.4). Idempotency is dataset-wide — a
trace materializes into a dataset at most once regardless of which path
brought it in — so the dedup scan reads both keys.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import EvaluationItemModel

_ATTRIBUTION_KEYS = ("backflow", "annotation")


def item_trace_id(metadata: Any) -> str | None:
    """The source trace id attributed in a materialized item's metadata."""
    if not isinstance(metadata, dict):
        return None
    for key in _ATTRIBUTION_KEYS:
        entry = metadata.get(key)
        if isinstance(entry, dict) and entry.get("trace_id"):
            return str(entry["trace_id"])
    return None


async def dataset_trace_ids(db: AsyncSession, dataset_id: uuid.UUID) -> set[str]:
    """Trace ids already materialized into the dataset, via either path."""
    rows = await db.execute(
        select(EvaluationItemModel.metadata_).where(
            EvaluationItemModel.dataset_id == dataset_id,
            ~EvaluationItemModel.deleted,
        )
    )
    return {tid for (metadata,) in rows.all() if (tid := item_trace_id(metadata)) is not None}
