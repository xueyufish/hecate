"""REST Agent write authorization across viewer and editor roles."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceRole

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def viewer_role_client() -> AsyncGenerator[AsyncClient, None]:
    """An httpx.AsyncClient wired to the FastAPI app with an injected
    AuthContext reporting VIEWER role and a fresh workspace. Bypasses the
    JWT membership gate so the role decision is the only variable.

    The autouse ``setup_database`` fixture handles schema setup and row
    cleanup; this fixture only injects the role-scoped auth context.
    """
    from hecate.core.config import settings
    from hecate.main import app
    from tests.conftest import test_session_factory

    workspace_id = uuid.uuid4()
    async with test_session_factory() as session:
        org = OrganizationModel(id=uuid.uuid4(), name="G1 Viewer Org", slug="g1-v-org", owner_id=uuid.uuid4())
        session.add(org)
        ws = WorkspaceModel(id=workspace_id, org_id=org.id, name="G1 Viewer WS", slug="g1-v-ws")
        session.add(ws)
        await session.commit()

    ctx = AuthContext(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000010"),
        org_id=None,
        workspace_id=workspace_id,
        role=WorkspaceRole.VIEWER,
        auth_method="jwt",
        api_key_scope=None,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_auth_context() -> AuthContext:
        return ctx

    settings.JWT_SECRET = "test-jwt-secret-for-ci"
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_context] = override_get_auth_context

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            yield ac
        finally:
            app.dependency_overrides.clear()


@pytest.fixture
async def editor_role_client() -> AsyncGenerator[AsyncClient, None]:
    """Companion to viewer_role_client: injected AuthContext reports EDITOR.

    Like viewer_role_client, schema setup and row cleanup are left to the
    autouse ``setup_database`` fixture.
    """
    from hecate.core.config import settings
    from hecate.main import app
    from tests.conftest import test_session_factory

    workspace_id = uuid.uuid4()
    async with test_session_factory() as session:
        org = OrganizationModel(id=uuid.uuid4(), name="G1 Editor Org", slug="g1-e-org", owner_id=uuid.uuid4())
        session.add(org)
        ws = WorkspaceModel(id=workspace_id, org_id=org.id, name="G1 Editor WS", slug="g1-e-ws")
        session.add(ws)
        await session.commit()

    ctx = AuthContext(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000011"),
        org_id=None,
        workspace_id=workspace_id,
        role=WorkspaceRole.EDITOR,
        auth_method="jwt",
        api_key_scope=None,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_auth_context() -> AuthContext:
        return ctx

    settings.JWT_SECRET = "test-jwt-secret-for-ci"
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_auth_context] = override_get_auth_context

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            yield ac
        finally:
            app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("POST", "/api/agents", {"name": "viewer-creates", "model_config": {"model": "gpt-4o"}, "mode": "chat"}),
        ("PUT", "/api/agents/{agent_id}", {"name": "viewer-updates"}),
        ("DELETE", "/api/agents/{agent_id}", None),
        (
            "POST",
            "/api/agents/import",
            {
                "version": "1.0",
                "agent": {"name": "viewer-imports", "model_config": {"model": "gpt-4o"}, "mode": "chat"},
                "workflow": {"name": "viewer-workflow", "graph_dsl": {}},
            },
        ),
        ("POST", "/api/agents/{agent_id}/skills", {"skill_name": "test-skill"}),
        ("POST", "/api/agents/{agent_id}/skills/promote", {"skill_name": "test-skill"}),
        ("DELETE", "/api/agents/{agent_id}/skills/test-skill", None),
    ],
)
async def test_viewer_agent_writes_return_403(
    viewer_role_client: AsyncClient, method: str, path: str, payload: dict | None
) -> None:
    """Every REST Agent mutation rejects a viewer before touching storage."""
    from sqlalchemy import func, select

    from hecate.models.agent import AgentModel
    from hecate.models.workflow import WorkflowModel
    from tests.conftest import test_session_factory

    path = path.format(agent_id=uuid.uuid4())
    response = await viewer_role_client.request(method, path, json=payload)
    assert response.status_code == 403, response.text
    async with test_session_factory() as db:
        assert (await db.execute(select(func.count()).select_from(AgentModel))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(WorkflowModel))).scalar_one() == 0


async def test_editor_create_agent_succeeds(editor_role_client: AsyncClient) -> None:
    """Companion: editor gets 201 on POST /api/agents."""
    resp = await editor_role_client.post(
        "/api/agents",
        json={"name": "editor-creates", "model_config": {"model": "gpt-4o"}, "mode": "chat"},
    )
    assert resp.status_code == 201, f"expected 201 for editor create, got {resp.status_code}: {resp.text}"


async def test_editor_can_use_other_agent_write_routes(editor_role_client: AsyncClient) -> None:
    """The editor gate preserves authorized Agent configuration operations."""
    from hecate.models.agent import AgentModel
    from hecate.models.skill import SkillModel
    from tests.conftest import test_session_factory

    created = await editor_role_client.post(
        "/api/agents",
        json={"name": "editor-managed", "model_config": {"model": "gpt-4o"}, "mode": "chat"},
    )
    assert created.status_code == 201, created.text
    agent_id = created.json()["id"]
    async with test_session_factory() as db:
        agent = await db.get(AgentModel, uuid.UUID(agent_id))
        assert agent is not None
        db.add(
            SkillModel(
                workspace_id=agent.workspace_id,
                name="editor-skill",
                description="test",
                source="user",
                instructions="Use the skill.",
            )
        )
        await db.commit()

    updated = await editor_role_client.put(f"/api/agents/{agent_id}", json={"name": "editor-updated"})
    assert updated.status_code == 200, updated.text
    attached = await editor_role_client.post(f"/api/agents/{agent_id}/skills", json={"skill_name": "editor-skill"})
    assert attached.status_code == 200, attached.text
    detached = await editor_role_client.delete(f"/api/agents/{agent_id}/skills/editor-skill")
    assert detached.status_code == 200, detached.text
    promoted = await editor_role_client.post(
        f"/api/agents/{agent_id}/skills/promote", json={"skill_name": "editor-skill"}
    )
    assert promoted.status_code == 200, promoted.text
    deleted = await editor_role_client.delete(f"/api/agents/{agent_id}")
    assert deleted.status_code == 204, deleted.text

    imported = await editor_role_client.post(
        "/api/agents/import",
        json={
            "version": "1.0",
            "agent": {"name": "editor-imported", "model_config": {"model": "gpt-4o"}, "mode": "chat"},
        },
    )
    assert imported.status_code == 201, imported.text


async def test_editor_cannot_bind_foreign_knowledge(editor_role_client: AsyncClient) -> None:
    """A valid editor role cannot attach another workspace's knowledge base."""
    from hecate.models.knowledge import KnowledgeBaseModel
    from tests.conftest import test_session_factory

    foreign_id = uuid.uuid4()
    async with test_session_factory() as db:
        db.add(
            KnowledgeBaseModel(
                id=foreign_id,
                workspace_id=uuid.uuid4(),
                name="Foreign knowledge",
                collection_name="foreign_knowledge",
                embedding_model="test",
                chunk_strategy="fixed",
                search_mode="dense",
            )
        )
        await db.commit()

    response = await editor_role_client.post(
        "/api/agents",
        json={
            "name": "invalid-knowledge-reference",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
            "knowledge_base_ids": [str(foreign_id)],
        },
    )
    assert response.status_code == 400, response.text
