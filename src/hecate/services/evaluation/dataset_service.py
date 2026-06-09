"""Evaluation dataset service — CRUD operations for datasets and items.

Provides async methods for creating, reading, updating, and deleting
evaluation datasets and their items, plus JSON import/export.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import EvaluationDatasetModel, EvaluationItemModel

logger = logging.getLogger(__name__)


class EvaluationDatasetService:
    """Manage evaluation datasets and their items.

    Args:
        db: Async SQLAlchemy session for database operations.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_dataset(
        self,
        name: str,
        description: str | None = None,
        metadata: dict | None = None,
        workspace_id: UUID | None = None,
    ) -> EvaluationDatasetModel:
        """Create a new evaluation dataset.

        Args:
            name: Human-readable dataset name.
            description: Optional longer description.
            metadata: Optional JSON metadata.
            workspace_id: Optional workspace ID for tenant isolation.

        Returns:
            The created dataset model with generated id and timestamps.
        """
        ds = EvaluationDatasetModel(
            name=name,
            description=description,
            metadata_=metadata or {},
            workspace_id=workspace_id or uuid.UUID(int=0),
        )
        self.db.add(ds)
        await self.db.flush()
        await self.db.refresh(ds)
        return ds

    async def get_dataset(
        self,
        dataset_id: UUID,
        workspace_id: UUID | None = None,
    ) -> EvaluationDatasetModel:
        """Retrieve a dataset by ID.

        Args:
            dataset_id: UUID of the dataset.
            workspace_id: Optional workspace ID for tenant isolation.

        Returns:
            The dataset model.

        Raises:
            ValueError: If the dataset is not found or has been deleted.
        """
        conditions = [
            EvaluationDatasetModel.id == dataset_id,
            ~EvaluationDatasetModel.deleted,
        ]
        if workspace_id is not None:
            conditions.append(EvaluationDatasetModel.workspace_id == workspace_id)
        result = await self.db.execute(select(EvaluationDatasetModel).where(*conditions))
        ds = result.scalar_one_or_none()
        if ds is None:
            msg = f"Dataset {dataset_id} not found"
            raise ValueError(msg)
        return ds

    async def list_datasets(
        self,
        page: int = 1,
        page_size: int = 20,
        workspace_id: UUID | None = None,
    ) -> tuple[list[EvaluationDatasetModel], int]:
        """List datasets with pagination.

        Args:
            page: 1-indexed page number.
            page_size: Number of items per page.
            workspace_id: Optional workspace ID for tenant isolation.

        Returns:
            Tuple of (dataset list, total count).
        """
        conditions = [~EvaluationDatasetModel.deleted]
        if workspace_id is not None:
            conditions.append(EvaluationDatasetModel.workspace_id == workspace_id)
        count_stmt = select(func.count()).select_from(EvaluationDatasetModel).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = (
            select(EvaluationDatasetModel)
            .where(*conditions)
            .order_by(EvaluationDatasetModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        items = result.scalars().all()
        return list(items), total

    async def update_dataset(
        self,
        dataset_id: UUID,
        **kwargs: object,
    ) -> EvaluationDatasetModel:
        """Update a dataset's fields.

        Args:
            dataset_id: UUID of the dataset to update.
            **kwargs: Fields to update (name, description, metadata).

        Returns:
            The updated dataset model.

        Raises:
            ValueError: If the dataset is not found.
        """
        ds = await self.get_dataset(dataset_id)
        if "name" in kwargs and kwargs["name"] is not None:
            ds.name = str(kwargs["name"])
        if "description" in kwargs:
            ds.description = str(kwargs["description"]) if kwargs["description"] is not None else None
        if "metadata" in kwargs and kwargs["metadata"] is not None:
            ds.metadata_ = kwargs["metadata"]
        await self.db.flush()
        await self.db.refresh(ds)
        return ds

    async def delete_dataset(self, dataset_id: UUID) -> None:
        """Soft-delete a dataset and all its items.

        Args:
            dataset_id: UUID of the dataset to delete.

        Raises:
            ValueError: If the dataset is not found.
        """
        ds = await self.get_dataset(dataset_id)
        ds.deleted = True
        ds.deleted_at = datetime.now(UTC)

        # Also soft-delete all items in this dataset
        result = await self.db.execute(
            select(EvaluationItemModel).where(
                EvaluationItemModel.dataset_id == dataset_id,
                ~EvaluationItemModel.deleted,
            )
        )
        for item in result.scalars().all():
            item.deleted = True
            item.deleted_at = datetime.now(UTC)

        await self.db.flush()

    async def add_items(
        self,
        dataset_id: UUID,
        items: list[dict],
    ) -> int:
        """Add a batch of items to a dataset.

        Each item dict must contain a non-empty ``query`` key.

        Args:
            dataset_id: UUID of the target dataset.
            items: List of item dicts with query, expected_answer, context, metadata.

        Returns:
            Number of items successfully added.

        Raises:
            ValueError: If the dataset is not found.
        """
        await self.get_dataset(dataset_id)
        count = 0
        for item_data in items:
            query = item_data.get("query", "")
            if not query or not str(query).strip():
                continue
            item = EvaluationItemModel(
                dataset_id=dataset_id,
                query=str(query),
                expected_answer=item_data.get("expected_answer"),
                context=item_data.get("context"),
                metadata_=item_data.get("metadata", {}),
            )
            self.db.add(item)
            count += 1
        await self.db.flush()
        return count

    async def list_items(
        self,
        dataset_id: UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EvaluationItemModel], int]:
        """List items in a dataset with pagination.

        Args:
            dataset_id: UUID of the dataset.
            page: 1-indexed page number.
            page_size: Number of items per page.

        Returns:
            Tuple of (item list, total count).
        """
        base_query = select(EvaluationItemModel).where(
            EvaluationItemModel.dataset_id == dataset_id,
            ~EvaluationItemModel.deleted,
        )
        count_stmt = select(func.count()).select_from(base_query.subquery())
        total = (await self.db.execute(count_stmt)).scalar_one()

        offset = (page - 1) * page_size
        stmt = base_query.order_by(EvaluationItemModel.created_at.desc()).offset(offset).limit(page_size)
        result = await self.db.execute(stmt)
        items = result.scalars().all()
        return list(items), total

    async def delete_item(self, item_id: UUID) -> None:
        """Soft-delete a single item.

        Args:
            item_id: UUID of the item to delete.

        Raises:
            ValueError: If the item is not found.
        """
        result = await self.db.execute(
            select(EvaluationItemModel).where(
                EvaluationItemModel.id == item_id,
                ~EvaluationItemModel.deleted,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            msg = f"Item {item_id} not found"
            raise ValueError(msg)
        item.deleted = True
        item.deleted_at = datetime.now(UTC)
        await self.db.flush()

    async def import_json(
        self,
        dataset_id: UUID,
        json_data: list[dict],
    ) -> dict[str, int]:
        """Import items from a JSON-compatible list of dicts.

        Args:
            dataset_id: UUID of the target dataset.
            json_data: List of dicts with query, expected_answer, context.

        Returns:
            Dict with total, valid, and skipped counts.
        """
        await self.get_dataset(dataset_id)
        total = len(json_data)
        valid = 0
        skipped = 0
        for entry in json_data:
            query = entry.get("query", "")
            if not query or not str(query).strip():
                skipped += 1
                continue
            item = EvaluationItemModel(
                dataset_id=dataset_id,
                query=str(query),
                expected_answer=entry.get("expected_answer"),
                context=entry.get("context"),
                metadata_=entry.get("metadata", {}),
            )
            self.db.add(item)
            valid += 1
        await self.db.flush()
        return {"total": total, "valid": valid, "skipped": skipped}

    async def export_json(self, dataset_id: UUID) -> list[dict]:
        """Export all items in a dataset as a list of dicts.

        Args:
            dataset_id: UUID of the dataset to export.

        Returns:
            List of item dicts with query, expected_answer, context, metadata.

        Raises:
            ValueError: If the dataset is not found.
        """
        await self.get_dataset(dataset_id)
        result = await self.db.execute(
            select(EvaluationItemModel)
            .where(
                EvaluationItemModel.dataset_id == dataset_id,
                ~EvaluationItemModel.deleted,
            )
            .order_by(EvaluationItemModel.created_at.asc())
        )
        items = result.scalars().all()
        return [
            {
                "query": item.query,
                "expected_answer": item.expected_answer,
                "context": item.context,
                "metadata": item.metadata_,
            }
            for item in items
        ]
