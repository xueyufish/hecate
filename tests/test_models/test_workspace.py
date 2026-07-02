"""Tests for WorkspaceModel.

Validates ORM behavior, schema serialization, and constraints for the
workspace entity used in multi-tenant resource isolation.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.organization import OrganizationModel
from hecate.models.workspace import (
    WorkspaceCreateSchema,
    WorkspaceModel,
    WorkspaceReadSchema,
)


@pytest.mark.asyncio
async def test_create_workspace(db_session: AsyncSession) -> None:
    """Test creating a workspace with valid data."""
    org = OrganizationModel(
        name="Test Org",
        slug="test-org",
        owner_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    db_session.add(org)
    await db_session.flush()

    ws = WorkspaceModel(
        org_id=org.id,
        name="Test Workspace",
        slug="test-ws",
    )
    db_session.add(ws)
    await db_session.flush()

    assert ws.id is not None
    assert ws.name == "Test Workspace"
    assert ws.slug == "test-ws"
    assert ws.org_id == org.id
    assert ws.deleted is False


@pytest.mark.asyncio
async def test_workspace_read_schema(db_session: AsyncSession) -> None:
    """Test WorkspaceReadSchema serialization."""
    org = OrganizationModel(
        name="Test Org",
        slug="test-org",
        owner_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    db_session.add(org)
    await db_session.flush()

    ws = WorkspaceModel(
        org_id=org.id,
        name="Test Workspace",
        slug="test-ws",
    )
    db_session.add(ws)
    await db_session.flush()

    schema = WorkspaceReadSchema.model_validate(ws)
    data = schema.model_dump()

    assert data["name"] == "Test Workspace"
    assert data["slug"] == "test-ws"
    assert data["org_id"] == org.id


@pytest.mark.asyncio
async def test_workspace_create_schema() -> None:
    """Test WorkspaceCreateSchema validation."""
    data = {"name": "New Workspace", "slug": "new-ws"}
    schema = WorkspaceCreateSchema.model_validate(data)
    assert schema.name == "New Workspace"
    assert schema.slug == "new-ws"


@pytest.mark.asyncio
async def test_workspace_soft_delete(db_session: AsyncSession) -> None:
    """Test soft delete behavior."""
    org = OrganizationModel(
        name="Test Org",
        slug="test-org",
        owner_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    db_session.add(org)
    await db_session.flush()

    ws = WorkspaceModel(
        org_id=org.id,
        name="To Delete",
        slug="to-delete",
    )
    db_session.add(ws)
    await db_session.flush()

    assert ws.deleted is False

    ws.deleted = True
    from datetime import UTC, datetime

    ws.deleted_at = datetime.now(UTC)
    await db_session.flush()

    assert ws.deleted is True
    assert ws.deleted_at is not None


@pytest.mark.asyncio
async def test_workspace_settings_json(db_session: AsyncSession) -> None:
    """Test settings JSON field."""
    org = OrganizationModel(
        name="Test Org",
        slug="test-org",
        owner_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    db_session.add(org)
    await db_session.flush()

    ws = WorkspaceModel(
        org_id=org.id,
        name="With Settings",
        slug="with-settings",
        settings={"max_agents": 10, "features": ["rag", "tools"]},
    )
    db_session.add(ws)
    await db_session.flush()

    assert ws.settings == {"max_agents": 10, "features": ["rag", "tools"]}
