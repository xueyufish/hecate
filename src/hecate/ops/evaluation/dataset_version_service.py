"""Named dataset version service (7.3b).

Versions are named, immutable freezes of a dataset's live item set. The
freeze reuses the run-snapshot serialization (:mod:`hecate.ops.evaluation.snapshot`)
so a version's ``content_hash`` always equals the hash the same items
produce in a run snapshot — the publish gate can therefore compare hashes
across the two without translation.

Lifecycle: ``create`` freezes the current live items; versions are
immutable and soft-deletable; ``checkout`` copies a version's items back
into the live dataset (the live dataset remains the only editable surface);
``diff`` aligns items by id and classifies content changes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationDatasetModel,
    EvaluationDatasetVersionModel,
    EvaluationItemModel,
)
from hecate.ops.evaluation.snapshot import (
    build_snapshot,
    compute_content_hash,
    serialize_snapshot_item,
)

logger = logging.getLogger(__name__)

# Fields the diff classifies on: snapshot content fields plus the exemption
# marker (an exemption change is a meaningful dataset difference even
# though it does not move the content hash — 7.3c).
_DIFF_FIELDS = ("query", "expected_answer", "context", "tags", "metadata", "known_bad")


class DatasetNotFoundError(LookupError):
    """Dataset with the given id does not exist in the workspace."""


def _coerce_datetime(value: object) -> datetime | None:
    """Coerce a snapshot entry's ISO datetime string back to datetime."""
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class DatasetVersionNotFoundError(LookupError):
    """Dataset version with the given id does not exist (or was deleted)."""


class DatasetVersionNameConflictError(ValueError):
    """A version with this name already exists on the dataset.

    Soft-deleted versions keep their names reserved, so the conflict
    check intentionally spans deleted rows too.
    """


def _diff_projection(entry: dict) -> dict:
    """Project a snapshot entry onto the fields the diff classifies on."""
    return {field: entry.get(field) for field in _DIFF_FIELDS if field != "known_bad"} | {
        "known_bad": bool(entry.get("known_bad"))
    }


def _diff_entries(base_entries: list[dict], target_entries: list[dict]) -> dict:
    """Align two frozen entry lists by item id and classify the difference.

    ``added`` — present in target, absent in base; ``removed`` — present
    in base, absent in target; ``changed`` — same id, differing projection,
    carrying per-field ``{"base", "target"}`` deltas.
    """
    base_by_id = {entry["id"]: entry for entry in base_entries}
    target_by_id = {entry["id"]: entry for entry in target_entries}

    added = [target_by_id[item_id] for item_id in target_by_id if item_id not in base_by_id]
    removed = [base_by_id[item_id] for item_id in base_by_id if item_id not in target_by_id]
    changed: list[dict] = []
    for item_id, base_entry in base_by_id.items():
        target_entry = target_by_id.get(item_id)
        if target_entry is None:
            continue
        base_proj = _diff_projection(base_entry)
        target_proj = _diff_projection(target_entry)
        fields = {
            field: {"base": base_proj[field], "target": target_proj[field]}
            for field in _DIFF_FIELDS
            if base_proj[field] != target_proj[field]
        }
        if fields:
            changed.append({"item_id": item_id, "fields": fields})
    changed.sort(key=lambda change: change["item_id"])
    added.sort(key=lambda entry: entry["id"])
    removed.sort(key=lambda entry: entry["id"])
    return {"added": added, "removed": removed, "changed": changed}


