"""Tests for WorkspaceMemberModel.

Validates ORM behavior, role enum, and constraints for workspace membership.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.organization import OrganizationModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import (
    WorkspaceMemberModel,
    WorkspaceRole,
)


@pytest.mark.asyncio
async def test_create_workspace_member(db_session: AsyncSession) -> None:
    """Test creating a workspace member with valid data."""
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

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    member = WorkspaceMemberModel(
        user_id=user_id,
        workspace_id=ws.id,
        role=WorkspaceRole.ADMIN,
    )
    db_session.add(member)
    await db_session.flush()

    assert member.id is not None
    assert member.user_id == user_id
    assert member.workspace_id == ws.id
    assert member.role == WorkspaceRole.ADMIN


@pytest.mark.asyncio
async def test_workspace_member_unique_constraint(db_session: AsyncSession) -> None:
    """Test unique (user_id, workspace_id) constraint."""
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

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000002")

    member1 = WorkspaceMemberModel(
        user_id=user_id,
        workspace_id=ws.id,
        role=WorkspaceRole.ADMIN,
    )
    db_session.add(member1)
    await db_session.flush()

    member2 = WorkspaceMemberModel(
        user_id=user_id,
        workspace_id=ws.id,
        role=WorkspaceRole.VIEWER,
    )
    db_session.add(member2)

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_workspace_role_enum_values() -> None:
    """Test WorkspaceRole enum values."""
    assert WorkspaceRole.ADMIN.value == "admin"
    assert WorkspaceRole.EDITOR.value == "editor"
    assert WorkspaceRole.VIEWER.value == "viewer"


@pytest.mark.asyncio
async def test_workspace_member_update_role(db_session: AsyncSession) -> None:
    """Test updating member role."""
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

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    member = WorkspaceMemberModel(
        user_id=user_id,
        workspace_id=ws.id,
        role=WorkspaceRole.VIEWER,
    )
    db_session.add(member)
    await db_session.flush()

    assert member.role == WorkspaceRole.VIEWER

    member.role = WorkspaceRole.ADMIN
    await db_session.flush()

    assert member.role == WorkspaceRole.ADMIN
