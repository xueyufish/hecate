"""Organization service for CRUD operations and ownership management.

Handles creation, listing, updating, soft-deletion, and ownership
transfer for organizations.
"""

from __future__ import annotations

import logging
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.organization import (
    OrganizationModel,
    OrganizationUpdateSchema,
)

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """Convert a name to a kebab-case slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "default"


class OrganizationService:
    """Manages organization lifecycle — create, read, update, delete."""

    async def create(
        self,
        db: AsyncSession,
        *,
        name: str,
        slug: str | None,
        owner_id: uuid.UUID,
        settings: dict | None = None,
    ) -> OrganizationModel:
        """Create a new organization.

        Args:
            db: Async database session.
            name: Organization display name.
            slug: Optional slug (auto-generated from name if omitted).
            owner_id: User ID of the organization owner.
            settings: Optional JSON settings.

        Returns:
            The newly created OrganizationModel.

        Raises:
            ValueError: If the slug is already taken.
        """
        resolved_slug = slug or _slugify(name)

        existing = await db.execute(select(OrganizationModel).where(OrganizationModel.slug == resolved_slug))
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Slug '{resolved_slug}' is already taken")

        org = OrganizationModel(
            name=name,
            slug=resolved_slug,
            owner_id=owner_id,
            settings=settings,
        )
        db.add(org)
        await db.flush()
        logger.info("Organization created: %s (%s)", name, resolved_slug)
        return org

    async def get(self, db: AsyncSession, org_id: uuid.UUID) -> OrganizationModel | None:
        """Get an organization by ID."""
        result = await db.execute(
            select(OrganizationModel).where(
                OrganizationModel.id == org_id,
                OrganizationModel.deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def list_by_owner(
        self,
        db: AsyncSession,
        owner_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[OrganizationModel], int]:
        """List organizations owned by a user."""
        conditions = [
            OrganizationModel.owner_id == owner_id,
            OrganizationModel.deleted.is_(False),
        ]
        total = (await db.execute(select(func.count()).select_from(OrganizationModel).where(*conditions))).scalar_one()

        offset = (page - 1) * page_size
        result = await db.execute(
            select(OrganizationModel)
            .where(*conditions)
            .order_by(OrganizationModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        return list(result.scalars().all()), total

    async def update(
        self,
        db: AsyncSession,
        org: OrganizationModel,
        data: OrganizationUpdateSchema,
    ) -> OrganizationModel:
        """Update an organization's fields."""
        if data.name is not None:
            org.name = data.name
        if data.settings is not None:
            org.settings = data.settings
        await db.flush()
        return org

    async def soft_delete(self, db: AsyncSession, org: OrganizationModel) -> None:
        """Soft-delete an organization."""
        from datetime import UTC, datetime

        org.deleted = True
        org.deleted_at = datetime.now(UTC)
        await db.flush()
        logger.info("Organization soft-deleted: %s", org.slug)

    async def transfer_ownership(
        self,
        db: AsyncSession,
        org: OrganizationModel,
        new_owner_id: uuid.UUID,
    ) -> OrganizationModel:
        """Transfer organization ownership to another user.

        Args:
            db: Async database session.
            org: The organization to transfer.
            new_owner_id: The user ID of the new owner.

        Returns:
            The updated OrganizationModel.

        Raises:
            ValueError: If the new owner is not a member of any workspace in the org.
        """
        from hecate.models.workspace import WorkspaceModel
        from hecate.models.workspace_member import WorkspaceMemberModel

        # Verify new owner is a member of at least one workspace in the org
        result = await db.execute(
            select(WorkspaceMemberModel)
            .join(WorkspaceModel, WorkspaceMemberModel.workspace_id == WorkspaceModel.id)
            .where(
                WorkspaceModel.org_id == org.id,
                WorkspaceMemberModel.user_id == new_owner_id,
                WorkspaceMemberModel.deleted.is_(False),
                WorkspaceModel.deleted.is_(False),
            )
            .limit(1)
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("New owner must be a member of at least one workspace in this organization")

        org.owner_id = new_owner_id
        await db.flush()
        logger.info("Organization ownership transferred: %s → %s", org.slug, new_owner_id)
        return org