class EvaluationDatasetVersionService:
    """Manage named dataset versions: freeze, list, checkout, diff.

    Args:
        db: Async SQLAlchemy session for database operations.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Freeze / CRUD
    # ------------------------------------------------------------------

    async def create_version(
        self,
        dataset_id: UUID,
        name: str,
        description: str | None,
        workspace_id: UUID | None,
        created_by: UUID | None,
    ) -> EvaluationDatasetVersionModel:
        """Freeze the dataset's current live items as a named version.

        The name must be unique within the dataset across soft-deleted
        rows too — a version name always refers to the same frozen
        content, even after deletion.

        Raises:
            DatasetNotFoundError: If the dataset does not exist.
            DatasetVersionNameConflictError: If the name is taken.
        """
        await self._get_dataset(dataset_id, workspace_id)
        existing = await self._find_any_by_name(dataset_id, name)
        if existing is not None:
            msg = f"Version name {name!r} already exists on dataset {dataset_id} (deleted names stay reserved)"
            raise DatasetVersionNameConflictError(msg)

        items, content_hash = await self._freeze_live(dataset_id)
        version = EvaluationDatasetVersionModel(
            dataset_id=dataset_id,
            name=name,
            description=description,
            items=items,
            content_hash=content_hash,
            created_by=created_by,
            workspace_id=workspace_id or uuid.UUID(int=0),
        )
        self.db.add(version)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            # Race between the existence query and the unique constraint
            # (two concurrent creates with the same name). Translate to
            # the same 409 the service uses for non-race conflicts.
            await self.db.rollback()
            raise DatasetVersionNameConflictError(
                f"Version name {name!r} already exists on dataset {dataset_id}"
            ) from exc
        await self.db.refresh(version)
        logger.info("Created dataset version %s (%s) with %d items", version.id, name, len(items))
        return version

    async def list_versions(
        self,
        dataset_id: UUID,
        workspace_id: UUID | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[EvaluationDatasetVersionModel], int]:
        """List the dataset's versions, newest first (deleted excluded)."""
        conditions = [EvaluationDatasetVersionModel.dataset_id == dataset_id]
        if workspace_id is not None:
            conditions.append(EvaluationDatasetVersionModel.workspace_id == workspace_id)
        base_query = select(EvaluationDatasetVersionModel).where(*conditions, ~EvaluationDatasetVersionModel.deleted)
        total = (await self.db.execute(select(func.count()).select_from(base_query.subquery()))).scalar_one()
        stmt = (
            base_query.order_by(EvaluationDatasetVersionModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        versions = list((await self.db.execute(stmt)).scalars().all())
        return versions, int(total or 0)

    async def get_version(
        self,
        version_id: UUID,
        workspace_id: UUID | None = None,
    ) -> EvaluationDatasetVersionModel:
        """Fetch one non-deleted version.

        Raises:
            DatasetVersionNotFoundError: If missing, deleted, or foreign.
        """
        version = await self._find_version(version_id, workspace_id)
        if version is None:
            msg = f"Dataset version {version_id} not found"
            raise DatasetVersionNotFoundError(msg)
        return version

    async def delete_version(self, version_id: UUID, workspace_id: UUID | None = None) -> None:
        """Soft-delete a version; runs that referenced it are unaffected.

        Raises:
            DatasetVersionNotFoundError: If missing, deleted, or foreign.
        """
        version = await self.get_version(version_id, workspace_id)
        version.deleted = True
        version.deleted_at = datetime.now(UTC)
        await self.db.flush()

    # ------------------------------------------------------------------
    # Checkout / diff
    # ------------------------------------------------------------------

    async def checkout(
        self,
        dataset_id: UUID,
        version_id: UUID,
        workspace_id: UUID | None = None,
    ) -> dict:
        """Restore a version's items as the dataset's live items.

        The previous live items are soft-deleted and the version's items
        are re-inserted under new ids (content, tags, metadata, and
        known-bad markers with provenance are carried over). Destructive
        by design — callers freeze the current live state first if they
        need to keep it.

        Returns:
            A diff summary of the transition (counts relative to the
            previous live state).
        """
        await self._get_dataset(dataset_id, workspace_id)
        version = await self.get_version(version_id, workspace_id)
        if version.dataset_id != dataset_id:
            msg = f"Dataset version {version_id} does not belong to dataset {dataset_id}"
            raise DatasetVersionNotFoundError(msg)

        live_items = await self._load_live_items(dataset_id)
        previous_entries = [serialize_snapshot_item(item) for item in live_items]
        version_entries = list(version.items or [])

        # Compute the transition before mutating: version items entering
        # live are "added", live items absent from the version are
        # "removed", same-id content changes are "changed".
        transition = _diff_entries(previous_entries, version_entries)

        now = datetime.now(UTC)
        for item in live_items:
            item.deleted = True
            item.deleted_at = now

        restored = 0
        for entry in version_entries:
            known_bad = bool(entry.get("known_bad"))
            marked_by = entry.get("known_bad_marked_by")
            marked_at = entry.get("known_bad_marked_at")
            self.db.add(
                EvaluationItemModel(
                    dataset_id=dataset_id,
                    query=str(entry.get("query") or ""),
                    expected_answer=entry.get("expected_answer"),
                    context=entry.get("context") or [],
                    tags=[str(t) for t in (entry.get("tags") or []) if t is not None],
                    metadata_=dict(entry.get("metadata") or {}),
                    known_bad=known_bad,
                    known_bad_reason=entry.get("known_bad_reason") if known_bad else None,
                    known_bad_marked_by=UUID(str(marked_by)) if known_bad and marked_by else None,
                    known_bad_marked_at=_coerce_datetime(marked_at) if known_bad else None,
                    workspace_id=version.workspace_id,
                )
            )
            restored += 1

        await self.db.flush()
        logger.info(
            "Checkout version %s onto dataset %s: +%d/-%d/~%d",
            version_id,
            dataset_id,
            len(transition["added"]),
            len(transition["removed"]),
            len(transition["changed"]),
        )
        return {
            "added": len(transition["added"]),
            "removed": len(transition["removed"]),
            "changed": len(transition["changed"]),
            "live_items_after": restored,
        }

    async def diff(
        self,
        dataset_id: UUID,
        version_id: UUID,
        against: UUID | None,
        workspace_id: UUID | None = None,
    ) -> dict:
        """Diff a version against another version (``against``) or the live set.

        ``against=None`` means live. Items align by id; classification uses
        the content projection (content fields plus the known-bad marker).
        """
        await self._get_dataset(dataset_id, workspace_id)
        version = await self.get_version(version_id, workspace_id)
        if version.dataset_id != dataset_id:
            msg = f"Dataset version {version_id} does not belong to dataset {dataset_id}"
            raise DatasetVersionNotFoundError(msg)
        base_entries = list(version.items or [])

        if against is None:
            live_items = await self._load_live_items(dataset_id)
            target_entries = [serialize_snapshot_item(item) for item in live_items]
            target: dict = {
                "kind": "live",
                "content_hash": compute_content_hash(target_entries),
            }
        else:
            other = await self.get_version(against, workspace_id)
            if other.dataset_id != dataset_id:
                msg = f"Dataset version {against} does not belong to dataset {dataset_id}"
                raise DatasetVersionNotFoundError(msg)
            target_entries = list(other.items or [])
            target = {
                "kind": "version",
                "version_id": str(other.id),
                "name": other.name,
                "content_hash": str(other.content_hash),
            }

        result = _diff_entries(base_entries, target_entries)
        return {
            "base": {
                "kind": "version",
                "version_id": str(version.id),
                "name": version.name,
                "content_hash": str(version.content_hash),
            },
            "target": target,
            **result,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _get_dataset(self, dataset_id: UUID, workspace_id: UUID | None) -> EvaluationDatasetModel:
        conditions = [EvaluationDatasetModel.id == dataset_id, ~EvaluationDatasetModel.deleted]
        if workspace_id is not None:
            conditions.append(EvaluationDatasetModel.workspace_id == workspace_id)
        dataset = (await self.db.execute(select(EvaluationDatasetModel).where(*conditions))).scalar_one_or_none()
        if dataset is None:
            msg = f"Dataset {dataset_id} not found"
            raise DatasetNotFoundError(msg)
        return dataset

    async def _find_any_by_name(
        self,
        dataset_id: UUID,
        name: str,
    ) -> EvaluationDatasetVersionModel | None:
        # No deleted filter on purpose: soft-deleted versions keep their
        # names reserved (create rejects the reuse).
        stmt = select(EvaluationDatasetVersionModel).where(
            EvaluationDatasetVersionModel.dataset_id == dataset_id,
            EvaluationDatasetVersionModel.name == name,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def _find_version(
        self,
        version_id: UUID,
        workspace_id: UUID | None,
    ) -> EvaluationDatasetVersionModel | None:
        conditions = [EvaluationDatasetVersionModel.id == version_id, ~EvaluationDatasetVersionModel.deleted]
        if workspace_id is not None:
            conditions.append(EvaluationDatasetVersionModel.workspace_id == workspace_id)
        result = await self.db.execute(select(EvaluationDatasetVersionModel).where(*conditions))
        return result.scalar_one_or_none()

    async def _freeze_live(self, dataset_id: UUID) -> tuple[list[dict], str]:
        items = await self._load_live_items(dataset_id)
        return build_snapshot(items)

    async def _load_live_items(self, dataset_id: UUID) -> list[EvaluationItemModel]:
        stmt = (
            select(EvaluationItemModel)
            .where(
                EvaluationItemModel.dataset_id == dataset_id,
                ~EvaluationItemModel.deleted,
            )
            .order_by(EvaluationItemModel.created_at.asc(), EvaluationItemModel.id.asc())
        )
        return list((await self.db.execute(stmt)).scalars().all())
