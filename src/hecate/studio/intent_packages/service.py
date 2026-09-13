"""Intent package service (6.49).

Lifecycle for governed intent-package assets:

- **Draft CRUD** — packages, categories, samples; the draft is the only
  editable surface.
- **Bulk import/export** — CSV and JSON, all-or-nothing with row-level
  error reports.
- **Versions** — named immutable freezes of the draft (via
  :mod:`hecate.studio.intent_packages.snapshot`); unique names across
  soft-deleted rows so a name always refers to the same frozen content.
- **Publish** — deterministic gate evaluation
  (:mod:`hecate.studio.intent_packages.publish_gate`); ``require`` mode
  blocks with the verdict, ``force`` publishes with a bypass audit entry.
- **Corrections** — misroute feedback appends draft samples with
  provenance; published versions are never touched.

Cross-workspace access denies with "not found" — the workspace filter is
part of every lookup, so foreign packages are indistinguishable from
missing ones.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.intent_package import (
    IntentCategoryCreateSchema,
    IntentCategoryUpdateSchema,
    IntentCorrectionCreateSchema,
    IntentImportReportSchema,
    IntentPackageCategoryModel,
    IntentPackageModel,
    IntentPackageSampleModel,
    IntentPackageVersionCreateSchema,
    IntentPackageVersionModel,
    SampleSourceType,
)
from hecate.studio.intent_packages.publish_gate import (
    PackageGateResult,
    evaluate_gate,
    resolve_gate_config,
)
from hecate.studio.intent_packages.snapshot import build_package_content

logger = logging.getLogger(__name__)

# CSV interchange schema: one row per sample; category-level fields repeat.
CSV_HEADER = ("category", "category_description", "domain", "policy_gated", "utterance")

ZERO_UUID = uuid.UUID(int=0)


def _definition(entry: dict) -> dict:
    """The definition fields of an import entry (everything but samples)."""
    return {key: entry.get(key) for key in ("name", "description", "domain", "policy_gated")}


class PackageNotFoundError(LookupError):
    """Package with the given id does not exist in the workspace."""


class CategoryNotFoundError(LookupError):
    """Category with the given id does not exist in the package."""


class PackageNameConflictError(ValueError):
    """A non-deleted package with this name already exists in the workspace."""


class CategoryNameConflictError(ValueError):
    """A non-deleted category with this name already exists in the package."""


class VersionNameConflictError(ValueError):
    """A version with this name already exists on the package.

    Soft-deleted versions keep their names reserved, so the conflict check
    intentionally spans deleted rows too.
    """


class VersionNotFoundError(LookupError):
    """Version with the given id does not exist (or was deleted)."""


class ImportValidationError(ValueError):
    """Bulk import rejected; carries per-row error entries."""

    def __init__(self, errors: list[dict]) -> None:
        self.errors = errors
        count = len(errors)
        super().__init__(f"Import rejected with {count} error(s): {errors[:5]}")


class IntentPublishBlockedError(Exception):
    """Publish rejected by the require-mode gate; carries the verdict."""

    def __init__(self, gate_result: PackageGateResult) -> None:
        self.gate_result = gate_result
        failing = [s.name for s in gate_result.signals if not s.passed]
        super().__init__(f"publish rejected by gate; failing signals: {failing}")


def _now() -> datetime:
    return datetime.now(UTC)


class IntentPackageService:
    """Manage intent packages: draft CRUD, import/export, versions, publish.

    Args:
        db: Async SQLAlchemy session for database operations.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Package CRUD
    # ------------------------------------------------------------------

    async def create_package(
        self,
        name: str,
        description: str | None,
        workspace_id: UUID,
    ) -> IntentPackageModel:
        """Create a package; the name must be free among non-deleted ones."""
        await self._ensure_name_free(name, workspace_id)
        package = IntentPackageModel(
            name=name,
            description=description,
            workspace_id=workspace_id,
        )
        self.db.add(package)
        await self.db.flush()
        await self.db.refresh(package)
        logger.info("Created intent package %s (%s)", package.id, name)
        return package

    async def list_packages(
        self,
        workspace_id: UUID,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[IntentPackageModel], int]:
        """List the workspace's packages, newest first (deleted excluded)."""
        base = select(IntentPackageModel).where(
            IntentPackageModel.workspace_id == workspace_id,
            ~IntentPackageModel.deleted,
        )
        total = (await self.db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = await self.db.execute(
            base.order_by(IntentPackageModel.created_at.desc(), IntentPackageModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows.scalars().all()), total

    async def get_package(self, package_id: UUID, workspace_id: UUID) -> IntentPackageModel:
        """Fetch one package; foreign workspaces are indistinguishable from missing."""
        return await self._get_package(package_id, workspace_id)

    async def update_package(
        self,
        package_id: UUID,
        workspace_id: UUID,
        name: str | None = None,
        description: str | None = None,
    ) -> IntentPackageModel:
        """Update package metadata; rename re-checks the name conflict."""
        package = await self._get_package(package_id, workspace_id)
        if name is not None and name != package.name:
            await self._ensure_name_free(name, workspace_id)
            package.name = name
        if description is not None:
            package.description = description
        await self.db.flush()
        await self.db.refresh(package)
        return package

    async def delete_package(self, package_id: UUID, workspace_id: UUID) -> None:
        """Soft-delete a package and its draft rows (versions stay readable)."""
        package = await self._get_package(package_id, workspace_id)
        now = _now()
        package.deleted = True
        package.deleted_at = now
        categories = await self._draft_categories(package_id)
        for category in categories:
            category.deleted = True
            category.deleted_at = now
        if categories:
            sample_rows = await self.db.execute(
                select(IntentPackageSampleModel).where(
                    IntentPackageSampleModel.category_id.in_([c.id for c in categories]),
                    ~IntentPackageSampleModel.deleted,
                )
            )
            for sample in sample_rows.scalars():
                sample.deleted = True
                sample.deleted_at = now
        await self.db.flush()

    # ------------------------------------------------------------------
    # Draft categories & samples
    # ------------------------------------------------------------------

    async def add_category(
        self,
        package_id: UUID,
        workspace_id: UUID,
        data: IntentCategoryCreateSchema,
    ) -> IntentPackageCategoryModel:
        """Add a category to the draft."""
        await self._get_package(package_id, workspace_id)
        await self._ensure_category_name_free(package_id, data.name)
        category = IntentPackageCategoryModel(
            package_id=package_id,
            name=data.name,
            description=data.description,
            domain=data.domain,
            policy_gated=data.policy_gated,
            position=await self._next_category_position(package_id),
        )
        self.db.add(category)
        await self.db.flush()
        await self.db.refresh(category)
        return category

    async def update_category(
        self,
        package_id: UUID,
        category_id: UUID,
        workspace_id: UUID,
        data: IntentCategoryUpdateSchema,
    ) -> IntentPackageCategoryModel:
        """Update a draft category."""
        category = await self._get_category(package_id, category_id, workspace_id)
        if data.name is not None and data.name != category.name:
            await self._ensure_category_name_free(package_id, data.name)
            category.name = data.name
        if data.description is not None:
            category.description = data.description
        if data.domain is not None:
            category.domain = data.domain
        if data.policy_gated is not None:
            category.policy_gated = data.policy_gated
        await self.db.flush()
        await self.db.refresh(category)
        return category

    async def delete_category(
        self,
        package_id: UUID,
        category_id: UUID,
        workspace_id: UUID,
    ) -> None:
        """Soft-delete a draft category and its samples."""
        category = await self._get_category(package_id, category_id, workspace_id)
        now = _now()
        category.deleted = True
        category.deleted_at = now
        sample_rows = await self.db.execute(
            select(IntentPackageSampleModel).where(
                IntentPackageSampleModel.category_id == category_id,
                ~IntentPackageSampleModel.deleted,
            )
        )
        for sample in sample_rows.scalars():
            sample.deleted = True
            sample.deleted_at = now
        await self.db.flush()

    async def list_categories(
        self,
        package_id: UUID,
        workspace_id: UUID,
    ) -> list[IntentPackageCategoryModel]:
        """List the draft's categories in deterministic freeze order."""
        await self._get_package(package_id, workspace_id)
        return await self._draft_categories(package_id)

    async def add_sample(
        self,
        package_id: UUID,
        category_id: UUID,
        workspace_id: UUID,
        utterance: str,
        provenance: dict | None = None,
    ) -> IntentPackageSampleModel:
        """Add a sample utterance to a draft category."""
        await self._get_category(package_id, category_id, workspace_id)
        sample = IntentPackageSampleModel(
            category_id=category_id,
            utterance=utterance,
            provenance=provenance or {},
            position=await self._next_sample_position(category_id),
        )
        self.db.add(sample)
        await self.db.flush()
        await self.db.refresh(sample)
        return sample

    async def list_samples(
        self,
        package_id: UUID,
        category_id: UUID,
        workspace_id: UUID,
    ) -> list[IntentPackageSampleModel]:
        """List a category's samples in deterministic freeze order."""
        await self._get_category(package_id, category_id, workspace_id)
        return await self._category_samples(category_id)

    async def delete_sample(
        self,
        package_id: UUID,
        category_id: UUID,
        sample_id: UUID,
        workspace_id: UUID,
    ) -> None:
        """Soft-delete a draft sample."""
        rows = await self.db.execute(
            select(IntentPackageSampleModel).where(
                IntentPackageSampleModel.id == sample_id,
                IntentPackageSampleModel.category_id == category_id,
                ~IntentPackageSampleModel.deleted,
            )
        )
        sample = rows.scalar_one_or_none()
        if sample is None:
            raise CategoryNotFoundError(f"Sample {sample_id} not found in category {category_id}")
        await self._get_category(package_id, category_id, workspace_id)
        sample.deleted = True
        sample.deleted_at = _now()
        await self.db.flush()

    # ------------------------------------------------------------------
    # Corrections (backflow)
    # ------------------------------------------------------------------

    async def submit_correction(
        self,
        package_id: UUID,
        workspace_id: UUID,
        data: IntentCorrectionCreateSchema,
        submitted_by: UUID | None,
    ) -> IntentPackageSampleModel:
        """Append a corrected sample to the draft with mandatory provenance.

        The category may be addressed by id or by name. Published versions
        are never modified — the correction surfaces at runtime only after
        a new version is frozen and published.
        """
        category: IntentPackageCategoryModel | None = None
        if data.category_id is not None:
            category = await self._get_category(package_id, data.category_id, workspace_id)
        elif data.category_name is not None:
            for candidate in await self._draft_categories(package_id):
                if candidate.name == data.category_name:
                    category = candidate
                    break
            if category is None:
                raise CategoryNotFoundError(f"Category {data.category_name!r} not found in package {package_id}")
        else:
            msg = "correction requires category_id or category_name"
            raise ValueError(msg)

        provenance = {
            "source_type": SampleSourceType.CORRECTION.value,
            "reason": data.reason,
            "created_by": str(submitted_by) if submitted_by else None,
            "source_session_id": str(data.source_session_id) if data.source_session_id else None,
            "source_turn_id": str(data.source_turn_id) if data.source_turn_id else None,
        }
        return await self.add_sample(package_id, category.id, workspace_id, data.utterance, provenance)

    # ------------------------------------------------------------------
    # Bulk import / export
    # ------------------------------------------------------------------

    async def import_content(
        self,
        package_id: UUID,
        workspace_id: UUID,
        content_format: str,
        payload: str,
        mode: str = "merge",
    ) -> IntentImportReportSchema:
        """Bulk-import categories and samples; all-or-nothing per invocation.

        The payload is parsed and validated in full before any write; a
        single invalid row aborts the import with a row-level error report.
        ``merge`` appends to the draft (new categories are created from the
        payload; existing categories keep their fields and receive the
        payload's samples); ``replace`` soft-deletes the draft first.
        """
        if content_format not in ("csv", "json"):
            msg = f"unsupported import format {content_format!r} (csv or json)"
            raise ValueError(msg)
        if mode not in ("merge", "replace"):
            msg = f"unsupported import mode {mode!r} (merge or replace)"
            raise ValueError(msg)
        await self._get_package(package_id, workspace_id)

        entries, errors = (
            self._parse_json_import(payload) if content_format == "json" else self._parse_csv_import(payload)
        )
        if errors:
            raise ImportValidationError(errors)

        categories_created = categories_merged = samples_created = samples_merged = 0
        if mode == "replace":
            for category in await self._draft_categories(package_id):
                await self.delete_category(package_id, category.id, workspace_id)

        existing = {category.name: category for category in await self._draft_categories(package_id)}
        names_at_start = set(existing)
        for entry in entries:
            category = existing.get(entry["name"])
            if category is None:
                category = await self.add_category(
                    package_id,
                    workspace_id,
                    IntentCategoryCreateSchema(
                        name=entry["name"],
                        description=entry.get("description"),
                        domain=entry.get("domain"),
                        policy_gated=entry.get("policy_gated", False),
                    ),
                )
                existing[entry["name"]] = category
                categories_created += 1
            else:
                categories_merged += 1
            category_is_new = entry["name"] not in names_at_start
            for sample_entry in entry["samples"]:
                await self.add_sample(
                    package_id,
                    category.id,
                    workspace_id,
                    sample_entry["utterance"],
                    sample_entry.get("provenance") or {},
                )
                if category_is_new:
                    samples_created += 1
                else:
                    samples_merged += 1

        return IntentImportReportSchema(
            categories_created=categories_created,
            samples_created=samples_created,
            categories_merged=categories_merged,
            samples_merged=samples_merged,
            mode=mode,
            format=content_format,
        )

    async def export_content(
        self,
        package_id: UUID,
        workspace_id: UUID,
        content_format: str,
    ) -> str:
        """Export the draft as CSV text or a JSON string (round-trip safe)."""
        if content_format not in ("csv", "json"):
            msg = f"unsupported export format {content_format!r} (csv or json)"
            raise ValueError(msg)
        await self._get_package(package_id, workspace_id)
        categories = await self._draft_categories(package_id)
        samples_by_category: dict[str, list[IntentPackageSampleModel]] = {}
        for category in categories:
            samples_by_category[str(category.id)] = await self._category_samples(category.id)

        if content_format == "json":
            payload = {
                "categories": [
                    {
                        "name": category.name,
                        "description": category.description,
                        "domain": category.domain,
                        "policy_gated": bool(category.policy_gated),
                        "samples": [sample.utterance for sample in samples_by_category[str(category.id)]],
                    }
                    for category in categories
                ]
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_HEADER)
        for category in categories:
            samples = samples_by_category[str(category.id)]
            rows = samples if samples else [None]
            for sample in rows:
                writer.writerow(
                    [
                        category.name,
                        category.description or "",
                        category.domain or "",
                        "true" if category.policy_gated else "false",
                        sample.utterance if sample is not None else "",
                    ]
                )
        return buffer.getvalue()

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    async def create_version(
        self,
        package_id: UUID,
        workspace_id: UUID,
        data: IntentPackageVersionCreateSchema,
        created_by: UUID | None,
    ) -> IntentPackageVersionModel:
        """Freeze the draft content into a named, unpublished version."""
        await self._get_package(package_id, workspace_id)
        existing = await self._find_any_version_by_name(package_id, data.name)
        if existing is not None:
            msg = f"Version name {data.name!r} already exists on package {package_id} (deleted names stay reserved)"
            raise VersionNameConflictError(msg)

        categories = await self._draft_categories(package_id)
        if not categories:
            msg = f"Package {package_id} draft has no categories; nothing to freeze"
            raise ValueError(msg)
        samples: list[IntentPackageSampleModel] = []
        for category in categories:
            samples.extend(await self._category_samples(category.id))

        content, content_hash = build_package_content(categories, samples)
        version = IntentPackageVersionModel(
            package_id=package_id,
            name=data.name,
            description=data.description,
            content=content,
            content_hash=content_hash,
            created_by=created_by,
            workspace_id=workspace_id,
        )
        self.db.add(version)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            await self.db.rollback()
            raise VersionNameConflictError(
                f"Version name {data.name!r} already exists on package {package_id}"
            ) from exc
        await self.db.refresh(version)
        logger.info("Created intent package version %s (%s) hash=%s", version.id, data.name, content_hash[:12])
        return version

    async def list_versions(
        self,
        package_id: UUID,
        workspace_id: UUID,
        page: int = 1,
        page_size: int = 50,
        published_only: bool = False,
    ) -> tuple[list[IntentPackageVersionModel], int]:
        """List versions, newest first (deleted excluded)."""
        await self._get_package(package_id, workspace_id)
        base = select(IntentPackageVersionModel).where(
            IntentPackageVersionModel.package_id == package_id,
            ~IntentPackageVersionModel.deleted,
        )
        if published_only:
            base = base.where(IntentPackageVersionModel.published_at.is_not(None))
        total = (await self.db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        rows = await self.db.execute(
            base.order_by(IntentPackageVersionModel.created_at.desc(), IntentPackageVersionModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows.scalars().all()), total

    async def get_version(
        self,
        package_id: UUID,
        version_id: UUID,
        workspace_id: UUID,
    ) -> IntentPackageVersionModel:
        """Fetch one version by id — soft-deleted included (references stay readable)."""
        await self._get_package(package_id, workspace_id)
        rows = await self.db.execute(
            select(IntentPackageVersionModel).where(
                IntentPackageVersionModel.id == version_id,
                IntentPackageVersionModel.package_id == package_id,
                ~IntentPackageVersionModel.deleted,
            )
        )
        version = rows.scalar_one_or_none()
        if version is None:
            # A soft-deleted version stays readable for existing references.
            rows = await self.db.execute(
                select(IntentPackageVersionModel).where(
                    IntentPackageVersionModel.id == version_id,
                    IntentPackageVersionModel.package_id == package_id,
                )
            )
            version = rows.scalar_one_or_none()
        if version is None:
            raise VersionNotFoundError(f"Version {version_id} not found on package {package_id}")
        return version

    async def delete_version(
        self,
        package_id: UUID,
        version_id: UUID,
        workspace_id: UUID,
    ) -> None:
        """Soft-delete a version; its name stays reserved."""
        version = await self.get_version(package_id, version_id, workspace_id)
        version.deleted = True
        version.deleted_at = _now()
        await self.db.flush()

    async def latest_published_version(
        self,
        package_id: UUID,
        workspace_id: UUID | None = None,
    ) -> IntentPackageVersionModel | None:
        """The most recently published version, or None when unpublished.

        The workspace filter is optional so composition-root providers that
        resolve by trusted references can skip the tenant check.
        """
        stmt = (
            select(IntentPackageVersionModel)
            .where(
                IntentPackageVersionModel.package_id == package_id,
                IntentPackageVersionModel.published_at.is_not(None),
                ~IntentPackageVersionModel.deleted,
            )
            .order_by(IntentPackageVersionModel.published_at.desc(), IntentPackageVersionModel.id.desc())
            .limit(1)
        )
        if workspace_id is not None:
            stmt = stmt.where(IntentPackageVersionModel.workspace_id == workspace_id)
        rows = await self.db.execute(stmt)
        return rows.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish_version(
        self,
        package_id: UUID,
        version_id: UUID,
        workspace_id: UUID,
        gate_config_raw: dict | None,
        force: bool = False,
        actor_user_id: UUID | None = None,
        linked_result: dict | None = None,
    ) -> IntentPackageVersionModel:
        """Publish a version after deterministic gate evaluation.

        ``require`` mode with any unmet enabled signal raises
        :class:`IntentPublishBlockedError` (rendered as 409 by the API) and
        leaves the version untouched. ``force`` publishes anyway, records
        ``bypassed_by_force`` in the gate report, and writes an audit entry.

        Args:
            linked_result: Recognition-accuracy result for this version
                (resolved by the evaluation linkage); None when absent.
        """
        version = await self.get_version(package_id, version_id, workspace_id)
        config = resolve_gate_config(gate_config_raw)
        gate_result: PackageGateResult | None = None
        blocking = False
        if config.mode != "off":
            # 7.3 linkage: when the accuracy signal is enabled and the caller
            # did not supply a result, resolve the latest recognition run.
            if config.min_pass_rate is not None and linked_result is None:
                from hecate.studio.intent_packages.eval_linkage import IntentEvalLinkageService

                linked_result = await IntentEvalLinkageService(self.db).latest_recognition_result(
                    package_id, version_id, workspace_id
                )
            gate_result = evaluate_gate(config, version.content, linked_result)
            blocking = gate_result.blocking
            if blocking and not force:
                raise IntentPublishBlockedError(gate_result)

        version.published_at = _now()
        version.gate_report = gate_result.to_report_payload(bypassed=bool(blocking and force)) if gate_result else None
        await self.db.flush()
        # ``updated_at`` is server-onupdate — refresh so the caller can
        # serialize the row without triggering a lazy load.
        await self.db.refresh(version)

        if blocking and force and actor_user_id is not None:
            await self._record_gate_bypass(package_id, version, actor_user_id, gate_result)

        logger.info("Published intent package version %s (%s)", version.id, version.name)
        return version

    async def _record_gate_bypass(
        self,
        package_id: UUID,
        version: IntentPackageVersionModel,
        actor_user_id: UUID,
        gate_result: PackageGateResult,
    ) -> None:
        """Persist a require-gate bypass to the audit log (6.49, mirrors 7.3a)."""
        from hecate.models.audit import AuditLogModel

        failing_signals = [s.name for s in gate_result.signals if not s.passed]
        try:
            self.db.add(
                AuditLogModel(
                    org_id=version.workspace_id,
                    workspace_id=version.workspace_id,
                    user_id=actor_user_id,
                    action="INTENT_PACKAGE_GATE_BYPASS",
                    resource_type="intent_package",
                    resource_id=package_id,
                    success=True,
                    metadata_={
                        "version_id": str(version.id),
                        "version_name": version.name,
                        "failing_signals": failing_signals,
                    },
                )
            )
            await self.db.flush()
        except Exception:
            logger.exception("Failed to record intent package gate bypass audit")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_package(self, package_id: UUID, workspace_id: UUID) -> IntentPackageModel:
        rows = await self.db.execute(
            select(IntentPackageModel).where(
                IntentPackageModel.id == package_id,
                IntentPackageModel.workspace_id == workspace_id,
                ~IntentPackageModel.deleted,
            )
        )
        package = rows.scalar_one_or_none()
        if package is None:
            raise PackageNotFoundError(f"Intent package {package_id} not found")
        return package

    async def _get_category(
        self,
        package_id: UUID,
        category_id: UUID,
        workspace_id: UUID,
    ) -> IntentPackageCategoryModel:
        await self._get_package(package_id, workspace_id)
        rows = await self.db.execute(
            select(IntentPackageCategoryModel).where(
                IntentPackageCategoryModel.id == category_id,
                IntentPackageCategoryModel.package_id == package_id,
                ~IntentPackageCategoryModel.deleted,
            )
        )
        category = rows.scalar_one_or_none()
        if category is None:
            raise CategoryNotFoundError(f"Category {category_id} not found in package {package_id}")
        return category

    async def _ensure_name_free(self, name: str, workspace_id: UUID) -> None:
        rows = await self.db.execute(
            select(IntentPackageModel).where(
                IntentPackageModel.name == name,
                IntentPackageModel.workspace_id == workspace_id,
                ~IntentPackageModel.deleted,
            )
        )
        if rows.scalar_one_or_none() is not None:
            raise PackageNameConflictError(f"Intent package name {name!r} already exists in workspace")

    async def _ensure_category_name_free(self, package_id: UUID, name: str) -> None:
        rows = await self.db.execute(
            select(IntentPackageCategoryModel).where(
                IntentPackageCategoryModel.package_id == package_id,
                IntentPackageCategoryModel.name == name,
                ~IntentPackageCategoryModel.deleted,
            )
        )
        if rows.scalar_one_or_none() is not None:
            raise CategoryNameConflictError(f"Category name {name!r} already exists in package {package_id}")

    async def _draft_categories(self, package_id: UUID) -> list[IntentPackageCategoryModel]:
        rows = await self.db.execute(
            select(IntentPackageCategoryModel)
            .where(
                IntentPackageCategoryModel.package_id == package_id,
                ~IntentPackageCategoryModel.deleted,
            )
            .order_by(IntentPackageCategoryModel.position.asc(), IntentPackageCategoryModel.id.asc())
        )
        return list(rows.scalars().all())

    async def _category_samples(self, category_id: UUID) -> list[IntentPackageSampleModel]:
        rows = await self.db.execute(
            select(IntentPackageSampleModel)
            .where(
                IntentPackageSampleModel.category_id == category_id,
                ~IntentPackageSampleModel.deleted,
            )
            .order_by(IntentPackageSampleModel.position.asc(), IntentPackageSampleModel.id.asc())
        )
        return list(rows.scalars().all())

    async def _next_category_position(self, package_id: UUID) -> int:
        rows = await self.db.execute(
            select(func.max(IntentPackageCategoryModel.position)).where(
                IntentPackageCategoryModel.package_id == package_id,
                ~IntentPackageCategoryModel.deleted,
            )
        )
        return (rows.scalar_one() or 0) + 1

    async def _next_sample_position(self, category_id: UUID) -> int:
        rows = await self.db.execute(
            select(func.max(IntentPackageSampleModel.position)).where(
                IntentPackageSampleModel.category_id == category_id,
                ~IntentPackageSampleModel.deleted,
            )
        )
        return (rows.scalar_one() or 0) + 1

    async def _find_any_version_by_name(
        self,
        package_id: UUID,
        name: str,
    ) -> IntentPackageVersionModel | None:
        rows = await self.db.execute(
            select(IntentPackageVersionModel).where(
                IntentPackageVersionModel.package_id == package_id,
                IntentPackageVersionModel.name == name,
            )
        )
        return rows.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Import parsers (validate everything before any write)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_import(payload: str) -> tuple[list[dict], list[dict]]:
        """Parse a JSON import payload into entries plus row-level errors."""
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as exc:
            return [], [{"row": None, "error": f"invalid JSON: {exc}"}]
        if not isinstance(raw, dict) or not isinstance(raw.get("categories"), list):
            return [], [{"row": None, "error": 'payload must be {"categories": [...]}'}]

        entries: list[dict] = []
        errors: list[dict] = []
        seen: dict[str, dict] = {}
        for index, category in enumerate(raw["categories"]):
            row = f"categories[{index}]"
            if not isinstance(category, dict):
                errors.append({"row": row, "error": "category must be an object"})
                continue
            name = (category.get("name") or "").strip()
            if not name:
                errors.append({"row": row, "error": "category name is required"})
                continue
            samples_raw = category.get("samples", [])
            if not isinstance(samples_raw, list):
                errors.append({"row": row, "error": "samples must be a list"})
                continue
            samples: list[dict] = []
            for sample_index, sample in enumerate(samples_raw):
                if isinstance(sample, str):
                    sample = {"utterance": sample}
                if not isinstance(sample, dict) or not str(sample.get("utterance") or "").strip():
                    errors.append({"row": f"{row}.samples[{sample_index}]", "error": "utterance is required"})
                    continue
                samples.append({"utterance": str(sample["utterance"]), "provenance": sample.get("provenance") or {}})
            entry = {
                "name": name,
                "description": category.get("description"),
                "domain": category.get("domain"),
                "policy_gated": bool(category.get("policy_gated", False)),
                "samples": samples,
            }
            # Conflict = same name with a differing *definition*; repeated
            # rows for one category only add samples.
            previous = seen.get(name)
            if previous is not None and _definition(previous) != _definition(entry):
                errors.append({"row": row, "error": f"conflicting definition for category {name!r}"})
                continue
            if previous is not None:
                previous["samples"].extend(samples)
            else:
                seen[name] = entry
                entries.append(entry)
        return entries, errors

    @staticmethod
    def _parse_csv_import(payload: str) -> tuple[list[dict], list[dict]]:
        """Parse a CSV import payload into entries plus row-level errors."""
        reader = csv.reader(io.StringIO(payload))
        entries: list[dict] = []
        errors: list[dict] = []
        seen: dict[str, dict] = {}
        header_skipped = False
        for line_number, row in enumerate(reader, start=1):
            if not row or all(not cell.strip() for cell in row):
                continue
            if not header_skipped and tuple(cell.strip() for cell in row) == CSV_HEADER:
                header_skipped = True
                continue
            row_id = f"csv:{line_number}"
            if len(row) != len(CSV_HEADER):
                errors.append({"row": row_id, "error": f"expected {len(CSV_HEADER)} columns, got {len(row)}"})
                continue
            category_name = row[0].strip()
            utterance = row[4].strip()
            if not category_name:
                errors.append({"row": row_id, "error": "category is required"})
                continue
            if not utterance:
                errors.append({"row": row_id, "error": "utterance is required"})
                continue
            policy_raw = row[3].strip().lower()
            if policy_raw and policy_raw not in ("true", "false"):
                errors.append({"row": row_id, "error": f"policy_gated must be true/false, got {policy_raw!r}"})
                continue
            entry: dict = {
                "name": category_name,
                "description": row[1].strip() or None,
                "domain": row[2].strip() or None,
                "policy_gated": policy_raw == "true",
                "samples": [{"utterance": utterance, "provenance": {}}],
            }
            previous = seen.get(category_name)
            if previous is not None and _definition(previous) != _definition(entry):
                errors.append({"row": row_id, "error": f"conflicting definition for category {category_name!r}"})
                continue
            if previous is not None:
                previous["samples"].append(entry["samples"][0])
            else:
                seen[category_name] = entry
                entries.append(entry)
        return entries, errors
