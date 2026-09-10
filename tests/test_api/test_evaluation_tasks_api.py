"""Tests for evaluation task REST API endpoints (7.2c).

The evaluator class registry is empty in the test environment (it is
populated by ``register_evaluators`` at app startup), so validation tests
seed it via monkeypatch — same approach the existing evaluation API tests
take by only exercising the invalid-evaluator path.
"""

from __future__ import annotations

import uuid

import pytest

from hecate.ops.evaluation.types import EvalInput, EvalOutput, Score


class _StubEvaluator:
    """Minimal evaluator standing in for a registered built-in."""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def description(self) -> str:
        return "stub evaluator"

    async def evaluate(self, input: EvalInput) -> EvalOutput:  # noqa: A002 — ABC signature
        return EvalOutput(scores=[Score(metric_name="stub", value=0.5, source="deterministic")])


@pytest.fixture
def seeded_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``stub`` resolve through the service's evaluator lookup."""
    import hecate.ops.evaluation.tasks.service as service_mod

    monkeypatch.setattr(
        service_mod,
        "get_evaluator_class",
        lambda name: _StubEvaluator if name == "stub" else None,
    )


def _offline_payload(**overrides) -> dict:
    payload = {
        "task_type": "offline",
        "name": "nightly-regression",
        "dataset_id": str(uuid.uuid4()),
        "evaluators": ["stub"],
    }
    payload.update(overrides)
    return payload


def _online_payload(**overrides) -> dict:
    payload = {
        "task_type": "online",
        "name": "prod-relevancy",
        "agent_id": str(uuid.uuid4()),
        "sampling_rate": 0.1,
        "evaluators": ["stub"],
    }
    payload.update(overrides)
    return payload


