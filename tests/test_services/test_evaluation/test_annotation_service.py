"""Tests for the annotation queue service (7.4 queues, 7.4a calibration writes).

Covers queue CRUD validation, intake (manual + from-task), the claim/skip/
submit workflow, human-score ledger writes (coexist, override, upsert), item
detail suggestions, and dataset backflow idempotency.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    AnnotationQueueItemModel,
    AnnotationQueueModel,
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    TaskType,
)
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
from hecate.ops.evaluation.annotation import (
    AnnotationItemStateError,
    AnnotationQueueNotFoundError,
    AnnotationService,
    AnnotationValidationError,
)
from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore

_WS = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
_OTHER_WS = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
_USER = uuid.UUID("00000000-0000-0000-0000-0000000000cc")
_NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)

_METRIC_DEFS = [
    {"name": "helpfulness", "data_type": "numeric", "min": 0.0, "max": 1.0},
    {"name": "tone", "data_type": "categorical", "categories": ["good", "neutral", "bad"]},
]


def _queue_data(**overrides):
    from types import SimpleNamespace

    payload = SimpleNamespace(
        name="weekly-review",
        description=None,
        instructions="Rate the output",
        metric_defs=[dict(d) for d in _METRIC_DEFS],
        assigned_user_ids=None,
    )
    for key, value in overrides.items():
        setattr(payload, key, value)
    return payload


async def _create_queue(db_session: AsyncSession, **overrides) -> AnnotationQueueModel:
    svc = AnnotationService(db_session)
    return await svc.create_queue(_queue_data(**overrides), workspace_id=_WS)


async def _seed_trace(
    db_session: AsyncSession,
    workspace_id: uuid.UUID = _WS,
    status: str = "completed",
    trace_type: str = "trace",
) -> TraceModel:
    session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_id=workspace_id)
    db_session.add(session_row)
    await db_session.flush()
    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type=trace_type,
        name=f"session:{session_row.id}",
        session_id=session_row.id,
        status=status,
        start_time=_NOW,
        end_time=_NOW + timedelta(seconds=5),
        created_at=_NOW,
    )
    db_session.add(trace)
    await db_session.flush()
    return trace


async def _seed_online_task(db_session: AsyncSession) -> EvaluationTaskModel:
    task = EvaluationTaskModel(
        task_type=TaskType.ONLINE.value,
        name="prod-scoring",
        evaluator_configs=["stub"],
        config={"agent_id": str(uuid.uuid4()), "sampling_rate": 1.0},
        workspace_id=_WS,
    )
    db_session.add(task)
    await db_session.flush()
    return task


async def _seed_score(
    db_session: AsyncSession,
    task_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    metric_name: str = "helpfulness",
    value: float = 0.5,
    source: str = "llm_judge",
    workspace_id: uuid.UUID = _WS,
    overrides_score_id: uuid.UUID | None = None,
    annotator_id: uuid.UUID | None = None,
    created_at: datetime | None = None,
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=None if source == "human" else task_id,
        target_id=target_id,
        metric_name=metric_name,
        value=value,
        source=source,
        annotator_id=annotator_id,
        overrides_score_id=overrides_score_id,
        created_at=created_at or _NOW,
        workspace_id=workspace_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


class TestQueueCrud:
    async def test_create_and_get(self, db_session: AsyncSession) -> None:
        queue = await _create_queue(db_session)
        svc = AnnotationService(db_session)
        fetched = await svc.get_queue(queue.id, workspace_id=_WS)
        assert fetched is not None
        assert [d["name"] for d in fetched.metric_defs] == ["helpfulness", "tone"]

    async def test_workspace_isolation(self, db_session: AsyncSession) -> None:
        queue = await _create_queue(db_session)
        svc = AnnotationService(db_session)
        assert await svc.get_queue(queue.id, workspace_id=_OTHER_WS) is None

    @pytest.mark.parametrize(
        "overrides",
        [
            {"metric_defs": [{"name": "a", "data_type": "numeric", "min": 1.0, "max": 0.0}]},
            {"metric_defs": [{"name": "a", "data_type": "categorical", "categories": []}]},
            {"metric_defs": [{"name": "a", "data_type": "weird"}]},
            {
                "metric_defs": [
                    {"name": "a", "data_type": "numeric", "min": 0, "max": 1},
                    {"name": "a", "data_type": "boolean"},
                ]
            },
            {"metric_defs": []},
        ],
    )
    async def test_invalid_metric_defs_rejected(self, db_session: AsyncSession, overrides: dict) -> None:
        with pytest.raises(AnnotationValidationError):
            await _create_queue(db_session, **overrides)


class TestIntake:
    async def test_add_items_creates_pending_items(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        t1, t2 = await _seed_trace(db_session), await _seed_trace(db_session)
        result = await svc.add_items(queue.id, workspace_id=_WS, target_ids=[t1.id, t2.id], added_by=_USER)
        assert result["created"] == 2 and result["rejected"] == []

    async def test_duplicate_add_is_noop(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        again = await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        assert again["created"] == 0 and again["already_present"] == [trace.id]

    async def test_unknown_or_child_trace_rejected(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        span = await _seed_trace(db_session, trace_type="span")
        result = await svc.add_items(queue.id, workspace_id=_WS, target_ids=[uuid.uuid4(), span.id], added_by=_USER)
        assert len(result["rejected"]) == 2 and result["created"] == 0

    async def test_bulk_cap_enforced(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        with pytest.raises(AnnotationValidationError):
            await svc.add_items(queue.id, workspace_id=_WS, target_ids=[uuid.uuid4()] * 101, added_by=_USER)

    async def test_from_task_enqueues_matching_targets(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        task = await _seed_online_task(db_session)
        low, high = await _seed_trace(db_session), await _seed_trace(db_session)
        await _seed_score(db_session, task.id, low.id, value=0.3)
        await _seed_score(db_session, task.id, high.id, value=0.9)

        result = await svc.add_items_from_task(
            queue.id, workspace_id=_WS, task_id=task.id, max_score=0.5, added_by=_USER
        )
        assert result["created"] == 1
        items = (await db_session.execute(select(AnnotationQueueItemModel))).scalars().all()
        assert [i.target_id for i in items] == [low.id]

    async def test_from_task_unknown_task_raises(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        with pytest.raises(AnnotationQueueNotFoundError):
            await svc.add_items_from_task(queue.id, workspace_id=_WS, task_id=uuid.uuid4(), added_by=_USER)


class TestWorkflow:
    async def test_claim_then_submit_completes(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()

        claimed = await svc.claim_item(item.id, workspace_id=_WS, user_id=_USER)
        assert claimed.status == "claimed" and claimed.claimed_by == _USER

        completed = await svc.submit_item(
            item.id,
            workspace_id=_WS,
            user_id=_USER,
            annotations=[{"metric_name": "helpfulness", "value": 0.8}],
        )
        assert completed.status == "completed" and completed.completed_by == _USER

        score = (
            await db_session.execute(select(EvaluationTaskScoreModel).where(EvaluationTaskScoreModel.source == "human"))
        ).scalar_one()
        assert score.value == 0.8 and score.task_id is None and score.annotator_id == _USER
        assert score.target_id == trace.id

    async def test_unassigned_user_cannot_claim(self, db_session: AsyncSession) -> None:
        queue = await _create_queue(db_session, assigned_user_ids=[_USER])
        svc = AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()

        other = uuid.UUID("00000000-0000-0000-0000-0000000000dd")
        with pytest.raises(AnnotationValidationError):
            await svc.claim_item(item.id, workspace_id=_WS, user_id=other)

    async def test_claim_completed_item_rejected(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()
        await svc.submit_item(
            item.id, workspace_id=_WS, user_id=_USER, annotations=[{"metric_name": "helpfulness", "value": 0.5}]
        )
        with pytest.raises(AnnotationItemStateError):
            await svc.claim_item(item.id, workspace_id=_WS, user_id=_USER)

    async def test_skip_writes_no_scores(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()
        skipped = await svc.skip_item(item.id, workspace_id=_WS, user_id=_USER)
        assert skipped.status == "skipped"
        scores = (await db_session.execute(select(EvaluationTaskScoreModel))).scalars().all()
        assert scores == []

    async def test_value_outside_domain_rejected(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()
        with pytest.raises(AnnotationValidationError):
            await svc.submit_item(
                item.id, workspace_id=_WS, user_id=_USER, annotations=[{"metric_name": "helpfulness", "value": 1.5}]
            )
        with pytest.raises(AnnotationValidationError):
            await svc.submit_item(
                item.id, workspace_id=_WS, user_id=_USER, annotations=[{"metric_name": "tone", "value_label": "meh"}]
            )

    async def test_categorical_annotation_stores_label_and_index(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()
        await svc.submit_item(
            item.id, workspace_id=_WS, user_id=_USER, annotations=[{"metric_name": "tone", "value_label": "bad"}]
        )
        score = (
            await db_session.execute(select(EvaluationTaskScoreModel).where(EvaluationTaskScoreModel.source == "human"))
        ).scalar_one()
        assert score.value_label == "bad" and score.value == 2.0  # index of "bad"


class TestOverrideAndUpsert:
    async def test_valid_override_persists_pointer(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        task = await _seed_online_task(db_session)
        machine = await _seed_score(db_session, task.id, trace.id, value=0.35)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()

        await svc.submit_item(
            item.id,
            workspace_id=_WS,
            user_id=_USER,
            annotations=[
                {
                    "metric_name": "helpfulness",
                    "value": 0.9,
                    "overrides_score_id": machine.id,
                    "reason_code": "judge_too_harsh",
                    "justification": "answer was correct",
                }
            ],
        )
        human = (
            await db_session.execute(select(EvaluationTaskScoreModel).where(EvaluationTaskScoreModel.source == "human"))
        ).scalar_one()
        assert human.overrides_score_id == machine.id and human.reason_code == "judge_too_harsh"
        assert human.reasoning == "answer was correct"
        await db_session.refresh(machine)
        assert machine.value == 0.35  # original untouched

    async def test_override_without_reason_rejected(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        task = await _seed_online_task(db_session)
        machine = await _seed_score(db_session, task.id, trace.id, value=0.35)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()
        with pytest.raises(AnnotationValidationError):
            await svc.submit_item(
                item.id,
                workspace_id=_WS,
                user_id=_USER,
                annotations=[{"metric_name": "helpfulness", "value": 0.9, "overrides_score_id": machine.id}],
            )

    async def test_cross_target_override_rejected(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        task = await _seed_online_task(db_session)
        other_target = await _seed_trace(db_session)
        machine = await _seed_score(db_session, task.id, other_target.id, value=0.35)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()
        with pytest.raises(AnnotationValidationError):
            await svc.submit_item(
                item.id,
                workspace_id=_WS,
                user_id=_USER,
                annotations=[
                    {
                        "metric_name": "helpfulness",
                        "value": 0.9,
                        "overrides_score_id": machine.id,
                        "reason_code": "other",
                        "justification": "x",
                    }
                ],
            )

    async def test_resubmission_by_same_annotator_upserts(self, db_session: AsyncSession) -> None:
        svc = AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        q1, q2 = await _create_queue(db_session), await _create_queue(db_session, name="second")
        for queue in (q1, q2):
            await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        items = (await db_session.execute(select(AnnotationQueueItemModel))).scalars().all()
        for item in items:
            await svc.submit_item(
                item.id, workspace_id=_WS, user_id=_USER, annotations=[{"metric_name": "helpfulness", "value": 0.7}]
            )
        rows = (
            (
                await db_session.execute(
                    select(EvaluationTaskScoreModel).where(
                        EvaluationTaskScoreModel.source == "human",
                        EvaluationTaskScoreModel.annotator_id == _USER,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1 and rows[0].value == 0.7


class TestItemDetail:
    async def test_suggestions_prefill_latest_machine_scores(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        task_a, task_b = await _seed_online_task(db_session), await _seed_online_task(db_session)
        await _seed_score(db_session, task_a.id, trace.id, metric_name="helpfulness", value=0.35)
        await _seed_score(
            db_session,
            task_b.id,
            trace.id,
            metric_name="helpfulness",
            value=0.5,
            created_at=_NOW + timedelta(seconds=1),
        )
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()

        detail = await svc.get_item_detail(item.id, workspace_id=_WS)
        suggestions = detail["suggestions"]
        assert len(suggestions) == 1
        assert suggestions[0]["metric_name"] == "helpfulness"
        assert suggestions[0]["value"] == 0.5  # latest across tasks wins
        assert suggestions[0]["source"] == "llm_judge"

    async def test_projection_with_events(self, db_session: AsyncSession) -> None:
        queue = await _create_queue(db_session)
        trace = await _seed_trace(db_session)
        store = InMemoryEventStore()
        await store.append(
            Event(
                session_id=trace.session_id,
                superstep=1,
                event_type=EventType.LLM_REQUEST,
                timestamp=_NOW + timedelta(seconds=1),
                payload={"messages": [{"role": "user", "content": "what is X?"}]},
            )
        )
        await store.append(
            Event(
                session_id=trace.session_id,
                superstep=2,
                event_type=EventType.LLM_REQUEST,
                timestamp=_NOW + timedelta(seconds=2),
                payload={"messages": [{"role": "assistant", "content": "X is a thing"}]},
            )
        )
        svc_with_store = AnnotationService(db_session, event_store=store)
        await svc_with_store.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()

        detail = await svc_with_store.get_item_detail(item.id, workspace_id=_WS)
        assert detail["projection"] is not None
        assert detail["projection"]["messages"][-1] == {"role": "assistant", "content": "X is a thing"}


class TestDatasetBackflow:
    async def _seed_projected_trace_with_annotation(
        self, db_session: AsyncSession
    ) -> tuple[AnnotationQueueModel, AnnotationService]:
        """Queue + completed item whose trace projects a full exchange."""
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        trace = await _seed_trace(db_session)
        store = InMemoryEventStore()
        await store.append(
            Event(
                session_id=trace.session_id,
                superstep=1,
                event_type=EventType.LLM_REQUEST,
                timestamp=_NOW + timedelta(seconds=1),
                payload={"messages": [{"role": "user", "content": "what is X?"}]},
            )
        )
        await store.append(
            Event(
                session_id=trace.session_id,
                superstep=2,
                event_type=EventType.LLM_REQUEST,
                timestamp=_NOW + timedelta(seconds=2),
                payload={"messages": [{"role": "assistant", "content": "X is a thing"}]},
            )
        )
        svc = AnnotationService(db_session, event_store=store)
        await svc.add_items(queue.id, workspace_id=_WS, target_ids=[trace.id], added_by=_USER)
        item = (await db_session.execute(select(AnnotationQueueItemModel))).scalar_one()
        await svc.submit_item(
            item.id, workspace_id=_WS, user_id=_USER, annotations=[{"metric_name": "helpfulness", "value": 0.9}]
        )
        return queue, svc

    async def test_push_creates_dataset_and_items(self, db_session: AsyncSession) -> None:
        queue, svc = await self._seed_projected_trace_with_annotation(db_session)

        result = await svc.push_to_dataset(queue.id, workspace_id=_WS, dataset_id=None, dataset_name="human-golden-v1")
        assert result["created"] == 1
        dataset = (
            await db_session.execute(
                select(EvaluationDatasetModel).where(EvaluationDatasetModel.name == "human-golden-v1")
            )
        ).scalar_one()
        rows = (
            (await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset.id)))
            .scalars()
            .all()
        )
        assert rows[0].query == "what is X?" and rows[0].generated_answer == "X is a thing"
        assert rows[0].metadata_["annotation"]["trace_id"] == rows[0].metadata_["annotation"]["trace_id"]
        assert "human-annotation" in rows[0].tags and queue.name in rows[0].tags

    async def test_duplicate_push_is_idempotent(self, db_session: AsyncSession) -> None:
        queue, svc = await self._seed_projected_trace_with_annotation(db_session)
        first = await svc.push_to_dataset(queue.id, workspace_id=_WS, dataset_id=None, dataset_name="golden")
        second = await svc.push_to_dataset(queue.id, workspace_id=_WS, dataset_id=None, dataset_name="golden")
        assert first["created"] == 1 and second["created"] == 0 and second["skipped"] == 1

    async def test_exactly_one_dataset_selector_required(self, db_session: AsyncSession) -> None:
        queue, svc = await _create_queue(db_session), AnnotationService(db_session)
        with pytest.raises(AnnotationValidationError):
            await svc.push_to_dataset(queue.id, workspace_id=_WS, dataset_id=None, dataset_name=None)
