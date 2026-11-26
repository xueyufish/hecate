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

from hecate.models.intent_package import IntentPackageVersionModel
from hecate.runtime.intent.types import EvidenceCategory, EvidencePayload, IntentEvidencePort

logger = logging.getLogger(__name__)


class StudioIntentEvidenceProvider(IntentEvidencePort):
    """Load published evidence for intent packages from the studio store."""

    def __init__(self, workspace_id: uuid.UUID | None = None) -> None:
        self._workspace_id = workspace_id

    async def get_evidence(
        self,
        package_id: uuid.UUID,
        version_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> EvidencePayload:
        from hecate.core.database import async_session_factory

        if self._workspace_id is not None:
            if workspace_id is not None and workspace_id != self._workspace_id:
                raise LookupError("Intent evidence is outside the execution workspace")
            workspace_id = self._workspace_id
        async with async_session_factory() as session:
            stmt = select(IntentPackageVersionModel).where(
                IntentPackageVersionModel.package_id == package_id,
                IntentPackageVersionModel.workspace_id == workspace_id,
                IntentPackageVersionModel.published_at.is_not(None),
                ~IntentPackageVersionModel.deleted,
            )
            if version_id is not None:
                stmt = stmt.where(IntentPackageVersionModel.id == version_id)
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

            # The save path validates against this same immutable snapshot.
            # Retained draft rows may contain changes made after the release.
            payload_categories = tuple(
                EvidenceCategory(
                    name=category["name"],
                    description=category.get("description"),
                    domain=category.get("domain"),
                    policy_gated=bool(category.get("policy_gated", False)),
                    samples=tuple(sample["utterance"] for sample in category.get("samples", [])),
                )
                for category in (version.content or {}).get("categories", [])
            )
            return EvidencePayload(
                package_id=package_id,
                version_id=version.id,
                version_name=version.name,
                categories=payload_categories,
            )


def create_intent_evidence_port(workspace_id: uuid.UUID | None = None) -> IntentEvidencePort:
    """Create an evidence port scoped to an execution workspace."""
    return StudioIntentEvidenceProvider(workspace_id)


__all__ = ["StudioIntentEvidenceProvider", "create_intent_evidence_port"]
