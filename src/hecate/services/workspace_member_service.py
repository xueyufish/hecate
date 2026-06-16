"""Workspace member service for membership and role management.

Handles adding, removing, and updating member roles within workspaces,
with enforcement of the "at least one admin" invariant.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.workspace_member import (
    WorkspaceMemberModel,
    WorkspaceRole,
)

logger = logging.getLogger(__name__)


class WorkspaceMemberService:
    """Manages workspace membership and role assignments."""

    async def add_member(
        self,
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        role: WorkspaceRole = WorkspaceRole.VIEWER,
    ) -> WorkspaceMemberModel:
        """Add a user to a workspace with the given role.

        Args:
            db: Async database session.
            workspace_id: The workspace UUID.
            user_id: The user UUID to add.
            role: The role to assign.

        Returns:
            The newly created WorkspaceMemberModel.

        Raises:
            ValueError: If the user is already a member.
        """
        existing = await db.execute(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.deleted.is_(False),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError("User is already a member of this workspace")

        membership = WorkspaceMemberModel(
            user_id=user_id,
            workspace_id=workspace_id,
            role=role,
        )
        db.add(membership)
        await db.flush()
        logger.info("Member added: user %s → workspace %s as %s", user_id, workspace_id, role.value)
        return membership

    async def remove_member(
        self,
        db: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Remove a user from a workspace.

        Args:
            db: Async database session.
            workspace_id: The workspace UUID.
            user_id: The user UUID to remove.

        Raises:
            ValueError: If the user is not a member or is the last admin.
        """
        result = await db.execute(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.deleted.is_(False),
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise ValueError("User is not a member of this workspace")

        # Check last admin invariant
        if membership.role == WorkspaceRole.ADMIN:
            admin_count = (
                await db.execute(
                    select(func.count())
                    .select_from(WorkspaceMemberModel)
                    .where(
                        WorkspaceMemberModel.workspace_id == workspace_id,
                        WorkspaceMemberModel.role == WorkspaceRole.ADMIN,
                        WorkspaceMemberModel.deleted.is_(False),
                    )
                )
            ).scalar_one()
            if admin_count <= 1:
                raise ValueError("Cannot remove the last admin from a workspace")

        from datetime import UTC, datetime

        membership.deleted = True
        membership.deleted_at = datetime.now(UTC)
        await db.flush()
        logger.info("Member removed: user %s from workspace %s", user_id, workspace_id)

    async def update_role(
        self,
        db: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        new_role: WorkspaceRole,
    ) -> WorkspaceMemberModel:
        """Update a member's role.

        Args:
            db: Async database session.
            workspace_id: The workspace UUID.
            user_id: The user UUID.
            new_role: The new role to assign.

        Returns:
            The updated WorkspaceMemberModel.

        Raises:
            ValueError: If member not found or demoting the last admin.
        """
        result = await db.execute(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.deleted.is_(False),
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise ValueError("User is not a member of this workspace")

        # Check last admin invariant when demoting admin
        if membership.role == WorkspaceRole.ADMIN and new_role != WorkspaceRole.ADMIN:
            admin_count = (
                await db.execute(
                    select(func.count())
                    .select_from(WorkspaceMemberModel)
                    .where(
                        WorkspaceMemberModel.workspace_id == workspace_id,
                        WorkspaceMemberModel.role == WorkspaceRole.ADMIN,
                        WorkspaceMemberModel.deleted.is_(False),
                    )
                )
            ).scalar_one()
            if admin_count <= 1:
                raise ValueError("Workspace must have at least one admin")

        membership.role = new_role
        await db.flush()
        logger.info("Role updated: user %s in workspace %s → %s", user_id, workspace_id, new_role.value)
        return membership

    async def list_members(
        self,
        db: AsyncSession,
        workspace_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[WorkspaceMemberModel], int]:
        """List members of a workspace."""
        conditions = [
            WorkspaceMemberModel.workspace_id == workspace_id,
            WorkspaceMemberModel.deleted.is_(False),
        ]
        total = (
            await db.execute(select(func.count()).select_from(WorkspaceMemberModel).where(*conditions))
        ).scalar_one()

        offset = (page - 1) * page_size
        result = await db.execute(
            select(WorkspaceMemberModel)
            .where(*conditions)
            .order_by(WorkspaceMemberModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def check_role(
        self,
        db: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> WorkspaceRole | None:
        """Get a user's role in a workspace.

        Returns:
            The WorkspaceRole or None if not a member.
        """
        result = await db.execute(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.deleted.is_(False),
            )
        )
        membership = result.scalar_one_or_none()
        return membership.role if membership else None
