"""Workflow service for managing workflow CRUD and versioning.

Provides business logic for workflow operations:
- Create/Read/Update/Delete workflows
- Version management (list, get, rollback)
- Graph DSL validation via GraphCompiler
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.compiler import GraphCompiler
from hecate.engine.graph_dsl import GraphValidationError, parse_graph
from hecate.models.workflow import (
    WorkflowCreateSchema,
    WorkflowDetailSchema,
    WorkflowModel,
    WorkflowReadSchema,
    WorkflowUpdateSchema,
    WorkflowVersionModel,
    WorkflowVersionReadSchema,
)

logger = logging.getLogger(__name__)


class WorkflowService:
    """Service for workflow CRUD and version management."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize with a database session.

        Args:
            db: Async SQLAlchemy session for database operations.
        """
        self.db = db
        self.compiler = GraphCompiler()

    async def create_workflow(
        self,
        data: WorkflowCreateSchema,
        workspace_id: uuid.UUID | None = None,
    ) -> WorkflowDetailSchema:
        """Create a new workflow with initial version.

        Args:
            data: Workflow creation data.
            workspace_id: Optional workspace ID (defaults to zero UUID).

        Returns:
            The created workflow with version details.

        Raises:
            GraphValidationError: If the graph DSL is invalid.
        """
        if workspace_id is None:
            workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")

        # Validate and compile the graph DSL
        graph_config = parse_graph(data.graph_dsl)
        compiled = self.compiler.compile(graph_config, execution_mode=data.execution_mode)

        # Create workflow
        workflow = WorkflowModel(
            name=data.name,
            workspace_id=workspace_id,
            current_version=1,
            execution_mode=data.execution_mode,
        )
        self.db.add(workflow)
        await self.db.flush()

        # Create initial version
        version = WorkflowVersionModel(
            workflow_id=workflow.id,
            version=1,
            graph_dsl=data.graph_dsl,
            compiled_graph=compiled.to_json(),
            change_summary=data.change_summary or "Initial version",
        )
        self.db.add(version)
        await self.db.flush()

        logger.info(f"Created workflow {workflow.id} with version 1")

        return WorkflowDetailSchema(
            id=workflow.id,
            workspace_id=workflow.workspace_id,
            name=workflow.name,
            current_version=workflow.current_version,
            execution_mode=workflow.execution_mode,
            published_version=workflow.published_version,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            deleted_at=workflow.deleted_at,
            version=WorkflowVersionReadSchema.model_validate(version),
        )

    async def get_workflow(self, workflow_id: uuid.UUID) -> WorkflowDetailSchema:
        """Get a workflow with its current version.

        Args:
            workflow_id: UUID of the workflow.

        Returns:
            The workflow with current version details.

        Raises:
            ValueError: If workflow not found.
        """
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Get current version
        version = await self._get_version(workflow_id, workflow.current_version)

        return WorkflowDetailSchema(
            id=workflow.id,
            workspace_id=workflow.workspace_id,
            name=workflow.name,
            current_version=workflow.current_version,
            execution_mode=workflow.execution_mode,
            published_version=workflow.published_version,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            deleted_at=workflow.deleted_at,
            version=WorkflowVersionReadSchema.model_validate(version) if version else None,
        )

    async def update_workflow(
        self,
        workflow_id: uuid.UUID,
        data: WorkflowUpdateSchema,
    ) -> WorkflowDetailSchema:
        """Update a workflow — name change and/or new version with updated DSL.

        Args:
            workflow_id: UUID of the workflow to update.
            data: Update data.

        Returns:
            The updated workflow.

        Raises:
            ValueError: If workflow not found.
            GraphValidationError: If new graph DSL is invalid.
        """
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Update name if provided
        if data.name is not None:
            workflow.name = data.name

        # Update execution_mode if provided
        if data.execution_mode is not None and data.execution_mode != workflow.execution_mode:
            workflow.execution_mode = data.execution_mode
            if data.graph_dsl is None:
                current_version = await self._get_version(workflow_id, workflow.current_version)
                if current_version is not None:
                    graph_config = parse_graph(current_version.graph_dsl)
                    self.compiler.compile(graph_config, execution_mode=workflow.execution_mode)

        # Create new version if graph_dsl provided
        if data.graph_dsl is not None:
            graph_config = parse_graph(data.graph_dsl)
            compiled = self.compiler.compile(graph_config, execution_mode=workflow.execution_mode)

            new_version_num = workflow.current_version + 1
            version = WorkflowVersionModel(
                workflow_id=workflow.id,
                version=new_version_num,
                graph_dsl=data.graph_dsl,
                compiled_graph=compiled.to_json(),
                change_summary=data.change_summary or "",
            )
            self.db.add(version)
            workflow.current_version = new_version_num

            logger.info(f"Created version {new_version_num} for workflow {workflow_id}")

        await self.db.flush()

        return await self.get_workflow(workflow_id)

    async def delete_workflow(self, workflow_id: uuid.UUID) -> None:
        """Soft delete a workflow.

        Args:
            workflow_id: UUID of the workflow to delete.

        Raises:
            ValueError: If workflow not found.
        """
        from datetime import UTC, datetime

        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow.deleted = True
        workflow.deleted_at = datetime.now(UTC)
        await self.db.flush()
        logger.info(f"Deleted workflow {workflow_id}")

    async def list_workflows(
        self,
        workspace_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List workflows with pagination.

        Args:
            workspace_id: Optional workspace filter.
            page: Page number (1-indexed).
            page_size: Items per page.

        Returns:
            Dict with 'items' and 'total' keys.
        """
        conditions = [~WorkflowModel.deleted]
        if workspace_id is not None:
            conditions.append(WorkflowModel.workspace_id == workspace_id)

        # Count total
        count_stmt = select(func.count()).select_from(WorkflowModel).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar_one()

        # Fetch page
        offset = (page - 1) * page_size
        stmt = (
            select(WorkflowModel)
            .where(*conditions)
            .order_by(WorkflowModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        workflows = result.scalars().all()

        return {
            "items": [WorkflowReadSchema.model_validate(w) for w in workflows],
            "total": total,
        }

    async def list_versions(self, workflow_id: uuid.UUID) -> list[WorkflowVersionReadSchema]:
        """List all versions of a workflow.

        Args:
            workflow_id: UUID of the workflow.

        Returns:
            List of version schemas ordered by version number.
        """
        stmt = (
            select(WorkflowVersionModel)
            .where(
                WorkflowVersionModel.workflow_id == workflow_id,
                ~WorkflowVersionModel.deleted,
            )
            .order_by(WorkflowVersionModel.version.asc())
        )
        result = await self.db.execute(stmt)
        versions = result.scalars().all()

        return [WorkflowVersionReadSchema.model_validate(v) for v in versions]

    async def get_version(
        self,
        workflow_id: uuid.UUID,
        version: int,
    ) -> WorkflowVersionReadSchema:
        """Get a specific version of a workflow.

        Args:
            workflow_id: UUID of the workflow.
            version: Version number.

        Returns:
            The version schema.

        Raises:
            ValueError: If version not found.
        """
        v = await self._get_version(workflow_id, version)
        if v is None:
            raise ValueError(f"Version {version} not found for workflow {workflow_id}")

        return WorkflowVersionReadSchema.model_validate(v)

    async def rollback_to_version(
        self,
        workflow_id: uuid.UUID,
        target_version: int,
    ) -> WorkflowDetailSchema:
        """Rollback a workflow to a specific version.

        Creates a new version with the target version's graph DSL.

        Args:
            workflow_id: UUID of the workflow.
            target_version: Version number to rollback to.

        Returns:
            The updated workflow with new version.

        Raises:
            ValueError: If workflow or target version not found.
        """
        # Get workflow
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Get target version
        target = await self._get_version(workflow_id, target_version)
        if target is None:
            raise ValueError(f"Version {target_version} not found for workflow {workflow_id}")

        # Create new version with target's graph DSL
        new_version_num = workflow.current_version + 1
        version = WorkflowVersionModel(
            workflow_id=workflow.id,
            version=new_version_num,
            graph_dsl=target.graph_dsl,
            compiled_graph=target.compiled_graph,
            change_summary=f"Rollback to version {target_version}",
        )
        self.db.add(version)
        workflow.current_version = new_version_num
        await self.db.flush()

        logger.info(f"Rolled back workflow {workflow_id} to version {target_version} (new version {new_version_num})")

        return await self.get_workflow(workflow_id)

    async def validate_dsl(
        self,
        graph_dsl: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate a graph DSL without persisting (dry-run compile).

        Args:
            graph_dsl: The graph DSL definition to validate.

        Returns:
            Dict with ``valid`` (bool) and optional ``errors`` (list[str]).
        """
        try:
            graph_config = parse_graph(graph_dsl)
            self.compiler.compile(graph_config)
            return {"valid": True, "errors": []}
        except GraphValidationError as e:
            return {"valid": False, "errors": [str(e)]}

    async def publish_version(
        self,
        workflow_id: uuid.UUID,
        version: int,
    ) -> WorkflowDetailSchema:
        """Publish a specific version to production.

        Sets the published_version pointer and manages the "production" label.

        Args:
            workflow_id: UUID of the workflow.
            version: Version number to publish.

        Returns:
            The updated workflow.

        Raises:
            ValueError: If workflow or version not found.
        """
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        target = await self._get_version(workflow_id, version)
        if target is None:
            raise ValueError(f"Version {version} not found for workflow {workflow_id}")

        all_versions = await self.list_versions(workflow_id)
        for v in all_versions:
            if "production" in v.labels and v.version != version:
                ver_model = await self._get_version(workflow_id, v.version)
                if ver_model is not None:
                    ver_model.labels = [lbl for lbl in ver_model.labels if lbl != "production"]

        if "production" not in (target.labels or []):
            target.labels = list(target.labels or []) + ["production"]

        workflow.published_version = version
        await self.db.flush()

        logger.info(f"Published version {version} for workflow {workflow_id}")

        return await self.get_workflow(workflow_id)

    async def get_version_by_label(
        self,
        workflow_id: uuid.UUID,
        label: str,
    ) -> WorkflowVersionReadSchema | None:
        """Find a version by label.

        Args:
            workflow_id: UUID of the workflow.
            label: Label to search for.

        Returns:
            The matching version or None.
        """
        versions = await self.list_versions(workflow_id)
        for v in versions:
            if label in (v.labels or []):
                return v
        return None

    async def get_published_version(
        self,
        workflow_id: uuid.UUID,
    ) -> WorkflowVersionReadSchema:
        """Get the currently published version.

        Args:
            workflow_id: UUID of the workflow.

        Returns:
            The published version schema.

        Raises:
            ValueError: If workflow not found or no published version.
        """
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")
        if workflow.published_version is None:
            raise ValueError(f"Workflow {workflow_id} has no published version")

        return await self.get_version(workflow_id, workflow.published_version)

    async def diff_versions(
        self,
        workflow_id: uuid.UUID,
        v1: int,
        v2: int,
    ) -> dict[str, Any]:
        """Compare two versions' graph DSL.

        Args:
            workflow_id: UUID of the workflow.
            v1: First version number.
            v2: Second version number.

        Returns:
            Dict with diff summary and details.

        Raises:
            ValueError: If either version not found.
        """
        ver1 = await self._get_version(workflow_id, v1)
        if ver1 is None:
            raise ValueError(f"Version {v1} not found for workflow {workflow_id}")
        ver2 = await self._get_version(workflow_id, v2)
        if ver2 is None:
            raise ValueError(f"Version {v2} not found for workflow {workflow_id}")

        try:
            from deepdiff import DeepDiff

            diff = DeepDiff(ver1.graph_dsl, ver2.graph_dsl, ignore_order=True)
            import json

            return {
                "v1": v1,
                "v2": v2,
                "identical": not bool(diff),
                "summary": {
                    "values_changed": len(diff.get("values_changed", {})),
                    "dictionary_item_added": len(diff.get("dictionary_item_added", {})),
                    "dictionary_item_removed": len(diff.get("dictionary_item_removed", {})),
                    "type_changes": len(diff.get("type_changes", {})),
                },
                "details": json.loads(diff.to_json()),
            }
        except ImportError:
            return {
                "v1": v1,
                "v2": v2,
                "identical": ver1.graph_dsl == ver2.graph_dsl,
                "summary": {"error": "deepdiff not installed"},
                "details": {},
            }

    async def _get_version(
        self,
        workflow_id: uuid.UUID,
        version: int,
    ) -> WorkflowVersionModel | None:
        """Internal helper to get a specific version.

        Args:
            workflow_id: UUID of the workflow.
            version: Version number.

        Returns:
            The WorkflowVersionModel or None if not found.
        """
        stmt = select(WorkflowVersionModel).where(
            WorkflowVersionModel.workflow_id == workflow_id,
            WorkflowVersionModel.version == version,
            ~WorkflowVersionModel.deleted,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
