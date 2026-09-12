"""Automated trace-backflow rule service (7.2d).

A rule selects scored production traces of one online evaluation task by
score-band filters and materializes them into an evaluation dataset when
explicitly triggered (``run_rule``) — no cron, no worker hook. Rules are the
automated half of dataset backflow; the human-verified half lives in the
annotation service's ``push_to_dataset``.

Materialization mirrors the annotation path's shape: ``build_eval_input``
projects the trace's session window into ``query`` / ``generated_answer``
(plus ``conversation_history`` for multi-turn windows), the item is
self-contained, machine scores are recorded as provenance
(``metadata_.backflow``) — never as ``expected_answer`` — and dedup is
dataset-wide across both paths (see ``trace_dedup``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationBackflowRuleModel,
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    TaskType,
)
from hecate.ops.evaluation.tasks.trace_input import build_eval_input
from hecate.ops.evaluation.trace_dedup import dataset_trace_ids

logger = logging.getLogger(__name__)


class BackflowRuleNotFoundError(LookupError):
    """Rule with the given id does not exist in the workspace."""


class BackflowValidationError(ValueError):
    """Rule payload or run preconditions violate a validation rule."""


def _validate_filters(filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize score filters; reject empty lists and inverted bands."""
    if not filters:
        raise BackflowValidationError("backflow rules require at least one score filter")
    normalized: list[dict[str, Any]] = []
    for raw in filters:
        metric_name = str(raw.get("metric_name") or "").strip()
        if not metric_name:
            raise BackflowValidationError("score filters require a metric_name")
        minimum, maximum = raw.get("min_score"), raw.get("max_score")
        if minimum is not None and maximum is not None and float(minimum) > float(maximum):
            raise BackflowValidationError(f"filter {metric_name!r}: min_score must be <= max_score")
        entry: dict[str, Any] = {"metric_name": metric_name}
        if minimum is not None:
            entry["min_score"] = float(minimum)
        if maximum is not None:
            entry["max_score"] = float(maximum)
        normalized.append(entry)
    return normalized


def _filter_condition(entry: dict[str, Any]) -> Any:
    """SQL condition matching one filter against a score row."""
    condition = EvaluationTaskScoreModel.metric_name == entry["metric_name"]
    if entry.get("min_score") is not None:
        condition &= EvaluationTaskScoreModel.value >= entry["min_score"]
    if entry.get("max_score") is not None:
        condition &= EvaluationTaskScoreModel.value <= entry["max_score"]
    return condition


def _rule_data_to_filters(data_filters: list[Any]) -> list[dict[str, Any]]:
    return _validate_filters([f if isinstance(f, dict) else f.model_dump() for f in data_filters])


