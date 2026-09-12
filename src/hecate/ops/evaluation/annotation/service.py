"""Annotation queue service — CRUD, intake, review workflow, dataset backflow.

Queues are reviewer worklists over completed production root traces; items
move through ``pending → claimed → completed | skipped``. Submitted
annotations land in ``evaluation_task_scores`` with ``source="human"`` and
``task_id=NULL`` — coexisting with automated rows so calibration can pair
them. Reviewers see the same read-side trace projection the online scorer
consumes (``tasks.trace_input.build_eval_input``), pre-filled with the
latest automated scores as read-only suggestions.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    AnnotationQueueItemModel,
    AnnotationQueueModel,
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    QueueItemStatus,
    TaskScoreStatus,
)
from hecate.models.session import SessionModel
from hecate.ops.evaluation.dataset_service import EvaluationDatasetService
from hecate.ops.evaluation.tasks.trace_input import build_eval_input

logger = logging.getLogger(__name__)

_MAX_BULK_ITEMS = 100
_DEFAULT_FROM_TASK_LIMIT = 50
_MAX_FROM_TASK_LIMIT = 500
_JUSTIFICATION_MAX = 5000

_HUMAN_SOURCE = "human"
_NUMERIC_TOLERANCE = 0.1

_SUGGESTED_REASON_CODES = (
    "judge_wrong_fact",
    "judge_too_harsh",
    "judge_too_lenient",
    "missing_context",
    "labeling_error",
    "other",
)


class AnnotationQueueNotFoundError(LookupError):
    """Queue with the given id does not exist in the workspace."""


class AnnotationQueueItemNotFoundError(LookupError):
    """Queue item with the given id does not exist in the workspace."""


class AnnotationValidationError(ValueError):
    """Queue/annotation payload violates a validation rule."""


class AnnotationItemStateError(ValueError):
    """Item is not in a state that allows the requested transition."""


def _validate_metric_defs(defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate metric definitions and return them normalized.

    Rules: names unique within the queue; ``numeric`` needs min/max
    (``min <= max``); ``categorical`` needs a non-empty categories list;
    ``boolean`` takes none of those.
    """
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not defs:
        raise AnnotationValidationError("queues require at least one metric definition")
    for raw in defs:
        name = str(raw.get("name") or "").strip()
        data_type = raw.get("data_type")
        if not name:
            raise AnnotationValidationError("metric_defs entries require a name")
        if name in seen:
            raise AnnotationValidationError(f"duplicate metric name in metric_defs: {name!r}")
        seen.add(name)
        if data_type not in ("numeric", "categorical", "boolean"):
            raise AnnotationValidationError(f"metric {name!r}: unknown data_type {data_type!r}")
        entry: dict[str, Any] = {"name": name, "data_type": data_type}
        if data_type == "numeric":
            minimum, maximum = raw.get("min"), raw.get("max")
            if minimum is None or maximum is None:
                raise AnnotationValidationError(f"metric {name!r}: numeric requires min and max")
            if float(minimum) > float(maximum):
                raise AnnotationValidationError(f"metric {name!r}: min must be <= max")
            entry["min"] = float(minimum)
            entry["max"] = float(maximum)
        elif data_type == "categorical":
            categories = [str(c) for c in (raw.get("categories") or []) if str(c).strip()]
            if not categories:
                raise AnnotationValidationError(f"metric {name!r}: categorical requires categories")
            entry["categories"] = categories
        normalized.append(entry)
    return normalized


