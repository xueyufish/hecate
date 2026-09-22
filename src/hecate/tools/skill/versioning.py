"""Skill versioning service (5.9d).

Implements the skill-version lifecycle on top of immutable
``skill_versions`` snapshots, mirroring the agent-version machinery
(1.3.20) minus the publish pointer:

- **commit** — freeze the live skill row's content fields into a new
  version (name, instructions, allowed tools, scripts, references,
  description, max_tokens).
- **rollback** — restore a target version's content as a *new* version
  *and* write it back to the live row (skills have no publish pointer:
  the live row is what runtime serves, so a rollback that only created
  a snapshot would be a no-op).
- **diff / list / get / status / delete** — version management with
  deletion constraints (versions pinned by an agent snapshot are
  undeletable).

Deliberately out of scope: plugin-sourced skills (``provider`` unset) —
their lifecycle belongs to the owning plugin package.

The immutability promise is content-level (bytes), not behavioral:
``content_hash`` covers only the 5-field set shared with agent-version
reference manifests so hashes stay comparable across the two.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.canonical_hash import canonical_hash
from hecate.models.skill import SkillModel
from hecate.models.skill_version import SkillVersionModel

logger = logging.getLogger(__name__)

#: Bump when the snapshot shape changes; resolvers ignore unknown fields.
SKILL_SNAPSHOT_SCHEMA_VERSION = 1

#: Content fields captured in every snapshot (superset of the hashed set:
#: description and max_tokens shape runtime injection but are excluded
#: from the manifest-comparable hash).
SKILL_SNAPSHOT_FIELDS: tuple[str, ...] = (
    "name",
    "instructions",
    "allowed_tools",
    "scripts",
    "references",
    "description",
    "max_tokens",
)

#: The hashed subset — identical to the agent ref-manifest skill field set.
_HASHED_FIELDS: tuple[str, ...] = ("name", "instructions", "allowed_tools", "scripts", "references")


class SkillNotVersionableError(Exception):
    """Plugin-sourced skills carry no platform-managed version history.

    Their lifecycle belongs to the owning plugin package; the API layer
    surfaces this as 409.
    """


class SkillVersionNotFoundError(Exception):
    """Skill or version does not exist (API layer: 404)."""


class SkillVersionPinnedError(Exception):
    """A version is pinned by an agent snapshot and cannot be deleted (409)."""


class SkillRollbackConflictError(Exception):
    """The live row changed since the caller read it (optimistic lock, 409)."""


def snapshot_skill(skill: SkillModel) -> dict[str, Any]:
    """Extract the skill's frozen content fields as a snapshot dict."""
    return {
        "name": skill.name,
        "instructions": skill.instructions,
        "allowed_tools": list(skill.allowed_tools or []),
        "scripts": list(skill.scripts or []),
        "references": list(skill.references or []),
        "description": skill.description,
        "max_tokens": skill.max_tokens,
    }


def skill_content_hash(skill: SkillModel) -> str:
    """Hash the 5-field content set shared with agent ref manifests."""
    return canonical_hash(
        {
            "name": skill.name,
            "instructions": skill.instructions,
            "allowed_tools": skill.allowed_tools,
            "scripts": skill.scripts,
            "references": skill.references,
        }
    )


