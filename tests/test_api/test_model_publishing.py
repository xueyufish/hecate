"""Tests for model service publishing (6.47) and management quick wins (6.48).

Covers the publish lifecycle (evidence-gated publish, reversible unpublish,
reference-surface gating, delete guard), the audit trail, agent-commit
warnings, list search/filter, and provider call-count aggregation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from hecate.core.deps import get_current_user_id
from hecate.main import app
from hecate.models.audit import AuditLogModel
from hecate.models.trace import TraceModel
from hecate.studio.agents.versioning import AgentVersionService

UNPUB_MODEL = "publishing-model"


@pytest.fixture
def models_client(client: AsyncClient) -> AsyncClient:  # noqa: ARG001
    """Client with get_current_user_id overridden for /v1/models."""

    async def override_user_id() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override_user_id
    yield client
    app.dependency_overrides.pop(get_current_user_id, None)


def _stub_llm_test(monkeypatch, *, ok: bool = True) -> None:
    """Make POST /api/models/test succeed (or fail) without a real LLM."""
    from hecate_llm.service import llm_service

    async def fake_test_connection(*args: object, **kwargs: object):
        if ok:
            return SimpleNamespace(
                content="ok",
                model=kwargs.get("model_id"),
                usage={},
                finish_reason="stop",
            )
        return SimpleNamespace(content=None, model=None, usage={"error": "boom"}, finish_reason="error")

    monkeypatch.setattr(llm_service, "test_connection", fake_test_connection)


async def _seed_provider_and_model(
    client: AsyncClient,
    *,
    provider_display: str = "Publishing Provider",
    model_id: str = UNPUB_MODEL,
) -> dict:
    """Register a provider + custom model through the API; return the model row."""
    provider_resp = await client.post(
        "/api/model-providers",
        json={"display_name": provider_display, "api_key": "sk-test", "is_enabled": True},
    )
    assert provider_resp.status_code == 201
    provider = provider_resp.json()

    model_resp = await client.post(
        "/api/models",
        json={"provider_id": provider["id"], "model_id": model_id, "display_name": f"Test {model_id}"},
    )
    assert model_resp.status_code == 201
    return model_resp.json()


async def _pass_test(client: AsyncClient, monkeypatch, model_id: str = UNPUB_MODEL) -> None:
    """Run the inline model test with a stubbed gateway (records evidence)."""
    _stub_llm_test(monkeypatch, ok=True)
    resp = await client.post("/api/models/test", json={"model_id": model_id, "prompt": "hi"})
    assert resp.status_code == 200


class TestPublishLifecycle:
    async def test_publish_requires_test_evidence(self, client: AsyncClient) -> None:
        """Publishing without any passing test is refused with a 409 code."""
        model = await _seed_provider_and_model(client)

        resp = await client.post(f"/api/models/{model['id']}/publish")
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "MODEL_TEST_REQUIRED"

        listed = await client.get("/api/models")
        assert listed.json()["items"][0]["models"][0]["is_published"] is False

    async def test_publish_after_test_success_makes_model_referenceable(self, client, monkeypatch) -> None:
        """调测通过后发布: test → publish → /v1/models sees the model."""
        model = await _seed_provider_and_model(client)
        await _pass_test(client, monkeypatch)

        resp = await client.post(f"/api/models/{model['id']}/publish")
        assert resp.status_code == 200
        assert resp.json()["is_published"] is True
        assert resp.json()["last_test_passed_at"] is not None

        v1 = await client.get("/v1/models")
        assert UNPUB_MODEL in [m["id"] for m in v1.json()["data"]]

    async def test_failed_test_does_not_set_evidence(self, client, monkeypatch) -> None:
        """A failing model test leaves the publish gate closed."""
        model = await _seed_provider_and_model(client)
        _stub_llm_test(monkeypatch, ok=False)

        resp = await client.post("/api/models/test", json={"model_id": UNPUB_MODEL, "prompt": "hi"})
        assert resp.status_code == 400

        publish_resp = await client.post(f"/api/models/{model['id']}/publish")
        assert publish_resp.status_code == 409

    async def test_unpublish_is_reversible(self, client, monkeypatch) -> None:
        """Unpublish hides from the reference surface; republish restores it."""
        model = await _seed_provider_and_model(client)
        await _pass_test(client, monkeypatch)

        await client.post(f"/api/models/{model['id']}/publish")
        await client.post(f"/api/models/{model['id']}/unpublish")

        v1 = await client.get("/v1/models")
        assert UNPUB_MODEL not in [m["id"] for m in v1.json()["data"]]

        # Still manageable on the settings surface.
        listed = await client.get("/api/models")
        assert UNPUB_MODEL in [m["model_id"] for g in listed.json()["items"] for m in g["models"]]

        # Still testable inline.
        await _pass_test(client, monkeypatch)

        republish = await client.post(f"/api/models/{model['id']}/publish")
        assert republish.status_code == 200
        v1 = await client.get("/v1/models")
        assert UNPUB_MODEL in [m["id"] for m in v1.json()["data"]]

    async def test_publish_and_unpublish_write_audit_events(self, client, monkeypatch, db_session) -> None:
        """Both lifecycle actions persist audit records with actor and model."""
        model = await _seed_provider_and_model(client)
        await _pass_test(client, monkeypatch)

        await client.post(f"/api/models/{model['id']}/publish")
        await client.post(f"/api/models/{model['id']}/unpublish")

        result = await db_session.execute(
            select(AuditLogModel)
            .where(
                AuditLogModel.resource_type == "model",
                AuditLogModel.resource_id == uuid.UUID(model["id"]),
            )
            .order_by(AuditLogModel.created_at)
        )
        actions = [row.action for row in result.scalars().all()]
        assert "system.model.publish" in actions
        assert "system.model.unpublish" in actions


class TestReferenceSurface:
    async def test_unpublished_hidden_from_v1_but_manageable_and_testable(self, client, monkeypatch) -> None:
        """Reference-only gating: hidden from /v1/models, open on settings."""
        await _seed_provider_and_model(client)

        v1 = await client.get("/v1/models")
        assert UNPUB_MODEL not in [m["id"] for m in v1.json()["data"]]

        listed = await client.get("/api/models")
        assert UNPUB_MODEL in [m["model_id"] for g in listed.json()["items"] for m in g["models"]]

        await _pass_test(client, monkeypatch)

    async def test_existing_binding_survives_unpublish(self, client, monkeypatch, db_session) -> None:
        """The invocation seam (AgentVersionService.resolve) never blocks an
        unpublished model bound before the unpublish (reference-only gate)."""
        from hecate.models.agent import AgentModel

        model = await _seed_provider_and_model(client)
        await _pass_test(client, monkeypatch)
        await client.post(f"/api/models/{model['id']}/publish")
        await client.post(f"/api/models/{model['id']}/unpublish")

        agent = AgentModel(
            workspace_id=uuid.UUID(int=0),
            name="Bound Agent",
            model_config_db={"model": UNPUB_MODEL},
            mode="chat",
        )
        db_session.add(agent)
        await db_session.flush()

        resolved = await AgentVersionService(db_session).resolve(agent.id)
        assert resolved.config["model_config"]["model"] == UNPUB_MODEL


class TestDeleteGuard:
    async def test_delete_refused_while_agent_references_model(self, client, db_session) -> None:
        """Deleting a model bound by an agent returns 409 with the reference."""
        from hecate.models.agent import AgentModel

        model = await _seed_provider_and_model(client)
        agent = AgentModel(
            workspace_id=uuid.UUID(int=0),
            name="Guarded Agent",
            model_config_db={"model": UNPUB_MODEL},
            mode="chat",
        )
        db_session.add(agent)
        await db_session.flush()

        resp = await client.delete(f"/api/models/{model['id']}")
        assert resp.status_code == 409
        error = resp.json()["detail"]["error"]
        assert error["code"] == "MODEL_IN_USE"
        refs = error["details"]["references"]
        assert {"type": "agent", "name": "Guarded Agent"} in [{"type": r["type"], "name": r["name"]} for r in refs]

    async def test_delete_refused_while_workflow_references_model(self, client, db_session) -> None:
        """A graph DSL containing the model id blocks deletion."""
        from hecate.models.workflow import WorkflowModel, WorkflowVersionModel

        model = await _seed_provider_and_model(client)
        workflow = WorkflowModel(workspace_id=uuid.UUID(int=0), name="Guarded Flow", current_version=1)
        db_session.add(workflow)
        await db_session.flush()
        db_session.add(
            WorkflowVersionModel(
                workflow_id=workflow.id,
                version=1,
                graph_dsl={"nodes": [{"id": "n1", "config": {"model": UNPUB_MODEL}}]},
                compiled_graph={},
                change_summary="",
                workspace_id=uuid.UUID(int=0),
            )
        )
        await db_session.flush()

        resp = await client.delete(f"/api/models/{model['id']}")
        assert resp.status_code == 409
        refs = resp.json()["detail"]["error"]["details"]["references"]
        assert {"type": "workflow", "name": "Guarded Flow"} in [{"type": r["type"], "name": r["name"]} for r in refs]

    async def test_delete_allows_unreferenced_model(self, client) -> None:
        """An unreferenced model deletes cleanly and leaves both surfaces."""
        model = await _seed_provider_and_model(client)

        resp = await client.delete(f"/api/models/{model['id']}")
        assert resp.status_code == 204

        listed = await client.get("/api/models")
        assert UNPUB_MODEL not in [m["model_id"] for g in listed.json()["items"] for m in g["models"]]

    async def test_delete_matches_exact_ids_not_prefixes(self, client, db_session) -> None:
        """A graph referencing 'pub-model-2' must not guard 'pub-model'."""
        from hecate.models.workflow import WorkflowModel, WorkflowVersionModel

        model = await _seed_provider_and_model(client, model_id="pub-model")
        other = await _seed_provider_and_model(client, model_id="pub-model-2")

        workflow = WorkflowModel(workspace_id=uuid.UUID(int=0), name="Prefix Flow", current_version=1)
        db_session.add(workflow)
        await db_session.flush()
        db_session.add(
            WorkflowVersionModel(
                workflow_id=workflow.id,
                version=1,
                graph_dsl={"nodes": [{"id": "n1", "config": {"model": "pub-model-2"}}]},
                compiled_graph={},
                change_summary="",
                workspace_id=uuid.UUID(int=0),
            )
        )
        await db_session.flush()

        resp = await client.delete(f"/api/models/{model['id']}")
        assert resp.status_code == 204, "prefix collision must not block deletion"

        # And the genuinely referenced model stays guarded.
        resp = await client.delete(f"/api/models/{other['id']}")
        assert resp.status_code == 409


class TestSearchAndFilter:
    async def test_model_list_search(self, client: AsyncClient) -> None:
        """search= filters models by model_id/display_name substring."""
        await _seed_provider_and_model(client, provider_display="Alpha Prov", model_id="alpha-model")
        await _seed_provider_and_model(client, provider_display="Beta Prov", model_id="beta-model")

        resp = await client.get("/api/models", params={"search": "alpha"})
        models = [m["model_id"] for g in resp.json()["items"] for m in g["models"]]
        assert models == ["alpha-model"]

    async def test_model_list_publish_state_filter(self, client, monkeypatch) -> None:
        """publish_state=published/unpublished partitions the model list."""
        alpha = await _seed_provider_and_model(client, provider_display="Alpha Prov", model_id="alpha-model")
        await _seed_provider_and_model(client, provider_display="Beta Prov", model_id="beta-model")

        resp = await client.get("/api/models", params={"publish_state": "unpublished"})
        models = [m["model_id"] for g in resp.json()["items"] for m in g["models"]]
        assert set(models) == {"alpha-model", "beta-model"}

        await _pass_test(client, monkeypatch, model_id="alpha-model")
        await client.post(f"/api/models/{alpha['id']}/publish")

        published = await client.get("/api/models", params={"publish_state": "published"})
        assert [m["model_id"] for g in published.json()["items"] for m in g["models"]] == ["alpha-model"]

        unpublished = await client.get("/api/models", params={"publish_state": "unpublished"})
        assert [m["model_id"] for g in unpublished.json()["items"] for m in g["models"]] == ["beta-model"]

    async def test_provider_list_search(self, client: AsyncClient) -> None:
        """search= filters providers by name/display_name substring."""
        await _seed_provider_and_model(client, provider_display="Alpha Prov", model_id="alpha-model")
        await _seed_provider_and_model(client, provider_display="Beta Prov", model_id="beta-model")

        resp = await client.get("/api/model-providers", params={"search": "beta"})
        names = [p["display_name"] for p in resp.json()["items"]]
        assert names == ["Beta Prov"]


class TestCallCounts:
    async def _seed_traces(self, db_session) -> None:
        now = datetime.now(UTC)

        def _trace(model: str, started: datetime) -> TraceModel:
            return TraceModel(
                trace_id=uuid.uuid4(),
                type="generation",
                name="gen",
                metadata_={"model": model},
                start_time=started,
            )

        # 3 recent + 2 old calls of the registered model, 1 recent orphan
        # generation, and 1 recent non-generation row that must not count.
        db_session.add_all(
            [_trace(UNPUB_MODEL, now) for _ in range(3)]
            + [_trace(UNPUB_MODEL, now - timedelta(days=40)) for _ in range(2)]
            + [_trace("ghost-model", now)]
        )
        db_session.add(
            TraceModel(
                trace_id=uuid.uuid4(),
                type="tool",
                name="tool-call",
                metadata_={"model": UNPUB_MODEL},
                start_time=now,
            )
        )
        await db_session.flush()

    async def test_provider_card_call_counts(self, client: AsyncClient, db_session) -> None:
        """30-day window and all-time counts land on the provider card."""
        await _seed_provider_and_model(client)
        await self._seed_traces(db_session)

        resp = await client.get("/api/model-providers")
        providers = resp.json()["items"]
        assert len(providers) == 1
        assert providers[0]["call_count_30d"] == 3
        assert providers[0]["call_count_total"] == 5
        assert resp.json()["unmatched_call_count_30d"] == 1
        assert resp.json()["unmatched_call_count_total"] == 1

    async def test_call_counts_use_single_aggregation_query(self, client: AsyncClient, db_session) -> None:
        """The aggregation is one grouped query, not N+1 per provider."""
        from sqlalchemy import event as sa_event

        from tests.conftest import test_engine

        await _seed_provider_and_model(client, provider_display="Prov A", model_id="model-a")
        await _seed_provider_and_model(client, provider_display="Prov B", model_id="model-b")
        now = datetime.now(UTC)
        db_session.add_all(
            [
                TraceModel(
                    trace_id=uuid.uuid4(),
                    type="generation",
                    name="gen",
                    metadata_={"model": "model-a"},
                    start_time=now,
                ),
                TraceModel(
                    trace_id=uuid.uuid4(),
                    type="generation",
                    name="gen",
                    metadata_={"model": "ghost-model"},
                    start_time=now,
                ),
            ]
        )
        await db_session.flush()

        trace_queries: list[str] = []

        def _count(conn, cursor, statement, parameters, context, executemany):
            if "traces" in statement and statement.lstrip().upper().startswith("SELECT"):
                trace_queries.append(statement)

        sa_event.listen(test_engine.sync_engine, "before_cursor_execute", _count)
        try:
            resp = await client.get("/api/model-providers")
        finally:
            sa_event.remove(test_engine.sync_engine, "before_cursor_execute", _count)

        assert resp.status_code == 200
        assert len(trace_queries) == 1, "call-count aggregation must be a single query over traces"


class TestAgentCommitWarning:
    async def _seed_agent(self, db_session, model_name: str):
        from hecate.models.agent import AgentModel

        agent = AgentModel(
            workspace_id=uuid.UUID(int=0),
            name="Warning Agent",
            model_config_db={"model": model_name},
            mode="chat",
        )
        db_session.add(agent)
        await db_session.flush()
        return agent

    async def test_commit_warns_on_unpublished_model(self, client: AsyncClient, db_session) -> None:
        """Commit succeeds but carries a warning for registry-known unpublished models."""
        await _seed_provider_and_model(client)  # UNPUB_MODEL, unpublished
        agent = await self._seed_agent(db_session, UNPUB_MODEL)

        result = await AgentVersionService(db_session).commit_version(agent.id)
        assert result["warnings"] == [f"Model '{UNPUB_MODEL}' is not published"]
        assert result["version"] == 1

    async def test_commit_silent_for_published_or_unknown_models(self, client, monkeypatch, db_session) -> None:
        """Published models and registry-unknown models produce no warning."""
        model = await _seed_provider_and_model(client)
        await _pass_test(client, monkeypatch)
        await client.post(f"/api/models/{model['id']}/publish")

        agent = await self._seed_agent(db_session, UNPUB_MODEL)
        result = await AgentVersionService(db_session).commit_version(agent.id)
        assert "warnings" not in result

        unknown_agent = await self._seed_agent(db_session, "not-in-registry-model")
        result = await AgentVersionService(db_session).commit_version(unknown_agent.id)
        assert "warnings" not in result


@pytest.fixture
def client(admin_client: AsyncClient) -> AsyncClient:
    """Model-domain tests run as platform admin — they exercise provider
    business behavior, not the admin gate (covered by the dedicated authz
    matrix in test_e2e_model_provider.py)."""
    return admin_client
