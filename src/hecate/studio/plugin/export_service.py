"""Skill export — workspace skills as Agent Plugins 1.0 bundles (feature 5.5d).

Two-phase export: :meth:`SkillExportService.preview_export` computes the
bundle plan with no side effects, :meth:`SkillExportService.execute_export`
materializes exactly what was previewed. The bundle is a static snapshot —
nothing is activated or executed, and later edits to the source skills have
no effect on an already-exported bundle.
"""

from __future__ import annotations

import json
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.plugin.dual_format import AGENT_PLUGIN_SCHEMA_URL, sanitize_package_name
from hecate.models.skill import SkillModel

EXPORTABLE_SOURCES = ("user", "project")


@dataclass(frozen=True)
class ExportPlanEntry:
    """One skill's placement in the planned bundle."""

    skill_id: uuid.UUID
    original_name: str
    dir_name: str
    renamed: bool
    size_bytes: int


@dataclass(frozen=True)
class ExportPlan:
    """Bundle plan returned by the preview phase (no side effects)."""

    bundle_name: str
    entries: list[ExportPlanEntry]
    warnings: list[str]
    total_bytes: int


@dataclass(frozen=True)
class ExportResult:
    """Artifacts produced by the execution phase."""

    bundle_dir: Path
    zip_path: Path | None
    plan: ExportPlan


class SkillExportService:
    """Export workspace ``user``/``project`` skills as a conformant bundle.

    Exporting a skill requires the same access as reading it — selection is
    workspace-scoped and callers gate on their existing skill-read
    permissions before reaching this service.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def _resolve_skills(
        self, workspace_id: uuid.UUID, skill_names: list[str] | None
    ) -> tuple[list[SkillModel], list[str]]:
        """Selectable skills plus warnings for requested-but-excluded names."""
        warnings: list[str] = []
        stmt = select(SkillModel).where(
            SkillModel.workspace_id == workspace_id,
            SkillModel.source.in_(EXPORTABLE_SOURCES),
            SkillModel.deleted_at.is_(None),
        )
        if skill_names:
            stmt = stmt.where(SkillModel.name.in_(skill_names))
        rows = list((await self._db.execute(stmt.order_by(SkillModel.name))).scalars().unique().all())
        if skill_names:
            found = {s.name for s in rows}
            for requested in skill_names:
                if requested not in found:
                    warnings.append(
                        f"skill {requested!r} excluded (source not in {list(EXPORTABLE_SOURCES)} or not found)"
                    )
        return rows, warnings

    async def preview_export(
        self,
        workspace_id: uuid.UUID,
        *,
        bundle_name: str | None = None,
        skill_names: list[str] | None = None,
        max_total_mb: int | None = None,
    ) -> ExportPlan:
        """Compute the bundle plan: names, sanitization mapping, warnings, size."""
        if max_total_mb is None:
            from hecate.core.config import settings

            max_total_mb = settings.AGENT_PLUGIN_MAX_PACKAGE_MB
        skills, warnings = await self._resolve_skills(workspace_id, skill_names)
        if not skills:
            msg = "No exportable skills selected (sources: user, project)"
            raise ValueError(msg)

        name = sanitize_package_name(bundle_name or f"hecate-skills-{workspace_id.hex[:8]}")
        entries: list[ExportPlanEntry] = []
        used: dict[str, int] = {}
        total = 0
        for skill in skills:
            dir_name = sanitize_package_name(skill.name)
            if dir_name in used:
                used[dir_name] += 1
                dir_name = f"{dir_name}-{used[dir_name]}"
            else:
                used[dir_name] = 1
            size = len(skill.instructions.encode("utf-8")) + len(skill.description.encode("utf-8"))
            entries.append(
                ExportPlanEntry(
                    skill_id=skill.id,
                    original_name=skill.name,
                    dir_name=dir_name,
                    renamed=dir_name != skill.name,
                    size_bytes=size,
                )
            )
            total += size
        warnings.extend(f"skill {e.original_name!r} sanitized to {e.dir_name!r}" for e in entries if e.renamed)

        cap = max_total_mb * 1024 * 1024
        if total > cap:
            msg = f"Export size {total} bytes exceeds bundle cap {cap} bytes"
            raise ValueError(msg)
        return ExportPlan(bundle_name=name, entries=entries, warnings=warnings, total_bytes=total)

    async def execute_export(
        self,
        workspace_id: uuid.UUID,
        output_dir: str | Path,
        *,
        bundle_name: str | None = None,
        skill_names: list[str] | None = None,
        with_zip: bool = False,  # noqa: FBT001
        max_total_mb: int | None = None,
    ) -> ExportResult:
        """Materialize the previewed bundle: plugin.json + skills/<dir>/SKILL.md.

        The execution matches the preview plan; provenance (original name,
        workspace, export time) is written into each skill's frontmatter
        ``metadata`` under ``hecate.*`` keys.
        """
        plan = await self.preview_export(
            workspace_id, bundle_name=bundle_name, skill_names=skill_names, max_total_mb=max_total_mb
        )
        skills, _ = await self._resolve_skills(workspace_id, skill_names)
        by_id = {s.id: s for s in skills}

        bundle_dir = Path(output_dir) / plan.bundle_name
        if bundle_dir.exists():
            msg = f"Export target already exists: {bundle_dir}"
            raise ValueError(msg)
        (bundle_dir / "skills").mkdir(parents=True)

        plugin_json = {
            "$schema": AGENT_PLUGIN_SCHEMA_URL,
            "name": plan.bundle_name,
            "version": "0.1.0",
            "description": f"Exported skills bundle ({len(plan.entries)} skills)",
        }
        (bundle_dir / "plugin.json").write_text(json.dumps(plugin_json, indent=2), encoding="utf-8")

        exported_at = datetime.now(UTC).isoformat()
        for entry in plan.entries:
            skill = by_id[entry.skill_id]
            metadata = dict(skill.metadata_ or {})
            metadata["hecate.original-name"] = skill.name
            metadata["hecate.workspace"] = str(workspace_id)
            metadata["hecate.exported-at"] = exported_at
            front: dict[str, Any] = {"name": entry.dir_name, "description": skill.description}
            if skill.allowed_tools:
                front["allowed-tools"] = list(skill.allowed_tools)
            front["metadata"] = metadata
            content = f"---\n{yaml.safe_dump(front, sort_keys=False, allow_unicode=True)}---\n\n{skill.instructions}\n"
            skill_dir = bundle_dir / "skills" / entry.dir_name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

        zip_path: Path | None = None
        if with_zip:
            zip_path = bundle_dir.parent / f"{plan.bundle_name}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for child in sorted(bundle_dir.rglob("*")):
                    if child.is_file():
                        zf.write(child, child.relative_to(bundle_dir.parent))
        return ExportResult(bundle_dir=bundle_dir, zip_path=zip_path, plan=plan)