class TestTaskCrudEndpoints:
    async def test_create_offline_task_201(self, client: object, seeded_registry: None) -> None:
        response = await client.post("/api/evaluation/tasks", json=_offline_payload())  # type: ignore[union-attr]
        assert response.status_code == 201
        data = response.json()
        assert data["task_type"] == "offline"
        assert data["status"] == "active"
        assert data["config"]["answer_source"] == "manual"
        assert data["evaluator_configs"] == ["stub"]

    async def test_create_online_task_201(self, client: object, seeded_registry: None) -> None:
        response = await client.post("/api/evaluation/tasks", json=_online_payload())  # type: ignore[union-attr]
        assert response.status_code == 201
        data = response.json()
        assert data["config"]["sampling_rate"] == 0.1
        assert data["config"]["max_traces_per_cycle"] == 50

    async def test_create_online_task_invalid_sampling_rate_rejected(
        self, client: object, seeded_registry: None
    ) -> None:
        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/tasks", json=_online_payload(sampling_rate=0)
        )
        assert response.status_code == 422

    async def test_create_offline_task_missing_dataset_rejected(self, client: object, seeded_registry: None) -> None:
        payload = _offline_payload()
        del payload["dataset_id"]
        response = await client.post("/api/evaluation/tasks", json=payload)  # type: ignore[union-attr]
        assert response.status_code == 400

    async def test_create_task_unknown_evaluator_rejected(self, client: object, seeded_registry: None) -> None:
        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/tasks", json=_offline_payload(evaluators=["nonexistent"])
        )
        assert response.status_code == 400
        assert "nonexistent" in response.json()["detail"]["error"]["message"]

    async def test_agent_answer_source_without_agent_id_rejected(self, client: object, seeded_registry: None) -> None:
        response = await client.post(  # type: ignore[union-attr]
            "/api/evaluation/tasks", json=_offline_payload(answer_source="agent")
        )
        assert response.status_code == 400

    async def test_get_task_404_for_unknown(self, client: object) -> None:
        response = await client.get(f"/api/evaluation/tasks/{uuid.uuid4()}")  # type: ignore[union-attr]
        assert response.status_code == 404

    async def test_workspace_isolation(self, client: object, seeded_registry: None, db_session: object) -> None:
        """A task from another workspace behaves as nonexistent."""
        other_workspace = uuid.uuid4()
        from hecate.models.evaluation import EvaluationTaskModel

        foreign = EvaluationTaskModel(
            task_type="offline",
            name="foreign-task",
            evaluator_configs=["stub"],
            config={"dataset_id": str(uuid.uuid4()), "answer_source": "manual"},
            workspace_id=other_workspace,
        )
        db_session.add(foreign)  # type: ignore[union-attr]
        await db_session.flush()  # type: ignore[union-attr]

        get_resp = await client.get(f"/api/evaluation/tasks/{foreign.id}")  # type: ignore[union-attr]
        assert get_resp.status_code == 404

        list_resp = await client.get("/api/evaluation/tasks")  # type: ignore[union-attr]
        assert list_resp.status_code == 200
        assert foreign.id not in {item["id"] for item in list_resp.json()["items"]}

    async def test_update_and_delete_task(self, client: object, seeded_registry: None) -> None:
        create_resp = await client.post("/api/evaluation/tasks", json=_offline_payload())  # type: ignore[union-attr]
        task_id = create_resp.json()["id"]

        update_resp = await client.put(  # type: ignore[union-attr]
            f"/api/evaluation/tasks/{task_id}", json={"name": "renamed", "threshold": 0.7}
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["name"] == "renamed"
        assert update_resp.json()["config"]["threshold"] == 0.7

        delete_resp = await client.delete(f"/api/evaluation/tasks/{task_id}")  # type: ignore[union-attr]
        assert delete_resp.status_code == 204

        get_resp = await client.get(f"/api/evaluation/tasks/{task_id}")  # type: ignore[union-attr]
        assert get_resp.status_code == 404

    async def test_list_tasks_filter_by_type(self, client: object, seeded_registry: None) -> None:
        await client.post("/api/evaluation/tasks", json=_offline_payload())  # type: ignore[union-attr]
        await client.post("/api/evaluation/tasks", json=_online_payload(name="second"))  # type: ignore[union-attr]

        offline = await client.get("/api/evaluation/tasks?task_type=offline")  # type: ignore[union-attr]
        assert offline.status_code == 200
        assert offline.json()["total"] == 1
        assert offline.json()["items"][0]["task_type"] == "offline"


class TestTaskLifecycleEndpoints:
    async def test_enable_disable_online_task(self, client: object, seeded_registry: None) -> None:
        create_resp = await client.post("/api/evaluation/tasks", json=_online_payload())  # type: ignore[union-attr]
        task_id = create_resp.json()["id"]

        disable_resp = await client.post(f"/api/evaluation/tasks/{task_id}/disable")  # type: ignore[union-attr]
        assert disable_resp.status_code == 200
        assert disable_resp.json()["status"] == "disabled"

        enable_resp = await client.post(f"/api/evaluation/tasks/{task_id}/enable")  # type: ignore[union-attr]
        assert enable_resp.status_code == 200
        assert enable_resp.json()["status"] == "active"

    async def test_enable_offline_task_rejected(self, client: object, seeded_registry: None) -> None:
        create_resp = await client.post("/api/evaluation/tasks", json=_offline_payload())  # type: ignore[union-attr]
        task_id = create_resp.json()["id"]
        response = await client.post(f"/api/evaluation/tasks/{task_id}/enable")  # type: ignore[union-attr]
        assert response.status_code == 400

    async def test_enable_unknown_evaluator_rejected(self, client: object, seeded_registry: None) -> None:
        create_resp = await client.post("/api/evaluation/tasks", json=_online_payload())  # type: ignore[union-attr]
        task_id = create_resp.json()["id"]

        # Simulate the evaluator disappearing between create and enable.
        import hecate.ops.evaluation.tasks.service as service_mod

        original = service_mod.get_evaluator_class
        try:
            service_mod.get_evaluator_class = lambda name: None  # type: ignore[assignment]
            response = await client.post(f"/api/evaluation/tasks/{task_id}/enable")  # type: ignore[union-attr]
            assert response.status_code == 400
        finally:
            service_mod.get_evaluator_class = original  # type: ignore[assignment]


class TestTaskRunEndpoints:
    async def test_trigger_run_returns_202(self, client: object, seeded_registry: None) -> None:
        create_resp = await client.post("/api/evaluation/tasks", json=_offline_payload())  # type: ignore[union-attr]
        task_id = create_resp.json()["id"]

        from hecate.ops.evaluation.tasks import runner as runner_mod

        async def _noop(self: object, run_id: object, task: object) -> None:  # noqa: ARG001
            return None

        original = runner_mod.OfflineTaskRunner.run_in_background
        runner_mod.OfflineTaskRunner.run_in_background = _noop  # type: ignore[method-assign]
        try:
            response = await client.post(f"/api/evaluation/tasks/{task_id}/runs")  # type: ignore[union-attr]
        finally:
            runner_mod.OfflineTaskRunner.run_in_background = original  # type: ignore[method-assign]

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "pending"

    async def test_trigger_run_on_online_task_rejected(self, client: object, seeded_registry: None) -> None:
        create_resp = await client.post("/api/evaluation/tasks", json=_online_payload())  # type: ignore[union-attr]
        task_id = create_resp.json()["id"]
        response = await client.post(f"/api/evaluation/tasks/{task_id}/runs")  # type: ignore[union-attr]
        assert response.status_code == 400

    async def test_list_task_runs_404_for_unknown_task(self, client: object) -> None:
        response = await client.get(f"/api/evaluation/tasks/{uuid.uuid4()}/runs")  # type: ignore[union-attr]
        assert response.status_code == 404

    async def test_runs_endpoint_filters_by_task_id(
        self, client: object, seeded_registry: None, db_session: object
    ) -> None:
        """GET /api/evaluation/runs?task_id= only returns that task's runs."""
        create_resp = await client.post("/api/evaluation/tasks", json=_offline_payload())  # type: ignore[union-attr]
        task = create_resp.json()

        from hecate.models.evaluation import EvaluationRunModel

        run = EvaluationRunModel(
            dataset_id=uuid.uuid4(),
            task_id=uuid.UUID(task["id"]),
            status="pending",
            workspace_id=uuid.UUID(task["workspace_id"]),
        )
        db_session.add(run)  # type: ignore[union-attr]
        await db_session.flush()  # type: ignore[union-attr]

        response = await client.get(f"/api/evaluation/runs?task_id={task['id']}")  # type: ignore[union-attr]
        assert response.status_code == 200
        assert response.json()["total"] == 1
        item = response.json()["items"][0]
        assert item["task_id"] == task["id"]
        assert item["summary"] is None


class TestScoreEndpoints:
    async def test_list_scores_empty(self, client: object) -> None:
        response = await client.get("/api/evaluation/scores")  # type: ignore[union-attr]
        assert response.status_code == 200
        assert response.json()["total"] == 0

    async def test_list_scores_filter_by_task(self, client: object, seeded_registry: None, db_session: object) -> None:
        """Scores are visible through the API and filtered by task_id.

        The score row is written directly with the request workspace's id
        (obtained from a created task) so the workspace filter matches.
        """
        create_resp = await client.post("/api/evaluation/tasks", json=_online_payload(sampling_rate=1.0))  # type: ignore[union-attr]
        task = create_resp.json()

        from hecate.models.evaluation import EvaluationTaskScoreModel

        score = EvaluationTaskScoreModel(
            task_id=uuid.UUID(task["id"]),
            target_id=uuid.uuid4(),
            session_id=uuid.UUID("00000000-0000-0000-0000-00000000abcd"),
            metric_name="relevancy",
            value=0.8,
            workspace_id=uuid.UUID(task["workspace_id"]),
        )
        db_session.add(score)  # type: ignore[union-attr]
        await db_session.flush()  # type: ignore[union-attr]

        response = await client.get(f"/api/evaluation/scores?task_id={task['id']}")  # type: ignore[union-attr]
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["metric_name"] == "relevancy"

        by_session = await client.get(  # type: ignore[union-attr]
            "/api/evaluation/scores?session_id=00000000-0000-0000-0000-00000000abcd"
        )
        assert by_session.status_code == 200
        assert by_session.json()["total"] == 1

        no_match = await client.get(f"/api/evaluation/scores?session_id={uuid.uuid4()}")  # type: ignore[union-attr]
        assert no_match.json()["total"] == 0
