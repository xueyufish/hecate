"""Tests for evaluation report REST API endpoints (7.2e).

Rows are seeded with explicit naive-UTC ``created_at``/``completed_at``
values so the report window filters behave identically on SQLite (where
server_default ``CURRENT_TIMESTAMP`` stores naive UTC) and PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    EvaluationTaskModel,
    EvaluationTaskScoreModel,
)

# Fixed window inside which every seeded row falls.
WINDOW = {"start_date": "2026-09-01T00:00:00Z", "end_date": "2026-09-11T00:00:00Z"}


def _naive(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour)


async def _seed_run(
    db_session,
    dataset_id: uuid.UUID | None = None,
    *,
    summary: dict | None = None,
    status: str = "completed",
    workspace_id: uuid.UUID | None = None,
    created: datetime | None = None,
    completed: datetime | None = None,
    workflow_id: uuid.UUID | None = None,
) -> EvaluationRunModel:
    run = EvaluationRunModel(
        dataset_id=dataset_id or uuid.uuid4(),
        evaluator_configs=["stub"],
        status=status,
        summary=summary,
        workspace_id=workspace_id if workspace_id is not None else uuid.UUID(int=0),
        created_at=created or _naive(5),
        completed_at=completed or _naive(5, 13),
        workflow_id=workflow_id,
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _seed_items(db_session, dataset_id: uuid.UUID, count: int) -> None:
    db_session.add_all(
        [EvaluationItemModel(dataset_id=dataset_id, query=f"q{i}", created_at=_naive(1)) for i in range(count)]
    )
    await db_session.flush()


async def _seed_online_score(
    db_session,
    *,
    value: float,
    metric: str = "helpfulness",
    agent_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    source: str = "llm_judge",
    workspace_id: uuid.UUID | None = None,
    created: datetime | None = None,
) -> EvaluationTaskScoreModel:
    score = EvaluationTaskScoreModel(
        task_id=task_id or uuid.uuid4(),
        target_id=target_id or uuid.uuid4(),
        session_id=session_id,
        agent_id=agent_id,
        metric_name=metric,
        value=value,
        source=source,
        status="error" if value == -1.0 else "completed",
        workspace_id=workspace_id if workspace_id is not None else uuid.UUID(int=0),
        created_at=created or _naive(5),
    )
    db_session.add(score)
    await db_session.flush()
    return score


async def _seed_dataset_with_items(db_session, item_count: int) -> uuid.UUID:
    dataset = EvaluationDatasetModel(name=f"ds-{uuid.uuid4()}", created_at=_naive(1))
    db_session.add(dataset)
    await db_session.flush()
    await _seed_items(db_session, dataset.id, item_count)
    return dataset.id


class TestOverviewReport:
    async def test_overview_mixed_data(self, client, db_session) -> None:
        await _seed_run(db_session, summary={"pass_rate": 0.9})
        await _seed_run(db_session, summary={"pass_rate": 0.7})
        for value in (0.8, 0.6, 1.0):
            await _seed_online_score(db_session, value=value)

        resp = await client.get("/api/evaluation/reports/overview", params=WINDOW)
        assert resp.status_code == 200
        data = resp.json()
        assert data["quality"]["offline_pass_rate"] == 0.8
        assert data["quality"]["offline_runs_counted"] == 2
        assert data["quality"]["online_avg_score"] == 0.8
        assert data["volume"]["completed_runs"] == 2
        assert data["volume"]["online_scored"] == 3
        assert data["coverage"]["active_datasets"] == 2
        assert data["error_rate"]["error_count"] == 0
        assert data["error_rate"]["ratio"] == 0.0

    async def test_overview_empty_workspace_zeroed(self, client) -> None:
        resp = await client.get("/api/evaluation/reports/overview", params=WINDOW)
        assert resp.status_code == 200
        data = resp.json()
        assert data["quality"]["offline_pass_rate"] is None
        assert data["quality"]["online_avg_score"] is None
        assert data["volume"]["completed_runs"] == 0
        assert data["volume"]["online_scored"] == 0
        assert data["coverage"]["active_datasets"] == 0
        assert data["coverage"]["median_items"] is None
        assert data["coverage"]["low_sample_run_ratio"] == 0.0
        assert data["error_rate"]["total_scores"] == 0

    async def test_coverage_low_sample_ratio(self, client, db_session) -> None:
        small = await _seed_dataset_with_items(db_session, 8)
        big_a = await _seed_dataset_with_items(db_session, 50)
        big_b = await _seed_dataset_with_items(db_session, 50)
        await _seed_run(db_session, dataset_id=small, summary={"pass_rate": 1.0})
        await _seed_run(db_session, dataset_id=big_a, summary={"pass_rate": 1.0})
        await _seed_run(db_session, dataset_id=big_b, summary={"pass_rate": 1.0})

        resp = await client.get("/api/evaluation/reports/overview", params=WINDOW)
        assert resp.status_code == 200
        coverage = resp.json()["coverage"]
        assert coverage["low_sample_run_ratio"] == 0.3333
        assert coverage["active_datasets"] == 3
        assert coverage["median_items"] == 50.0

    async def test_error_rate_counts_sentinel_values(self, client, db_session) -> None:
        await _seed_online_score(db_session, value=0.5)
        await _seed_online_score(db_session, value=-1.0)
        run = await _seed_run(db_session, summary=None)
        db_session.add(
            EvaluationScoreModel(
                run_id=run.id,
                item_id=uuid.uuid4(),
                metric_name="faithfulness",
                value=-1.0,
                created_at=_naive(5),
            )
        )
        await db_session.flush()

        resp = await client.get("/api/evaluation/reports/overview", params=WINDOW)
        error_rate = resp.json()["error_rate"]
        assert error_rate["total_scores"] == 3
        assert error_rate["error_count"] == 2
        assert error_rate["ratio"] == 0.6667

    async def test_workspace_isolation(self, client, db_session) -> None:
        foreign = uuid.uuid4()
        await _seed_run(db_session, summary={"pass_rate": 0.9}, workspace_id=foreign)
        await _seed_online_score(db_session, value=0.9, workspace_id=foreign)

        resp = await client.get("/api/evaluation/reports/overview", params=WINDOW)
        assert resp.status_code == 200
        data = resp.json()
        assert data["volume"]["completed_runs"] == 0
        assert data["volume"]["online_scored"] == 0


class TestTrendsReport:
    async def test_offline_trend_same_day_bucket_mean(self, client, db_session) -> None:
        dataset_id = await _seed_dataset_with_items(db_session, 10)
        await _seed_run(db_session, dataset_id=dataset_id, summary={"pass_rate": 1.0}, completed=_naive(5, 10))
        await _seed_run(db_session, dataset_id=dataset_id, summary={"pass_rate": 0.5}, completed=_naive(5, 15))

        resp = await client.get(
            "/api/evaluation/reports/trends", params={**WINDOW, "dimension": "dataset", "bucket": "day"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dimension"] == "dataset"
        assert len(data["series"]) == 1
        series = data["series"][0]
        assert series["kind"] == "offline"
        assert series["metric"] == "pass_rate"
        assert series["group_id"] == str(dataset_id)
        assert series["points"] == [{"bucket": "2026-09-05", "value": 0.75, "count": 2}]

    async def test_hour_bucketing_splits_same_day(self, client, db_session) -> None:
        dataset_id = await _seed_dataset_with_items(db_session, 10)
        await _seed_run(db_session, dataset_id=dataset_id, summary={"pass_rate": 1.0}, completed=_naive(5, 10))
        await _seed_run(db_session, dataset_id=dataset_id, summary={"pass_rate": 0.5}, completed=_naive(5, 15))

        resp = await client.get(
            "/api/evaluation/reports/trends", params={**WINDOW, "dimension": "dataset", "bucket": "hour"}
        )
        assert resp.status_code == 200
        points = resp.json()["series"][0]["points"]
        assert [p["bucket"] for p in points] == ["2026-09-05T10:00", "2026-09-05T15:00"]
        assert [p["value"] for p in points] == [1.0, 0.5]

    async def test_agent_trend_from_online_scores(self, client, db_session) -> None:
        agent = uuid.uuid4()
        await _seed_online_score(db_session, value=0.8, agent_id=agent, metric="helpfulness", created=_naive(4))
        await _seed_online_score(db_session, value=0.6, agent_id=agent, metric="helpfulness", created=_naive(4, 18))
        await _seed_online_score(db_session, value=-1.0, agent_id=agent, metric="helpfulness", created=_naive(4))

        resp = await client.get(
            "/api/evaluation/reports/trends",
            params={**WINDOW, "dimension": "agent", "bucket": "day", "metric_name": "helpfulness"},
        )
        assert resp.status_code == 200
        series = resp.json()["series"]
        assert len(series) == 1
        assert series[0]["kind"] == "online"
        assert series[0]["group_id"] == str(agent)
        assert series[0]["points"] == [{"bucket": "2026-09-04", "value": 0.7, "count": 2}]

    async def test_invalid_dimension_rejected_422(self, client) -> None:
        resp = await client.get("/api/evaluation/reports/trends", params={**WINDOW, "dimension": "session"})
        assert resp.status_code == 422

    async def test_hour_bucket_rejects_over_30d_window(self, client) -> None:
        resp = await client.get(
            "/api/evaluation/reports/trends",
            params={
                "dimension": "dataset",
                "bucket": "hour",
                "start_date": "2026-01-01T00:00:00Z",
                "end_date": "2026-09-11T00:00:00Z",
            },
        )
        assert resp.status_code == 400


class TestDistributionsReport:
    async def test_run_histogram_excludes_errors(self, client, db_session) -> None:
        run = await _seed_run(db_session)
        for value in (0.95, 0.95, 0.95, 0.95):
            db_session.add(
                EvaluationScoreModel(
                    run_id=run.id,
                    item_id=uuid.uuid4(),
                    metric_name="faithfulness",
                    value=value,
                    created_at=_naive(5),
                )
            )
        db_session.add(
            EvaluationScoreModel(
                run_id=run.id,
                item_id=uuid.uuid4(),
                metric_name="faithfulness",
                value=-1.0,
                created_at=_naive(5),
            )
        )
        await db_session.flush()

        resp = await client.get("/api/evaluation/reports/distributions", params={"run_id": str(run.id)})
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "run"
        metric = data["metrics"][0]
        assert metric["metric_name"] == "faithfulness"
        assert metric["count"] == 4
        assert metric["error_count"] == 1
        top_bin = metric["bins"][-1]
        assert top_bin["lower"] == 0.9 and top_bin["upper"] == 1.0
        assert top_bin["count"] == 4
        assert sum(b["count"] for b in metric["bins"]) == 4

    async def test_task_scope_histogram(self, client, db_session) -> None:
        task = EvaluationTaskModel(
            task_type="online",
            name="prod",
            evaluator_configs=["stub"],
            config={},
            created_at=_naive(1),
        )
        db_session.add(task)
        await db_session.flush()
        await _seed_online_score(db_session, value=0.3, task_id=task.id, metric="helpfulness")

        resp = await client.get("/api/evaluation/reports/distributions", params={"task_id": str(task.id)})
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "task"
        assert data["metrics"][0]["count"] == 1
        assert data["metrics"][0]["bins"][3]["count"] == 1

    async def test_scope_validation(self, client) -> None:
        both = await client.get(
            "/api/evaluation/reports/distributions",
            params={"run_id": str(uuid.uuid4()), "task_id": str(uuid.uuid4())},
        )
        assert both.status_code == 400
        neither = await client.get("/api/evaluation/reports/distributions")
        assert neither.status_code == 400

    async def test_unknown_run_404(self, client) -> None:
        resp = await client.get("/api/evaluation/reports/distributions", params={"run_id": str(uuid.uuid4())})
        assert resp.status_code == 404


class TestBreakdownsReport:
    async def test_group_by_source(self, client, db_session) -> None:
        await _seed_online_score(db_session, value=0.8, source="llm_judge")
        await _seed_online_score(db_session, value=0.8, source="llm_judge")
        await _seed_online_score(db_session, value=0.4, source="human")

        resp = await client.get("/api/evaluation/reports/breakdowns", params={**WINDOW, "group_by": "source"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["group_by"] == "source"
        assert data["total"] == 2
        by_key = {g["key"]: g for g in data["groups"]}
        assert by_key["llm_judge"]["metrics"] == [{"metric_name": "helpfulness", "avg": 0.8, "count": 2}]
        assert by_key["human"]["metrics"] == [{"metric_name": "helpfulness", "avg": 0.4, "count": 1}]

    async def test_group_by_agent(self, client, db_session) -> None:
        agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
        await _seed_online_score(db_session, value=0.9, agent_id=agent_a, metric="relevance")
        await _seed_online_score(db_session, value=0.7, agent_id=agent_a, metric="relevance")
        await _seed_online_score(db_session, value=0.2, agent_id=agent_b, metric="relevance")

        resp = await client.get("/api/evaluation/reports/breakdowns", params={**WINDOW, "group_by": "agent"})
        assert resp.status_code == 200
        groups = {g["key"]: g for g in resp.json()["groups"]}
        assert groups[str(agent_a)]["metrics"] == [{"metric_name": "relevance", "avg": 0.8, "count": 2}]
        assert groups[str(agent_b)]["metrics"] == [{"metric_name": "relevance", "avg": 0.2, "count": 1}]

    async def test_metric_filter(self, client, db_session) -> None:
        await _seed_online_score(db_session, value=0.9, metric="relevance")
        await _seed_online_score(db_session, value=0.1, metric="other")

        resp = await client.get(
            "/api/evaluation/reports/breakdowns",
            params={**WINDOW, "group_by": "agent", "metric_name": "relevance"},
        )
        assert resp.status_code == 200
        groups = resp.json()["groups"]
        assert len(groups) == 1
        assert groups[0]["metrics"][0]["metric_name"] == "relevance"

    async def test_invalid_group_by_rejected_422(self, client) -> None:
        resp = await client.get("/api/evaluation/reports/breakdowns", params={**WINDOW, "group_by": "metric"})
        assert resp.status_code == 422


class TestSessionRollupReport:
    async def test_session_aggregates_trace_scores(self, client, db_session) -> None:
        session = uuid.uuid4()
        agent = uuid.uuid4()
        for i, value in enumerate((0.2, 0.6, 1.0)):
            await _seed_online_score(
                db_session,
                value=value,
                agent_id=agent,
                session_id=session,
                target_id=uuid.uuid4(),
                created=_naive(5, 10 + i),
            )

        resp = await client.get("/api/evaluation/reports/sessions", params=WINDOW)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        item = data["items"][0]
        assert item["session_id"] == str(session)
        assert item["agent_id"] == str(agent)
        assert item["trace_count"] == 3
        assert item["metrics"] == [{"metric_name": "helpfulness", "avg": 0.6, "count": 3}]

    async def test_ordering_and_pagination(self, client, db_session) -> None:
        older, newer = uuid.uuid4(), uuid.uuid4()
        await _seed_online_score(db_session, value=0.5, session_id=older, created=_naive(3))
        await _seed_online_score(db_session, value=0.9, session_id=newer, created=_naive(6))

        resp = await client.get("/api/evaluation/reports/sessions", params={**WINDOW, "page_size": 1})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["items"][0]["session_id"] == str(newer)

        second = await client.get("/api/evaluation/reports/sessions", params={**WINDOW, "page_size": 1, "page": 2})
        assert second.status_code == 200
        assert second.json()["items"][0]["session_id"] == str(older)
