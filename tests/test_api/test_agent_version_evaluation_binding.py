"""Agent publish-gate ↔ evaluation version binding (step11).

Covers the binding contract end to end at the service level:

- Gate: only runs bound to the published-to version qualify; a v1 run
  never admits v2 (missing-matching-evaluation rejection); a matching v2
  run participates (positive and negative); unbound historical runs and
  cross-workspace runs never match; force bypass records the audit and
  never counts as a gate pass.
- Engine: a bound run pins ``agent_version`` at start and fails
  explicitly on an unresolvable version or a cross-workspace task; the
  execution definition maps the frozen snapshot faithfully.
- Adapter: the version definition overrides the live row for persona,
  model, skill bindings (explicit loader names).
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.audit import AuditLogModel
from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    EvaluationTaskModel,
    EvaluationTaskUpdateSchema,
    RunStatus,
)
from hecate.models.skill import SkillModel
from hecate.ops.evaluation.engine import EvaluationEngine
from hecate.ops.evaluation.format_evaluators import ExactMatchEvaluator
from hecate.studio.agents.versioning import (
    AgentVersionExecutionDefinition,
    AgentVersionService,
    build_execution_definition,
    canonical_hash,
)

_PASSING_VALUE = 0.9
_DEFAULT_THRESHOLD = 0.8


async def _seed_agent(client, db: AsyncSession, *, persona: str = "helpful"):
    resp = await client.post(
        "/api/agents",
        json={"name": "Binding Agent", "model_config": {"model": "gpt-4o"}, "mode": "chat", "persona": persona},
    )
    assert resp.status_code == 201, resp.text
    agent_id = uuid.UUID(resp.json()["id"])
    agent = await db.get(AgentModel, agent_id)
    return agent


async def _commit_version(db: AsyncSession, agent: AgentModel) -> int:
    service = AgentVersionService(db)
    committed = await service.commit_version(agent.id)
    return int(committed["version"])


async def _seed_bound_run(
    db: AsyncSession,
    agent: AgentModel,
    version: int | None,
    *,
    values: list[float],
    threshold: float = _DEFAULT_THRESHOLD,
    run_workspace: uuid.UUID | None = None,
    task_workspace: uuid.UUID | None = None,
) -> EvaluationRunModel:
    """Task + completed run (optionally version-bound) with deterministic scores."""
    dataset = EvaluationDatasetModel(name=f"ds-{uuid.uuid4().hex[:8]}", workspace_id=agent.workspace_id)
    db.add(dataset)
    await db.flush()
    item = EvaluationItemModel(
        dataset_id=dataset.id,
        query="q",
        generated_answer="stored",
        workspace_id=agent.workspace_id,
    )
    db.add(item)
    await db.flush()

    effective_task_ws = task_workspace if task_workspace is not None else agent.workspace_id
    task = EvaluationTaskModel(
        task_type="offline",
        name=f"task-{uuid.uuid4().hex[:8]}",
        evaluator_configs=["exact_match"],
        config={"dataset_id": str(dataset.id), "answer_source": "agent", "agent_id": str(agent.id)},
        workspace_id=effective_task_ws,
    )
    db.add(task)
    await db.flush()

    resolved = await AgentVersionService(db).resolve(agent.id, version) if version is not None else None
    run = EvaluationRunModel(
        agent_id=agent.id if resolved else None,
        agent_config_hash=canonical_hash(resolved.config) if resolved else None,
        agent_snapshot={"config": resolved.config, "ref_manifest": resolved.ref_manifest} if resolved else None,
        dataset_id=dataset.id,
        task_id=task.id,
        agent_version=version,
        status=RunStatus.COMPLETED.value,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        summary={"threshold": threshold},
        workspace_id=run_workspace if run_workspace is not None else agent.workspace_id,
        # Bound runs freeze a complete single-metric scope so the positive
        # paths keep exercising version matching; scope-less historical runs
        # are covered separately by the with_scope=False coverage tests.
        evaluation_scope=(
            {
                "schema_version": 1,
                "item_ids": [str(item.id)],
                "required_metrics": ["exact_match"],
                "exempted_item_ids": [],
                "dataset_snapshot_hash": None,
            }
            if resolved is not None
            else None
        ),
    )
    db.add(run)
    await db.flush()

    for value in values:
        db.add(
            EvaluationScoreModel(
                run_id=run.id,
                item_id=item.id,
                metric_name="exact_match",
                value=value,
                source="deterministic",
                workspace_id=run.workspace_id,
            )
        )
    await db.flush()
    return run


async def _publish(client, agent_id, version: int, *, force: bool = False):
    payload = {"force": True} if force else {}
    return await client.post(f"/api/agents/{agent_id}/publish/{version}", json=payload)


# --- Gate: version-matched consumption ---


async def test_v1_scores_do_not_admit_v2(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)

    await _seed_bound_run(db_session, agent, 1, values=[_PASSING_VALUE])

    await client.put(
        f"/api/agents/{agent.id}",
        json={"evaluation_gate": {"mode": "require", "require_run": True, "min_pass_rate": 0.8}},
    )

    blocked = await _publish(client, str(agent.id), 2)
    assert blocked.status_code == 409, blocked.text
    report = blocked.json()["detail"]["error"]["details"]["evaluation_report"]
    assert report["missing_matching_evaluation"] == {"version": 2}

    admitted = await _publish(client, str(agent.id), 1)
    assert admitted.status_code == 200, admitted.text


async def test_matching_v2_run_participates(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    await _seed_bound_run(db_session, agent, 2, values=[_PASSING_VALUE])

    await client.put(
        f"/api/agents/{agent.id}",
        json={"evaluation_gate": {"mode": "require", "require_run": True, "min_pass_rate": 0.8}},
    )
    admitted = await _publish(client, str(agent.id), 2)
    assert admitted.status_code == 200, admitted.text


async def test_failing_v2_run_blocks_and_force_bypass_records_audit(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    await _seed_bound_run(db_session, agent, 1, values=[_PASSING_VALUE])
    failing = await _seed_bound_run(db_session, agent, 2, values=[0.2])

    await client.put(
        f"/api/agents/{agent.id}",
        json={"evaluation_gate": {"mode": "require", "require_run": True, "min_pass_rate": 0.8}},
    )

    blocked = await _publish(client, str(agent.id), 2)
    assert blocked.status_code == 409, blocked.text
    report = blocked.json()["detail"]["error"]["details"]["evaluation_report"]
    assert "missing_matching_evaluation" not in report

    forced = await _publish(client, str(agent.id), 2, force=True)
    assert forced.status_code == 200, forced.text
    assert forced.json()["evaluation_report"]["bypassed_by_force"] is True
    assert forced.json()["evaluation_report"]["passed"] is False

    audit_rows = await db_session.execute(
        AuditLogModel.__table__.select().where(AuditLogModel.workspace_id == agent.workspace_id)
    )
    audits = audit_rows.mappings().all()
    assert any("gate" in str(row).lower() or "bypass" in str(row).lower() for row in audits)
    assert failing.agent_version == 2


async def test_unbound_run_never_matches_any_version(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    # Legacy shape: completed, passing, but agent_version NULL.
    await _seed_bound_run(db_session, agent, None, values=[_PASSING_VALUE])

    await client.put(
        f"/api/agents/{agent.id}",
        json={"evaluation_gate": {"mode": "require", "require_run": True, "min_pass_rate": 0.8}},
    )
    for version in (1, 2):
        blocked = await _publish(client, str(agent.id), version)
        assert blocked.status_code == 409, blocked.text
        report = blocked.json()["detail"]["error"]["details"]["evaluation_report"]
        assert report["missing_matching_evaluation"] == {"version": version}


async def test_cross_workspace_run_excluded(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    foreign_workspace = uuid.uuid4()
    await _seed_bound_run(
        db_session,
        agent,
        1,
        values=[_PASSING_VALUE],
        run_workspace=foreign_workspace,
        task_workspace=foreign_workspace,
    )

    await client.put(
        f"/api/agents/{agent.id}",
        json={"evaluation_gate": {"mode": "require", "require_run": True, "min_pass_rate": 0.8}},
    )
    blocked = await _publish(client, str(agent.id), 1)
    assert blocked.status_code == 409, blocked.text
    report = blocked.json()["detail"]["error"]["details"]["evaluation_report"]
    assert report["missing_matching_evaluation"] == {"version": 1}


# --- Engine: binding at run start ---


class _StubEvaluator(ExactMatchEvaluator):
    @property
    def name(self) -> str:
        return "exact_match"

    @property
    def description(self) -> str:
        return "stub evaluator"

    async def evaluate(self, input):  # noqa: A002 — ABC signature
        from hecate.ops.evaluation.types import EvalOutput, Score

        return EvalOutput(scores=[Score(metric_name="exact_match", value=0.5, source="deterministic")])


async def _seed_dataset_with_answer(db: AsyncSession, agent: AgentModel):
    dataset = EvaluationDatasetModel(name=f"ds-{uuid.uuid4().hex[:8]}", workspace_id=agent.workspace_id)
    db.add(dataset)
    await db.flush()
    item = EvaluationItemModel(
        dataset_id=dataset.id,
        query="q",
        generated_answer="stored answer",
        workspace_id=agent.workspace_id,
    )
    db.add(item)
    await db.flush()
    return dataset, item


async def test_engine_binds_version_and_completes(client, db_session: AsyncSession, monkeypatch) -> None:
    agent = await _seed_agent(client, db_session)
    version = await _commit_version(db_session, agent)
    dataset, _item = await _seed_dataset_with_answer(db_session, agent)

    task = EvaluationTaskModel(
        task_type="offline",
        name="bind-task",
        evaluator_configs=["exact_match"],
        config={"dataset_id": str(dataset.id), "answer_source": "agent", "agent_id": str(agent.id)},
        workspace_id=agent.workspace_id,
    )
    run = EvaluationRunModel(
        dataset_id=dataset.id,
        task_id=task.id,
        status=RunStatus.RUNNING.value,
        workspace_id=agent.workspace_id,
    )
    db_session.add_all([task, run])
    await db_session.flush()

    calls = []

    async def generate(query, agent_id, agent_definition=None, rollout_capture=None):
        calls.append(agent_definition)
        assert query == "q"
        assert agent_definition.prompt_override == "helpful"
        return "fresh version answer"

    agent.persona = "changed draft"
    await db_session.flush()
    engine = EvaluationEngine(db_session)
    monkeypatch.setattr(engine, "_generate_answer_via_agent", generate)
    await engine.run(
        [_StubEvaluator()],
        dataset.id,
        answer_source="agent",
        agent_id=agent.id,
        agent_version=version,
        run=run,
    )
    assert run.status == RunStatus.COMPLETED.value
    assert run.agent_version == version
    assert run.agent_id == agent.id
    assert run.agent_config_hash == canonical_hash(run.agent_snapshot["config"])
    assert run.evaluation_scope == {
        "schema_version": 1,
        "item_ids": [str(_item.id)],
        "required_metrics": ["exact_match"],
        "exempted_item_ids": [],
        "dataset_snapshot_hash": None,
    }
    assert len(calls) == 1  # Stored dataset answers cannot count as executing the bound version.
    with pytest.raises(ValueError, match="completed evaluation run"):
        await engine.run([_StubEvaluator()], dataset.id, answer_source="agent", agent_id=agent.id, run=run)


async def test_engine_unknown_version_fails_run_explicitly(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    dataset, _item = await _seed_dataset_with_answer(db_session, agent)
    task = EvaluationTaskModel(
        task_type="offline",
        name="bind-task",
        evaluator_configs=["exact_match"],
        config={"dataset_id": str(dataset.id), "answer_source": "agent", "agent_id": str(agent.id)},
        workspace_id=agent.workspace_id,
    )
    run = EvaluationRunModel(
        dataset_id=dataset.id,
        task_id=task.id,
        status=RunStatus.RUNNING.value,
        workspace_id=agent.workspace_id,
    )
    db_session.add_all([task, run])
    await db_session.flush()

    engine = EvaluationEngine(db_session)
    with pytest.raises(ValueError):
        await engine.run(
            [_StubEvaluator()],
            dataset.id,
            answer_source="agent",
            agent_id=agent.id,
            agent_version=99,
            run=run,
        )
    assert run.status == RunStatus.FAILED.value
    assert run.summary["error"] == "agent_version_unresolvable"
    assert run.evaluation_scope is None  # failed binding freezes nothing


async def test_engine_cross_workspace_task_fails_run(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    version = await _commit_version(db_session, agent)
    dataset, _item = await _seed_dataset_with_answer(db_session, agent)
    foreign_workspace = uuid.uuid4()
    task = EvaluationTaskModel(
        task_type="offline",
        name="foreign-task",
        evaluator_configs=["exact_match"],
        config={"dataset_id": str(dataset.id), "answer_source": "agent", "agent_id": str(agent.id)},
        workspace_id=foreign_workspace,
    )
    run = EvaluationRunModel(
        dataset_id=dataset.id,
        task_id=task.id,
        status=RunStatus.RUNNING.value,
        workspace_id=foreign_workspace,
    )
    db_session.add(task)
    await db_session.flush()  # assign task.id before the run references it

    run = EvaluationRunModel(
        dataset_id=dataset.id,
        task_id=task.id,
        status=RunStatus.RUNNING.value,
        workspace_id=foreign_workspace,
    )
    db_session.add(run)
    await db_session.flush()

    engine = EvaluationEngine(db_session)
    with pytest.raises(ValueError):
        await engine.run(
            [_StubEvaluator()],
            dataset.id,
            answer_source="agent",
            agent_id=agent.id,
            agent_version=version,
            run=run,
        )
    assert run.status == RunStatus.FAILED.value
    assert run.summary["error"] == "agent_version_cross_workspace"


# --- Definition mapping + adapter consumption ---


def test_build_execution_definition_maps_snapshot() -> None:
    from hecate.studio.agents.versioning import ResolvedAgentConfig

    resolved = ResolvedAgentConfig(
        agent_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        source="version",
        version=2,
        config={
            "persona": "frozen persona",
            "model_config": {"model": "gpt-x"},
            "tools": ["t1"],
            "knowledge_base_ids": ["kb1"],
            "skills": ["s1"],
        },
        workflow_version=3,
    )
    definition = build_execution_definition(resolved)
    assert definition.version == 2
    assert definition.prompt_override == "frozen persona"
    assert definition.model_config == {"model": "gpt-x"}
    assert definition.tools == ["t1"]
    assert definition.knowledge_base_ids == ["kb1"]
    assert definition.skills == ["s1"]
    assert definition.workflow_version == 3

    live = ResolvedAgentConfig(
        agent_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        source="live",
        version=None,
        config={},
    )
    with pytest.raises(ValueError):
        build_execution_definition(live)


async def test_adapter_version_definition_overrides_live(client, db_session: AsyncSession, monkeypatch) -> None:
    import hecate_llm.service as llm_module

    from hecate.core.composition.agent_execution_port import AgentExecutionPort

    agent = AgentModel(
        workspace_id=uuid.UUID(int=0),
        name="Override Agent",
        model_config_db={"model": "gpt-live"},
        mode="chat",
        persona="live persona",
        skills=["live-skill"],
    )
    live_skill = SkillModel(
        name="live-skill",
        description="live",
        instructions="live instructions",
        source="user",
        workspace_id=agent.workspace_id,
    )
    version_skill = SkillModel(
        name="version-skill",
        description="frozen",
        instructions="frozen instructions",
        source="user",
        workspace_id=agent.workspace_id,
    )
    from hecate.models.tool import ToolModel

    tool = ToolModel(
        name="t1", description="bound tool", parameters={}, source="custom", workspace_id=agent.workspace_id
    )
    db_session.add_all([agent, live_skill, version_skill, tool])
    await db_session.flush()

    definition = AgentVersionExecutionDefinition(
        version=1,
        prompt_override="frozen persona",
        model_config={"model": "gpt-frozen", "temperature": 0.2, "max_tokens": 123},
        tools=["t1"],
        knowledge_base_ids=[],
        skills=["version-skill"],
        workflow_version=None,
    )

    captured: dict = {}

    class _FakeResponse:
        content = "ok"
        model = None
        usage = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(llm_module.llm_service, "chat", _fake_chat)

    port = AgentExecutionPort(db_session)
    response = await port.agent_execute(
        agent_id=agent.id,
        messages=[{"role": "user", "content": "hi"}],
        channel_snapshot={},
        agent_definition=definition,
    )
    assert response["response"] == "ok"
    assert captured["model"] == "gpt-frozen"
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 123
    assert [tool["name"] for tool in captured["tools"]] == ["t1"]
    system_content = captured["messages"][0]["content"]
    assert system_content.startswith("frozen persona")
    assert "version-skill" in system_content
    assert "live-skill" not in system_content


async def test_task_retarget_cannot_transfer_run_identity(client, db_session: AsyncSession) -> None:
    first = await _seed_agent(client, db_session)
    second = await _seed_agent(client, db_session)
    await _commit_version(db_session, first)
    await _commit_version(db_session, second)
    run = await _seed_bound_run(db_session, first, 1, values=[0.9])
    task = await db_session.get(EvaluationTaskModel, run.task_id)
    task.config = {**task.config, "agent_id": str(second.id), "agent_version": 1}
    first.persona = "new draft"
    await db_session.flush()
    service = AgentVersionService(db_session)
    assert (await service._latest_completed_run(first.id, 1)).id == run.id
    assert await service._latest_completed_run(second.id, 1) is None
    task.deleted = True
    await db_session.flush()
    assert (await service._latest_completed_run(first.id, 1)).id == run.id
    run.agent_config_hash = "0" * 64
    await db_session.flush()
    assert await service._latest_completed_run(first.id, 1) is None


async def test_task_update_preserves_changes_and_clears_version(client, db_session: AsyncSession, monkeypatch) -> None:
    from hecate.ops.evaluation.tasks.service import EvaluationTaskService

    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    run = await _seed_bound_run(db_session, agent, 1, values=[0.9])
    task = await db_session.get(EvaluationTaskModel, run.task_id)
    monkeypatch.setattr("hecate.ops.evaluation.tasks.service.get_evaluator_class", lambda name: _StubEvaluator)
    task.evaluator_configs = ["exact_match"]
    task.config = {**task.config, "agent_version": 1}
    await db_session.flush()
    service = EvaluationTaskService(db_session)
    await service.update_task(task.id, agent.workspace_id, EvaluationTaskUpdateSchema(threshold=0.6))
    assert task.config["agent_version"] == 1
    await service.update_task(task.id, agent.workspace_id, EvaluationTaskUpdateSchema(agent_version=2))
    assert task.config["agent_version"] == 2
    assert run.agent_version == 1
    await service.update_task(task.id, agent.workspace_id, EvaluationTaskUpdateSchema(agent_version=None))
    assert "agent_version" not in task.config
    assert run.agent_version == 1


async def test_drift_only_gate_still_requires_matching_version(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    agent.evaluation_gate = {"mode": "require", "block_on_drift": True}
    await db_session.flush()
    blocked = await _publish(client, agent.id, 1)
    assert blocked.status_code == 409
    report = blocked.json()["detail"]["error"]["details"]["evaluation_report"]
    assert report["passed"] is False
    assert report["missing_matching_evaluation"] == {"version": 1}


async def test_warn_reports_missing_match_without_blocking(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    agent.evaluation_gate = {"mode": "warn", "require_run": True}
    await db_session.flush()
    response = await _publish(client, agent.id, 1)
    assert response.status_code == 200, response.text
    report = response.json()["evaluation_report"]
    assert report["passed"] is False
    assert report["bypassed_by_force"] is False


@pytest.mark.parametrize(
    "baseline_value,candidate_value,expected", [(None, 0.9, 409), (0.9, 0.5, 409), (0.9, 0.95, 200)]
)
async def test_regression_uses_published_version_baseline(
    client, db_session: AsyncSession, baseline_value, candidate_value, expected
) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    agent.published_version = 1
    agent.evaluation_gate = {"mode": "require", "block_on_regression": True}
    if baseline_value is not None:
        await _seed_bound_run(db_session, agent, 1, values=[baseline_value])
    await _seed_bound_run(db_session, agent, 2, values=[candidate_value])
    await db_session.flush()
    response = await _publish(client, agent.id, 2)
    assert response.status_code == expected, response.text
    if baseline_value is None:
        report = response.json()["detail"]["error"]["details"]["evaluation_report"]
        signal = next(s for s in report["signals"] if s["name"] == "block_on_regression")
        assert signal["detail"]["reason"] == "missing_matching_baseline"


async def test_force_audit_failure_does_not_move_publish_pointer(client, db_session: AsyncSession, monkeypatch) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    agent.evaluation_gate = {"mode": "require", "require_run": True}
    await db_session.flush()
    service = AgentVersionService(db_session)

    async def fail_audit(*args):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(service, "_record_gate_bypass", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await service.publish_version(agent.id, 1, force=True, actor_user_id=uuid.uuid4())
    assert agent.published_version is None


async def test_actual_engine_execution_uses_frozen_skill_and_config(
    client, db_session: AsyncSession, monkeypatch
) -> None:
    from types import SimpleNamespace

    from hecate_llm.service import llm_service

    from hecate.tools.skill.versioning import SkillVersionService

    agent = await _seed_agent(client, db_session, persona="frozen persona")
    skill = SkillModel(
        name="pinned",
        description="frozen description",
        instructions="frozen instructions",
        source="user",
        provider="user",
        auto_load=True,
        workspace_id=agent.workspace_id,
    )
    db_session.add(skill)
    await db_session.flush()
    await SkillVersionService(db_session).commit(skill.id)
    agent.skills = [skill.name]
    agent.model_config_db = {"model": "gpt-frozen", "temperature": 0.1}
    version = await _commit_version(db_session, agent)
    dataset, _item = await _seed_dataset_with_answer(db_session, agent)
    agent.persona = "changed persona"
    agent.skills = []
    agent.model_config_db = {"model": "gpt-live", "temperature": 0.9}
    skill.instructions = "changed instructions"
    await db_session.flush()
    captured = []

    async def chat(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(content="fresh answer", model=None, usage={}, tool_calls=[])

    class FreshEvaluator(_StubEvaluator):
        async def evaluate(self, input):
            assert input.generated_answer == "fresh answer"
            return await super().evaluate(input)

    monkeypatch.setattr(llm_service, "chat", chat)
    result = await EvaluationEngine(db_session).run(
        [FreshEvaluator()],
        dataset.id,
        answer_source="agent",
        agent_id=agent.id,
        agent_version=version,
        summary_config={"threshold": 0.4},
    )
    run = await db_session.get(EvaluationRunModel, result.run_id)
    assert run.status == "completed"
    assert run.agent_snapshot["ref_manifest"][0]["version"] == 1
    assert len(captured) == 1
    assert captured[0]["model"] == "gpt-frozen"
    assert captured[0]["temperature"] == 0.1
    system = captured[0]["messages"][0]["content"]
    assert "frozen persona" in system
    assert "frozen instructions" in system
    assert "changed" not in system
    agent.evaluation_gate = {"mode": "require", "min_pass_rate": 1.0}
    await db_session.flush()
    published = await _publish(client, agent.id, version)
    assert published.status_code == 200, published.text
    assert published.json()["evaluation_report"]["passed"] is True


@pytest.mark.parametrize("failure", ["invocation", "foreign_dataset", "empty_dataset", "deleted_agent"])
async def test_invalid_bound_execution_cannot_become_gate_evidence(
    client, db_session: AsyncSession, monkeypatch, failure
) -> None:
    from sqlalchemy import select

    agent = await _seed_agent(client, db_session)
    version = await _commit_version(db_session, agent)
    dataset, item = await _seed_dataset_with_answer(db_session, agent)
    if failure == "foreign_dataset":
        dataset.workspace_id = uuid.uuid4()
    if failure == "empty_dataset":
        item.deleted = True
    if failure == "deleted_agent":
        agent.deleted = True
    await db_session.flush()
    engine = EvaluationEngine(db_session)

    async def generate(*args, **kwargs):
        raise ValueError("agent invocation failed")

    monkeypatch.setattr(engine, "_generate_answer_via_agent", generate)
    with pytest.raises(ValueError):
        await engine.run(
            [_StubEvaluator()], dataset.id, answer_source="agent", agent_id=agent.id, agent_version=version
        )
    run = (
        await db_session.execute(select(EvaluationRunModel).where(EvaluationRunModel.dataset_id == dataset.id))
    ).scalar_one()
    assert run.status == "failed"
    assert run.completed_at is not None


async def test_bound_run_cannot_be_relabelled_or_executed_live(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    run = await _seed_bound_run(db_session, agent, 1, values=[0.9])
    run.status = "running"
    run.completed_at = None
    await db_session.flush()
    engine = EvaluationEngine(db_session)
    with pytest.raises(ValueError, match="original agent_version"):
        await engine.run([_StubEvaluator()], run.dataset_id, answer_source="agent", agent_id=agent.id, run=run)
    with pytest.raises(ValueError, match="binding_conflict"):
        await engine.run(
            [_StubEvaluator()], run.dataset_id, answer_source="agent", agent_id=agent.id, agent_version=2, run=run
        )
    assert run.agent_version == 1
    assert run.status == "failed"


async def test_workflow_agent_executes_pinned_workflow(client, db_session: AsyncSession, monkeypatch) -> None:
    from types import SimpleNamespace

    from hecate.execution.entry_service import EntryExecutionService
    from hecate.models.workflow import WorkflowModel, WorkflowVersionModel

    agent = await _seed_agent(client, db_session)
    workflow = WorkflowModel(name="frozen-workflow", current_version=1, workspace_id=agent.workspace_id)
    db_session.add(workflow)
    await db_session.flush()
    db_session.add(
        WorkflowVersionModel(workflow_id=workflow.id, version=1, graph_dsl={}, workspace_id=agent.workspace_id)
    )
    agent.mode = "workflow"
    agent.workflow_id = workflow.id
    await db_session.flush()
    version = await _commit_version(db_session, agent)
    workflow.current_version = 2
    agent.workflow_id = None
    agent.mode = "chat"
    dataset, _item = await _seed_dataset_with_answer(db_session, agent)
    captured = []

    async def execute(self, **kwargs):
        captured.append(kwargs)
        return SimpleNamespace(result={"content": "workflow answer", "usage": {}})

    monkeypatch.setattr(EntryExecutionService, "execute", execute)
    await EvaluationEngine(db_session).run(
        [_StubEvaluator()], dataset.id, answer_source="agent", agent_id=agent.id, agent_version=version
    )
    assert len(captured) == 1
    assert captured[0]["agent_mode"] == "workflow"
    assert captured[0]["workflow_id"] == workflow.id
    assert captured[0]["workflow_version"] == 1
    assert captured[0]["agent_version"] == version


@pytest.mark.parametrize("resource", ["skill", "knowledge_base", "tool"])
async def test_version_resources_do_not_fall_back_across_workspaces(client, db_session: AsyncSession, resource) -> None:
    from hecate.core.composition.agent_execution_port import AgentExecutionPort
    from hecate.models.tool import ToolModel

    agent = await _seed_agent(client, db_session)
    definition = AgentVersionExecutionDefinition(
        version=1,
        prompt_override=None,
        model_config={},
        tools=[],
        skills=[],
        knowledge_base_ids=[],
        workflow_version=None,
    )
    if resource == "skill":
        definition.ref_manifest.append(
            {"resource_type": "skill", "resource_id": "missing", "skill_id": str(uuid.uuid4()), "version": 1}
        )
    elif resource == "knowledge_base":
        definition.knowledge_base_ids.append(str(uuid.uuid4()))
    else:
        definition.tools.append("foreign")
        db_session.add(
            ToolModel(
                name="foreign", description="foreign tool", parameters={}, source="custom", workspace_id=uuid.uuid4()
            )
        )
        await db_session.flush()
    with pytest.raises(ValueError, match="does not resolve"):
        await AgentExecutionPort(db_session).agent_execute(
            agent.id, [{"role": "user", "content": "q"}], {}, agent_definition=definition
        )


@pytest.mark.parametrize("state", ["unchanged", "changed", "missing_snapshot"])
async def test_bound_run_drift_gate_uses_current_dataset(client, db_session: AsyncSession, state) -> None:
    from sqlalchemy import select

    from hecate.ops.evaluation.snapshot import build_snapshot

    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    run = await _seed_bound_run(db_session, agent, 1, values=[0.9])
    item = (
        await db_session.execute(select(EvaluationItemModel).where(EvaluationItemModel.dataset_id == run.dataset_id))
    ).scalar_one()
    items, content_hash = build_snapshot([item])
    if state != "missing_snapshot":
        run.dataset_snapshot = {"items": items, "hash": content_hash}
        run.evaluation_scope = {**run.evaluation_scope, "dataset_snapshot_hash": content_hash}
    if state == "changed":
        item.query = "changed after evaluation"
    agent.evaluation_gate = {"mode": "require", "block_on_drift": True}
    await db_session.flush()
    published = await _publish(client, agent.id, 1)
    assert published.status_code == (200 if state == "unchanged" else 409), published.text


async def test_offline_runner_passes_version_and_freezes_binding(client, db_session: AsyncSession, monkeypatch) -> None:
    from contextlib import asynccontextmanager

    from hecate.ops.evaluation.tasks.runner import OfflineTaskRunner

    agent = await _seed_agent(client, db_session)
    version = await _commit_version(db_session, agent)
    dataset, _item = await _seed_dataset_with_answer(db_session, agent)
    task = EvaluationTaskModel(
        task_type="offline",
        name="runner binding",
        evaluator_configs=["exact_match"],
        workspace_id=agent.workspace_id,
        config={
            "dataset_id": str(dataset.id),
            "answer_source": "agent",
            "agent_id": str(agent.id),
            "agent_version": version,
            "threshold": 0.4,
        },
    )
    db_session.add(task)
    await db_session.flush()
    run = EvaluationRunModel(dataset_id=dataset.id, task_id=task.id, status="pending", workspace_id=agent.workspace_id)
    db_session.add(run)
    await db_session.flush()

    @asynccontextmanager
    async def session_factory():
        yield db_session

    async def generate(self, query, agent_id, agent_definition=None, rollout_capture=None):
        assert agent_definition.version == version
        return "fresh runner answer"

    monkeypatch.setattr("hecate.core.database.async_session_factory", session_factory)
    monkeypatch.setattr("hecate.ops.evaluation.tasks.runner.get_evaluator_class", lambda name: _StubEvaluator)
    monkeypatch.setattr(EvaluationEngine, "_generate_answer_via_agent", generate)
    await OfflineTaskRunner(db_session)._execute(run.id, task.id)
    assert run.status == "completed"
    assert run.agent_id == agent.id
    assert run.agent_version == version
    assert run.dataset_snapshot["hash"]


@pytest.mark.parametrize("task_type,source", [("offline", "manual"), ("offline", "workflow"), ("online", None)])
async def test_task_rejects_inapplicable_agent_version(client, db_session: AsyncSession, task_type, source) -> None:
    agent = await _seed_agent(client, db_session)
    dataset, _item = await _seed_dataset_with_answer(db_session, agent)
    response = await client.post(
        "/api/evaluation/tasks",
        json={
            "task_type": task_type,
            "name": "invalid binding",
            "evaluators": ["contains"],
            "dataset_id": str(dataset.id),
            "answer_source": source,
            "agent_id": str(agent.id),
            "agent_version": 1,
        },
    )
    assert response.status_code == 400, response.text
    assert "agent_version requires" in response.text


async def test_version_registry_loads_frozen_l2_skill_after_draft_unbinds_it(client, db_session: AsyncSession) -> None:
    from hecate.core.composition.entry_assembly import build_tool_registry
    from hecate.tools.skill.versioning import SkillVersionService

    agent = await _seed_agent(client, db_session)
    skill = SkillModel(
        name="bound-l2",
        description="bound",
        instructions="frozen L2 instructions",
        source="user",
        provider="user",
        workspace_id=agent.workspace_id,
    )
    extra = SkillModel(
        name="draft-only",
        description="draft",
        instructions="draft instructions",
        source="user",
        provider="user",
        workspace_id=agent.workspace_id,
    )
    db_session.add_all([skill, extra])
    await db_session.flush()
    await SkillVersionService(db_session).commit(skill.id)
    agent.skills = [skill.name]
    version = await _commit_version(db_session, agent)
    resolved = await AgentVersionService(db_session).resolve(agent.id, version)
    agent.skills = [extra.name]
    skill.instructions = "changed L2 instructions"
    await db_session.flush()
    registry = build_tool_registry(
        db_session,
        skill_ref_manifest=resolved.ref_manifest,
        workspace_id=agent.workspace_id,
        explicit_skill_names=resolved.config["skills"],
    )
    context = {"agent_id": str(agent.id), "workspace_id": str(agent.workspace_id)}
    result = await registry.execute("load_skill", {"skill_name": skill.name}, context)
    assert "frozen L2 instructions" in str(result)
    assert "changed L2 instructions" not in str(result)
    with pytest.raises(ValueError, match="not advertised"):
        await registry.execute("load_skill", {"skill_name": extra.name}, context)


# --- Evidence coverage completeness (step11: frozen scope × required metrics) ---


async def _seed_scoped_run(
    db: AsyncSession,
    agent: AgentModel,
    version: int,
    *,
    n_items: int = 2,
    required_metrics: list[str],
    scores: list[tuple[int, str, float]],
    known_bad_indexes: list[int] | None = None,
    threshold: float = _DEFAULT_THRESHOLD,
    with_scope: bool = True,
    known_bad_reason: str | None = "known-bad sample",
) -> tuple[EvaluationRunModel, list[EvaluationItemModel]]:
    """Bound completed run with a frozen evaluation scope and score rows.

    ``scores`` entries are ``(item_index, metric_name, value)`` over the
    created items; ``known_bad_indexes`` marks items known-bad (exempted in
    the frozen scope). ``with_scope=False`` seeds a pre-freezing historical
    run (bound but without ``evaluation_scope``).
    """
    known_bad_indexes = known_bad_indexes or []
    dataset = EvaluationDatasetModel(name=f"ds-{uuid.uuid4().hex[:8]}", workspace_id=agent.workspace_id)
    db.add(dataset)
    await db.flush()
    items: list[EvaluationItemModel] = []
    for index in range(n_items):
        item = EvaluationItemModel(
            dataset_id=dataset.id,
            query=f"q{index}",
            generated_answer="stored",
            known_bad=index in known_bad_indexes,
            known_bad_reason=known_bad_reason if index in known_bad_indexes else None,
            workspace_id=agent.workspace_id,
        )
        db.add(item)
        items.append(item)
    await db.flush()

    task = EvaluationTaskModel(
        task_type="offline",
        name=f"task-{uuid.uuid4().hex[:8]}",
        evaluator_configs=required_metrics,
        config={"dataset_id": str(dataset.id), "answer_source": "agent", "agent_id": str(agent.id)},
        workspace_id=agent.workspace_id,
    )
    db.add(task)
    await db.flush()

    resolved = await AgentVersionService(db).resolve(agent.id, version)
    run = EvaluationRunModel(
        agent_id=agent.id,
        agent_config_hash=canonical_hash(resolved.config),
        agent_snapshot={"config": resolved.config, "ref_manifest": resolved.ref_manifest},
        dataset_id=dataset.id,
        task_id=task.id,
        agent_version=version,
        status=RunStatus.COMPLETED.value,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        summary={"threshold": threshold},
        workspace_id=agent.workspace_id,
        evaluation_scope=(
            {
                "schema_version": 1,
                "item_ids": [str(item.id) for item in items],
                "required_metrics": list(dict.fromkeys(required_metrics)),
                "exempted_item_ids": sorted(str(items[i].id) for i in known_bad_indexes),
                "dataset_snapshot_hash": None,
            }
            if with_scope
            else None
        ),
    )
    db.add(run)
    await db.flush()

    for item_index, metric_name, value in scores:
        db.add(
            EvaluationScoreModel(
                run_id=run.id,
                item_id=items[item_index].id,
                metric_name=metric_name,
                value=value,
                source="deterministic",
                workspace_id=agent.workspace_id,
            )
        )
    await db.flush()
    return run, items


async def _set_require_gate(client, agent_id: str) -> None:
    resp = await client.put(
        f"/api/agents/{agent_id}",
        json={"evaluation_gate": {"mode": "require", "require_run": True, "min_pass_rate": 0.8}},
    )
    assert resp.status_code == 200


def _coverage_signal(report: dict) -> dict:
    return next(s for s in report["signals"] if s["name"] == "evidence_coverage")


async def test_complete_coverage_admits_publish_and_exempts_known_bad(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    # 3 items: indexes 0/1 required, index 2 known-bad exempted (no scores).
    run, _items = await _seed_scoped_run(
        db_session,
        agent,
        2,
        n_items=3,
        required_metrics=["exact_match", "is_json"],
        scores=[
            (0, "exact_match", _PASSING_VALUE),
            (0, "is_json", _PASSING_VALUE),
            (1, "exact_match", _PASSING_VALUE),
            (1, "is_json", _PASSING_VALUE),
        ],
        known_bad_indexes=[2],
    )
    assert run.evaluation_scope["exempted_item_ids"] == [str(_items[2].id)]

    await _set_require_gate(client, str(agent.id))
    admitted = await _publish(client, str(agent.id), 2)
    assert admitted.status_code == 200, admitted.text
    report = admitted.json()["evaluation_report"]
    assert _coverage_signal(report)["passed"] is True


async def test_missing_one_metric_rejects_with_location(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    await _seed_scoped_run(
        db_session,
        agent,
        2,
        required_metrics=["exact_match", "is_json"],
        # Item 1 lacks format_ok entirely.
        scores=[
            (0, "exact_match", _PASSING_VALUE),
            (0, "is_json", _PASSING_VALUE),
            (1, "exact_match", _PASSING_VALUE),
        ],
    )

    await _set_require_gate(client, str(agent.id))
    blocked = await _publish(client, str(agent.id), 2)
    assert blocked.status_code == 409, blocked.text
    report = blocked.json()["detail"]["error"]["details"]["evaluation_report"]
    coverage = _coverage_signal(report)
    assert coverage["passed"] is False
    assert coverage["detail"]["missing_count"] == 1
    assert coverage["detail"]["missing_samples"][0]["metric_name"] == "is_json"


async def test_error_score_counts_as_missing_not_quality(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    await _seed_scoped_run(
        db_session,
        agent,
        2,
        required_metrics=["exact_match", "is_json"],
        scores=[
            (0, "exact_match", -1.0),  # evaluator failure
            (0, "is_json", _PASSING_VALUE),
            (1, "exact_match", _PASSING_VALUE),
            (1, "is_json", _PASSING_VALUE),
        ],
    )

    await _set_require_gate(client, str(agent.id))
    blocked = await _publish(client, str(agent.id), 2)
    assert blocked.status_code == 409, blocked.text
    report = blocked.json()["detail"]["error"]["details"]["evaluation_report"]
    coverage = _coverage_signal(report)
    assert coverage["passed"] is False
    assert coverage["detail"]["missing_count"] == 1


async def test_low_score_is_quality_failure_with_full_coverage(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    await _seed_scoped_run(
        db_session,
        agent,
        2,
        required_metrics=["exact_match", "is_json"],
        scores=[
            (0, "exact_match", 0.2),  # valid but low: quality failure
            (0, "is_json", _PASSING_VALUE),
            (1, "exact_match", _PASSING_VALUE),
            (1, "is_json", _PASSING_VALUE),
        ],
    )

    await _set_require_gate(client, str(agent.id))
    blocked = await _publish(client, str(agent.id), 2)
    assert blocked.status_code == 409, blocked.text
    report = blocked.json()["detail"]["error"]["details"]["evaluation_report"]
    coverage = _coverage_signal(report)
    assert coverage["passed"] is True  # evidence is complete
    quality = next(s for s in report["signals"] if s["name"] == "min_pass_rate")
    assert quality["passed"] is False


async def test_duplicate_scores_do_not_mask_gap(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    await _seed_scoped_run(
        db_session,
        agent,
        2,
        required_metrics=["exact_match", "is_json"],
        scores=[
            (0, "exact_match", _PASSING_VALUE),
            (0, "is_json", _PASSING_VALUE),
            (1, "exact_match", _PASSING_VALUE),
            (1, "exact_match", 0.5),
            (1, "exact_match", 0.7),  # repeats cannot stand in for format_ok
        ],
    )

    await _set_require_gate(client, str(agent.id))
    blocked = await _publish(client, str(agent.id), 2)
    assert blocked.status_code == 409, blocked.text
    coverage = _coverage_signal(blocked.json()["detail"]["error"]["details"]["evaluation_report"])
    assert coverage["passed"] is False
    assert coverage["detail"]["missing_samples"][0]["metric_name"] == "is_json"


async def test_task_edit_does_not_move_frozen_scope(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    run, _items = await _seed_scoped_run(
        db_session,
        agent,
        2,
        required_metrics=["exact_match", "is_json"],
        scores=[(0, "exact_match", _PASSING_VALUE)],  # incomplete on purpose
    )
    frozen_scope = dict(run.evaluation_scope)

    # Reassign JSON so the changed task config is actually persisted.
    task = await db_session.get(EvaluationTaskModel, run.task_id)
    task.config = {**task.config, "tags": ["later"]}
    _items[0].query = "changed dataset query"
    await db_session.flush()

    await _set_require_gate(client, str(agent.id))
    blocked = await _publish(client, str(agent.id), 2)
    assert blocked.status_code == 409, blocked.text
    await db_session.refresh(run)
    assert run.evaluation_scope == frozen_scope
    coverage = _coverage_signal(blocked.json()["detail"]["error"]["details"]["evaluation_report"])
    assert coverage["detail"]["missing_count"] == 3  # item0 format_ok + item1 both


async def test_historical_run_without_scope_unverifiable(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    await _seed_scoped_run(
        db_session,
        agent,
        2,
        required_metrics=["exact_match"],
        scores=[(0, "exact_match", _PASSING_VALUE)],
        with_scope=False,  # bound before scope freezing
    )

    await _set_require_gate(client, str(agent.id))
    blocked = await _publish(client, str(agent.id), 2)
    assert blocked.status_code == 409, blocked.text
    coverage = _coverage_signal(blocked.json()["detail"]["error"]["details"]["evaluation_report"])
    assert coverage["passed"] is False
    assert coverage["detail"]["reason"] == "missing_evaluation_scope"

    # warn reports the unverifiable coverage but publishes.
    await client.put(
        f"/api/agents/{agent.id}",
        json={"evaluation_gate": {"mode": "warn", "require_run": True, "min_pass_rate": 0.8}},
    )
    warned = await _publish(client, str(agent.id), 2)
    assert warned.status_code == 200, warned.text
    assert _coverage_signal(warned.json()["evaluation_report"])["passed"] is False


async def test_force_bypasses_incomplete_with_audit(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    await _seed_scoped_run(
        db_session,
        agent,
        2,
        required_metrics=["exact_match", "is_json"],
        scores=[(0, "exact_match", _PASSING_VALUE)],
    )

    await _set_require_gate(client, str(agent.id))
    forced = await _publish(client, str(agent.id), 2, force=True)
    assert forced.status_code == 200, forced.text
    report = forced.json()["evaluation_report"]
    assert report["bypassed_by_force"] is True
    assert _coverage_signal(report)["passed"] is False
    audit_rows = await db_session.execute(
        AuditLogModel.__table__.select().where(AuditLogModel.workspace_id == agent.workspace_id)
    )
    assert audit_rows.mappings().all(), "force bypass must persist an audit record"


async def test_engine_live_run_has_no_scope(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    dataset, _item = await _seed_dataset_with_answer(db_session, agent)
    run = EvaluationRunModel(
        dataset_id=dataset.id,
        status=RunStatus.RUNNING.value,
        workspace_id=agent.workspace_id,
    )
    db_session.add(run)
    await db_session.flush()

    engine = EvaluationEngine(db_session)
    await engine.run([_StubEvaluator()], dataset.id, answer_source="agent", agent_id=agent.id, run=run)
    assert run.status == RunStatus.COMPLETED.value
    assert run.agent_version is None
    assert run.evaluation_scope is None


@pytest.mark.parametrize("invalid", ["human", "llm_judge", "deleted", "foreign_workspace", "out_of_range"])
async def test_untrusted_score_cannot_complete_deterministic_evidence(client, db_session, invalid) -> None:
    from sqlalchemy import select

    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    run, items = await _seed_scoped_run(
        db_session,
        agent,
        1,
        n_items=1,
        required_metrics=["exact_match", "is_json"],
        scores=[(0, "exact_match", 0.9), (0, "is_json", 0.9)],
    )
    score = (
        await db_session.execute(
            select(EvaluationScoreModel).where(
                EvaluationScoreModel.run_id == run.id, EvaluationScoreModel.metric_name == "is_json"
            )
        )
    ).scalar_one()
    if invalid in {"human", "llm_judge"}:
        score.source = invalid
    elif invalid == "deleted":
        score.deleted = True
    elif invalid == "foreign_workspace":
        score.workspace_id = uuid.uuid4()
    else:
        score.value = 2.0
    await db_session.flush()
    await _set_require_gate(client, str(agent.id))
    response = await _publish(client, agent.id, 1)
    assert response.status_code == 409, response.text
    signal = _coverage_signal(response.json()["detail"]["error"]["details"]["evaluation_report"])
    assert signal["detail"]["missing_count"] == 1
    assert signal["detail"]["missing_samples"] == [{"item_id": str(items[0].id), "metric_name": "is_json"}]


@pytest.mark.parametrize(
    "malformation",
    [
        "empty_object",
        "missing_items",
        "empty_items",
        "empty_metrics",
        "invalid_uuid",
        "string_metrics",
        "unknown_metric",
        "all_exempt",
        "duplicate_items",
        "foreign_exemption",
        "unknown_schema",
        "bad_hash",
    ],
)
async def test_unverifiable_scope_fails_closed(client, db_session, malformation) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    run, items = await _seed_scoped_run(
        db_session, agent, 1, n_items=1, required_metrics=["exact_match"], scores=[(0, "exact_match", 0.9)]
    )
    scope = copy.deepcopy(run.evaluation_scope)
    if malformation == "empty_object":
        scope = {}
    elif malformation == "missing_items":
        del scope["item_ids"]
    elif malformation == "empty_items":
        scope["item_ids"] = []
    elif malformation == "empty_metrics":
        scope["required_metrics"] = []
    elif malformation == "invalid_uuid":
        scope["item_ids"] = ["invalid"]
    elif malformation == "string_metrics":
        scope["required_metrics"] = "exact_match"
    elif malformation == "unknown_metric":
        scope["required_metrics"] = ["custom-metric"]
    elif malformation == "all_exempt":
        scope["exempted_item_ids"] = scope["item_ids"]
    elif malformation == "duplicate_items":
        scope["item_ids"] *= 2
    elif malformation == "foreign_exemption":
        scope["exempted_item_ids"] = [str(uuid.uuid4())]
    elif malformation == "unknown_schema":
        scope["schema_version"] = 99
    else:
        scope["dataset_snapshot_hash"] = "not-a-hash"
    run.evaluation_scope = scope
    agent.evaluation_gate = {"mode": "require", "require_run": True}
    await db_session.flush()
    response = await _publish(client, agent.id, 1)
    assert response.status_code == 409, response.text
    coverage = _coverage_signal(response.json()["detail"]["error"]["details"]["evaluation_report"])
    assert coverage["passed"] is False
    assert agent.published_version is None


async def test_exempt_scores_do_not_change_scoped_quality(client, db_session) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _seed_scoped_run(
        db_session,
        agent,
        1,
        required_metrics=["exact_match"],
        scores=[(0, "exact_match", 0.9), (1, "exact_match", 0.0)],
        known_bad_indexes=[1],
    )
    await _set_require_gate(client, str(agent.id))
    response = await _publish(client, agent.id, 1)
    assert response.status_code == 200, response.text
    report = response.json()["evaluation_report"]
    assert report["deterministic_pass_rate"] == 1.0
    assert report["deterministic_metric_averages"] == {"exact_match": 0.9}


@pytest.mark.parametrize("outside", ["item", "metric"])
async def test_unscoped_scores_cannot_change_quality_verdict(client, db_session, outside) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    run, items = await _seed_scoped_run(
        db_session,
        agent,
        1,
        n_items=1,
        required_metrics=["exact_match"],
        scores=[(0, "exact_match", 0.0 if outside == "item" else 0.9)],
    )
    for _ in range(9 if outside == "item" else 1):
        db_session.add(
            EvaluationScoreModel(
                run_id=run.id,
                item_id=uuid.uuid4() if outside == "item" else items[0].id,
                metric_name="exact_match" if outside == "item" else "is_json",
                value=1.0 if outside == "item" else 0.0,
                source="deterministic",
                workspace_id=run.workspace_id,
            )
        )
    await db_session.flush()
    await _set_require_gate(client, str(agent.id))
    response = await _publish(client, agent.id, 1)
    assert response.status_code == (409 if outside == "item" else 200), response.text
    report = (
        response.json()["evaluation_report"]
        if outside == "metric"
        else response.json()["detail"]["error"]["details"]["evaluation_report"]
    )
    assert _coverage_signal(report)["passed"] is True
    assert report["deterministic_pass_rate"] == (0.0 if outside == "item" else 1.0)


@pytest.mark.parametrize("baseline_problem", ["missing_scope", "missing_score", "metrics_changed"])
async def test_incomplete_or_incomparable_baseline_cannot_pass_regression(client, db_session, baseline_problem) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    metrics = ["is_json"] if baseline_problem == "metrics_changed" else ["exact_match"]
    await _seed_scoped_run(
        db_session,
        agent,
        1,
        required_metrics=metrics,
        scores=[(0, metrics[0], 0.9)] + ([] if baseline_problem == "missing_score" else [(1, metrics[0], 0.9)]),
        with_scope=baseline_problem != "missing_scope",
    )
    await _seed_scoped_run(
        db_session,
        agent,
        2,
        required_metrics=["exact_match"],
        scores=[(0, "exact_match", 0.95), (1, "exact_match", 0.95)],
    )
    agent.published_version = 1
    agent.evaluation_gate = {"mode": "require", "block_on_regression": True}
    await db_session.flush()
    response = await _publish(client, agent.id, 2)
    assert response.status_code == 409, response.text
    report = response.json()["detail"]["error"]["details"]["evaluation_report"]
    regression = next(signal for signal in report["signals"] if signal["name"] == "block_on_regression")
    assert regression["detail"]["reason"] == (
        "incompatible_evaluation_metrics" if baseline_problem == "metrics_changed" else "incomplete_baseline_evidence"
    )
    assert agent.published_version == 1


async def test_scope_freezes_tags_exemptions_and_only_deterministic_metrics(client, db_session, monkeypatch) -> None:
    from hecate.ops.evaluation.types import EvalOutput, Score

    agent = await _seed_agent(client, db_session)
    version = await _commit_version(db_session, agent)
    dataset, first = await _seed_dataset_with_answer(db_session, agent)
    first.tags = ["smoke"]
    second = EvaluationItemModel(
        dataset_id=dataset.id, query="second", tags=["smoke"], known_bad=False, workspace_id=agent.workspace_id
    )
    skipped = EvaluationItemModel(dataset_id=dataset.id, query="skip", tags=["other"], workspace_id=agent.workspace_id)
    db_session.add_all([second, skipped])
    await db_session.flush()
    run = EvaluationRunModel(
        dataset_id=dataset.id,
        status="running",
        workspace_id=agent.workspace_id,
        dataset_snapshot={
            "hash": "a" * 64,
            "items": [
                {"id": str(first.id)},
                {"id": str(second.id), "known_bad": True},
                {"id": str(skipped.id), "known_bad": True},
            ],
        },
    )
    db_session.add(run)
    await db_session.flush()
    calls = []

    class StubJudge:
        name = "correctness"

        async def evaluate(self, input):
            return EvalOutput(scores=[Score(metric_name="correctness", value=-1.0, source="llm_judge")])

    async def generate(query, agent_id, agent_definition=None, rollout_capture=None):
        assert set(run.evaluation_scope["item_ids"]) == {str(first.id), str(second.id)}
        assert run.evaluation_scope["required_metrics"] == ["exact_match"]
        assert run.evaluation_scope["exempted_item_ids"] == [str(second.id)]
        calls.append(query)
        return "fresh"

    engine = EvaluationEngine(db_session)
    monkeypatch.setattr(engine, "_generate_answer_via_agent", generate)
    await engine.run(
        [_StubEvaluator(), StubJudge()],
        dataset.id,
        answer_source="agent",
        agent_id=agent.id,
        agent_version=version,
        run=run,
        tags=["smoke"],
        summary_config={"threshold": 0.4},
    )
    assert len(calls) == 2
    agent.evaluation_gate = {"mode": "require", "min_pass_rate": 0.8}
    await db_session.flush()
    response = await _publish(client, agent.id, version)
    assert response.status_code == 200, response.text  # Judge errors remain informational.


@pytest.mark.parametrize("change", ["tags", "evaluators"])
async def test_retry_cannot_replace_frozen_evaluation_scope(client, db_session, monkeypatch, change) -> None:
    from hecate.ops.evaluation.format_evaluators import IsJsonEvaluator

    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    run, items = await _seed_scoped_run(db_session, agent, 1, required_metrics=["exact_match"], scores=[])
    original = dict(run.evaluation_scope)
    run.completed_at = None
    run.status = "running"
    items[0].tags = ["smoke"]
    await db_session.flush()

    async def unexpected_generation(*args, **kwargs):
        pytest.fail("scope conflict must be rejected before model execution")

    engine = EvaluationEngine(db_session)
    monkeypatch.setattr(engine, "_generate_answer_via_agent", unexpected_generation)
    with pytest.raises(ValueError, match="evaluation_scope_conflict"):
        await engine.run(
            [IsJsonEvaluator()] if change == "evaluators" else [ExactMatchEvaluator()],
            run.dataset_id,
            answer_source="agent",
            agent_id=agent.id,
            agent_version=1,
            run=run,
            tags=["smoke"] if change == "tags" else None,
        )
    assert run.evaluation_scope == original
    assert run.status == "failed"


async def test_run_read_exposes_scope_without_raw_agent_snapshot(client, db_session) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    run, _items = await _seed_scoped_run(
        db_session, agent, 1, n_items=1, required_metrics=["exact_match"], scores=[(0, "exact_match", 0.9)]
    )
    response = await client.get(f"/api/evaluation/runs/{run.id}")
    assert response.status_code == 200, response.text
    assert response.json()["evaluation_scope"] == run.evaluation_scope
    assert "agent_snapshot" not in response.json()


@pytest.mark.parametrize("failure", ["exception", "empty_output"])
async def test_real_engine_partial_evaluator_output_blocks_publish(client, db_session, monkeypatch, failure) -> None:
    from hecate.ops.evaluation.types import EvalOutput, Score

    agent = await _seed_agent(client, db_session)
    version = await _commit_version(db_session, agent)
    dataset, item = await _seed_dataset_with_answer(db_session, agent)
    item.query = "missing"
    for index in range(9):
        db_session.add(EvaluationItemModel(dataset_id=dataset.id, query=str(index), workspace_id=agent.workspace_id))
    await db_session.flush()

    class PartialEvaluator(ExactMatchEvaluator):
        async def evaluate(self, input):
            if input.query == "missing":
                if failure == "exception":
                    raise RuntimeError("evaluation unavailable")
                return EvalOutput(scores=[])
            return EvalOutput(scores=[Score(metric_name=self.name, value=1.0, source="deterministic")])

    async def generate(*args, **kwargs):
        return "fresh answer"

    engine = EvaluationEngine(db_session)
    monkeypatch.setattr(engine, "_generate_answer_via_agent", generate)
    result = await engine.run(
        [PartialEvaluator()],
        dataset.id,
        answer_source="agent",
        agent_id=agent.id,
        agent_version=version,
        summary_config={"threshold": 0.8},
    )
    run = await db_session.get(EvaluationRunModel, result.run_id)
    assert run.status == "completed"  # Evaluator failures stay isolated; publication checks evidence separately.
    await _set_require_gate(client, str(agent.id))
    response = await _publish(client, agent.id, version)
    assert response.status_code == 409, response.text
    report = response.json()["detail"]["error"]["details"]["evaluation_report"]
    assert _coverage_signal(report)["detail"]["missing_count"] == 1
    assert _coverage_signal(report)["detail"]["missing_samples"] == [
        {"item_id": str(item.id), "metric_name": "exact_match"}
    ]
    assert report["passed"] is False


async def test_coverage_counts_all_missing_pairs_but_bounds_report(client, db_session) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _seed_scoped_run(db_session, agent, 1, n_items=30, required_metrics=["exact_match", "is_json"], scores=[])
    await _set_require_gate(client, str(agent.id))
    response = await _publish(client, agent.id, 1)
    assert response.status_code == 409, response.text
    detail = _coverage_signal(response.json()["detail"]["error"]["details"]["evaluation_report"])["detail"]
    assert detail["missing_count"] == 60
    assert len(detail["missing_samples"]) == 20


async def test_custom_evaluator_cannot_claim_builtin_gate_support_by_name(client, db_session, monkeypatch) -> None:
    from hecate.ops.evaluation.types import EvalOutput, Score

    agent = await _seed_agent(client, db_session)
    version = await _commit_version(db_session, agent)
    dataset, _item = await _seed_dataset_with_answer(db_session, agent)

    class UnregisteredEvaluator:
        name = "exact_match"

        async def evaluate(self, input):
            return EvalOutput(scores=[Score(metric_name=self.name, value=1.0, source="deterministic")])

    async def generate(*args, **kwargs):
        return "fresh answer"

    engine = EvaluationEngine(db_session)
    monkeypatch.setattr(engine, "_generate_answer_via_agent", generate)
    await engine.run(
        [UnregisteredEvaluator()], dataset.id, answer_source="agent", agent_id=agent.id, agent_version=version
    )
    agent.evaluation_gate = {"mode": "require", "require_run": True}
    await db_session.flush()
    response = await _publish(client, agent.id, version)
    assert response.status_code == 409, response.text
    detail = _coverage_signal(response.json()["detail"]["error"]["details"]["evaluation_report"])["detail"]
    assert detail["reason"] == "no_required_deterministic_metrics"
