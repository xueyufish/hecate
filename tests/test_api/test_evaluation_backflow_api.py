"""Tests for backflow rule REST endpoints (7.2d).

Covers rule CRUD status codes, validation errors (schema 422 / cross-object
400), workspace isolation, and the run endpoint's response shape. Selection,
projection, and idempotency behavior are covered in the service tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import EvaluationTaskScoreModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel


class _StubEvaluator:
    """Placeholder class so the ``stub`` evaluator name resolves."""


@pytest.fixture
def seeded_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``stub`` resolve through the service's evaluator lookup."""
    import hecate.ops.evaluation.tasks.service as service_mod

    monkeypatch.setattr(
        service_mod,
        "get_evaluator_class",
        lambda name: _StubEvaluator if name == "stub" else None,
    )


_NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


async def _seed_dataset_and_task(client, db_session: AsyncSession) -> tuple[str, str, str]:
    """Create a dataset and an online task through the API; return their ids."""
    dataset = (await client.post("/api/evaluation/datasets", json={"name": f"corpus-{uuid.uuid4().hex[:8]}"})).json()
    task = (
        await client.post(
            "/api/evaluation/tasks",
            json={
                "task_type": "online",
                "name": "prod-scoring",
                "agent_id": str(uuid.uuid4()),
                "sampling_rate": 0.1,
                "evaluators": ["stub"],
            },
        )
    ).json()
    return dataset["id"], task["id"], task["workspace_id"]


def _rule_payload(task_id: str, dataset_id: str, **overrides) -> dict:
    payload = {
        "name": "low-score-harvest",
        "task_id": task_id,
        "dataset_id": dataset_id,
        "filters": [{"metric_name": "correctness", "max_score": 0.5}],
    }
    payload.update(overrides)
    return payload


class TestRuleEndpoints:
    async def test_create_list_get_update_delete(self, client, seeded_registry: None) -> None:
        dataset_id, task_id, _ws = await _seed_dataset_and_task(client, None)

        created = await client.post("/api/evaluation/backflow-rules", json=_rule_payload(task_id, dataset_id))
        assert created.status_code == 201
        rule = created.json()
        assert rule["name"] == "low-score-harvest"
        assert rule["limit"] == 500 and rule["max_turns"] is None
        assert rule["filters"] == [{"metric_name": "correctness", "max_score": 0.5}]

        listing = await client.get("/api/evaluation/backflow-rules")
        assert listing.status_code == 200
        assert listing.json()["total"] == 1

        fetched = await client.get(f"/api/evaluation/backflow-rules/{rule['id']}")
        assert fetched.status_code == 200 and fetched.json()["id"] == rule["id"]

        updated = await client.put(f"/api/evaluation/backflow-rules/{rule['id']}", json={"limit": 10, "max_turns": 3})
        assert updated.status_code == 200
        assert updated.json()["limit"] == 10 and updated.json()["max_turns"] == 3

        deleted = await client.delete(f"/api/evaluation/backflow-rules/{rule['id']}")
        assert deleted.status_code == 204
        assert (await client.get(f"/api/evaluation/backflow-rules/{rule['id']}")).status_code == 404

    async def test_unknown_rule_404(self, client) -> None:
        response = await client.get(f"/api/evaluation/backflow-rules/{uuid.uuid4()}")
        assert response.status_code == 404

    async def test_inverted_score_band_400(self, client, seeded_registry: None) -> None:
        dataset_id, task_id, _ws = await _seed_dataset_and_task(client, None)
        response = await client.post(
            "/api/evaluation/backflow-rules",
            json=_rule_payload(
                task_id, dataset_id, filters=[{"metric_name": "correctness", "min_score": 0.8, "max_score": 0.3}]
            ),
        )
        assert response.status_code == 400

    async def test_offline_task_400(self, client, seeded_registry: None) -> None:
        dataset_id, task_id, _ws = await _seed_dataset_and_task(client, None)
        offline = await client.post(
            "/api/evaluation/tasks",
            json={
                "task_type": "offline",
                "name": "offline-regression",
                "dataset_id": dataset_id,
                "evaluators": ["stub"],
            },
        )
        assert offline.status_code == 201
        response = await client.post(
            "/api/evaluation/backflow-rules", json=_rule_payload(offline.json()["id"], dataset_id)
        )
        assert response.status_code == 400

    async def test_empty_filters_422(self, client, seeded_registry: None) -> None:
        dataset_id, task_id, _ws = await _seed_dataset_and_task(client, None)
        response = await client.post(
            "/api/evaluation/backflow-rules", json=_rule_payload(task_id, dataset_id, filters=[])
        )
        assert response.status_code == 422

    async def test_workspace_isolation_404_on_run(self, client, seeded_registry: None) -> None:
        response = await client.post(f"/api/evaluation/backflow-rules/{uuid.uuid4()}/run")
        assert response.status_code == 404


class TestRunEndpoint:
    async def test_run_with_no_matches(self, client, seeded_registry: None) -> None:
        dataset_id, task_id, _ws = await _seed_dataset_and_task(client, None)
        rule = (await client.post("/api/evaluation/backflow-rules", json=_rule_payload(task_id, dataset_id))).json()
        response = await client.post(f"/api/evaluation/backflow-rules/{rule['id']}/run")
        assert response.status_code == 200
        body = response.json()
        assert body["created"] == 0 and body["skipped"] == 0 and body["dataset_id"] == dataset_id

    async def test_run_skips_unprojectable_trace(self, client, seeded_registry: None, db_session) -> None:
        dataset_id, task_id, ws = await _seed_dataset_and_task(client, None)
        rule = (await client.post("/api/evaluation/backflow-rules", json=_rule_payload(task_id, dataset_id))).json()

        session_row = SessionModel(conversation_id=uuid.uuid4(), agent_id=uuid.uuid4(), workspace_id=uuid.UUID(ws))
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
        db_session.add(
            EvaluationTaskScoreModel(
                task_id=uuid.UUID(task_id),
                target_id=trace.id,
                metric_name="correctness",
                value=0.2,
                workspace_id=uuid.UUID(ws),
            )
        )
        await db_session.flush()

        response = await client.post(f"/api/evaluation/backflow-rules/{rule['id']}/run")
        assert response.status_code == 200
        # the trace has no session events, so projection yields nothing
        assert response.json()["created"] == 0 and response.json()["skipped"] == 1
