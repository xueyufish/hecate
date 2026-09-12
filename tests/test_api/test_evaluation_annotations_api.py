"""Tests for annotation queue REST endpoints (7.4/7.4a).

Covers queue CRUD + intake, claim/skip/submit state transitions with status
codes, push-dataset, calibration statistics, and the ``source`` filter on
the scores endpoint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import EvaluationTaskModel, EvaluationTaskScoreModel, TaskType
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel

_WS_SUFFIX = "aa"
_NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)

_QUEUE_PAYLOAD = {
    "name": "weekly-review",
    "instructions": "Rate the output",
    "metric_defs": [
        {"name": "helpfulness", "data_type": "numeric", "min": 0, "max": 1},
        {"name": "tone", "data_type": "categorical", "categories": ["good", "neutral", "bad"]},
    ],
}


async def _seed_trace(db_session: AsyncSession, workspace_id: uuid.UUID) -> TraceModel:
    session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_id=workspace_id)
    db_session.add(session_row)
    await db_session.flush()
    trace = TraceModel(
        trace_id=uuid.uuid4(),
        type="trace",
        name=f"session:{session_row.id}",
        session_id=session_row.id,
        status="completed",
        start_time=_NOW,
        end_time=_NOW + timedelta(seconds=5),
        created_at=_NOW,
    )
    db_session.add(trace)
    await db_session.flush()
    return trace


async def _seed_online_task(db_session: AsyncSession, workspace_id: uuid.UUID) -> EvaluationTaskModel:
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


class TestQueueEndpoints:
    async def test_create_and_list_with_counts(self, client, db_session) -> None:
        create = await client.post("/api/evaluation/annotation-queues", json=_QUEUE_PAYLOAD)
        assert create.status_code == 201
        queue = create.json()
        assert [d["name"] for d in queue["metric_defs"]] == ["helpfulness", "tone"]

        listing = await client.get("/api/evaluation/annotation-queues")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1
        assert listing.json()["items"][0]["counts"] == {}

    async def test_invalid_metric_defs_422(self, client) -> None:
        payload = {**_QUEUE_PAYLOAD, "metric_defs": [{"name": "x", "data_type": "weird"}]}
        response = await client.post("/api/evaluation/annotation-queues", json=payload)
        assert response.status_code == 422

    async def test_workspace_isolation(self, client, db_session) -> None:
        created = await client.post("/api/evaluation/annotation-queues", json=_QUEUE_PAYLOAD)
        other = await client.get(f"/api/evaluation/annotation-queues/{uuid.uuid4()}")
        assert other.status_code == 404
        assert created.status_code == 201


class TestIntakeAndWorkflowEndpoints:
    async def _queue_with_item(self, client, db_session) -> tuple[str, str, str]:
        queue = (await client.post("/api/evaluation/annotation-queues", json=_QUEUE_PAYLOAD)).json()
        trace = await _seed_trace(db_session, uuid.UUID(queue["workspace_id"]))
        added = await client.post(
            f"/api/evaluation/annotation-queues/{queue['id']}/items",
            json={"target_ids": [str(trace.id)]},
        )
        assert added.status_code == 200 and added.json()["created"] == 1
        items = (await client.get(f"/api/evaluation/annotation-queues/{queue['id']}/items")).json()
        return queue["id"], items["items"][0]["id"], queue["workspace_id"]

    async def test_from_task_intake(self, client, db_session) -> None:
        queue = (await client.post("/api/evaluation/annotation-queues", json=_QUEUE_PAYLOAD)).json()
        ws = uuid.UUID(queue["workspace_id"])
        task = await _seed_online_task(db_session, ws)
        trace = await _seed_trace(db_session, ws)
        db_session.add(
            EvaluationTaskScoreModel(
                task_id=task.id, target_id=trace.id, metric_name="helpfulness", value=0.3, workspace_id=ws
            )
        )
        await db_session.flush()

        response = await client.post(
            f"/api/evaluation/annotation-queues/{queue['id']}/items/from-task",
            json={"task_id": str(task.id), "max_score": 0.5},
        )
        assert response.status_code == 200
        assert response.json()["created"] == 1

    async def test_claim_skip_submit_statuses(self, client, db_session) -> None:
        queue_id, item_id, ws = await self._queue_with_item(client, db_session)
        base = f"/api/evaluation/annotation-queues/{queue_id}/items/{item_id}"

        claimed = await client.post(f"{base}/claim")
        assert claimed.status_code == 200 and claimed.json()["status"] == "claimed"

        skipped = await client.post(f"{base}/skip")
        assert skipped.status_code == 200 and skipped.json()["status"] == "skipped"

        resubmit = await client.post(
            f"{base}/submit", json={"annotations": [{"metric_name": "helpfulness", "value": 0.5}]}
        )
        assert resubmit.status_code == 409

    async def test_submit_persists_human_score_and_409_after(self, client, db_session) -> None:
        queue_id, item_id, _ws = await self._queue_with_item(client, db_session)
        base = f"/api/evaluation/annotation-queues/{queue_id}/items/{item_id}"
        await client.post(f"{base}/claim")

        submitted = await client.post(
            f"{base}/submit",
            json={
                "annotations": [
                    {"metric_name": "helpfulness", "value": 0.8},
                    {"metric_name": "tone", "value_label": "good"},
                ]
            },
        )
        assert submitted.status_code == 200 and submitted.json()["status"] == "completed"

        scores = (await client.get("/api/evaluation/scores?source=human")).json()
        assert scores["total"] == 2
        assert all(item["task_id"] is None and item["annotator_id"] for item in scores["items"])

        again = await client.post(
            f"{base}/submit", json={"annotations": [{"metric_name": "helpfulness", "value": 0.9}]}
        )
        assert again.status_code == 409

    async def test_invalid_value_400(self, client, db_session) -> None:
        queue_id, item_id, _ws = await self._queue_with_item(client, db_session)
        response = await client.post(
            f"/api/evaluation/annotation-queues/{queue_id}/items/{item_id}/submit",
            json={"annotations": [{"metric_name": "helpfulness", "value": 1.5}]},
        )
        assert response.status_code == 400

    async def test_item_detail_returns_suggestions(self, client, db_session) -> None:
        queue_id, item_id, ws = await self._queue_with_item(client, db_session)
        task = await _seed_online_task(db_session, uuid.UUID(ws))
        items = (await client.get(f"/api/evaluation/annotation-queues/{queue_id}/items")).json()
        target_id = items["items"][0]["target_id"]
        db_session.add(
            EvaluationTaskScoreModel(
                task_id=task.id,
                target_id=uuid.UUID(target_id),
                metric_name="helpfulness",
                value=0.35,
                workspace_id=uuid.UUID(ws),
            )
        )
        await db_session.flush()

        detail = await client.get(f"/api/evaluation/annotation-queues/{queue_id}/items/{item_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["suggestions"][0]["value"] == 0.35
        assert body["item"]["status"] == "pending"

    async def test_unknown_item_404(self, client, db_session) -> None:
        queue = (await client.post("/api/evaluation/annotation-queues", json=_QUEUE_PAYLOAD)).json()
        response = await client.get(f"/api/evaluation/annotation-queues/{queue['id']}/items/{uuid.uuid4()}")
        assert response.status_code == 404


class TestPushDatasetEndpoint:
    async def test_push_requires_dataset_selector(self, client, db_session) -> None:
        queue = (await client.post("/api/evaluation/annotation-queues", json=_QUEUE_PAYLOAD)).json()
        response = await client.post(f"/api/evaluation/annotation-queues/{queue['id']}/push-dataset", json={})
        assert response.status_code == 400


class TestCalibrationEndpoint:
    async def test_numeric_calibration_stats(self, client, db_session) -> None:
        queue = (await client.post("/api/evaluation/annotation-queues", json=_QUEUE_PAYLOAD)).json()
        ws = uuid.UUID(queue["workspace_id"])
        task = await _seed_online_task(db_session, ws)

        pairs = [
            (0.9, 0.9),
            (0.8, 0.8),
            (0.7, 0.7),
            (0.6, 0.6),
            (0.5, 0.5),
            (0.4, 0.4),
            (0.3, 0.3),
            (0.2, 0.2),
            (0.1, 0.1),
            (0.35, 0.9),
        ]
        for index, (machine, human_value) in enumerate(pairs):
            trace = await _seed_trace(db_session, ws)
            db_session.add(
                EvaluationTaskScoreModel(
                    task_id=task.id,
                    target_id=trace.id,
                    metric_name="helpfulness",
                    value=machine,
                    created_at=_NOW + timedelta(seconds=index),
                    workspace_id=ws,
                )
            )
            db_session.add(
                EvaluationTaskScoreModel(
                    task_id=None,
                    target_id=trace.id,
                    metric_name="helpfulness",
                    value=human_value,
                    source="human",
                    annotator_id=uuid.uuid4(),
                    created_at=_NOW + timedelta(seconds=100 + index),
                    workspace_id=ws,
                )
            )
        await db_session.flush()

        response = await client.get("/api/evaluation/calibration?metric_name=helpfulness")
        assert response.status_code == 200
        metric = response.json()["metrics"][0]
        assert metric["pair_count"] == 10
        assert metric["agreement_rate"] == 0.9  # 9 within 0.1 tolerance
        assert metric["mae"] == 0.055  # one 0.55 outlier across 10 pairs
        assert metric["data_mode"] == "numeric"
        assert metric["heatmap"]

    async def test_workspace_isolation(self, client, db_session) -> None:
        response = await client.get("/api/evaluation/calibration")
        assert response.status_code == 200
        assert response.json()["metrics"] == []
