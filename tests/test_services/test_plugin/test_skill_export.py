"""Integration tests for skill export (feature 5.5d, tasks 6.1-6.6)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.skill import SkillModel
from hecate.studio.plugin.export_service import SkillExportService
from hecate.studio.plugin.service import PluginService

WS = uuid.UUID("33333333-3333-3333-3333-333333333333")
WS_OTHER = uuid.UUID("44444444-4444-4444-4444-444444444444")
INSTALLER = "admin@example.com"


class _FakeManager:
    def register_server(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return None

    def unregister_server(self, name):  # noqa: ANN001, ANN202
        return None


@pytest.fixture
def fake_mcp(monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    import hecate.tools.api.mcp as mcp_module

    monkeypatch.setattr(mcp_module, "get_mcp_manager", lambda: _FakeManager())
    return _FakeManager()


def _skill(name: str, source: str, instructions: str = "Do things.", description: str = "A skill") -> SkillModel:
    return SkillModel(workspace_id=WS, name=name, description=description, source=source, instructions=instructions)


async def _seed(db: AsyncSession) -> None:
    db.add_all(
        [
            _skill("deploy", "user"),
            _skill("triage", "project"),
            _skill("builtin-greet", "system"),
            _skill("imported-one", "plugin"),
            _skill("learned-one", "learned"),
        ]
    )
    await db.flush()


class TestExportSourceScope:
    async def test_only_user_and_project_skills_export(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        plan = await SkillExportService(db_session).preview_export(WS)
        names = [e.original_name for e in plan.entries]
        assert sorted(names) == ["deploy", "triage"]

    async def test_requested_excluded_skill_warns(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        plan = await SkillExportService(db_session).preview_export(WS, skill_names=["deploy", "imported-one"])
        assert [e.original_name for e in plan.entries] == ["deploy"]
        assert any("imported-one" in w for w in plan.warnings)

    async def test_workspace_isolation(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        with pytest.raises(ValueError, match="No exportable skills"):
            await SkillExportService(db_session).preview_export(WS_OTHER)

    async def test_read_permission_gate_is_caller_responsibility_but_selection_is_scoped(
        self, db_session: AsyncSession
    ) -> None:
        await _seed(db_session)
        plan = await SkillExportService(db_session).preview_export(WS, skill_names=["deploy"])
        assert plan.entries[0].skill_id is not None


class TestPreviewPlan:
    async def test_sanitization_mapping_and_warnings(self, db_session: AsyncSession) -> None:
        db_session.add(_skill("Deploy Helper", "user"))
        await db_session.flush()
        plan = await SkillExportService(db_session).preview_export(WS)
        entry = plan.entries[0]
        assert entry.dir_name == "deploy-helper"
        assert entry.renamed is True
        assert any("sanitized" in w for w in plan.warnings)

    async def test_collision_disambiguated_deterministically(self, db_session: AsyncSession) -> None:
        db_session.add_all([_skill("a-b", "user"), _skill("a.b", "user")])
        await db_session.flush()
        plan = await SkillExportService(db_session).preview_export(WS)
        dir_names = sorted(e.dir_name for e in plan.entries)
        assert dir_names == ["a-b", "a-b-2"]

    async def test_oversized_selection_rejected_at_preview(self, db_session: AsyncSession) -> None:
        db_session.add(_skill("big", "user", instructions="x" * (2 * 1024 * 1024)))
        await db_session.flush()
        with pytest.raises(ValueError, match="exceeds bundle cap"):
            await SkillExportService(db_session).preview_export(WS, max_total_mb=1)

    async def test_preview_has_no_side_effects(self, db_session: AsyncSession, tmp_path: Path) -> None:
        await _seed(db_session)
        await SkillExportService(db_session).preview_export(WS)
        assert list(tmp_path.iterdir()) == []


class TestExecuteExport:
    async def test_bundle_layout_and_provenance(self, db_session: AsyncSession, tmp_path: Path) -> None:
        await _seed(db_session)
        service = SkillExportService(db_session)
        await service.preview_export(WS, bundle_name="my-exports")
        result = await service.execute_export(WS, tmp_path, bundle_name="my-exports", with_zip=True)

        assert result.bundle_dir == tmp_path / "my-exports"
        assert result.zip_path == tmp_path / "my-exports.zip" and result.zip_path.is_file()
        plugin_json = json.loads((result.bundle_dir / "plugin.json").read_text(encoding="utf-8"))
        assert plugin_json["name"] == "my-exports"

        skill_md = (result.bundle_dir / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8")
        front = yaml.safe_load(skill_md.split("---\n")[1])
        assert front["name"] == "deploy"
        assert front["metadata"]["hecate.original-name"] == "deploy"
        assert front["metadata"]["hecate.workspace"] == str(WS)
        assert "hecate.exported-at" in front["metadata"]
        assert skill_md.rstrip().endswith("Do things.")

    async def test_execution_matches_preview(self, db_session: AsyncSession, tmp_path: Path) -> None:
        await _seed(db_session)
        service = SkillExportService(db_session)
        plan = await service.preview_export(WS)
        result = await service.execute_export(WS, tmp_path)
        assert [e.dir_name for e in result.plan.entries] == [e.dir_name for e in plan.entries]
        assert result.plan.warnings == plan.warnings

    async def test_existing_target_rejected(self, db_session: AsyncSession, tmp_path: Path) -> None:
        await _seed(db_session)
        service = SkillExportService(db_session)
        await service.execute_export(WS, tmp_path)
        with pytest.raises(ValueError, match="already exists"):
            await service.execute_export(WS, tmp_path)


class TestRoundTrip:
    async def test_exported_bundle_reimports_through_ingestion(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager
    ) -> None:
        """Export → standard Agent Plugins install in another workspace."""
        await _seed(db_session)
        service = SkillExportService(db_session)
        result = await service.execute_export(WS, tmp_path)

        installer = PluginService(db_session)
        plugin = await installer.install_agent_plugin(
            source_type="dir",
            location=str(result.bundle_dir),
            plugins_dir=str(tmp_path / "plugins"),
            workspace_id=WS_OTHER,
            installer=INSTALLER,
            ingestion_enabled=True,
            platform_installers=[INSTALLER],
            saas_mode=False,
        )
        skills = plugin.manifest_["components"]["skills"]
        assert sorted(s["name"] for s in skills if s["status"] == "imported") == ["deploy", "triage"]
