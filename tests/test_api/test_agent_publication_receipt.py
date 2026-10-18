"""Agent publication receipts (step11): evidence identity per publish.

Covers the receipt contract end to end:

- Receipt anchoring: the recorded candidate/baseline run identities and
  report snapshot are the ones the gate actually consumed; later gate
  config edits, task edits or fresh evaluation runs never rewrite them.
- Atomicity: a receipt-write failure fails the whole publish (pointer
  unmoved, no orphan receipt); a require rejection produces no receipt.
- Outcome enum: passed / warn_passed / force_bypassed / gate_off —
  gate_off never carries "evaluation passed" semantics; force coexists
  with the bypass audit row; rollback creates no receipt.
- History query: newest-first pagination, workspace isolation (404),
  snapshot reads without recomputation, no backfill for pre-receipt
  publications.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.audit import AuditLogModel
from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationScoreModel,
    EvaluationTaskModel,
    RunStatus,
)
from hecate.studio.agents.versioning import (
    AgentPublicationModel,
    AgentVersionService,
    canonical_hash,
)

_PASSING_VALUE = 0.9
_DEFAULT_THRESHOLD = 0.8


async def _seed_agent(client, db: AsyncSession, *, persona: str = "helpful"):
    resp = await client.post(
        "/api/agents",
        json={"name": "Receipt Agent", "model_config": {"model": "gpt-4o"}, "mode": "chat", "persona": persona},
    )
    assert resp.status_code == 201, resp.text
    return await db.get(AgentModel, uuid.UUID(resp.json()["id"]))


async def _commit_version(db: AsyncSession, agent: AgentModel) -> int:
    return int((await AgentVersionService(db).commit_version(agent.id))["version"])


async def _seed_bound_run(
    db: AsyncSession,
    agent: AgentModel,
    version: int,
    *,
    value: float = _PASSING_VALUE,
) -> EvaluationRunModel:
    """Completed run bound to the version with complete single-metric scope."""
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
    task = EvaluationTaskModel(
        task_type="offline",
        name=f"task-{uuid.uuid4().hex[:8]}",
        evaluator_configs=["stub"],
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
        agent_version=version,
        dataset_id=dataset.id,
        task_id=task.id,
        status=RunStatus.COMPLETED.value,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        summary={"threshold": _DEFAULT_THRESHOLD},
        workspace_id=agent.workspace_id,
        evaluation_scope={
            "schema_version": 1,
            "item_ids": [str(item.id)],
            "required_metrics": ["is_json"],
            "exempted_item_ids": [],
            "dataset_snapshot_hash": None,
        },
    )
    db.add(run)
    await db.flush()
    db.add(
        EvaluationScoreModel(
            run_id=run.id,
            item_id=item.id,
            metric_name="is_json",
            value=value,
            source="deterministic",
            workspace_id=agent.workspace_id,
        )
    )
    await db.flush()
    return run


async def _set_gate(client, agent_id: str, gate: dict | None) -> None:
    resp = await client.put(f"/api/agents/{agent_id}", json={"evaluation_gate": gate})
    assert resp.status_code == 200, resp.text


async def _publish(client, agent_id: str, version: int, *, force: bool = False):
    payload = {"force": True} if force else {}
    return await client.post(f"/api/agents/{agent_id}/publish/{version}", json=payload)


async def _publications(client, agent_id: str, *, limit: int = 20, offset: int = 0):
    return await client.get(f"/api/agents/{agent_id}/publications?limit={limit}&offset={offset}")


async def test_receipt_anchors_runs_and_survives_later_changes(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    consumed = await _seed_bound_run(db_session, agent, 2)
    await _set_gate(client, str(agent.id), {"mode": "require", "require_run": True, "min_pass_rate": 0.8})

    published = await _publish(client, str(agent.id), 2)
    assert published.status_code == 200, published.text
    original_report = published.json()["evaluation_report"]
    original_hash = consumed.agent_config_hash

    # Later changes: stricter gate config, task edits, a fresh higher run.
    await _set_gate(client, str(agent.id), {"mode": "require", "require_run": True, "min_pass_rate": 0.99})
    task = await db_session.get(EvaluationTaskModel, consumed.task_id)
    task.config = {**task.config, "tags": ["edited-after-publish"]}
    await db_session.flush()
    await db_session.refresh(task)
    assert task.config["tags"] == ["edited-after-publish"]
    await _seed_bound_run(db_session, agent, 2, value=1.0)

    listed = await _publications(client, str(agent.id))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    receipt = body["items"][0]
    assert receipt["version"] == 2
    assert receipt["outcome"] == "passed"
    assert receipt["candidate_run_id"] == str(consumed.id)
    assert receipt["candidate_run_hash"] == original_hash
    assert receipt["gate_config"] == {"mode": "require", "require_run": True, "min_pass_rate": 0.8}
    assert receipt["gate_report"] == original_report
    assert set(receipt) == {
        "id",
        "agent_id",
        "version",
        "agent_config_hash",
        "candidate_run_id",
        "candidate_run_hash",
        "baseline_run_id",
        "baseline_run_hash",
        "gate_config",
        "gate_report",
        "outcome",
        "operator_user_id",
        "created_at",
    }


async def test_require_rejection_creates_no_receipt(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _set_gate(client, str(agent.id), {"mode": "require", "require_run": True})

    blocked = await _publish(client, str(agent.id), 1)
    assert blocked.status_code == 409, blocked.text

    listed = await _publications(client, str(agent.id))
    assert listed.json()["total"] == 0
    read = await client.get(f"/api/agents/{agent.id}")
    assert read.json()["published_version"] is None


@pytest.mark.parametrize("force", [False, True])
async def test_atomicity_receipt_failure_fails_publish(client, db_session: AsyncSession, force: bool) -> None:
    agent = await _seed_agent(client, db_session)
    agent_id = agent.id
    await _commit_version(db_session, agent)
    await db_session.commit()
    if force:
        await _set_gate(client, str(agent_id), {"mode": "require", "require_run": True})

    def violate_constraint(mapper, connection, target):
        # Exercise a real SQL constraint failure after the bypass audit flush.
        target.version = None

    event.listen(AgentPublicationModel, "before_insert", violate_constraint)
    try:
        with pytest.raises(IntegrityError, match="NOT NULL"):
            await _publish(client, str(agent_id), 1, force=force)
    finally:
        event.remove(AgentPublicationModel, "before_insert", violate_constraint)

    read = await client.get(f"/api/agents/{agent_id}")
    assert read.json()["published_version"] is None
    assert (await _publications(client, str(agent_id))).json()["total"] == 0
    audits = await db_session.execute(
        select(AuditLogModel).where(
            AuditLogModel.resource_id == agent_id,
            AuditLogModel.action == "AGENT_EVALUATION_GATE_BYPASS",
        )
    )
    assert audits.scalars().all() == []


async def test_force_outcome_and_bypass_audit_coexist(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _set_gate(client, str(agent.id), {"mode": "require", "require_run": True})

    forced = await _publish(client, str(agent.id), 1, force=True)
    assert forced.status_code == 200, forced.text
    assert forced.json()["evaluation_report"]["bypassed_by_force"] is True

    listed = await _publications(client, str(agent.id))
    receipt = listed.json()["items"][0]
    assert receipt["outcome"] == "force_bypassed"
    assert receipt["candidate_run_id"] is None  # no matching run existed
    audit_rows = (
        (
            await db_session.execute(
                AuditLogModel.__table__.select().where(
                    AuditLogModel.workspace_id == agent.workspace_id,
                    AuditLogModel.resource_id == agent.id,
                    AuditLogModel.action == "AGENT_EVALUATION_GATE_BYPASS",
                )
            )
        )
        .mappings()
        .all()
    )
    assert len(audit_rows) == 1
    assert str(audit_rows[0]["user_id"]) == receipt["operator_user_id"]
    assert audit_rows[0]["metadata"]["version"] == receipt["version"]
    assert audit_rows[0]["metadata"]["evaluation_report"] == receipt["gate_report"]


async def test_gate_off_outcome_is_not_evaluation_pass(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)

    published = await _publish(client, str(agent.id), 1)
    assert published.status_code == 200, published.text

    listed = await _publications(client, str(agent.id))
    receipt = listed.json()["items"][0]
    assert receipt["outcome"] == "gate_off"
    assert receipt["candidate_run_id"] is None
    serialized = json.dumps(receipt)
    assert '"passed"' not in serialized and "bypass" not in serialized


async def test_warn_pass_outcome(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _set_gate(client, str(agent.id), {"mode": "warn", "require_run": True})

    published = await _publish(client, str(agent.id), 1)
    assert published.status_code == 200, published.text
    receipt = (await _publications(client, str(agent.id))).json()["items"][0]
    assert receipt["outcome"] == "warn_passed"
    assert receipt["candidate_run_id"] is None


async def test_pagination_newest_first(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    for _ in range(3):
        await _commit_version(db_session, agent)
    from datetime import timedelta

    base_time = datetime.now(UTC)
    for sequence, version in enumerate((1, 2, 3)):
        published = await _publish(client, str(agent.id), version)
        assert published.status_code == 200
        # SQLite timestamps carry one-second resolution: make the ordering
        # explicit instead of relying on id tiebreaks.
        await db_session.execute(
            AgentPublicationModel.__table__.update()
            .where(AgentPublicationModel.__table__.c.version == version)
            .values(created_at=base_time + timedelta(minutes=sequence))
        )
        await db_session.flush()

    page1 = (await _publications(client, str(agent.id), limit=2)).json()
    page2 = (await _publications(client, str(agent.id), limit=2, offset=2)).json()
    assert page1["total"] == 3
    assert [r["version"] for r in page1["items"]] == [3, 2]
    assert [r["version"] for r in page2["items"]] == [1]


async def test_cross_workspace_404(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _publish(client, str(agent.id), 1)

    foreign_workspace = uuid.uuid4()
    foreign_agent = AgentModel(
        workspace_id=foreign_workspace,
        name="Foreign Agent",
        model_config_db={"model": "gpt-4o"},
        mode="chat",
    )
    db_session.add(foreign_agent)
    await db_session.flush()

    resp = await _publications(client, str(foreign_agent.id))
    assert resp.status_code == 404, resp.text


async def test_rollback_creates_no_receipt(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    published = await _publish(client, str(agent.id), 2)
    assert published.status_code == 200

    rolled = await client.post(f"/api/agents/{agent.id}/rollback/1")
    assert rolled.status_code == 200, rolled.text

    listed = await _publications(client, str(agent.id))
    assert listed.json()["total"] == 1  # only the original publish
    assert listed.json()["items"][0]["version"] == 2


async def test_no_backfill_for_legacy_publication(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    # Seed the shape left by a publication before receipts existed.
    agent.published_version = 1
    await db_session.commit()
    listed = await _publications(client, str(agent.id))
    assert listed.status_code == 200
    assert listed.json() == {"items": [], "total": 0}


@pytest.mark.parametrize(
    ("auth_method", "api_key_scope", "allowed"),
    [
        ("jwt", None, False),
        ("sso", None, False),
        ("ldap", None, False),
        ("api_key", "workspace", False),
        ("api_key", "system", True),
    ],
)
async def test_missing_workspace_requires_explicit_system_scope(
    client, db_session: AsyncSession, auth_context, auth_method, api_key_scope, allowed
) -> None:
    from dataclasses import replace

    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app

    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _commit_version(db_session, agent)
    assert (await _publish(client, str(agent.id), 1)).status_code == 200
    await _set_gate(client, str(agent.id), {"mode": "require", "require_run": True})
    context = replace(auth_context, workspace_id=None, role=None, auth_method=auth_method, api_key_scope=api_key_scope)

    async def override_context():
        return context

    original = app.dependency_overrides[get_auth_context]
    app.dependency_overrides[get_auth_context] = override_context
    try:
        history = await _publications(client, str(agent.id))
        published = await _publish(client, str(agent.id), 2, force=True)
    finally:
        app.dependency_overrides[get_auth_context] = original
    expected = 200 if allowed else 404
    assert history.status_code == expected, history.text
    assert published.status_code == expected, published.text
    read = await client.get(f"/api/agents/{agent.id}")
    assert read.json()["published_version"] == (2 if allowed else 1)
    assert (await _publications(client, str(agent.id))).json()["total"] == (2 if allowed else 1)


async def test_cross_workspace_publish_cannot_create_receipt(client, db_session: AsyncSession) -> None:
    agent = AgentModel(workspace_id=uuid.uuid4(), name="Foreign", model_config_db={}, mode="chat")
    db_session.add(agent)
    await db_session.flush()
    await _commit_version(db_session, agent)
    await db_session.commit()
    response = await _publish(client, str(agent.id), 1)
    assert response.status_code == 404, response.text
    await db_session.refresh(agent)
    assert agent.published_version is None
    assert (await db_session.execute(select(AgentPublicationModel))).scalars().all() == []


async def test_receipt_freezes_decision_inputs_before_evaluation(client, db_session: AsyncSession, monkeypatch) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    consumed = await _seed_bound_run(db_session, agent, 1)
    gate = {"mode": "require", "require_run": True, "min_pass_rate": 0.8}
    await _set_gate(client, str(agent.id), gate)
    await db_session.refresh(agent)
    service = AgentVersionService(db_session)
    evaluate = service._evaluate_gate

    async def evaluate_then_edit(config, candidate, baseline):
        result = await evaluate(config, candidate, baseline)
        agent.evaluation_gate = {"mode": "off"}
        await _seed_bound_run(db_session, agent, 1, value=1.0)
        return result

    monkeypatch.setattr(service, "_evaluate_gate", evaluate_then_edit)
    published = await service.publish_version(agent.id, 1)
    await db_session.commit()
    receipt = (await _publications(client, str(agent.id))).json()["items"][0]
    assert receipt["candidate_run_id"] == str(consumed.id)
    assert receipt["gate_config"] == gate
    assert receipt["gate_report"] == published["evaluation_report"]


@pytest.mark.parametrize("regression", [False, True])
async def test_baseline_records_only_consumed_regression_evidence(client, db_session: AsyncSession, regression) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    baseline = await _seed_bound_run(db_session, agent, 1)
    assert (await _publish(client, str(agent.id), 1)).status_code == 200
    await _commit_version(db_session, agent)
    await _seed_bound_run(db_session, agent, 2)
    await _set_gate(client, str(agent.id), {"mode": "require", "require_run": True, "block_on_regression": regression})
    response = await _publish(client, str(agent.id), 2)
    assert response.status_code == 200, response.text
    receipt = next(row for row in (await _publications(client, str(agent.id))).json()["items"] if row["version"] == 2)
    assert receipt["baseline_run_id"] == (str(baseline.id) if regression else None)
    assert receipt["baseline_run_hash"] == (baseline.agent_config_hash if regression else None)


async def test_republication_appends_and_history_never_evaluates(client, db_session: AsyncSession, monkeypatch) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    for _ in range(2):
        assert (await _publish(client, str(agent.id), 1)).status_code == 200

    async def unexpected_evaluation(*args, **kwargs):
        raise AssertionError("History must not evaluate the current gate")

    monkeypatch.setattr(AgentVersionService, "_evaluate_gate", unexpected_evaluation)
    body = (await _publications(client, str(agent.id))).json()
    assert body["total"] == 2
    assert len({row["id"] for row in body["items"]}) == 2
    assert [row["version"] for row in body["items"]] == [1, 1]


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (101, 0), (20, -1)])
async def test_invalid_pagination_is_rejected(client, db_session: AsyncSession, limit, offset) -> None:
    agent = await _seed_agent(client, db_session)
    assert (await _publications(client, str(agent.id), limit=limit, offset=offset)).status_code == 422


async def test_force_flag_without_bypass_still_records_passed(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    await _seed_bound_run(db_session, agent, 1)
    await _set_gate(client, str(agent.id), {"mode": "require", "require_run": True})
    published = await _publish(client, str(agent.id), 1, force=True)
    assert published.status_code == 200, published.text
    receipt = (await _publications(client, str(agent.id))).json()["items"][0]
    assert receipt["outcome"] == "passed"
    assert receipt["gate_report"]["bypassed_by_force"] is False


async def test_receipt_outcome_rejects_unknown_values(client, db_session: AsyncSession) -> None:
    agent = await _seed_agent(client, db_session)
    await _commit_version(db_session, agent)
    with pytest.raises(IntegrityError, match="ck_agent_publications_outcome"):
        async with db_session.begin_nested():
            db_session.add(
                AgentPublicationModel(
                    agent_id=agent.id,
                    version=1,
                    agent_config_hash="a" * 64,
                    workspace_id=agent.workspace_id,
                    outcome="invalid",
                )
            )
            await db_session.flush()
