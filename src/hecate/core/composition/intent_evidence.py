"""Composition-root provider for intent evidence (6.23 ⊕ 6.49).

Implements :class:`hecate.runtime.intent.types.IntentEvidencePort` against
the studio intent package store. This is the only place the runtime engine
touches package persistence — via the port, wired here, where the
composition root is allowed to see both sides. The runtime itself keeps
its zero-studio-dependency invariant (no lazy-import bridge needed).

Only **published** versions resolve: an unpublished pin raises LookupError,
and an unpinned reference resolves to the package's latest published
version. A studio-side outage therefore degrades recognition (the engine's
fallback path) instead of failing the turn.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from hecate.models.intent_package import (
    IntentPackageCategoryModel,
    IntentPackageSampleModel,
    IntentPackageVersionModel,
)
from hecate.runtime.intent.types import EvidenceCategory, EvidencePayload, IntentEvidencePort

logger = logging.getLogger(__name__)


class StudioIntentEvidenceProvider(IntentEvidencePort):
    """Load published evidence for intent packages from the studio store."""

    async def get_evidence(
        self,
        package_id: uuid.UUID,
        version_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> EvidencePayload:
        from hecate.core.database import async_session_factory

        async with async_session_factory() as session:
            stmt = select(IntentPackageVersionModel).where(
                IntentPackageVersionModel.package_id == package_id,
                IntentPackageVersionModel.published_at.is_not(None),
                ~IntentPackageVersionModel.deleted,
            )
            if version_id is not None:
                stmt = stmt.where(IntentPackageVersionModel.id == version_id)
            if workspace_id is not None:
                stmt = stmt.where(IntentPackageVersionModel.workspace_id == workspace_id)
            stmt = stmt.order_by(
                IntentPackageVersionModel.published_at.desc(),
                IntentPackageVersionModel.id.desc(),
            ).limit(1)
            version = (await session.execute(stmt)).scalar_one_or_none()
            if version is None:
                msg = f"No published version for intent package {package_id}"
                if version_id is not None:
                    msg += f" pinned to version {version_id}"
                raise LookupError(msg)

            categories = (
                (
                    await session.execute(
                        select(IntentPackageCategoryModel)
                        .where(
                            IntentPackageCategoryModel.package_id == package_id,
                            ~IntentPackageCategoryModel.deleted,
                        )
                        .order_by(
                            IntentPackageCategoryModel.position.asc(),
                            IntentPackageCategoryModel.id.asc(),
                        )
                    )
                )
                .scalars()
                .all()
            )

            samples_by_category: dict[str, list[str]] = {}
            if categories:
                sample_rows = (
                    (
                        await session.execute(
                            select(IntentPackageSampleModel)
                            .where(
                                IntentPackageSampleModel.category_id.in_([c.id for c in categories]),
                                ~IntentPackageSampleModel.deleted,
                            )
                            .order_by(
                                IntentPackageSampleModel.position.asc(),
                                IntentPackageSampleModel.id.asc(),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for sample in sample_rows:
                    samples_by_category.setdefault(str(sample.category_id), []).append(sample.utterance)

            payload_categories = tuple(
                EvidenceCategory(
                    name=category.name,
                    description=category.description,
                    domain=category.domain,
                    policy_gated=bool(category.policy_gated),
                    samples=tuple(samples_by_category.get(str(category.id), [])),
                )
                for category in categories
            )
            return EvidencePayload(
                package_id=package_id,
                version_id=version.id,
                version_name=version.name,
                categories=payload_categories,
            )


def create_intent_evidence_port() -> IntentEvidencePort:
    """Factory used by the composition wiring."""
    return StudioIntentEvidenceProvider()


__all__ = ["StudioIntentEvidenceProvider", "create_intent_evidence_port"]
