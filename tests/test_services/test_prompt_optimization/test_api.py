"""API tests for the prompt optimization surface (flag gating + lifecycle)."""

from __future__ import annotations

import uuid

import pytest

from hecate.core.config import settings
from hecate.models.prompt_optimization import (
    PromptOptimizationCandidateModel,
    PromptOptimizationRunModel,
)
from tests.test_services.test_prompt_optimization.helpers import (
    dataset_items,
    seed_agent,
    seed_dataset_version,
    seed_prompt,
    ws_id,
)

CREATE_BODY = {
    "evaluator_configs": ["stub"],
    "primary_metric": "stub",
}


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PROMPT_OPTIMIZATION_ENABLED", True)


async def test_create_run_returns_404_when_flag_off(client, db_session, default_workspace) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    body = {
        "prompt_id": str(prompt.id),
        "agent_id": str(agent.id),
        "dataset_id": str(version.dataset_id),
        "dataset_version_id": str(version.id),
        **CREATE_BODY,
    }
    response = await client.post("/api/prompt-optimization/runs", json=body)
    assert response.status_code == 404


async def test_create_run_returns_202_and_persists_created_row(
    client, db_session, default_workspace, flag_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    # Registry + background handoff are stubbed: the API test covers the
    # HTTP contract, the loop logic lives in test_runner.

    class StubEvaluator:
        @property
        def name(self) -> str:
            return "stub"

        @property
        def description(self) -> str:
            return "stub"

    monkeypatch.setattr(
        "hecate.ops.prompt_optimization.service.get_evaluator_class",
        lambda name: StubEvaluator if name == "stub" else None,
    )
    spawned: list[str] = []

    class StubRunner:
        def __init__(self, db) -> None:
            pass

        async def run_in_background(self, run_id) -> None:
            spawned.append(str(run_id))

    monkeypatch.setattr("hecate.ops.api.prompt_optimization.PromptOptimizationRunner", StubRunner)

    body = {
        "prompt_id": str(prompt.id),
        "agent_id": str(agent.id),
        "dataset_id": str(version.dataset_id),
        "dataset_version_id": str(version.id),
        **CREATE_BODY,
    }
    response = await client.post("/api/prompt-optimization/runs", json=body)
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "created"
    assert payload["budget_preset"] == "medium"
    assert spawned == [payload["id"]]

    row = await db_session.get(PromptOptimizationRunModel, uuid.UUID(payload["id"]))
    assert row is not None
    assert row.prompt_id == prompt.id


async def test_create_run_validation_error_maps_to_400(client, db_session, default_workspace, flag_on) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(1))
    await db_session.flush()

    body = {
        "prompt_id": str(prompt.id),
        "agent_id": str(agent.id),
        "dataset_id": str(version.dataset_id),
        "dataset_version_id": str(version.id),
        **CREATE_BODY,
    }
    response = await client.post("/api/prompt-optimization/runs", json=body)
    assert response.status_code == 400
    assert "at least 2 items" in response.json()["detail"]


async def test_list_and_get_runs_scoped(client, db_session, default_workspace, flag_on) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    run = PromptOptimizationRunModel(
        id=uuid.uuid4(),
        prompt_id=prompt.id,
        base_version=1,
        agent_id=agent.id,
        dataset_id=version.dataset_id,
        dataset_version_id=version.id,
        dataset_version_hash=version.content_hash,
        split_ratio=0.8,
        evaluator_configs=["stub"],
        primary_metric="stub",
        min_improvement=0.02,
        max_regression=0.05,
        budget_preset="light",
        max_rounds=2,
        mutation_call_limit=10,
        rollout_item_limit=100,
        strategy="reflective_mutation",
        reflection_model="m",
        status="awaiting_review",
        workspace_id=ws,
    )
    db_session.add(run)
    await db_session.flush()

    listing = await client.get("/api/prompt-optimization/runs")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1

    detail = await client.get(f"/api/prompt-optimization/runs/{run.id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "awaiting_review"

    missing = await client.get(f"/api/prompt-optimization/runs/{__import__('uuid').uuid4()}")
    assert missing.status_code == 404


async def test_reject_endpoint_requires_reason(client, db_session, default_workspace, flag_on) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    run = PromptOptimizationRunModel(
        id=uuid.uuid4(),
        prompt_id=prompt.id,
        base_version=1,
        agent_id=agent.id,
        dataset_id=version.dataset_id,
        dataset_version_id=version.id,
        dataset_version_hash=version.content_hash,
        split_ratio=0.8,
        evaluator_configs=["stub"],
        primary_metric="stub",
        min_improvement=0.02,
        max_regression=0.05,
        budget_preset="light",
        max_rounds=2,
        mutation_call_limit=10,
        rollout_item_limit=100,
        strategy="reflective_mutation",
        reflection_model="m",
        status="awaiting_review",
        workspace_id=ws,
    )
    db_session.add(run)
    candidate = PromptOptimizationCandidateModel(
        run_id=run.id,
        round_no=1,
        template="T {{q}}",
        status="pending_review",
        workspace_id=ws,
    )
    db_session.add(candidate)
    await db_session.flush()

    empty = await client.post(f"/api/prompt-optimization/candidates/{candidate.id}/reject", json={"reason": ""})
    assert empty.status_code == 422  # reason is mandatory (min_length=1)

    ok = await client.post(
        f"/api/prompt-optimization/candidates/{candidate.id}/reject", json={"reason": "worse on tone"}
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "rejected"
    assert ok.json()["rejection_reason"] == "worse on tone"
