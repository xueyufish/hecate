"""Prompt service for managing prompt CRUD and versioning.

Provides business logic for prompt operations:
- Create/Read/Update/Delete prompts
- Version management (list, get, rollback)
- Label-based retrieval for deployment
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.prompt import (
    PromptCreateSchema,
    PromptDetailSchema,
    PromptModel,
    PromptReadSchema,
    PromptUpdateSchema,
    PromptVersionModel,
    PromptVersionReadSchema,
)
from hecate.services.memory.template_engine import TemplateEngine

logger = logging.getLogger(__name__)


class PromptService:
    """Service for prompt CRUD and version management."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize with a database session.

        Args:
            db: Async SQLAlchemy session for database operations.
        """
        self.db = db
        self.template_engine = TemplateEngine()

    async def create_prompt(
        self,
        data: PromptCreateSchema,
        workspace_id: uuid.UUID | None = None,
    ) -> PromptDetailSchema:
        """Create a new prompt with initial version.

        Args:
            data: Prompt creation data.
            workspace_id: Optional workspace ID.

        Returns:
            The created prompt with version details.

        Raises:
            ValueError: If template validation fails.
        """
        if workspace_id is None:
            workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")

        # Validate template
        self.template_engine.validate(data.template)

        # Extract variables if not provided
        variables = data.variables
        if not variables:
            variables = self.template_engine.extract_variables(data.template)

        # Create prompt
        prompt = PromptModel(
            name=data.name,
            workspace_id=workspace_id,
            current_version=1,
        )
        self.db.add(prompt)
        await self.db.flush()

        # Create initial version
        version = PromptVersionModel(
            prompt_id=prompt.id,
            version=1,
            template=data.template,
            variables=variables,
            labels=data.labels,
        )
        self.db.add(version)
        await self.db.flush()

        logger.info(f"Created prompt {prompt.id} with version 1")

        return PromptDetailSchema(
            id=prompt.id,
            workspace_id=prompt.workspace_id,
            name=prompt.name,
            current_version=prompt.current_version,
            created_at=prompt.created_at,
            updated_at=prompt.updated_at,
            deleted_at=prompt.deleted_at,
            version=PromptVersionReadSchema.model_validate(version),
        )

    async def get_prompt(self, prompt_id: uuid.UUID) -> PromptDetailSchema:
        """Get a prompt with its current version.

        Args:
            prompt_id: UUID of the prompt.

        Returns:
            The prompt with current version details.

        Raises:
            ValueError: If prompt not found.
        """
        result = await self.db.execute(
            select(PromptModel).where(
                PromptModel.id == prompt_id,
                PromptModel.deleted_at.is_(None),
            )
        )
        prompt = result.scalar_one_or_none()
        if prompt is None:
            raise ValueError(f"Prompt {prompt_id} not found")

        version = await self._get_version(prompt_id, prompt.current_version)

        return PromptDetailSchema(
            id=prompt.id,
            workspace_id=prompt.workspace_id,
            name=prompt.name,
            current_version=prompt.current_version,
            created_at=prompt.created_at,
            updated_at=prompt.updated_at,
            deleted_at=prompt.deleted_at,
            version=PromptVersionReadSchema.model_validate(version) if version else None,
        )

    async def update_prompt(
        self,
        prompt_id: uuid.UUID,
        data: PromptUpdateSchema,
    ) -> PromptDetailSchema:
        """Update a prompt — create new version if template changed.

        Args:
            prompt_id: UUID of the prompt to update.
            data: Update data.

        Returns:
            The updated prompt.

        Raises:
            ValueError: If prompt not found or template invalid.
        """
        result = await self.db.execute(
            select(PromptModel).where(
                PromptModel.id == prompt_id,
                PromptModel.deleted_at.is_(None),
            )
        )
        prompt = result.scalar_one_or_none()
        if prompt is None:
            raise ValueError(f"Prompt {prompt_id} not found")

        # Create new version if template changed
        if data.template is not None:
            # Validate template
            self.template_engine.validate(data.template)

            # Extract variables if not provided
            variables = data.variables
            if variables is None:
                variables = self.template_engine.extract_variables(data.template)

            # Get current version for labels
            current_version = await self._get_version(prompt_id, prompt.current_version)
            labels = data.labels if data.labels is not None else (current_version.labels if current_version else [])

            new_version_num = prompt.current_version + 1
            version = PromptVersionModel(
                prompt_id=prompt.id,
                version=new_version_num,
                template=data.template,
                variables=variables,
                labels=labels,
            )
            self.db.add(version)
            prompt.current_version = new_version_num

            logger.info(f"Created version {new_version_num} for prompt {prompt_id}")

        await self.db.flush()

        return await self.get_prompt(prompt_id)

    async def delete_prompt(self, prompt_id: uuid.UUID) -> None:
        """Soft delete a prompt.

        Args:
            prompt_id: UUID of the prompt to delete.

        Raises:
            ValueError: If prompt not found.
        """
        result = await self.db.execute(
            select(PromptModel).where(
                PromptModel.id == prompt_id,
                PromptModel.deleted_at.is_(None),
            )
        )
        prompt = result.scalar_one_or_none()
        if prompt is None:
            raise ValueError(f"Prompt {prompt_id} not found")

        prompt.deleted_at = datetime.now(UTC)
        await self.db.flush()
        logger.info(f"Deleted prompt {prompt_id}")

    async def list_prompts(
        self,
        workspace_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List prompts with pagination.

        Args:
            workspace_id: Optional workspace filter.
            page: Page number (1-indexed).
            page_size: Items per page.

        Returns:
            Dict with 'items' and 'total' keys.
        """
        conditions = [PromptModel.deleted_at.is_(None)]
        if workspace_id is not None:
            conditions.append(PromptModel.workspace_id == workspace_id)

        count_stmt = select(func.count()).select_from(PromptModel).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = (
            select(PromptModel)
            .where(*conditions)
            .order_by(PromptModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        prompts = result.scalars().all()

        return {
            "items": [PromptReadSchema.model_validate(p) for p in prompts],
            "total": total,
        }

    async def list_versions(self, prompt_id: uuid.UUID) -> list[PromptVersionReadSchema]:
        """List all versions of a prompt.

        Args:
            prompt_id: UUID of the prompt.

        Returns:
            List of version schemas ordered by version number.
        """
        stmt = (
            select(PromptVersionModel)
            .where(
                PromptVersionModel.prompt_id == prompt_id,
                PromptVersionModel.deleted_at.is_(None),
            )
            .order_by(PromptVersionModel.version.asc())
        )
        result = await self.db.execute(stmt)
        versions = result.scalars().all()

        return [PromptVersionReadSchema.model_validate(v) for v in versions]

    async def get_version(
        self,
        prompt_id: uuid.UUID,
        version: int,
    ) -> PromptVersionReadSchema:
        """Get a specific version of a prompt.

        Args:
            prompt_id: UUID of the prompt.
            version: Version number.

        Returns:
            The version schema.

        Raises:
            ValueError: If version not found.
        """
        v = await self._get_version(prompt_id, version)
        if v is None:
            raise ValueError(f"Version {version} not found for prompt {prompt_id}")

        return PromptVersionReadSchema.model_validate(v)

    async def rollback_to_version(
        self,
        prompt_id: uuid.UUID,
        target_version: int,
    ) -> PromptDetailSchema:
        """Rollback a prompt to a specific version.

        Args:
            prompt_id: UUID of the prompt.
            target_version: Version number to rollback to.

        Returns:
            The updated prompt with new version.

        Raises:
            ValueError: If prompt or target version not found.
        """
        result = await self.db.execute(
            select(PromptModel).where(
                PromptModel.id == prompt_id,
                PromptModel.deleted_at.is_(None),
            )
        )
        prompt = result.scalar_one_or_none()
        if prompt is None:
            raise ValueError(f"Prompt {prompt_id} not found")

        target = await self._get_version(prompt_id, target_version)
        if target is None:
            raise ValueError(f"Version {target_version} not found for prompt {prompt_id}")

        new_version_num = prompt.current_version + 1
        version = PromptVersionModel(
            prompt_id=prompt.id,
            version=new_version_num,
            template=target.template,
            variables=target.variables,
            labels=target.labels,
        )
        self.db.add(version)
        prompt.current_version = new_version_num
        await self.db.flush()

        logger.info(f"Rolled back prompt {prompt_id} to version {target_version}")

        return await self.get_prompt(prompt_id)

    async def get_by_label(self, label: str) -> PromptDetailSchema | None:
        """Get a prompt by deployment label.

        Args:
            label: The deployment label (e.g., "production").

        Returns:
            The prompt with the labeled version, or None if not found.
        """
        # Find the latest version with this label
        stmt = (
            select(PromptVersionModel)
            .where(
                PromptVersionModel.labels.contains([label]),
                PromptVersionModel.deleted_at.is_(None),
            )
            .order_by(PromptVersionModel.created_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        version = result.scalar_one_or_none()

        if version is None:
            return None

        # Get the parent prompt
        prompt_result = await self.db.execute(
            select(PromptModel).where(
                PromptModel.id == version.prompt_id,
                PromptModel.deleted_at.is_(None),
            )
        )
        prompt = prompt_result.scalar_one_or_none()

        if prompt is None:
            return None

        return PromptDetailSchema(
            id=prompt.id,
            workspace_id=prompt.workspace_id,
            name=prompt.name,
            current_version=prompt.current_version,
            created_at=prompt.created_at,
            updated_at=prompt.updated_at,
            deleted_at=prompt.deleted_at,
            version=PromptVersionReadSchema.model_validate(version),
        )

    async def _get_version(
        self,
        prompt_id: uuid.UUID,
        version: int,
    ) -> PromptVersionModel | None:
        """Internal helper to get a specific version."""
        stmt = select(PromptVersionModel).where(
            PromptVersionModel.prompt_id == prompt_id,
            PromptVersionModel.version == version,
            PromptVersionModel.deleted_at.is_(None),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