class AnnotationService:
    """CRUD and review workflow for annotation queues."""

    def __init__(self, db: AsyncSession, event_store: Any | None = None) -> None:
        self.db = db
        self._event_store = event_store

    # -- queue CRUD ---------------------------------------------------------

    async def create_queue(self, data: Any, workspace_id: uuid.UUID) -> AnnotationQueueModel:
        """Persist a new annotation queue with validated metric definitions."""
        metric_defs = _validate_metric_defs([d if isinstance(d, dict) else d.model_dump() for d in data.metric_defs])
        queue = AnnotationQueueModel(
            name=data.name,
            description=data.description,
            instructions=data.instructions,
            metric_defs=metric_defs,
            assigned_user_ids=[str(u) for u in (data.assigned_user_ids or [])],
            workspace_id=workspace_id,
        )
        self.db.add(queue)
        await self.db.flush()
        await self.db.refresh(queue)
        return queue

    async def get_queue(self, queue_id: uuid.UUID, workspace_id: uuid.UUID) -> AnnotationQueueModel | None:
        result = await self.db.execute(
            select(AnnotationQueueModel).where(
                AnnotationQueueModel.id == queue_id,
                AnnotationQueueModel.workspace_id == workspace_id,
                ~AnnotationQueueModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def _get_queue_or_raise(self, queue_id: uuid.UUID, workspace_id: uuid.UUID) -> AnnotationQueueModel:
        queue = await self.get_queue(queue_id, workspace_id)
        if queue is None:
            raise AnnotationQueueNotFoundError(str(queue_id))
        return queue

    async def list_queues(
        self,
        workspace_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AnnotationQueueModel], int]:
        """List workspace queues, newest first."""
        conditions = [
            AnnotationQueueModel.workspace_id == workspace_id,
            ~AnnotationQueueModel.deleted,
        ]
        base_query = select(AnnotationQueueModel).where(*conditions)
        total = (await self.db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
        offset = (page - 1) * page_size
        result = await self.db.execute(
            base_query.order_by(AnnotationQueueModel.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), int(total)

    async def queue_item_counts(self, queue_ids: list[uuid.UUID]) -> dict[str, dict[str, int]]:
        """Per-queue item counts grouped by status, for the queue list view."""
        if not queue_ids:
            return {}
        rows = await self.db.execute(
            select(
                AnnotationQueueItemModel.queue_id,
                AnnotationQueueItemModel.status,
                func.count(),
            )
            .where(
                AnnotationQueueItemModel.queue_id.in_(queue_ids),
                ~AnnotationQueueItemModel.deleted,
            )
            .group_by(AnnotationQueueItemModel.queue_id, AnnotationQueueItemModel.status)
        )
        counts: dict[str, dict[str, int]] = {}
        for queue_id, status, count in rows.all():
            counts.setdefault(str(queue_id), {})[str(status)] = int(count)
        return counts

    async def update_queue(
        self,
        queue_id: uuid.UUID,
        workspace_id: uuid.UUID,
        data: Any,
    ) -> AnnotationQueueModel:
        """Apply a partial update; metric definitions are re-validated."""
        queue = await self._get_queue_or_raise(queue_id, workspace_id)
        if data.name is not None:
            queue.name = data.name
        if data.description is not None:
            queue.description = data.description
        if data.instructions is not None:
            queue.instructions = data.instructions
        if data.assigned_user_ids is not None:
            queue.assigned_user_ids = list(data.assigned_user_ids)
        if data.metric_defs is not None:
            queue.metric_defs = _validate_metric_defs([d.model_dump() for d in data.metric_defs])
        await self.db.flush()
        await self.db.refresh(queue)
        return queue

    async def delete_queue(self, queue_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
        """Soft-delete a queue. Items and produced scores are retained."""
        queue = await self._get_queue_or_raise(queue_id, workspace_id)
        queue.deleted = True
        queue.deleted_at = datetime.now(UTC)
        await self.db.flush()

    # -- intake ---------------------------------------------------------------

    async def add_items(
        self,
        queue_id: uuid.UUID,
        workspace_id: uuid.UUID,
        target_ids: list[uuid.UUID],
        added_by: uuid.UUID,
    ) -> dict[str, Any]:
        """Enqueue completed root traces; duplicates in the queue are no-ops.

        Returns ``{created, already_present, rejected}`` where ``rejected``
        lists target ids that are not workspace-visible completed root
        traces.
        """
        if len(target_ids) > _MAX_BULK_ITEMS:
            raise AnnotationValidationError(f"bulk add is capped at {_MAX_BULK_ITEMS} items per call")
        queue = await self._get_queue_or_raise(queue_id, workspace_id)

        existing_ids = await self._queue_target_ids(queue.id)
        created, already_present, rejected = 0, [], []
        for target_id in target_ids:
            if target_id in existing_ids:
                already_present.append(target_id)
                continue
            trace = await self._workspace_trace(target_id, workspace_id)
            if trace is None:
                rejected.append({"target_id": str(target_id), "reason": "not a workspace-scoped completed root trace"})
                continue
            self.db.add(
                AnnotationQueueItemModel(
                    queue_id=queue.id,
                    target_type="trace",
                    target_id=target_id,
                    status=QueueItemStatus.PENDING.value,
                    added_by=added_by,
                    workspace_id=workspace_id,
                )
            )
            existing_ids.add(target_id)
            created += 1
        await self.db.flush()
        return {"created": created, "already_present": already_present, "rejected": rejected}

    async def add_items_from_task(
        self,
        queue_id: uuid.UUID,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID,
        metric_name: str | None = None,
        min_score: float | None = None,
        max_score: float | None = None,
        limit: int = _DEFAULT_FROM_TASK_LIMIT,
        added_by: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Enqueue the target traces of matching online-task score samples.

        Selects the task's non-error score rows matching the optional
        ``metric_name`` / score bounds, takes up to ``limit`` distinct
        targets (score rows oldest first), and skips traces already in the
        queue.
        """
        if limit < 1 or limit > _MAX_FROM_TASK_LIMIT:
            raise AnnotationValidationError(f"limit must be in [1, {_MAX_FROM_TASK_LIMIT}]")
        queue = await self._get_queue_or_raise(queue_id, workspace_id)

        task = (
            await self.db.execute(
                select(EvaluationTaskModel).where(
                    EvaluationTaskModel.id == task_id,
                    EvaluationTaskModel.workspace_id == workspace_id,
                    ~EvaluationTaskModel.deleted,
                )
            )
        ).scalar_one_or_none()
        if task is None:
            raise AnnotationQueueNotFoundError(str(task_id))

        conditions: list[Any] = [
            EvaluationTaskScoreModel.task_id == task_id,
            EvaluationTaskScoreModel.workspace_id == workspace_id,
            ~EvaluationTaskScoreModel.deleted,
            EvaluationTaskScoreModel.value != -1.0,  # error sentinel carries no score
        ]
        if metric_name is not None:
            conditions.append(EvaluationTaskScoreModel.metric_name == metric_name)
        if min_score is not None:
            conditions.append(EvaluationTaskScoreModel.value >= min_score)
        if max_score is not None:
            conditions.append(EvaluationTaskScoreModel.value <= max_score)

        rows = await self.db.execute(
            select(EvaluationTaskScoreModel.target_id)
            .where(*conditions)
            .order_by(EvaluationTaskScoreModel.created_at.asc())
        )
        candidates: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for (target_id,) in rows.all():
            if target_id not in seen:
                seen.add(target_id)
                candidates.append(target_id)

        existing_ids = await self._queue_target_ids(queue.id)
        created, skipped_existing = 0, 0
        for target_id in candidates[:limit]:
            if target_id in existing_ids:
                skipped_existing += 1
                continue
            self.db.add(
                AnnotationQueueItemModel(
                    queue_id=queue.id,
                    target_type="trace",
                    target_id=target_id,
                    status=QueueItemStatus.PENDING.value,
                    added_by=added_by,
                    workspace_id=workspace_id,
                )
            )
            existing_ids.add(target_id)
            created += 1
        await self.db.flush()
        return {
            "created": created,
            "skipped_existing": skipped_existing,
            "candidates": len(candidates),
            "limit": limit,
        }

    async def _queue_target_ids(self, queue_id: uuid.UUID) -> set[uuid.UUID]:
        rows = await self.db.execute(
            select(AnnotationQueueItemModel.target_id).where(
                AnnotationQueueItemModel.queue_id == queue_id,
                ~AnnotationQueueItemModel.deleted,
            )
        )
        return {row[0] for row in rows.all()}

    async def _workspace_trace(self, trace_id: uuid.UUID, workspace_id: uuid.UUID) -> Any | None:
        """A completed root trace visible to the workspace.

        Tenant scope mirrors the traces API: the trace's session OR its
        agent belongs to the workspace.
        """
        from hecate.models.agent import AgentModel
        from hecate.models.trace import TraceModel

        session_ws = (
            select(SessionModel.id)
            .where(
                SessionModel.id == TraceModel.session_id,
                SessionModel.workspace_id == workspace_id,
                ~SessionModel.deleted,
            )
            .exists()
        )
        agent_ws = (
            select(AgentModel.id)
            .where(
                AgentModel.id == TraceModel.agent_id,
                AgentModel.workspace_id == workspace_id,
                ~AgentModel.deleted,
            )
            .exists()
        )
        result = await self.db.execute(
            select(TraceModel).where(
                TraceModel.id == trace_id,
                TraceModel.type == "trace",
                TraceModel.status == "completed",
                ~TraceModel.deleted,
                session_ws | agent_ws,
            )
        )
        return result.scalar_one_or_none()

    # -- item workflow --------------------------------------------------------

    async def list_items(
        self,
        queue_id: uuid.UUID,
        workspace_id: uuid.UUID,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AnnotationQueueItemModel], int]:
        """List a queue's items in enqueue order."""
        await self._get_queue_or_raise(queue_id, workspace_id)
        conditions: list[Any] = [
            AnnotationQueueItemModel.queue_id == queue_id,
            AnnotationQueueItemModel.workspace_id == workspace_id,
            ~AnnotationQueueItemModel.deleted,
        ]
        if status is not None:
            conditions.append(AnnotationQueueItemModel.status == status)
        base_query = select(AnnotationQueueItemModel).where(*conditions)
        total = (await self.db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
        offset = (page - 1) * page_size
        result = await self.db.execute(
            base_query.order_by(AnnotationQueueItemModel.created_at.asc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), int(total)

    async def _get_item_or_raise(
        self,
        item_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> tuple[AnnotationQueueItemModel, AnnotationQueueModel]:
        item = (
            await self.db.execute(
                select(AnnotationQueueItemModel).where(
                    AnnotationQueueItemModel.id == item_id,
                    AnnotationQueueItemModel.workspace_id == workspace_id,
                    ~AnnotationQueueItemModel.deleted,
                )
            )
        ).scalar_one_or_none()
        if item is None:
            raise AnnotationQueueItemNotFoundError(str(item_id))
        queue = await self.get_queue(item.queue_id, workspace_id)
        if queue is None:
            raise AnnotationQueueItemNotFoundError(str(item_id))
        return item, queue

    async def claim_item(
        self,
        item_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> AnnotationQueueItemModel:
        """Claim a pending item for review (assignees only when set)."""
        item, queue = await self._get_item_or_raise(item_id, workspace_id)
        if item.status not in (QueueItemStatus.PENDING.value,):
            raise AnnotationItemStateError(f"item is {item.status}; only pending items can be claimed")
        assigned = [uuid.UUID(str(u)) for u in (queue.assigned_user_ids or [])]
        if assigned and user_id not in assigned:
            raise AnnotationValidationError("user is not assigned to this queue")
        item.status = QueueItemStatus.CLAIMED.value
        item.claimed_by = user_id
        item.claimed_at = datetime.now(UTC)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def skip_item(
        self,
        item_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> AnnotationQueueItemModel:
        """Skip a pending or claimed item without scoring it."""
        item, _queue = await self._get_item_or_raise(item_id, workspace_id)
        if item.status not in (QueueItemStatus.PENDING.value, QueueItemStatus.CLAIMED.value):
            raise AnnotationItemStateError(f"item is {item.status}; only pending/claimed items can be skipped")
        item.status = QueueItemStatus.SKIPPED.value
        item.completed_by = user_id
        item.completed_at = datetime.now(UTC)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    async def delete_item(self, item_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
        """Soft-delete an item — only while pending or skipped."""
        item, _queue = await self._get_item_or_raise(item_id, workspace_id)
        if item.status not in (QueueItemStatus.PENDING.value, QueueItemStatus.SKIPPED.value):
            raise AnnotationItemStateError(f"item is {item.status}; only pending/skipped items can be deleted")
        item.deleted = True
        item.deleted_at = datetime.now(UTC)
        await self.db.flush()

    async def submit_item(
        self,
        item_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        annotations: list[dict[str, Any]],
    ) -> AnnotationQueueItemModel:
        """Persist annotations as human score rows and complete the item.

        Each entry needs a queue metric name and a value valid for that
        metric's definition. Entries referencing ``overrides_score_id``
        require ``reason_code`` and ``justification`` and must point at an
        automated row of the same target and metric. Resubmission by the
        same annotator for the same (target, metric) upserts their row.
        """
        item, queue = await self._get_item_or_raise(item_id, workspace_id)
        if item.status not in (QueueItemStatus.PENDING.value, QueueItemStatus.CLAIMED.value):
            raise AnnotationItemStateError(f"item is {item.status}; only pending/claimed items can be submitted")

        metric_defs = {d["name"]: d for d in (queue.metric_defs or [])}
        seen_metrics: set[str] = set()
        normalized: list[tuple[str, float, str | None, Any, str | None, str | None]] = []
        for entry in annotations:
            metric = str(entry.get("metric_name") or "")
            if metric not in metric_defs:
                raise AnnotationValidationError(f"metric {metric!r} is not defined on this queue")
            if metric in seen_metrics:
                raise AnnotationValidationError(f"duplicate annotation for metric {metric!r}")
            seen_metrics.add(metric)
            value, value_label = self._validated_value(metric_defs[metric], entry, metric)
            overrides_score_id = entry.get("overrides_score_id")
            justification = entry.get("justification")
            reason_code = entry.get("reason_code")
            if overrides_score_id is not None:
                if not reason_code or not str(reason_code).strip():
                    raise AnnotationValidationError(f"override for {metric!r} requires reason_code")
                if len(str(reason_code)) > 50:
                    raise AnnotationValidationError("reason_code must be at most 50 characters")
                if justification is None or not str(justification).strip():
                    raise AnnotationValidationError(f"override for {metric!r} requires justification")
                await self._validate_override_target(overrides_score_id, workspace_id, item.target_id, metric)
            if justification is not None and len(str(justification)) > _JUSTIFICATION_MAX:
                raise AnnotationValidationError("justification is too long")
            normalized.append((metric, value, value_label, overrides_score_id, reason_code, justification))

        for metric, value, value_label, overrides_score_id, reason_code, justification in normalized:
            await self._upsert_human_score(
                item=item,
                metric_name=metric,
                value=value,
                value_label=value_label,
                annotator_id=user_id,
                overrides_score_id=overrides_score_id,
                reason_code=reason_code,
                justification=justification,
            )

        item.status = QueueItemStatus.COMPLETED.value
        item.completed_by = user_id
        item.completed_at = datetime.now(UTC)
        await self.db.flush()
        await self.db.refresh(item)
        return item

    def _validated_value(
        self, metric_def: dict[str, Any], entry: dict[str, Any], metric: str
    ) -> tuple[float, str | None]:
        """Validate one annotation entry against its metric definition.

        Numeric entries carry ``value`` within ``[min, max]``. Categorical
        entries carry ``value_label`` from the categories list (stored as
        the category index). Boolean entries carry ``value_label`` of
        ``"true"``/``"false"``. Returns ``(value, value_label)``.
        """
        data_type = metric_def["data_type"]
        value_label = entry.get("value_label")
        raw_value = entry.get("value")
        if data_type == "numeric":
            if raw_value is None:
                raise AnnotationValidationError(f"metric {metric!r}: numeric annotation requires value")
            value = float(raw_value)
            if (
                "min" in metric_def
                and "max" in metric_def
                and not (float(metric_def["min"]) <= value <= float(metric_def["max"]))
            ):
                raise AnnotationValidationError(
                    f"metric {metric!r}: value {value} outside [{metric_def['min']}, {metric_def['max']}]"
                )
            return value, None
        if data_type == "categorical":
            if value_label is None:
                raise AnnotationValidationError(f"metric {metric!r}: categorical annotation requires value_label")
            label = str(value_label)
            categories = metric_def.get("categories") or []
            if label not in categories:
                raise AnnotationValidationError(f"metric {metric!r}: {label!r} is not a defined category")
            return float(categories.index(label)), label
        # boolean
        if value_label is None:
            raise AnnotationValidationError(f"metric {metric!r}: boolean annotation requires value_label")
        label = str(value_label).lower()
        if label not in ("true", "false"):
            raise AnnotationValidationError(f"metric {metric!r}: boolean value_label must be 'true' or 'false'")
        return (1.0 if label == "true" else 0.0), label

    async def _validate_override_target(
        self,
        overrides_score_id: Any,
        workspace_id: uuid.UUID,
        target_id: uuid.UUID,
        metric_name: str,
    ) -> None:
        """The overridden row must be an automated row of the same target/metric."""
        row = (
            await self.db.execute(
                select(EvaluationTaskScoreModel).where(
                    EvaluationTaskScoreModel.id == overrides_score_id,
                    EvaluationTaskScoreModel.workspace_id == workspace_id,
                    ~EvaluationTaskScoreModel.deleted,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise AnnotationValidationError("overrides_score_id does not reference an existing score row")
        if row.source == _HUMAN_SOURCE:
            raise AnnotationValidationError("overrides_score_id must reference an automated score row")
        if row.target_id != target_id or row.metric_name != metric_name:
            raise AnnotationValidationError(
                "overrides_score_id must reference an automated row of the same target and metric"
            )

    async def _upsert_human_score(
        self,
        item: AnnotationQueueItemModel,
        metric_name: str,
        value: float,
        value_label: str | None,
        annotator_id: uuid.UUID,
        overrides_score_id: Any,
        reason_code: str | None,
        justification: str | None,
    ) -> EvaluationTaskScoreModel:
        """Write (or update) the annotator's human row for this target+metric."""
        existing = (
            await self.db.execute(
                select(EvaluationTaskScoreModel).where(
                    EvaluationTaskScoreModel.source == _HUMAN_SOURCE,
                    EvaluationTaskScoreModel.annotator_id == annotator_id,
                    EvaluationTaskScoreModel.target_id == item.target_id,
                    EvaluationTaskScoreModel.metric_name == metric_name,
                    EvaluationTaskScoreModel.target_type == item.target_type,
                    ~EvaluationTaskScoreModel.deleted,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.value = value
            existing.value_label = value_label
            existing.reasoning = justification
            existing.overrides_score_id = overrides_score_id
            existing.reason_code = reason_code
            await self.db.flush()
            return existing

        row = EvaluationTaskScoreModel(
            task_id=None,
            target_type=item.target_type,
            target_id=item.target_id,
            session_id=await self._trace_session_id(item.target_id),
            agent_id=await self._trace_agent_id(item.target_id),
            metric_name=metric_name,
            value=value,
            value_label=value_label,
            reasoning=justification,
            source=_HUMAN_SOURCE,
            status=TaskScoreStatus.COMPLETED.value,
            annotator_id=annotator_id,
            overrides_score_id=overrides_score_id,
            reason_code=reason_code,
            workspace_id=item.workspace_id,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def _trace_session_id(self, target_id: uuid.UUID) -> uuid.UUID | None:
        from hecate.models.trace import TraceModel

        row = await self.db.execute(select(TraceModel.session_id).where(TraceModel.id == target_id))
        return row.scalar_one_or_none()

    async def _trace_agent_id(self, target_id: uuid.UUID) -> uuid.UUID | None:
        """Agent ownership via the session join (traces.agent_id has no writer)."""
        from hecate.models.trace import TraceModel

        row = await self.db.execute(
            select(SessionModel.agent_id)
            .select_from(TraceModel)
            .join(SessionModel, SessionModel.id == TraceModel.session_id)
            .where(TraceModel.id == target_id)
        )
        return row.scalar_one_or_none()

    # -- item detail ------------------------------------------------------------

    async def get_item_detail(
        self,
        item_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Item state plus the review context: trace projection and suggestions."""
        item, queue = await self._get_item_or_raise(item_id, workspace_id)
        trace = await self._load_trace(item.target_id)

        projection: dict[str, Any] | None = None
        if trace is not None and trace.session_id is not None:
            eval_input = await build_eval_input(
                trace.session_id,
                trace.start_time,
                trace.end_time,
                await self._get_event_store(),
            )
            if eval_input is not None:
                messages: list[dict[str, Any]] = list(eval_input.conversation_history or [])
                messages.append({"role": "user", "content": eval_input.query})
                messages.append({"role": "assistant", "content": eval_input.generated_answer})
                projection = {
                    "messages": messages,
                    "tool_calls": eval_input.tool_calls or [],
                }

        suggestions = await self._latest_automated_scores(item.target_id)
        return {
            "item": item,
            "queue": queue,
            "trace": trace,
            "projection": projection,
            "suggestions": suggestions,
        }

    async def _load_trace(self, target_id: uuid.UUID) -> Any | None:
        from hecate.models.trace import TraceModel

        result = await self.db.execute(select(TraceModel).where(TraceModel.id == target_id, ~TraceModel.deleted))
        return result.scalar_one_or_none()

    async def _latest_automated_scores(self, target_id: uuid.UUID) -> list[dict[str, Any]]:
        """Latest automated score per metric for the target (suggestion prefill)."""
        rows = await self.db.execute(
            select(EvaluationTaskScoreModel)
            .where(
                EvaluationTaskScoreModel.target_id == target_id,
                EvaluationTaskScoreModel.source != _HUMAN_SOURCE,
                ~EvaluationTaskScoreModel.deleted,
            )
            .order_by(EvaluationTaskScoreModel.created_at.desc())
        )
        latest: dict[str, EvaluationTaskScoreModel] = {}
        for row in rows.scalars().all():
            latest.setdefault(row.metric_name, row)
        return [
            {
                "metric_name": row.metric_name,
                "value": float(row.value),
                "source": row.source,
                "reasoning": row.reasoning,
                "score_id": row.id,
            }
            for row in sorted(latest.values(), key=lambda r: r.metric_name)
        ]

    async def _get_event_store(self) -> Any:
        if self._event_store is None:
            from hecate.core.config import settings
            from hecate.studio.event_state import create_event_store

            self._event_store = create_event_store(settings)
        return self._event_store

    # -- dataset backflow -------------------------------------------------------

    async def push_to_dataset(
        self,
        queue_id: uuid.UUID,
        workspace_id: uuid.UUID,
        dataset_id: uuid.UUID | None,
        dataset_name: str | None,
        item_ids: list[uuid.UUID] | None = None,
    ) -> dict[str, Any]:
        """Materialize completed annotated items into an evaluation dataset.

        Idempotent per dataset: a trace already present (matched via
        ``metadata_.annotation.trace_id``) is skipped. Items without a
        projection (no complete user→assistant exchange) are skipped too.
        """
        if (dataset_id is None) == (dataset_name is None):
            raise AnnotationValidationError("exactly one of dataset_id or dataset_name is required")
        queue = await self._get_queue_or_raise(queue_id, workspace_id)
        dataset = await self._resolve_dataset(dataset_id, dataset_name, workspace_id)

        conditions: list[Any] = [
            AnnotationQueueItemModel.queue_id == queue.id,
            AnnotationQueueItemModel.workspace_id == workspace_id,
            AnnotationQueueItemModel.status == QueueItemStatus.COMPLETED.value,
            ~AnnotationQueueItemModel.deleted,
        ]
        if item_ids:
            conditions.append(AnnotationQueueItemModel.id.in_(item_ids))
        items = list(
            (
                await self.db.execute(
                    select(AnnotationQueueItemModel)
                    .where(*conditions)
                    .order_by(AnnotationQueueItemModel.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

        materialized_trace_ids = await self._dataset_trace_ids(dataset.id)
        event_store = await self._get_event_store()

        created, skipped = 0, 0
        for item in items:
            if str(item.target_id) in materialized_trace_ids:
                skipped += 1
                continue
            trace = await self._load_trace(item.target_id)
            if trace is None or trace.session_id is None:
                skipped += 1
                continue
            eval_input = await build_eval_input(trace.session_id, trace.start_time, trace.end_time, event_store)
            if eval_input is None:
                skipped += 1
                continue
            labels = await self._human_labels(item.target_id)
            self.db.add(
                EvaluationItemModel(
                    dataset_id=dataset.id,
                    query=eval_input.query,
                    generated_answer=eval_input.generated_answer,
                    metadata_={
                        "annotation": {
                            "trace_id": str(item.target_id),
                            "queue_id": str(queue.id),
                            "queue_item_id": str(item.id),
                            "labels": labels,
                        }
                    },
                    tags=["human-annotation", queue.name],
                    workspace_id=workspace_id,
                )
            )
            materialized_trace_ids.add(str(item.target_id))
            created += 1
        await self.db.flush()
        return {"created": created, "skipped": skipped, "dataset_id": dataset.id}

    async def _resolve_dataset(
        self,
        dataset_id: uuid.UUID | None,
        dataset_name: str | None,
        workspace_id: uuid.UUID,
    ) -> EvaluationDatasetModel:
        if dataset_id is not None:
            dataset = (
                await self.db.execute(
                    select(EvaluationDatasetModel).where(
                        EvaluationDatasetModel.id == dataset_id,
                        EvaluationDatasetModel.workspace_id == workspace_id,
                        ~EvaluationDatasetModel.deleted,
                    )
                )
            ).scalar_one_or_none()
            if dataset is None:
                raise AnnotationValidationError(f"dataset {dataset_id} not found in workspace")
            return dataset
        dataset = (
            await self.db.execute(
                select(EvaluationDatasetModel).where(
                    EvaluationDatasetModel.name == dataset_name,
                    EvaluationDatasetModel.workspace_id == workspace_id,
                    ~EvaluationDatasetModel.deleted,
                )
            )
        ).scalar_one_or_none()
        if dataset is not None:
            return dataset
        # TODO: concurrent pushes with the same new name can race; acceptable
        # for v1 (single-workspace, low-frequency manual operation).
        return await EvaluationDatasetService(self.db).create_dataset(
            name=dataset_name or "", workspace_id=workspace_id
        )

    async def _dataset_trace_ids(self, dataset_id: uuid.UUID) -> set[str]:
        rows = await self.db.execute(
            select(EvaluationItemModel.metadata_).where(
                EvaluationItemModel.dataset_id == dataset_id,
                ~EvaluationItemModel.deleted,
            )
        )
        trace_ids: set[str] = set()
        for (metadata,) in rows.all():
            annotation = (metadata or {}).get("annotation") if isinstance(metadata, dict) else None
            if isinstance(annotation, dict) and annotation.get("trace_id"):
                trace_ids.add(str(annotation["trace_id"]))
        return trace_ids

    async def _human_labels(self, target_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = await self.db.execute(
            select(EvaluationTaskScoreModel)
            .where(
                EvaluationTaskScoreModel.target_id == target_id,
                EvaluationTaskScoreModel.source == _HUMAN_SOURCE,
                ~EvaluationTaskScoreModel.deleted,
            )
            .order_by(EvaluationTaskScoreModel.created_at.asc())
        )
        return [
            {
                "metric_name": row.metric_name,
                "value": float(row.value),
                "value_label": row.value_label,
                "reason_code": row.reason_code,
                "justification": row.reasoning,
                "annotator_id": str(row.annotator_id) if row.annotator_id else None,
            }
            for row in rows.scalars().all()
        ]


__all__ = [
    "AnnotationItemStateError",
    "AnnotationQueueItemNotFoundError",
    "AnnotationQueueNotFoundError",
    "AnnotationService",
    "AnnotationValidationError",
    "_SUGGESTED_REASON_CODES",
]
