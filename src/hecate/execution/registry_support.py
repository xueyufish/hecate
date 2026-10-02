"""Tenant resolution and transactional audit support for execution registries."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.audit import AuditLogModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole


async def require_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> WorkspaceModel:
    """Resolve a live workspace without inventing an organization identity."""
    workspace = await session.get(WorkspaceModel, workspace_id)
    if workspace is None or workspace.deleted:
        raise ValueError("workspace not found")
    return workspace


async def is_workspace_admin(session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Check the persisted active user and live workspace administrator membership."""
    user = await session.get(UserModel, user_id)
    if user is None or user.deleted or not user.active:
        return False
    member = (
        await session.execute(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.deleted.is_(False),
                WorkspaceMemberModel.role == WorkspaceRole.ADMIN,
            )
        )
    ).scalar_one_or_none()
    return member is not None


async def registry_audit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    detail: dict[str, Any],
    actor: uuid.UUID | None = None,
    success: bool = True,
) -> None:
    """Append an audit to the caller's transaction, including rejection outcomes.

    A caller retaining a rejected operation must commit this transaction rather
    than unconditionally rolling it back. The registry never commits unrelated
    caller writes or opens an independent database connection.
    """
    workspace = await require_workspace(session, workspace_id)
    session.add(
        AuditLogModel(
            org_id=workspace.org_id,
            workspace_id=workspace.id,
            user_id=actor or uuid.UUID(int=0),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            success=success,
            metadata_=detail,
        )
    )
    await session.flush()
