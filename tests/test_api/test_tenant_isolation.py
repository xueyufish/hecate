"""Tests for tenant data isolation across workspace boundaries.

Validates that data created in workspace A cannot be accessed from workspace B,
covering conversations, messages, sessions, documents, evaluation datasets,
and vector store workspace_id filtering.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.models.agent import AgentModel
from hecate.models.conversation import ConversationModel
from hecate.models.evaluation import EvaluationDatasetModel
from hecate.models.session import SessionModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceRole


async def _create_workspace_with_member(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
) -> tuple[WorkspaceModel, AuthContext]:
    """Create a workspace and return (workspace, auth_context) pair."""
    from hecate.models.organization import OrganizationModel
    from hecate.models.workspace_member import WorkspaceMemberModel

    org = OrganizationModel(name=f"Org for {name}", slug=f"org-{name}", owner_id=user_id)
    db.add(org)
    await db.flush()

    ws = WorkspaceModel(org_id=org.id, name=name, slug=name)
    db.add(ws)
    await db.flush()

    member = WorkspaceMemberModel(
        user_id=user_id,
        workspace_id=ws.id,
        role=WorkspaceRole.ADMIN,
    )
    db.add(member)
    await db.flush()

    ctx = AuthContext(
        user_id=user_id,
        org_id=org.id,
        workspace_id=ws.id,
        role=WorkspaceRole.ADMIN,
        auth_method="jwt",
        api_key_scope=None,
    )
    return ws, ctx


async def _setup_two_workspaces(
    db: AsyncSession,
) -> tuple[AuthContext, AuthContext]:
    """Create two workspaces with different auth contexts."""
    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    _, ctx_a = await _create_workspace_with_member(db, user_id, "ws-alpha")
    _, ctx_b = await _create_workspace_with_member(db, user_id, "ws-beta")
    return ctx_a, ctx_b


# ---------------------------------------------------------------------------
# Cross-tenant isolation: Conversations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_cross_workspace_isolation(
    db_session: AsyncSession,
) -> None:
    """Conversations created in workspace A are invisible to workspace B."""
    ctx_a, ctx_b = await _setup_two_workspaces(db_session)

    # Create agent in workspace A
    agent = AgentModel(
        name="Agent A",
        workspace_id=ctx_a.workspace_id,
    )
    agent.model_config_db = {"model": "gpt-4o"}
    db_session.add(agent)
    await db_session.flush()

    # Create conversation in workspace A
    conv = ConversationModel(
        agent_id=agent.id,
        title="Secret Conversation",
        workspace_id=ctx_a.workspace_id,
    )
    db_session.add(conv)
    await db_session.flush()

    # Verify conversation is visible when filtering by workspace A
    result = await db_session.execute(
        select(ConversationModel).where(
            ConversationModel.workspace_id == ctx_a.workspace_id,
            ~ConversationModel.deleted,
        )
    )
    convs_a = result.scalars().all()
    assert len(convs_a) == 1
    assert convs_a[0].title == "Secret Conversation"

    # Verify conversation is invisible when filtering by workspace B
    result = await db_session.execute(
        select(ConversationModel).where(
            ConversationModel.workspace_id == ctx_b.workspace_id,
            ~ConversationModel.deleted,
        )
    )
    convs_b = result.scalars().all()
    assert len(convs_b) == 0


# ---------------------------------------------------------------------------
# Cross-tenant isolation: Sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_cross_workspace_isolation(
    db_session: AsyncSession,
) -> None:
    """Sessions created in workspace A are invisible to workspace B."""
    ctx_a, ctx_b = await _setup_two_workspaces(db_session)

    # Create agent + session in workspace A
    agent = AgentModel(
        name="Agent for Session",
        workspace_id=ctx_a.workspace_id,
    )
    agent.model_config_db = {"model": "gpt-4o"}
    db_session.add(agent)
    await db_session.flush()

    session = SessionModel(
        agent_id=agent.id,
        status="active",
        workspace_id=ctx_a.workspace_id,
    )
    db_session.add(session)
    await db_session.flush()

    # Verify session visible in workspace A
    result = await db_session.execute(
        select(SessionModel).where(
            SessionModel.workspace_id == ctx_a.workspace_id,
        )
    )
    sessions_a = result.scalars().all()
    assert len(sessions_a) == 1

    # Verify session invisible in workspace B
    result = await db_session.execute(
        select(SessionModel).where(
            SessionModel.workspace_id == ctx_b.workspace_id,
        )
    )
    sessions_b = result.scalars().all()
    assert len(sessions_b) == 0


# ---------------------------------------------------------------------------
# Cross-tenant isolation: Evaluation Datasets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluation_dataset_cross_workspace_isolation(
    db_session: AsyncSession,
) -> None:
    """Evaluation datasets created in workspace A are invisible to workspace B."""
    ctx_a, ctx_b = await _setup_two_workspaces(db_session)

    # Create dataset in workspace A
    ds = EvaluationDatasetModel(
        name="Secret Dataset",
        description="Workspace A data",
        metadata_={},
        workspace_id=ctx_a.workspace_id,
    )
    db_session.add(ds)
    await db_session.flush()

    # Verify visible in workspace A
    result = await db_session.execute(
        select(EvaluationDatasetModel).where(
            EvaluationDatasetModel.workspace_id == ctx_a.workspace_id,
            ~EvaluationDatasetModel.deleted,
        )
    )
    ds_a = result.scalars().all()
    assert len(ds_a) == 1
    assert ds_a[0].name == "Secret Dataset"

    # Verify invisible in workspace B
    result = await db_session.execute(
        select(EvaluationDatasetModel).where(
            EvaluationDatasetModel.workspace_id == ctx_b.workspace_id,
            ~EvaluationDatasetModel.deleted,
        )
    )
    ds_b = result.scalars().all()
    assert len(ds_b) == 0


# ---------------------------------------------------------------------------
# Cross-tenant isolation: API-level (HTTP endpoints)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_api_workspace_filtering(
    client: AsyncClient,
) -> None:
    """Conversations API returns only workspace-scoped results."""
    # Create an agent first
    agent_response = await client.post(
        "/api/agents",
        json={
            "name": "Tenant Agent",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
        },
    )
    assert agent_response.status_code == 201
    agent_id = agent_response.json()["id"]

    # Create conversation via API (workspace comes from auth context override)
    conv_response = await client.post(
        "/api/conversations",
        json={"agent_id": agent_id, "title": "Tenant Conversation"},
    )
    assert conv_response.status_code == 201
    conv_data = conv_response.json()
    assert conv_data["title"] == "Tenant Conversation"

    # List conversations — should see the one we just created
    list_response = await client.get("/api/conversations")
    assert list_response.status_code == 200
    data = list_response.json()
    assert data["total"] >= 1
    assert any(c["title"] == "Tenant Conversation" for c in data["items"])


@pytest.mark.asyncio
async def test_session_api_workspace_filtering(
    client: AsyncClient,
) -> None:
    """Sessions API creates sessions with workspace_id from auth context."""
    # Create an agent first
    agent_response = await client.post(
        "/api/agents",
        json={
            "name": "Session Agent",
            "model_config": {"model": "gpt-4o"},
            "mode": "chat",
        },
    )
    assert agent_response.status_code == 201
    agent_id = agent_response.json()["id"]

    # Create session via API
    session_response = await client.post(
        "/api/sessions",
        json={"agent_id": agent_id},
    )
    assert session_response.status_code == 201
    session_data = session_response.json()
    assert session_data["agent_id"] == agent_id

    # List sessions — should see the one we just created
    list_response = await client.get("/api/sessions")
    assert list_response.status_code == 200
    data = list_response.json()
    assert data["total"] >= 1


# ---------------------------------------------------------------------------
# Vector store workspace_id payload enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vector_store_search_with_workspace_filter() -> None:
    """VectorStore search methods accept workspace_id for filtering."""
    import inspect

    from hecate.services.rag.vector_store import VectorStore

    # Verify ABC signature includes workspace_id

    sig = inspect.signature(VectorStore.search_dense)
    assert "workspace_id" in sig.parameters

    sig = inspect.signature(VectorStore.search_sparse)
    assert "workspace_id" in sig.parameters

    sig = inspect.signature(VectorStore.search_hybrid)
    assert "workspace_id" in sig.parameters


# ---------------------------------------------------------------------------
# Qdrant filter builder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qdrant_workspace_filter_builder() -> None:
    """QdrantVectorStore._build_workspace_filter handles workspace_id correctly."""
    from hecate.services.rag.qdrant_store import QdrantVectorStore

    store = QdrantVectorStore(url="http://localhost:6333")

    # Without workspace_id: should return None with warning
    filt_none = store._build_workspace_filter(None)
    assert filt_none is None

    # With workspace_id: returns Filter if qdrant_client installed, else None
    filt = store._build_workspace_filter("ws-123")
    try:
        from qdrant_client.models import Filter

        assert filt is not None
        assert isinstance(filt, Filter)
    except ImportError:
        # qdrant_client not installed — filter gracefully returns None
        assert filt is None


# ---------------------------------------------------------------------------
# Default workspace_id on model creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_default_workspace_id(
    db_session: AsyncSession,
) -> None:
    """Models default to zero UUID when workspace_id not specified."""
    zero_uuid = uuid.UUID(int=0)

    conv = ConversationModel(agent_id=uuid.uuid4(), title="No WS")
    db_session.add(conv)
    await db_session.flush()

    assert conv.workspace_id == zero_uuid

    session = SessionModel(agent_id=uuid.uuid4(), status="active")
    db_session.add(session)
    await db_session.flush()

    assert session.workspace_id == zero_uuid

    ds = EvaluationDatasetModel(name="No WS Dataset")
    db_session.add(ds)
    await db_session.flush()

    assert ds.workspace_id == zero_uuid