class BackflowService:
    """CRUD for backflow rules plus batch materialization runs."""

    def __init__(self, db: AsyncSession, event_store: Any | None = None) -> None:
        self.db = db
        self._event_store = event_store

    # -- rule CRUD -----------------------------------------------------------

    async def create_rule(self, data: Any, workspace_id: uuid.UUID) -> EvaluationBackflowRuleModel:
        """Persist a new rule after cross-object validation."""
        filters = _rule_data_to_filters(data.filters)
        task = await self._task(data.task_id, workspace_id)
        if task is None:
            raise BackflowValidationError(f"task {data.task_id} not found in workspace")
        if task.task_type != TaskType.ONLINE.value:
            raise BackflowValidationError(
                f"task {data.task_id} is an {task.task_type} task; backflow rules require an online task"
            )
        dataset = await self._dataset(data.dataset_id, workspace_id)
        if dataset is None:
            raise BackflowValidationError(f"dataset {data.dataset_id} not found in workspace")
        name = data.name.strip()
        await self._ensure_name_available(name, workspace_id)
        rule = EvaluationBackflowRuleModel(
            name=name,
            task_id=data.task_id,
            dataset_id=data.dataset_id,
            filters=filters,
            limit=data.limit,
            max_turns=data.max_turns,
            workspace_id=workspace_id,
        )
        self.db.add(rule)
        await self.db.flush()
        await self.db.refresh(rule)
        return rule

    async def get_rule(self, rule_id: uuid.UUID, workspace_id: uuid.UUID) -> EvaluationBackflowRuleModel | None:
        result = await self.db.execute(
            select(EvaluationBackflowRuleModel).where(
                EvaluationBackflowRuleModel.id == rule_id,
                EvaluationBackflowRuleModel.workspace_id == workspace_id,
                ~EvaluationBackflowRuleModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def list_rules(
        self,
        workspace_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EvaluationBackflowRuleModel], int]:
        """List workspace rules, newest first."""
        conditions = [
            EvaluationBackflowRuleModel.workspace_id == workspace_id,
            ~EvaluationBackflowRuleModel.deleted,
        ]
        base_query = select(EvaluationBackflowRuleModel).where(*conditions)
        total = (await self.db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
        offset = (page - 1) * page_size
        result = await self.db.execute(
            base_query.order_by(EvaluationBackflowRuleModel.created_at.desc()).offset(offset).limit(page_size)
        )
        return list(result.scalars().all()), int(total)

    async def update_rule(
        self,
        rule_id: uuid.UUID,
        workspace_id: uuid.UUID,
        data: Any,
    ) -> EvaluationBackflowRuleModel:
        """Apply a partial update; changed references and filters re-validate."""
        rule = await self._get_rule_or_raise(rule_id, workspace_id)
        if data.name is not None:
            name = data.name.strip()
            await self._ensure_name_available(name, workspace_id, exclude_id=rule.id)
            rule.name = name
        if data.task_id is not None:
            task = await self._task(data.task_id, workspace_id)
            if task is None:
                raise BackflowValidationError(f"task {data.task_id} not found in workspace")
            if task.task_type != TaskType.ONLINE.value:
                raise BackflowValidationError(
                    f"task {data.task_id} is an {task.task_type} task; backflow rules require an online task"
                )
            rule.task_id = data.task_id
        if data.dataset_id is not None:
            if await self._dataset(data.dataset_id, workspace_id) is None:
                raise BackflowValidationError(f"dataset {data.dataset_id} not found in workspace")
            rule.dataset_id = data.dataset_id
        if data.filters is not None:
            rule.filters = _rule_data_to_filters(data.filters)
        if data.limit is not None:
            rule.limit = data.limit
        if data.max_turns is not None:
            rule.max_turns = data.max_turns
        await self.db.flush()
        await self.db.refresh(rule)
        return rule

    async def delete_rule(self, rule_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
        """Soft-delete a rule. Materialized dataset items are retained."""
        rule = await self._get_rule_or_raise(rule_id, workspace_id)
        rule.deleted = True
        rule.deleted_at = datetime.now(UTC)
        await self.db.flush()

    # -- run -----------------------------------------------------------------

    async def run_rule(self, rule_id: uuid.UUID, workspace_id: uuid.UUID) -> dict[str, Any]:
        """Materialize the rule's matching traces into its dataset.

        Returns ``{created, skipped, dataset_id}``. Re-runs are no-ops for
        traces already materialized into the dataset via either backflow
        path.
        """
        rule = await self._get_rule_or_raise(rule_id, workspace_id)
        dataset = await self._dataset(rule.dataset_id, workspace_id)
        if dataset is None:
            raise BackflowValidationError(f"dataset {rule.dataset_id} not found in workspace")

        candidates = await self._select_candidates(rule)
        snapshots = await self._matched_score_snapshots(rule, candidates)
        materialized = await dataset_trace_ids(self.db, dataset.id)
        event_store = await self._get_event_store()

        created, skipped = 0, 0
        for target_id in candidates:
            if created >= rule.limit:
                break
            if str(target_id) in materialized:
                skipped += 1
                continue
            if not await self._materialize_item(rule, target_id, snapshots.get(str(target_id), []), event_store):
                skipped += 1
                continue
            materialized.add(str(target_id))
            created += 1

        rule.metadata_ = {
            **(rule.metadata_ or {}),
            "last_run": {"at": datetime.now(UTC).isoformat(), "created": created, "skipped": skipped},
        }
        await self.db.flush()
        return {"created": created, "skipped": skipped, "dataset_id": dataset.id}

    async def _select_candidates(self, rule: EvaluationBackflowRuleModel) -> list[uuid.UUID]:
        """Target ids whose score rows satisfy ALL filters, oldest hit first.

        The error sentinel (``value = -1.0``) carries no score and never
        matches. The walk is not SQL-capped: ``rule.limit`` caps
        *materializations* in ``run_rule`` (cheap skips don't consume the
        cap), so newly matched traces surface even when older ones were
        already materialized by earlier runs.
        """
        first_hit = func.min(EvaluationTaskScoreModel.created_at).label("first_hit")
        query = (
            select(EvaluationTaskScoreModel.target_id, first_hit)
            .where(
                EvaluationTaskScoreModel.task_id == rule.task_id,
                EvaluationTaskScoreModel.workspace_id == rule.workspace_id,
                ~EvaluationTaskScoreModel.deleted,
                EvaluationTaskScoreModel.value != -1.0,
            )
            .group_by(EvaluationTaskScoreModel.target_id)
        )
        for entry in rule.filters:
            query = query.having(func.sum(case((_filter_condition(entry), 1), else_=0)) >= 1)
        rows = await self.db.execute(query.order_by(first_hit.asc()))
        return [row[0] for row in rows.all()]

    async def _matched_score_snapshots(
        self,
        rule: EvaluationBackflowRuleModel,
        target_ids: list[uuid.UUID],
    ) -> dict[str, list[dict[str, Any]]]:
        """Per-target snapshot of the score rows that matched any filter."""
        if not target_ids:
            return {}
        matched = _filter_condition(rule.filters[0])
        for entry in rule.filters[1:]:
            matched |= _filter_condition(entry)
        rows = await self.db.execute(
            select(EvaluationTaskScoreModel)
            .where(
                EvaluationTaskScoreModel.task_id == rule.task_id,
                EvaluationTaskScoreModel.target_id.in_(target_ids),
                EvaluationTaskScoreModel.workspace_id == rule.workspace_id,
                ~EvaluationTaskScoreModel.deleted,
                EvaluationTaskScoreModel.value != -1.0,
                matched,
            )
            .order_by(EvaluationTaskScoreModel.created_at.asc())
        )
        snapshots: dict[str, list[dict[str, Any]]] = {}
        for row in rows.scalars().all():
            snapshots.setdefault(str(row.target_id), []).append(
                {
                    "metric_name": row.metric_name,
                    "value": float(row.value),
                    "source": row.source,
                    "scored_at": row.created_at.isoformat() if row.created_at else None,
                }
            )
        return snapshots

    async def _materialize_item(
        self,
        rule: EvaluationBackflowRuleModel,
        target_id: uuid.UUID,
        scores: list[dict[str, Any]],
        event_store: Any,
    ) -> bool:
        """Project one trace and persist its dataset item.

        Returns ``False`` (counted as skipped) when the trace is gone, its
        window has no complete user→assistant exchange, or the conversation
        exceeds the rule's ``max_turns`` bound (skipped rather than truncated
        mid-conversation, mirroring the projection's oversize semantics).
        """
        trace = await self._load_trace(target_id)
        if trace is None or trace.session_id is None:
            return False
        eval_input = await build_eval_input(trace.session_id, trace.start_time, trace.end_time, event_store)
        if eval_input is None:
            return False
        history = list(eval_input.conversation_history or [])
        if rule.max_turns is not None and len(history) > rule.max_turns * 2:
            return False
        self.db.add(
            EvaluationItemModel(
                dataset_id=rule.dataset_id,
                query=eval_input.query,
                generated_answer=eval_input.generated_answer,
                context=eval_input.retrieved_contexts or None,
                expected_answer=None,
                metadata_={
                    "backflow": {
                        "trace_id": str(target_id),
                        "task_id": str(rule.task_id),
                        "rule_id": str(rule.id),
                        "scores": scores,
                        "conversation_history": history or None,
                        "tool_calls": eval_input.tool_calls,
                        "agent_id": str(trace.agent_id) if trace.agent_id else None,
                        "session_id": str(trace.session_id),
                    }
                },
                tags=["trace-backflow", rule.name],
            )
        )
        return True

    # -- helpers ---------------------------------------------------------------

    async def _get_rule_or_raise(self, rule_id: uuid.UUID, workspace_id: uuid.UUID) -> EvaluationBackflowRuleModel:
        rule = await self.get_rule(rule_id, workspace_id)
        if rule is None:
            raise BackflowRuleNotFoundError(str(rule_id))
        return rule

    async def _ensure_name_available(
        self,
        name: str,
        workspace_id: uuid.UUID,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        conditions: list[Any] = [
            EvaluationBackflowRuleModel.name == name,
            EvaluationBackflowRuleModel.workspace_id == workspace_id,
            ~EvaluationBackflowRuleModel.deleted,
        ]
        if exclude_id is not None:
            conditions.append(EvaluationBackflowRuleModel.id != exclude_id)
        existing = (
            await self.db.execute(select(EvaluationBackflowRuleModel.id).where(*conditions).limit(1))
        ).scalar_one_or_none()
        if existing is not None:
            raise BackflowValidationError(f"backflow rule name {name!r} already exists in the workspace")

    async def _task(self, task_id: uuid.UUID, workspace_id: uuid.UUID) -> EvaluationTaskModel | None:
        result = await self.db.execute(
            select(EvaluationTaskModel).where(
                EvaluationTaskModel.id == task_id,
                EvaluationTaskModel.workspace_id == workspace_id,
                ~EvaluationTaskModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def _dataset(self, dataset_id: uuid.UUID, workspace_id: uuid.UUID) -> EvaluationDatasetModel | None:
        result = await self.db.execute(
            select(EvaluationDatasetModel).where(
                EvaluationDatasetModel.id == dataset_id,
                EvaluationDatasetModel.workspace_id == workspace_id,
                ~EvaluationDatasetModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def _load_trace(self, target_id: uuid.UUID) -> Any | None:
        from hecate.models.trace import TraceModel

        result = await self.db.execute(select(TraceModel).where(TraceModel.id == target_id, ~TraceModel.deleted))
        return result.scalar_one_or_none()

    async def _get_event_store(self) -> Any:
        if self._event_store is None:
            from hecate.core.config import settings
            from hecate.studio.event_state import create_event_store

            self._event_store = create_event_store(settings)
        return self._event_store
