"""Tests for PromptOptimizationService — run creation validation + cancel."""

from __future__ import annotations

import uuid

import pytest

from hecate.models.prompt_optimization import PromptOptimizationRunCreateSchema
from hecate.ops.prompt_optimization.service import (
    PromptOptimizationService,
    RunConflictError,
)
from tests.test_services.test_prompt_optimization.helpers import (
    dataset_items,
    seed_agent,
    seed_dataset_version,
    seed_prompt,
    ws_id,
)


def _create_data(prompt, agent, version, **overrides) -> PromptOptimizationRunCreateSchema:
    prompt_id = prompt if isinstance(prompt, uuid.UUID) else prompt.id
    agent_id = agent if isinstance(agent, uuid.UUID) else agent.id
    payload = {
        "prompt_id": prompt_id,
        "agent_id": agent_id,
        "dataset_id": version.dataset_id,
        "dataset_version_id": version.id,
        "evaluator_configs": ["stub"],
        "primary_metric": "stub",
    }
    payload.update(overrides)
    return PromptOptimizationRunCreateSchema(**payload)


@pytest.fixture
def _stub_evaluator_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_services.test_prompt_optimization.test_runner import StubEvaluator

    monkeypatch.setattr(
        "hecate.ops.prompt_optimization.service.get_evaluator_class",
        lambda name: StubEvaluator if name == "stub" else None,
    )


async def test_create_run_happy_path_applies_medium_preset(
    db_session, default_workspace, _stub_evaluator_registry
) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    run = await PromptOptimizationService(db_session).create_run(_create_data(prompt, agent, version), ws)

    assert run.status == "created"
    assert run.base_version == 1
    assert (run.max_rounds, run.mutation_call_limit, run.rollout_item_limit) == (5, 25, 400)
    assert run.dataset_version_hash == version.content_hash
    assert run.reflection_model  # defaulted


async def test_create_run_explicit_caps_override_preset(
    db_session, default_workspace, _stub_evaluator_registry
) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    run = await PromptOptimizationService(db_session).create_run(
        _create_data(prompt, agent, version, budget_preset="light", max_rounds=1, rollout_item_limit=12), ws
    )
    assert (run.max_rounds, run.mutation_call_limit, run.rollout_item_limit) == (1, 10, 12)


async def test_create_run_rejects_missing_prompt(db_session, default_workspace, _stub_evaluator_registry) -> None:
    ws = ws_id(default_workspace)
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    data = _create_data(uuid.UUID(int=123), agent, version)
    with pytest.raises(LookupError):
        await PromptOptimizationService(db_session).create_run(data, ws)


async def test_create_run_rejects_missing_agent(db_session, default_workspace, _stub_evaluator_registry) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    data = _create_data(prompt, uuid.UUID(int=456), version)
    with pytest.raises(LookupError):
        await PromptOptimizationService(db_session).create_run(data, ws)


async def test_create_run_rejects_missing_dataset_version(
    db_session, default_workspace, _stub_evaluator_registry
) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    await db_session.flush()

    data = PromptOptimizationRunCreateSchema(
        prompt_id=prompt.id,
        agent_id=agent.id,
        dataset_id=uuid.uuid4(),
        dataset_version_id=uuid.uuid4(),
        evaluator_configs=["stub"],
        primary_metric="stub",
    )
    with pytest.raises(LookupError):
        await PromptOptimizationService(db_session).create_run(data, ws)


async def test_create_run_rejects_dataset_version_mismatch(
    db_session, default_workspace, _stub_evaluator_registry
) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    data = _create_data(prompt, agent, version, dataset_id=uuid.uuid4())
    with pytest.raises(ValueError, match="does not match"):
        await PromptOptimizationService(db_session).create_run(data, ws)


async def test_create_run_rejects_unknown_evaluator(db_session, default_workspace, _stub_evaluator_registry) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    data = _create_data(prompt, agent, version, evaluator_configs=["nonexistent"])
    with pytest.raises(ValueError, match="unknown evaluator"):
        await PromptOptimizationService(db_session).create_run(data, ws)


async def test_create_run_rejects_primary_metric_not_in_set(
    db_session, default_workspace, _stub_evaluator_registry
) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    data = _create_data(prompt, agent, version, primary_metric="other")
    with pytest.raises(ValueError, match="primary_metric"):
        await PromptOptimizationService(db_session).create_run(data, ws)


async def test_create_run_rejects_tiny_dataset(db_session, default_workspace, _stub_evaluator_registry) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(1))
    await db_session.flush()

    with pytest.raises(ValueError, match="at least 2 items"):
        await PromptOptimizationService(db_session).create_run(_create_data(prompt, agent, version), ws)


async def test_create_run_conflict_on_active_run(db_session, default_workspace, _stub_evaluator_registry) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    service = PromptOptimizationService(db_session)
    await service.create_run(_create_data(prompt, agent, version), ws)
    with pytest.raises(RunConflictError):
        await service.create_run(_create_data(prompt, agent, version), ws)


async def test_cancel_concludes_created_run(db_session, default_workspace, _stub_evaluator_registry) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    service = PromptOptimizationService(db_session)
    run = await service.create_run(_create_data(prompt, agent, version), ws)
    cancelled = await service.cancel_run(run.id, ws)
    assert cancelled is not None
    assert cancelled.status == "concluded"
    assert cancelled.stop_reason == "cancelled"


async def test_cancel_running_run_only_sets_flag(db_session, default_workspace, _stub_evaluator_registry) -> None:
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, "Answer {{q}}.", ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    await db_session.flush()

    service = PromptOptimizationService(db_session)
    run = await service.create_run(_create_data(prompt, agent, version), ws)
    run.status = "running"
    await db_session.flush()

    cancelled = await service.cancel_run(run.id, ws)
    assert cancelled.status == "running"
    assert cancelled.stop_reason == "cancelled"
