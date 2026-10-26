"""Prompt service for managing prompt CRUD and versioning.

Provides business logic for prompt operations:
- Create/Read/Update/Delete prompts
- Version management (list, get, rollback)
- Label-based retrieval for deployment

Every read and write binds to an explicit workspace: the caller's workspace
is a required argument (an explicit zero UUID stays a legal scope), prompt
rows are matched on ``workspace_id``, and a version participates only when
it belongs to the target prompt, shares the prompt's workspace, and is not
soft-deleted. Legacy rows whose version workspace disagrees with the parent
prompt are rejected — never silently copied, rolled back, or listed.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, func, select, update
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
from hecate.studio.template_engine import TemplateEngine

logger = logging.getLogger(__name__)

PROMPT_NOT_FOUND = "Prompt not found"
VERSION_NOT_FOUND = "Prompt or version not found"


class PromptWriteConflictError(Exception):
    """The prompt's version changed while a write was being prepared."""


def _is_admin(user_role: Any) -> bool:
    """Whether the caller's role is the workspace admin role.

    Accepts ``WorkspaceRole`` members (a StrEnum) and plain strings so the
    service stays usable from non-HTTP callers; everything else — including
    ``None`` (no membership, system scope) — is not admin.
    """
    return user_role is not None and str(user_role) == "admin"


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
        workspace_id: uuid.UUID,
        user_role: Any = None,
    ) -> PromptDetailSchema:
        """Create a new prompt with initial version.

        The initial version explicitly inherits the prompt's workspace —
        never the model's zero-UUID default.

        Args:
            data: Prompt creation data.
            workspace_id: Workspace the prompt belongs to.
            user_role: Role of the caller for protected label checks.

        Returns:
            The created prompt with version details.

        Raises:
            ValueError: If template validation fails.
            PermissionError: If a non-admin assigns protected labels.
        """
        self._require_non_protected_labels(data.labels, user_role)

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
            commit_message=data.commit_message,
            workspace_id=prompt.workspace_id,
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

    async def get_prompt(self, prompt_id: uuid.UUID, workspace_id: uuid.UUID) -> PromptDetailSchema:
        """Get a prompt with its current version.

        Args:
            prompt_id: UUID of the prompt.
            workspace_id: Workspace the prompt must belong to.

        Returns:
            The prompt with current version details.

        Raises:
            LookupError: If the prompt is missing, foreign, soft-deleted,
                or its current version is not workspace-consistent.
        """
        prompt = await self._get_prompt(prompt_id, workspace_id)

        version = await self._get_version(prompt_id, prompt.current_version, workspace_id)
        if version is None:
            # A current version that does not resolve inside the workspace
            # is a binding error, not empty content.
            raise LookupError(VERSION_NOT_FOUND)

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

    async def update_prompt(
        self,
        prompt_id: uuid.UUID,
        data: PromptUpdateSchema,
        workspace_id: uuid.UUID,
        user_role: Any = None,
    ) -> PromptDetailSchema:
        """Update a prompt — create new version if template changed.

        The prompt row is re-read inside the caller's transaction (fresh,
        row-locked where the dialect supports it), so a prompt that was
        deleted or moved after the API precheck is refused, not followed.

        Args:
            prompt_id: UUID of the prompt to update.
            data: Update data.
            workspace_id: Workspace the prompt must belong to.
            user_role: Role of the user for protected label checks.

        Returns:
            The updated prompt.

        Raises:
            LookupError: If prompt or its current version is not visible.
            ValueError: If template invalid.
            PermissionError: If a non-admin carries or modifies protected labels.
        """
        prompt = await self._get_prompt(prompt_id, workspace_id, for_update=True)

        if data.labels is not None:
            self._require_non_protected_labels(data.labels, user_role)

        # Create new version if template changed
        if data.template is not None:
            # Validate template
            self.template_engine.validate(data.template)

            # Extract variables if not provided
            variables = data.variables
            if variables is None:
                variables = self.template_engine.extract_variables(data.template)

            # Get current version for labels and check protected labels
            current_version = await self._get_version(prompt_id, prompt.current_version, workspace_id)
            if current_version is None:
                raise LookupError(VERSION_NOT_FOUND)
            old_labels = current_version.labels or []
            new_labels = data.labels if data.labels is not None else old_labels
            self._check_protected_labels(old_labels, new_labels, user_role)
            # Carrying a protected label onto a new version (via omission,
            # explicit request, or copy) is an admin-only action.
            self._require_non_protected_labels(new_labels, user_role)

            new_version_num = await self._advance_version(prompt, current_version, workspace_id)
            version = PromptVersionModel(
                prompt_id=prompt.id,
                version=new_version_num,
                template=data.template,
                variables=variables,
                labels=new_labels,
                commit_message=data.commit_message,
                workspace_id=prompt.workspace_id,
            )
            self.db.add(version)

            logger.info(f"Created version {new_version_num} for prompt {prompt_id}")

        await self.db.flush()

        return await self.get_prompt(prompt_id, workspace_id)

    async def delete_prompt(self, prompt_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
        """Soft delete a prompt.

        Args:
            prompt_id: UUID of the prompt to delete.
            workspace_id: Workspace the prompt must belong to.

        Raises:
            LookupError: If prompt not found.
        """
        prompt = await self._get_prompt(prompt_id, workspace_id, for_update=True)

        deleted = (
            await self.db.execute(
                update(PromptModel)
                .where(PromptModel.id == prompt.id, PromptModel.workspace_id == workspace_id, ~PromptModel.deleted)
                .values(deleted=True, deleted_at=datetime.now(UTC))
                .returning(PromptModel.id)
                .execution_options(synchronize_session="fetch")
            )
        ).scalar_one_or_none()
        if deleted is None:
            raise LookupError(PROMPT_NOT_FOUND)
        logger.info(f"Deleted prompt {prompt_id}")

    async def list_prompts(
        self,
        workspace_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List prompts of one workspace with pagination.

        Args:
            workspace_id: Workspace whose prompts are listed.
            page: Page number (1-indexed).
            page_size: Items per page.

        Returns:
            Dict with 'items' and 'total' keys.
        """
        conditions = [~PromptModel.deleted, PromptModel.workspace_id == workspace_id]

        count_stmt = select(func.count()).select_from(PromptModel).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = (
            select(PromptModel)
            .where(*conditions)
            .order_by(PromptModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        prompts = result.scalars().all()

        return {
            "items": [PromptReadSchema.model_validate(p) for p in prompts],
            "total": total,
        }

    async def list_versions(
        self,
        prompt_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> list[PromptVersionReadSchema]:
        """List workspace-consistent versions of a prompt.

        Legacy versions whose workspace disagrees with the parent prompt
        are excluded rather than surfaced.

        Args:
            prompt_id: UUID of the prompt.
            workspace_id: Workspace the prompt must belong to.

        Returns:
            List of version schemas ordered by version number.

        Raises:
            LookupError: If prompt not found in the workspace.
        """
        await self._get_prompt(prompt_id, workspace_id)

        stmt = (
            select(PromptVersionModel)
            .join(PromptModel, PromptModel.id == PromptVersionModel.prompt_id)
            .where(
                PromptVersionModel.prompt_id == prompt_id,
                PromptVersionModel.workspace_id == workspace_id,
                ~PromptVersionModel.deleted,
                PromptModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
            )
            .order_by(PromptVersionModel.version.asc())
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        versions = result.scalars().all()
        if not versions and not await self.is_prompt_visible(prompt_id, workspace_id):
            raise LookupError(PROMPT_NOT_FOUND)

        return [PromptVersionReadSchema.model_validate(v) for v in versions]

    async def get_version(
        self,
        prompt_id: uuid.UUID,
        version: int,
        workspace_id: uuid.UUID,
    ) -> PromptVersionReadSchema:
        """Get a specific version of a prompt.

        Args:
            prompt_id: UUID of the prompt.
            version: Version number.
            workspace_id: Workspace the prompt must belong to.

        Returns:
            The version schema.

        Raises:
            LookupError: If the version is not visible in the workspace.
        """
        v = await self._get_version(prompt_id, version, workspace_id)
        if v is None:
            raise LookupError(VERSION_NOT_FOUND)

        return PromptVersionReadSchema.model_validate(v)

    async def rollback_to_version(
        self,
        prompt_id: uuid.UUID,
        target_version: int,
        workspace_id: uuid.UUID,
        user_role: Any = None,
    ) -> PromptDetailSchema:
        """Rollback a prompt to a specific version (copy semantics).

        The prompt and the target version are re-read inside the caller's
        transaction; the new version is a copy of the target content and
        explicitly inherits the prompt's workspace.

        Args:
            prompt_id: UUID of the prompt.
            target_version: Version number to rollback to.
            workspace_id: Workspace the prompt must belong to.
            user_role: Role of the caller for protected label checks.

        Returns:
            The updated prompt with new version.

        Raises:
            LookupError: If prompt or target version not visible.
            PermissionError: If a non-admin copies protected labels forward.
        """
        prompt = await self._get_prompt(prompt_id, workspace_id, for_update=True)

        target = await self._get_version(prompt_id, target_version, workspace_id)
        if target is None:
            raise LookupError(VERSION_NOT_FOUND)
        # Copying a protected label onto the new version is admin-only.
        self._require_non_protected_labels(target.labels or [], user_role)

        new_version_num = await self._advance_version(prompt, target, workspace_id)
        version = PromptVersionModel(
            prompt_id=prompt.id,
            version=new_version_num,
            template=target.template,
            variables=target.variables,
            labels=target.labels,
            commit_message=f"Rollback to version {target_version}",
            workspace_id=prompt.workspace_id,
        )
        self.db.add(version)
        await self.db.flush()

        logger.info(f"Rolled back prompt {prompt_id} to version {target_version}")

        return await self.get_prompt(prompt_id, workspace_id)

    async def get_by_label(
        self,
        label: str,
        workspace_id: uuid.UUID,
    ) -> PromptDetailSchema | None:
        """Get a prompt by deployment label within one workspace.

        Parent/child workspace consistency is enforced before ordering, so
        a newer foreign-workspace version with the same label can never
        shadow the caller's own, and versions of deleted prompts are not
        reachable through labels.

        Args:
            label: The deployment label (e.g., "production").
            workspace_id: Workspace to search in.

        Returns:
            The prompt with the labeled version, or None if not found.
        """
        # Find the latest workspace-consistent version with this label
        stmt = (
            select(PromptVersionModel)
            .join(PromptModel, PromptModel.id == PromptVersionModel.prompt_id)
            .where(
                self._label_matches(label),
                ~PromptVersionModel.deleted,
                PromptVersionModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
                PromptModel.workspace_id == workspace_id,
            )
            # Stable tiebreaks: identical created_at stamps must not make
            # the winner depend on physical row order.
            .order_by(
                PromptVersionModel.created_at.desc(),
                PromptVersionModel.prompt_id.desc(),
                PromptVersionModel.version.desc(),
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        version = result.scalar_one_or_none()

        if version is None:
            return None

        prompt_result = await self.db.execute(
            select(PromptModel)
            .where(
                PromptModel.id == version.prompt_id,
                PromptModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
            )
            .execution_options(populate_existing=True)
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

    # --- access probes (minimal, identifier-free) -------------------------

    async def is_prompt_visible(
        self, prompt_id: uuid.UUID, workspace_id: uuid.UUID, *, require_current_version: bool = False
    ) -> bool:
        """Whether a live prompt exists in the workspace (id-only probe)."""
        stmt = select(PromptModel.id).where(
            PromptModel.id == prompt_id,
            PromptModel.workspace_id == workspace_id,
            ~PromptModel.deleted,
        )
        if require_current_version:
            stmt = stmt.where(
                select(PromptVersionModel.id)
                .where(
                    PromptVersionModel.prompt_id == PromptModel.id,
                    PromptVersionModel.version == PromptModel.current_version,
                    PromptVersionModel.workspace_id == workspace_id,
                    ~PromptVersionModel.deleted,
                )
                .exists()
            )
        return (await self.db.execute(stmt)).scalar_one_or_none() is not None

    async def are_versions_visible(
        self,
        prompt_id: uuid.UUID,
        workspace_id: uuid.UUID,
        versions: Sequence[int],
    ) -> bool:
        """Whether every requested version is workspace-consistent.

        A version counts as visible only when its parent prompt is live in
        the workspace, the version row is prompt-owned, not soft-deleted,
        and shares the parent's workspace.
        """
        wanted = set(versions)
        if not wanted:
            return await self.is_prompt_visible(prompt_id, workspace_id)
        stmt = (
            select(func.count(func.distinct(PromptVersionModel.version)))
            .join(PromptModel, PromptModel.id == PromptVersionModel.prompt_id)
            .where(
                PromptVersionModel.prompt_id == prompt_id,
                PromptVersionModel.version.in_(wanted),
                PromptVersionModel.workspace_id == workspace_id,
                ~PromptVersionModel.deleted,
                PromptModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
            )
        )
        count = (await self.db.execute(stmt)).scalar_one()
        return count == len(wanted)

    # --- internal helpers --------------------------------------------------

    def _label_matches(self, label: str):
        """Exact JSON array membership on SQLite and PostgreSQL."""
        if self.db.get_bind().dialect.name == "sqlite":
            labels = func.json_each(PromptVersionModel.labels).table_valued("key", "value", "type")
            return select(labels.c.value).where(labels.c.type == "text", labels.c.value == label).exists()
        labels = func.json_array_elements_text(PromptVersionModel.labels.cast(JSON)).table_valued("value")
        return select(labels.c.value).where(labels.c.value == label).exists()

    async def _advance_version(self, prompt: PromptModel, source: PromptVersionModel, workspace_id: uuid.UUID) -> int:
        # A locking read alone does not protect SQLite. Claim the next
        # version with the ownership, source binding and old pointer in
        # the actual UPDATE, before inserting any new version content.
        current = prompt.current_version
        source_live = (
            select(PromptVersionModel.id)
            .where(
                PromptVersionModel.id == source.id,
                PromptVersionModel.prompt_id == prompt.id,
                PromptVersionModel.version == source.version,
                PromptVersionModel.workspace_id == workspace_id,
                ~PromptVersionModel.deleted,
            )
            .exists()
        )
        claimed = (
            await self.db.execute(
                update(PromptModel)
                .where(
                    PromptModel.id == prompt.id,
                    PromptModel.workspace_id == workspace_id,
                    ~PromptModel.deleted,
                    PromptModel.current_version == current,
                    source_live,
                )
                .values(current_version=current + 1)
                .returning(PromptModel.id)
                .execution_options(synchronize_session="fetch")
            )
        ).scalar_one_or_none()
        if claimed is None:
            if not await self.are_versions_visible(prompt.id, workspace_id, [source.version]):
                raise LookupError(VERSION_NOT_FOUND)
            raise PromptWriteConflictError("Prompt changed during write; refresh and retry")
        return current + 1

    async def _get_prompt(
        self,
        prompt_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        for_update: bool = False,
    ) -> PromptModel:
        """Fetch a live workspace-bound prompt or raise LookupError.

        ``populate_existing`` refreshes any identity-map entry loaded by an
        earlier precheck, so post-precheck moves or deletions are seen.
        """
        stmt = (
            select(PromptModel)
            .where(
                PromptModel.id == prompt_id,
                PromptModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update(of=PromptModel)
        prompt = (await self.db.execute(stmt)).scalar_one_or_none()
        if prompt is None:
            raise LookupError(PROMPT_NOT_FOUND)
        return prompt

    async def _get_version(
        self,
        prompt_id: uuid.UUID,
        version: int,
        workspace_id: uuid.UUID,
    ) -> PromptVersionModel | None:
        """Fetch a workspace-consistent version or None.

        The join on the parent prompt makes a version invisible when the
        prompt is missing, foreign, or soft-deleted — the version row alone
        never authorizes anything.
        """
        stmt = (
            select(PromptVersionModel)
            .join(PromptModel, PromptModel.id == PromptVersionModel.prompt_id)
            .where(
                PromptVersionModel.prompt_id == prompt_id,
                PromptVersionModel.version == version,
                PromptVersionModel.workspace_id == workspace_id,
                ~PromptVersionModel.deleted,
                PromptModel.workspace_id == workspace_id,
                ~PromptModel.deleted,
            )
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _require_non_protected_labels(labels: Sequence[str], user_role: Any) -> None:
        """Refuse protected labels on any new version unless admin."""
        from hecate.core.config import settings

        protected = set(settings.PROTECTED_PROMPT_LABELS) & set(labels or [])
        if protected and not _is_admin(user_role):
            msg = f"Cannot assign protected labels {protected}: requires admin role"
            raise PermissionError(msg)

    @staticmethod
    def _check_protected_labels(
        old_labels: Sequence[str],
        new_labels: Sequence[str],
        user_role: Any,
    ) -> None:
        """Check if protected labels are being modified by a non-admin user.

        Args:
            old_labels: Previous version labels.
            new_labels: New version labels.
            user_role: Role of the user making the change.

        Raises:
            PermissionError: If a non-admin user modifies protected labels.
        """
        from hecate.core.config import settings

        protected = set(settings.PROTECTED_PROMPT_LABELS)
        old_set = set(old_labels or [])
        new_set = set(new_labels or [])
        added = new_set - old_set
        removed = old_set - new_set
        changed_protected = (added | removed) & protected
        if changed_protected and not _is_admin(user_role):
            msg = f"Cannot modify protected labels {changed_protected}: requires admin role"
            raise PermissionError(msg)
