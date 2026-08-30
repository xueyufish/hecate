"""Workspace service for CRUD operations and resource isolation.

Handles creation, listing, updating, and soft-deletion of workspaces
within organizations. Automatically adds the creator as workspace admin.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.workspace import (
    WorkspaceModel,
    WorkspaceUpdateSchema,
)
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Convert a name to a kebab-case slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "default"


class WorkspaceService:
    """Manages workspace lifecycle — create, read, update, delete."""

    async def create(
        self,
        db: AsyncSession,
        *,
        org_id: uuid.UUID,
        name: str,
        slug: str | None,
        creator_id: uuid.UUID,
        settings: dict | None = None,
    ) -> WorkspaceModel:
        """Create a new workspace in an organization.

        The creator is automatically added as a workspace admin.

        Args:
            db: Async database session.
            org_id: Parent organization UUID.
            name: Workspace display name.
            slug: Optional slug (auto-generated from name if omitted).
            creator_id: User ID of the workspace creator.
            settings: Optional JSON settings.

        Returns:
            The newly created WorkspaceModel.

        Raises:
            ValueError: If the slug is already taken within the org.
        """
        resolved_slug = slug or _slugify(name)

        existing = await db.execute(
            select(WorkspaceModel).where(
                WorkspaceModel.org_id == org_id,
                WorkspaceModel.slug == resolved_slug,
                WorkspaceModel.deleted.is_(False),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Slug '{resolved_slug}' is already taken in this organization")

        workspace = WorkspaceModel(
            org_id=org_id,
            name=name,
            slug=resolved_slug,
            settings=settings,
        )
        db.add(workspace)
        await db.flush()

        # Auto-add creator as admin
        membership = WorkspaceMemberModel(
            user_id=creator_id,
            workspace_id=workspace.id,
            role=WorkspaceRole.ADMIN,
        )
        db.add(membership)
        await db.flush()

        logger.info("Workspace created: %s (%s) in org %s", name, resolved_slug, org_id)
        return workspace

    async def get(self, db: AsyncSession, workspace_id: uuid.UUID) -> WorkspaceModel | None:
        """Get a workspace by ID."""
        result = await db.execute(
            select(WorkspaceModel).where(
                WorkspaceModel.id == workspace_id,
                WorkspaceModel.deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def list_by_org_and_member(
        self,
        db: AsyncSession,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[WorkspaceModel], int]:
        """List workspaces in an org where the user is a member."""
        conditions = [
            WorkspaceModel.org_id == org_id,
            WorkspaceModel.deleted.is_(False),
            WorkspaceModel.id.in_(
                select(WorkspaceMemberModel.workspace_id).where(
                    WorkspaceMemberModel.user_id == user_id,
                    WorkspaceMemberModel.deleted.is_(False),
                )
            ),
        ]
        total = (await db.execute(select(func.count()).select_from(WorkspaceModel).where(*conditions))).scalar_one()

        offset = (page - 1) * page_size
        result = await db.execute(
            select(WorkspaceModel)
            .where(*conditions)
            .order_by(WorkspaceModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update(
        self,
        db: AsyncSession,
        workspace: WorkspaceModel,
        data: WorkspaceUpdateSchema,
    ) -> WorkspaceModel:
        """Update a workspace's fields."""
        if data.name is not None:
            workspace.name = data.name
        if data.settings is not None:
            workspace.settings = data.settings
        await db.flush()
        return workspace

    async def soft_delete(self, db: AsyncSession, workspace: WorkspaceModel) -> None:
        """Soft-delete a workspace if it has no active resources.

        Raises:
            ValueError: If the workspace still contains active resources.
        """
        from hecate.models.agent import AgentModel

        resource_types: list[str] = []

        # Check each resource type for active entries
        agent_count = (
            await db.execute(
                select(func.count())
                .select_from(AgentModel)
                .where(
                    AgentModel.workspace_id == workspace.id,
                    AgentModel.deleted.is_(False),
                )
            )
        ).scalar_one()
        if agent_count > 0:
            resource_types.append(f"agents ({agent_count})")

        if resource_types:
            raise ValueError(
                f"Workspace has active resources: {', '.join(resource_types)}. Delete all resources first."
            )

        from datetime import UTC, datetime

        workspace.deleted = True
        workspace.deleted_at = datetime.now(UTC)
        await db.flush()
        logger.info("Workspace soft-deleted: %s", workspace.slug)
