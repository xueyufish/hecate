"""Tests for the automated trace-backflow service (7.2d).

Covers rule CRUD validation, candidate selection (score bands, AND filters,
error sentinel, ordering, limit), cross-path idempotency (backflow x
annotation), materialization mapping (multi-turn history, provenance,
expected_answer stays empty), and the per-run summary on the rule.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
    TaskType,
)
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel
from hecate.ops.evaluation.annotation import AnnotationService
from hecate.ops.evaluation.backflow import (
    BackflowRuleNotFoundError,
    BackflowService,
    BackflowValidationError,
)
from hecate.ops.evaluation.trace_dedup import dataset_trace_ids
from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore

_WS = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
_OTHER_WS = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
_NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


def _rule_data(task_id: uuid.UUID, dataset_id: uuid.UUID, **overrides) -> SimpleNamespace:
    payload = SimpleNamespace(
        name="low-score-harvest",
        task_id=task_id,
        dataset_id=dataset_id,
        filters=[{"metric_name": "correctness", "max_score": 0.5}],
        limit=500,
        max_turns=None,
    )
    for key, value in overrides.items():
        setattr(payload, key, value)
    return payload


async def _seed_online_task(db_session: AsyncSession, workspace_id: uuid.UUID = _WS) -> EvaluationTaskModel:
    task = EvaluationTaskModel(
        task_type=TaskType.ONLINE.value,
        name="prod-scoring",
        evaluator_configs=["stub"],
        config={"agent_id": str(uuid.uuid4()), "sampling_rate": 1.0},
        workspace_id=workspace_id,
    )
    db_session.add(task)
    await db_session.flush()
    return task


async def _seed_offline_task(db_session: AsyncSession, workspace_id: uuid.UUID = _WS) -> EvaluationTaskModel:
    task = EvaluationTaskModel(
        task_type=TaskType.OFFLINE.value,
        name="offline-regression",
        evaluator_configs=["stub"],
        config={"dataset_id": str(uuid.uuid4())},
        workspace_id=workspace_id,
    )
    db_session.add(task)
    await db_session.flush()
    return task


async def _seed_dataset(db_session: AsyncSession, workspace_id: uuid.UUID = _WS) -> EvaluationDatasetModel:
    dataset = EvaluationDatasetModel(name=f"corpus-{uuid.uuid4().hex[:8]}", workspace_id=workspace_id)
    db_session.add(dataset)
    await db_session.flush()
    return dataset


async def _seed_trace(db_session: AsyncSession, workspace_id: uuid.UUID = _WS, window_seconds: int = 60) -> TraceModel:
    agent_id = uuid.uuid4()
    session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=agent_id, workspace_id=workspace_id)
    db_session.add(session_row)
    await db_session.flush()
    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type="trace",
        name=f"session:{session_row.id}",
        session_id=session_row.id,
        agent_id=agent_id,
        status="completed",
        start_time=_NOW,
        end_time=_NOW + timedelta(seconds=window_seconds),
        created_at=_NOW,
    )
    db_session.add(trace)
    await db_session.flush()
    return trace


async def _seed_score(
    db_session: AsyncSession,
    task_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    metric_name: str = "correctness",
    value: float = 0.3,
    workspace_id: uuid.UUID = _WS,
    created_at: datetime | None = None,
) -> EvaluationTaskScoreModel:
    row = EvaluationTaskScoreModel(
        task_id=task_id,
        target_id=target_id,
        metric_name=metric_name,
        value=value,
        source="llm_judge",
        created_at=created_at or _NOW,
        workspace_id=workspace_id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_exchanges(store: InMemoryEventStore, session_id: uuid.UUID, contents: list[tuple[str, str]]) -> None:
    superstep = 1
    timestamp = _NOW + timedelta(seconds=1)
    for role, content in contents:
        await store.append(
            Event(
                session_id=session_id,
                superstep=superstep,
                event_type=EventType.LLM_REQUEST,
                timestamp=timestamp,
                payload={"messages": [{"role": role, "content": content}]},
            )
        )
        superstep += 1
        timestamp += timedelta(seconds=1)


async def _dataset_items(db_session: AsyncSession, dataset_id: uuid.UUID) -> list[EvaluationItemModel]:
    rows = await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == dataset_id))
    return list(rows.scalars().all())


def _update_data(**overrides) -> SimpleNamespace:
    payload = SimpleNamespace(name=None, task_id=None, dataset_id=None, filters=None, limit=None, max_turns=None)
    for key, value in overrides.items():
        setattr(payload, key, value)
    return payload


class TestRuleCrud:
    async def test_create_and_get(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        svc = BackflowService(db_session)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        fetched = await svc.get_rule(rule.id, workspace_id=_WS)
        assert fetched is not None
        assert fetched.name == "low-score-harvest"
        assert fetched.limit == 500
        assert fetched.filters == [{"metric_name": "correctness", "max_score": 0.5}]

    async def test_workspace_isolation(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        svc = BackflowService(db_session)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        assert await svc.get_rule(rule.id, workspace_id=_OTHER_WS) is None
        with pytest.raises(BackflowRuleNotFoundError):
            await svc.run_rule(rule.id, workspace_id=_OTHER_WS)

    async def test_reject_offline_task(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_offline_task(db_session), await _seed_dataset(db_session)
        svc = BackflowService(db_session)
        with pytest.raises(BackflowValidationError, match="require an online task"):
            await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)

    async def test_reject_unknown_task(self, db_session: AsyncSession) -> None:
        dataset = await _seed_dataset(db_session)
        svc = BackflowService(db_session)
        with pytest.raises(BackflowValidationError, match="not found"):
            await svc.create_rule(_rule_data(uuid.uuid4(), dataset.id), workspace_id=_WS)

    async def test_reject_unknown_dataset(self, db_session: AsyncSession) -> None:
        task = await _seed_online_task(db_session)
        svc = BackflowService(db_session)
        with pytest.raises(BackflowValidationError, match="not found"):
            await svc.create_rule(_rule_data(task.id, uuid.uuid4()), workspace_id=_WS)

    @pytest.mark.parametrize(
        "filters",
        [
            [{"metric_name": "correctness", "min_score": 0.8, "max_score": 0.3}],
            [],
        ],
    )
    async def test_reject_invalid_filters(self, db_session: AsyncSession, filters: list[dict]) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        svc = BackflowService(db_session)
        with pytest.raises(BackflowValidationError):
            await svc.create_rule(_rule_data(task.id, dataset.id, filters=filters), workspace_id=_WS)

    async def test_reject_duplicate_name(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        svc = BackflowService(db_session)
        await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        other_dataset = await _seed_dataset(db_session)
        with pytest.raises(BackflowValidationError, match="already exists"):
            await svc.create_rule(_rule_data(task.id, other_dataset.id), workspace_id=_WS)

    async def test_update_revalidates_and_applies(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        svc = BackflowService(db_session)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        await svc.create_rule(_rule_data(task.id, dataset.id, name="second-rule"), workspace_id=_WS)
        with pytest.raises(BackflowValidationError, match="already exists"):
            await svc.update_rule(rule.id, workspace_id=_WS, data=_update_data(name="second-rule"))
        with pytest.raises(BackflowValidationError):
            await svc.update_rule(rule.id, workspace_id=_WS, data=_update_data(filters=[]))
        updated = await svc.update_rule(
            rule.id,
            workspace_id=_WS,
            data=_update_data(
                filters=[{"metric_name": "helpfulness", "min_score": 0.9}],
                limit=10,
                max_turns=2,
            ),
        )
        assert updated.limit == 10 and updated.max_turns == 2
        assert updated.filters == [{"metric_name": "helpfulness", "min_score": 0.9}]

    async def test_delete_soft_deletes(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        svc = BackflowService(db_session)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        await svc.delete_rule(rule.id, workspace_id=_WS)
        assert await svc.get_rule(rule.id, workspace_id=_WS) is None


class TestRunSelection:
    async def test_materializes_matching_traces(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        low, high = await _seed_trace(db_session), await _seed_trace(db_session)
        await _seed_score(db_session, task.id, low.id, value=0.3)
        await _seed_score(db_session, task.id, high.id, value=0.7)
        store = InMemoryEventStore()
        await _seed_exchanges(store, low.session_id, [("user", "what is X?"), ("assistant", "X is a thing")])

        svc = BackflowService(db_session, event_store=store)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        result = await svc.run_rule(rule.id, workspace_id=_WS)
        assert result["created"] == 1 and result["skipped"] == 0 and result["dataset_id"] == dataset.id
        items = await _dataset_items(db_session, dataset.id)
        assert len(items) == 1 and items[0].query == "what is X?"

    async def test_error_sentinel_never_matches(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        trace = await _seed_trace(db_session)
        await _seed_score(db_session, task.id, trace.id, value=-1.0)
        svc = BackflowService(db_session)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        result = await svc.run_rule(rule.id, workspace_id=_WS)
        assert result["created"] == 0 and result["skipped"] == 0

    async def test_filters_combine_with_and(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        both, only_correctness = await _seed_trace(db_session), await _seed_trace(db_session)
        for trace in (both, only_correctness):
            await _seed_score(db_session, task.id, trace.id, metric_name="correctness", value=0.2)
        await _seed_score(db_session, task.id, both.id, metric_name="helpfulness", value=0.9)
        await _seed_score(db_session, task.id, only_correctness.id, metric_name="helpfulness", value=0.1)
        store = InMemoryEventStore()
        await _seed_exchanges(store, both.session_id, [("user", "q"), ("assistant", "a")])

        svc = BackflowService(db_session, event_store=store)
        rule = await svc.create_rule(
            _rule_data(
                task.id,
                dataset.id,
                filters=[
                    {"metric_name": "correctness", "max_score": 0.5},
                    {"metric_name": "helpfulness", "min_score": 0.8},
                ],
            ),
            workspace_id=_WS,
        )
        result = await svc.run_rule(rule.id, workspace_id=_WS)
        assert result["created"] == 1
        items = await _dataset_items(db_session, dataset.id)
        assert items[0].query == "q"

    async def test_limit_caps_and_ordering_is_oldest_first(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        traces = []
        for index in range(3):
            trace = await _seed_trace(db_session)
            traces.append(trace)
            await _seed_score(db_session, task.id, trace.id, value=0.2, created_at=_NOW + timedelta(seconds=index))
        store = InMemoryEventStore()
        for trace in traces:
            await _seed_exchanges(store, trace.session_id, [("user", f"q-{trace.id}"), ("assistant", "a")])

        svc = BackflowService(db_session, event_store=store)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id, limit=2), workspace_id=_WS)
        first = await svc.run_rule(rule.id, workspace_id=_WS)
        assert first["created"] == 2
        items = await _dataset_items(db_session, dataset.id)
        assert [item.query for item in items] == [f"q-{traces[0].id}", f"q-{traces[1].id}"]
        second = await svc.run_rule(rule.id, workspace_id=_WS)
        assert second["created"] == 1 and second["skipped"] == 2

    async def test_zero_match_is_not_an_error(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        svc = BackflowService(db_session)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        result = await svc.run_rule(rule.id, workspace_id=_WS)
        assert result == {"created": 0, "skipped": 0, "dataset_id": dataset.id}

    async def test_unprojectable_trace_is_skipped(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        orphan_score_target = uuid.uuid4()  # score for a trace row that is gone
        silent = await _seed_trace(db_session)  # trace exists but has no events
        await _seed_score(db_session, task.id, orphan_score_target, value=0.2)
        await _seed_score(db_session, task.id, silent.id, value=0.3)
        svc = BackflowService(db_session, event_store=InMemoryEventStore())
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        result = await svc.run_rule(rule.id, workspace_id=_WS)
        assert result["created"] == 0 and result["skipped"] == 2


class TestCrossPathIdempotency:
    async def test_rerun_skips_materialized_traces(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        trace = await _seed_trace(db_session)
        await _seed_score(db_session, task.id, trace.id, value=0.2)
        store = InMemoryEventStore()
        await _seed_exchanges(store, trace.session_id, [("user", "q"), ("assistant", "a")])
        svc = BackflowService(db_session, event_store=store)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        assert (await svc.run_rule(rule.id, workspace_id=_WS))["created"] == 1
        rerun = await svc.run_rule(rule.id, workspace_id=_WS)
        assert rerun["created"] == 0 and rerun["skipped"] == 1

    async def test_annotation_path_blocks_backflow(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        trace = await _seed_trace(db_session)
        await _seed_score(db_session, task.id, trace.id, value=0.2)
        db_session.add(
            EvaluationItemModel(
                dataset_id=dataset.id,
                query="existing",
                metadata_={"annotation": {"trace_id": str(trace.id)}},
                workspace_id=_WS,
            )
        )
        await db_session.flush()
        svc = BackflowService(db_session, event_store=InMemoryEventStore())
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        result = await svc.run_rule(rule.id, workspace_id=_WS)
        assert result["created"] == 0 and result["skipped"] == 1

    async def test_backflow_path_blocks_annotation(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        trace = await _seed_trace(db_session)
        await _seed_score(db_session, task.id, trace.id, value=0.2)
        store = InMemoryEventStore()
        await _seed_exchanges(store, trace.session_id, [("user", "q"), ("assistant", "a")])
        svc = BackflowService(db_session, event_store=store)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        await svc.run_rule(rule.id, workspace_id=_WS)
        assert await dataset_trace_ids(db_session, dataset.id) == {str(trace.id)}
        # the annotation service's dedup shares the helper and sees it too
        annotation_svc = AnnotationService(db_session)
        assert await annotation_svc._dataset_trace_ids(dataset.id) == {str(trace.id)}


class TestMaterializationMapping:
    async def test_single_turn_item_shape(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        trace = await _seed_trace(db_session)
        await _seed_score(db_session, task.id, trace.id, value=0.2)
        store = InMemoryEventStore()
        await _seed_exchanges(store, trace.session_id, [("user", "what is X?"), ("assistant", "X is a thing")])
        svc = BackflowService(db_session, event_store=store)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        await svc.run_rule(rule.id, workspace_id=_WS)
        items = await _dataset_items(db_session, dataset.id)
        assert len(items) == 1
        item = items[0]
        assert item.query == "what is X?" and item.generated_answer == "X is a thing"
        assert item.expected_answer is None and item.context is None
        assert "trace-backflow" in item.tags and rule.name in item.tags
        backflow = item.metadata_["backflow"]
        assert backflow["trace_id"] == str(trace.id)
        assert backflow["task_id"] == str(task.id)
        assert backflow["rule_id"] == str(rule.id)
        assert len(backflow["scores"]) == 1
        assert backflow["scores"][0]["metric_name"] == "correctness"
        assert backflow["scores"][0]["value"] == 0.2
        assert backflow["scores"][0]["source"] == "llm_judge"
        assert backflow["scores"][0]["scored_at"] is not None
        assert backflow["conversation_history"] is None
        assert backflow["agent_id"] == str(trace.agent_id)
        assert backflow["session_id"] == str(trace.session_id)

    async def test_multi_turn_history_preserved(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        trace = await _seed_trace(db_session)
        await _seed_score(db_session, task.id, trace.id, value=0.2)
        store = InMemoryEventStore()
        await _seed_exchanges(
            store,
            trace.session_id,
            [("user", "u1"), ("assistant", "a1"), ("user", "u2"), ("assistant", "a2")],
        )
        svc = BackflowService(db_session, event_store=store)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        await svc.run_rule(rule.id, workspace_id=_WS)
        items = await _dataset_items(db_session, dataset.id)
        assert items[0].query == "u2" and items[0].generated_answer == "a2"
        assert items[0].metadata_["backflow"]["conversation_history"] == [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]

    async def test_max_turns_bound_skips_oversize_conversations(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        trace = await _seed_trace(db_session)
        await _seed_score(db_session, task.id, trace.id, value=0.2)
        store = InMemoryEventStore()
        await _seed_exchanges(
            store,
            trace.session_id,
            [
                ("user", "u1"),
                ("assistant", "a1"),
                ("user", "u2"),
                ("assistant", "a2"),
                ("user", "u3"),
                ("assistant", "a3"),
            ],
        )
        svc = BackflowService(db_session, event_store=store)
        tight = await svc.create_rule(_rule_data(task.id, dataset.id, name="tight", max_turns=1), workspace_id=_WS)
        assert (await svc.run_rule(tight.id, workspace_id=_WS))["created"] == 0
        loose = await svc.create_rule(_rule_data(task.id, dataset.id, name="loose", max_turns=2), workspace_id=_WS)
        assert (await svc.run_rule(loose.id, workspace_id=_WS))["created"] == 1

    async def test_oversize_window_skipped_not_truncated(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        trace = await _seed_trace(db_session, window_seconds=1000)
        await _seed_score(db_session, task.id, trace.id, value=0.2)
        store = InMemoryEventStore()
        contents = [(("user", "assistant")[index % 2], f"m-{index}") for index in range(202)]
        await _seed_exchanges(store, trace.session_id, contents)
        svc = BackflowService(db_session, event_store=store)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        result = await svc.run_rule(rule.id, workspace_id=_WS)
        assert result["created"] == 0 and result["skipped"] == 1

    async def test_last_run_summary_recorded(self, db_session: AsyncSession) -> None:
        task, dataset = await _seed_online_task(db_session), await _seed_dataset(db_session)
        trace = await _seed_trace(db_session)
        await _seed_score(db_session, task.id, trace.id, value=0.2)
        store = InMemoryEventStore()
        await _seed_exchanges(store, trace.session_id, [("user", "q"), ("assistant", "a")])
        svc = BackflowService(db_session, event_store=store)
        rule = await svc.create_rule(_rule_data(task.id, dataset.id), workspace_id=_WS)
        await svc.run_rule(rule.id, workspace_id=_WS)
        summary = rule.metadata_["last_run"]
        assert summary["created"] == 1 and summary["skipped"] == 0 and "at" in summary