class SkillVersionService:
    """Version lifecycle for skills (5.9d)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- lifecycle ---------------------------------------------------------

    async def commit(
        self,
        skill_id: uuid.UUID,
        *,
        name: str = "",
        change_summary: str = "",
        actor_user_id: uuid.UUID | None = None,
        learned_run_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Freeze the live skill row's content into a new immutable version.

        Raises:
            SkillVersionNotFoundError: If the skill does not exist.
            SkillNotVersionableError: If the skill is plugin-sourced.
        """
        skill = await self._get_skill(skill_id)
        if skill.provider is None:
            raise SkillNotVersionableError(
                f"Skill '{skill.name}' is plugin-sourced; its lifecycle is managed by the owning plugin package"
            )

        snapshot = snapshot_skill(skill)
        latest = await self.db.execute(
            select(func.max(SkillVersionModel.version)).where(
                SkillVersionModel.skill_id == skill.id,
                ~SkillVersionModel.deleted,
            )
        )
        next_version = (latest.scalar_one() or 0) + 1

        version = SkillVersionModel(
            skill_id=skill.id,
            version=next_version,
            name=name,
            change_summary=change_summary,
            config_snapshot=snapshot,
            schema_version=SKILL_SNAPSHOT_SCHEMA_VERSION,
            content_hash=skill_content_hash(skill),
            learned_run_id=learned_run_id,
            created_by=actor_user_id,
            workspace_id=skill.workspace_id,
        )
        self.db.add(version)
        await self.db.flush()
        await self.db.refresh(version)
        logger.info("Committed version %s for skill %s", next_version, skill_id)
        return self._version_detail(version)

    async def list_versions(self, skill_id: uuid.UUID) -> list[dict[str, Any]]:
        """List a skill's versions, newest first."""
        await self._get_skill(skill_id)
        result = await self.db.execute(
            select(SkillVersionModel)
            .where(
                SkillVersionModel.skill_id == skill_id,
                ~SkillVersionModel.deleted,
            )
            .order_by(SkillVersionModel.version.desc())
        )
        return [self._version_read(v) for v in result.scalars().all()]

    async def get_version(
        self,
        skill_id: uuid.UUID,
        version: int,
        *,
        include_snapshot: bool = False,
    ) -> dict[str, Any]:
        """Fetch one version; optionally include the full snapshot."""
        record = await self._get_version(skill_id, version)
        if record is None:
            raise SkillVersionNotFoundError(f"Version {version} not found for skill {skill_id}")
        if include_snapshot:
            return self._version_detail(record)
        return self._version_read(record)

    async def update_version(
        self,
        skill_id: uuid.UUID,
        version: int,
        *,
        name: str | None = None,
        change_summary: str | None = None,
    ) -> dict[str, Any]:
        """Rename a version / edit its notes (metadata only).

        The snapshot content columns are never touched.
        """
        record = await self._get_version(skill_id, version)
        if record is None:
            raise SkillVersionNotFoundError(f"Version {version} not found for skill {skill_id}")
        if name is not None:
            record.name = name
        if change_summary is not None:
            record.change_summary = change_summary
        await self.db.flush()
        await self.db.refresh(record)
        return self._version_detail(record)

    async def rollback_to_version(
        self,
        skill_id: uuid.UUID,
        target_version: int,
        *,
        actor_user_id: uuid.UUID | None = None,
        expected_content_hash: str | None = None,
    ) -> dict[str, Any]:
        """Create a new version carrying the target's content and write it back.

        Skills have no publish pointer — the live row is what runtime
        serves — so rollback both snapshots the restored content *and*
        writes it back to the live row (edit semantics, effective
        immediately). Agent snapshots pinning older versions are
        unaffected: pins resolve from self-contained version rows.

        Args:
            expected_content_hash: Optimistic-lock guard — the live row's
                ``content_hash`` as the caller last read it. A mismatch
                means someone else edited the skill in between; the API
                surfaces this as 409. ``None`` skips the check (internal
                callers such as the evolution publish path).

        Raises:
            SkillVersionNotFoundError: If the skill or target version does
                not exist.
            SkillRollbackConflictError: On an optimistic-lock mismatch.
        """
        skill = await self._get_skill(skill_id)
        target = await self._get_version(skill_id, target_version)
        if target is None:
            raise SkillVersionNotFoundError(f"Version {target_version} not found for skill {skill_id}")

        live_hash = skill_content_hash(skill)
        if expected_content_hash is not None and expected_content_hash != live_hash:
            raise SkillRollbackConflictError(f"Skill '{skill.name}' changed since it was read; reload and retry")

        latest = await self.db.execute(
            select(func.max(SkillVersionModel.version)).where(
                SkillVersionModel.skill_id == skill.id,
                ~SkillVersionModel.deleted,
            )
        )
        new_version_num = (latest.scalar_one() or 0) + 1

        version = SkillVersionModel(
            skill_id=skill.id,
            version=new_version_num,
            name=target.name,
            change_summary=f"Rollback to version {target_version}",
            config_snapshot=dict(target.config_snapshot),
            schema_version=target.schema_version,
            content_hash=target.content_hash,
            created_by=actor_user_id,
            workspace_id=skill.workspace_id,
        )
        self.db.add(version)

        # Write-back: the restored content takes effect immediately (edit
        # semantics). Same field set as the snapshot.
        skill.name = target.config_snapshot["name"]
        skill.instructions = target.config_snapshot["instructions"]
        skill.allowed_tools = list(target.config_snapshot.get("allowed_tools") or [])
        skill.scripts = list(target.config_snapshot.get("scripts") or [])
        skill.references = list(target.config_snapshot.get("references") or [])
        skill.description = target.config_snapshot["description"]
        skill.max_tokens = target.config_snapshot["max_tokens"]
        skill.content_hash = skill_content_hash(skill)

        await self.db.flush()
        await self.db.refresh(version)
        logger.info(
            "Rolled back skill %s to version %s (new version %s)",
            skill_id,
            target_version,
            new_version_num,
        )
        return self._version_detail(version)

    async def diff_versions(self, skill_id: uuid.UUID, v1: int, v2: int) -> dict[str, Any]:
        """Compare two versions' snapshots over the frozen field set."""
        ver1 = await self._get_version(skill_id, v1)
        if ver1 is None:
            raise SkillVersionNotFoundError(f"Version {v1} not found for skill {skill_id}")
        ver2 = await self._get_version(skill_id, v2)
        if ver2 is None:
            raise SkillVersionNotFoundError(f"Version {v2} not found for skill {skill_id}")

        identical = ver1.content_hash == ver2.content_hash
        details: dict[str, Any] = {}
        summary: dict[str, Any]
        try:
            from deepdiff import DeepDiff

            diff = DeepDiff(ver1.config_snapshot, ver2.config_snapshot, ignore_order=True)
            identical = identical and not bool(diff)
            details["config_changes"] = json.loads(diff.to_json())
            summary = {
                "values_changed": len(diff.get("values_changed", {})),
                "dictionary_item_added": len(diff.get("dictionary_item_added", {})),
                "dictionary_item_removed": len(diff.get("dictionary_item_removed", {})),
                "type_changes": len(diff.get("type_changes", {})),
            }
        except ImportError:
            identical = identical and ver1.config_snapshot == ver2.config_snapshot
            details["config_changes"] = {}
            summary = {"error": "deepdiff not installed"}

        return {"v1": v1, "v2": v2, "identical": identical, "summary": summary, "details": details}

    async def delete_version(self, skill_id: uuid.UUID, version: int) -> None:
        """Soft-delete a version, unless an agent snapshot pins it.

        Pinned versions must stay resolvable (their content is frozen
        into agent snapshots), so deletion is refused while any
        non-deleted agent version's reference manifest carries this
        ``(skill_id, version)`` pin.
        """
        record = await self._get_version(skill_id, version)
        if record is None:
            raise SkillVersionNotFoundError(f"Version {version} not found for skill {skill_id}")

        # Load only manifest-bearing columns: snapshots can be large and are
        # irrelevant here. JSON containment predicates are portable neither
        # across SQLite (tests) nor Postgres for list-of-objects, so the
        # manifest scan happens in Python over the slimmed rows.
        from datetime import UTC, datetime

        from hecate.models.agent_version import AgentVersionModel

        result = await self.db.execute(
            select(
                AgentVersionModel.agent_id,
                AgentVersionModel.version,
                AgentVersionModel.ref_manifest,
            ).where(~AgentVersionModel.deleted)
        )
        skill_key = str(skill_id)
        for agent_id, agent_version, manifest in result.all():
            for entry in manifest or []:
                if (
                    entry.get("resource_type") == "skill"
                    and str(entry.get("skill_id")) == skill_key
                    and entry.get("version") == version
                ):
                    raise SkillVersionPinnedError(
                        f"Version {version} is pinned by agent {agent_id} version {agent_version} and cannot be deleted"
                    )

        record.deleted = True
        record.deleted_at = datetime.now(UTC)
        await self.db.flush()

    # --- status / resolution -------------------------------------------------

    async def get_status(self, skill_id: uuid.UUID) -> dict[str, Any]:
        """Badge state: latest version and the uncommitted-changes flag.

        No snapshot means "never committed", which counts as uncommitted
        whenever the skill exists. Only the hashed field set participates
        in the comparison — governance fields never dirty the badge.
        """
        skill = await self._get_skill(skill_id)
        latest = await self.db.execute(
            select(func.max(SkillVersionModel.version)).where(
                SkillVersionModel.skill_id == skill.id,
                ~SkillVersionModel.deleted,
            )
        )
        latest_version = latest.scalar_one()
        has_changes = True
        if latest_version is not None:
            record = await self._get_version(skill.id, latest_version)
            has_changes = record is None or record.content_hash != skill_content_hash(skill)
        return {
            "skill_id": skill.id,
            "latest_version": latest_version,
            "has_uncommitted_changes": has_changes,
        }

    async def get_version_content(self, skill_id: uuid.UUID, version: int) -> SkillVersionModel | None:
        """Load a version record for pinned content resolution.

        Does not join the live skill row: pinned versions must stay
        resolvable after their source skill is soft-deleted. Returns
        ``None`` when the version row itself is missing or deleted.
        """
        result = await self.db.execute(
            select(SkillVersionModel).where(
                SkillVersionModel.skill_id == skill_id,
                SkillVersionModel.version == version,
                ~SkillVersionModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    # --- internals -----------------------------------------------------------

    async def _get_skill(self, skill_id: uuid.UUID) -> SkillModel:
        result = await self.db.execute(select(SkillModel).where(SkillModel.id == skill_id, ~SkillModel.deleted))
        skill = result.scalar_one_or_none()
        if skill is None:
            raise SkillVersionNotFoundError(f"Skill {skill_id} not found")
        return skill

    async def _get_version(self, skill_id: uuid.UUID, version: int) -> SkillVersionModel | None:
        result = await self.db.execute(
            select(SkillVersionModel).where(
                SkillVersionModel.skill_id == skill_id,
                SkillVersionModel.version == version,
                ~SkillVersionModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    def _version_read(self, record: SkillVersionModel) -> dict[str, Any]:
        return {
            "id": record.id,
            "skill_id": record.skill_id,
            "version": record.version,
            "name": record.name,
            "change_summary": record.change_summary,
            "content_hash": record.content_hash,
            "learned_run_id": record.learned_run_id,
            "created_by": record.created_by,
            "workspace_id": record.workspace_id,
            "created_at": record.created_at,
        }

    def _version_detail(self, record: SkillVersionModel) -> dict[str, Any]:
        payload = self._version_read(record)
        payload.update(
            {
                "schema_version": record.schema_version,
                "config_snapshot": record.config_snapshot,
            }
        )
        return payload
