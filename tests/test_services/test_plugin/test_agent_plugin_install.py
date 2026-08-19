"""Integration tests for Agent Plugins 1.0 install orchestration (5.5c)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.plugin import PluginModel
from hecate.models.skill import SkillModel
from hecate.services.plugin.service import (
    AGENT_PLUGIN_TYPE,
    FeatureDisabledError,
    PluginService,
)
from hecate.services.skill.loader import SkillLoader

WS = uuid.UUID("11111111-1111-1111-1111-111111111111")

INSTALLER = "admin@example.com"


def _write_package(root: Path, *, name: str = "docs-helper", with_stdio: bool = False) -> None:
    """Create a standard Agent Plugins package on disk."""
    (root / "skills" / "deploy").mkdir(parents=True)
    manifest: dict[str, Any] = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": name,
        "version": "1.0.0",
        "description": "test package",
    }
    (root / "plugin.json").write_text(json.dumps(manifest))
    (root / "skills" / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: Deploys things\n---\nRun the deploy."
    )
    mcp: dict[str, Any] = {"mcpServers": {"search": {"type": "streamable-http", "url": "https://api.example.com/mcp"}}}
    if with_stdio:
        mcp["mcpServers"]["local"] = {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "some-mcp-server"],
        }
    (root / "mcp.json").write_text(json.dumps(mcp))


def _write_bare_package(root: Path) -> None:
    (root / "deploy").mkdir(parents=True)
    (root / "deploy" / "SKILL.md").write_text("---\nname: deploy\ndescription: Deploys things\n---\nRun the deploy.")


class _FakeRegistry:
    def __init__(self) -> None:
        self.servers: dict[str, Any] = {}

    def register(self, name, endpoint, transport="http", workspace_id=None, headers=None):  # noqa: ANN001, ANN202
        info = SimpleNamespace(
            name=name,
            endpoint=endpoint,
            transport=transport,
            workspace_id=workspace_id,
            headers=headers,
        )
        self.servers[name] = info
        return info

    def unregister(self, name: str):  # noqa: ANN202
        return self.servers.pop(name, None)


class _FakeManager:
    def __init__(self) -> None:
        self.registry = _FakeRegistry()

    def register_server(self, name, endpoint, transport="http", workspace_id=None, headers=None):  # noqa: ANN001, ANN202
        return self.registry.register(name, endpoint, transport, workspace_id, headers)

    def unregister_server(self, name: str):  # noqa: ANN202
        return self.registry.unregister(name)


@pytest.fixture
def fake_mcp(monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    manager = _FakeManager()
    import hecate.api.management.mcp as mcp_module

    monkeypatch.setattr(mcp_module, "get_mcp_manager", lambda: manager)
    return manager


def _install_kwargs(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "source_type": "dir",
        "location": str(tmp_path / "src"),
        "plugins_dir": str(tmp_path / "plugins"),
        "workspace_id": WS,
        "installer": INSTALLER,
        "ingestion_enabled": True,
        "platform_installers": [INSTALLER],
        "saas_mode": False,
    }
    kwargs.update(overrides)
    return kwargs


class TestInstallOrchestration:
    """End-to-end install pipeline (task 4.1)."""

    async def test_install_full_package(self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))

        assert plugin.type == AGENT_PLUGIN_TYPE
        assert plugin.name == "docs-helper"
        assert plugin.status == "installed"
        assert plugin.entry == ""
        assert plugin.origin.startswith("dir:")
        assert plugin.content_hash and plugin.content_hash.startswith("sha256:")
        assert plugin.scan_result is not None
        assert plugin.scan_result["verdict"] == "allow"
        assert plugin.scan_result["findings"] == []
        assert plugin.manifest_["components"]["skills"] == [{"name": "deploy", "status": "imported"}]
        assert plugin.manifest_["components"]["mcp_servers"][0]["name"] == "search"

        skills = (await db_session.execute(select(SkillModel))).scalars().all()
        assert len(skills) == 1
        assert skills[0].source == "plugin"
        assert skills[0].plugin_id == plugin.id
        assert skills[0].origin == plugin.origin

        # Snapshot materialized into the managed directory; staging cleaned
        managed = tmp_path / "plugins" / "agent-plugins" / "docs-helper"
        assert (managed / "plugin.json").is_file()
        assert not list((tmp_path / "plugins" / "agent-plugins").glob(".staging-*"))

    async def test_install_rejects_when_disabled(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        with pytest.raises(FeatureDisabledError):
            await service.install_agent_plugin(**_install_kwargs(tmp_path, ingestion_enabled=False))
        assert (await db_session.execute(select(PluginModel))).scalars().first() is None

    async def test_skill_name_mismatch_skipped_but_package_installs(
        self, db_session: AsyncSession, tmp_path: Path
    ) -> None:
        src = tmp_path / "src"
        _write_package(src)
        (src / "skills" / "deploy" / "SKILL.md").write_text("---\nname: ship-it\ndescription: d\n---\nbody")
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))
        assert plugin.manifest_["components"]["skills"][0]["status"] == "skipped"
        assert (await db_session.execute(select(SkillModel))).scalars().first() is None

    async def test_oversized_package_rejected(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src)
        (src / "skills" / "deploy" / "big.bin").write_bytes(b"x" * 100)
        service = PluginService(db_session)
        from hecate.plugin.agent_plugins import AgentPluginValidationError

        with pytest.raises(AgentPluginValidationError, match="cap"):
            await service.install_agent_plugin(**_install_kwargs(tmp_path, max_package_mb=0))


class TestReinstallAndCollision:
    """Upsert / collision policy (tasks 3.4 + 4.3)."""

    async def test_same_origin_reinstall_upserts(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        first = await service.install_agent_plugin(**_install_kwargs(tmp_path))

        (src / "skills" / "review").mkdir()
        (src / "skills" / "review" / "SKILL.md").write_text(
            "---\nname: review\ndescription: Reviews code\n---\nReview it."
        )
        second = await service.install_agent_plugin(**_install_kwargs(tmp_path))

        assert second.id == first.id
        skills = (await db_session.execute(select(SkillModel))).scalars().all()
        assert sorted(s.name for s in skills) == ["deploy", "review"]

    async def test_different_origin_rejected(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        await service.install_agent_plugin(**_install_kwargs(tmp_path))

        other = tmp_path / "other-src"
        _write_package(other)
        from hecate.plugin.agent_plugins import AgentPluginValidationError

        with pytest.raises(AgentPluginValidationError, match="different"):
            await service.install_agent_plugin(**_install_kwargs(tmp_path, location=str(other)))

    async def test_collision_with_user_skill_rejected(self, db_session: AsyncSession, tmp_path: Path) -> None:
        db_session.add(
            SkillModel(
                workspace_id=WS,
                name="deploy",
                description="user skill",
                source="user",
                instructions="x",
            )
        )
        await db_session.flush()

        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        from hecate.plugin.agent_plugins import AgentPluginValidationError

        with pytest.raises(AgentPluginValidationError, match="collision.*deploy"):
            await service.install_agent_plugin(**_install_kwargs(tmp_path))


class TestTrustDispatch:
    """Component-level trust dispatch (task 6.3 semantics at install)."""

    async def test_stdio_skipped_for_workspace_install(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src, with_stdio=True)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))
        stdio_entry = next(e for e in plugin.manifest_["components"]["mcp_servers"] if e["name"] == "local")
        assert stdio_entry["status"] == "skipped"
        assert "platform installer" in stdio_entry["reason"]

    async def test_stdio_skipped_in_saas_mode(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src, with_stdio=True)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path, workspace_id=None, saas_mode=True))
        stdio_entry = next(e for e in plugin.manifest_["components"]["mcp_servers"] if e["name"] == "local")
        assert stdio_entry["status"] == "skipped"

    async def test_stdio_registered_for_platform_installer(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src, with_stdio=True)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path, workspace_id=None))
        stdio_entry = next(e for e in plugin.manifest_["components"]["mcp_servers"] if e["name"] == "local")
        assert stdio_entry["status"] == "registered"
        assert plugin.workspace_id is None
        # Platform-level install puts skills in the system workspace
        skills = (await db_session.execute(select(SkillModel))).scalars().all()
        assert skills[0].workspace_id == uuid.UUID(int=0)


class TestUninstallCascade:
    """Uninstall removes all four artifact classes (task 4.4)."""

    async def test_uninstall_removes_everything(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager
    ) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))
        await service.enable_plugin(plugin.id)
        assert "docs-helper__search" in fake_mcp.registry.servers

        await service.uninstall_agent_plugin(plugin.id, str(tmp_path / "plugins"))

        assert (await db_session.execute(select(SkillModel))).scalars().first() is None
        assert "docs-helper__search" not in fake_mcp.registry.servers
        await db_session.refresh(plugin)
        assert plugin.deleted_at is not None
        assert not (tmp_path / "plugins" / "agent-plugins" / "docs-helper").exists()

    async def test_uninstall_rmtree_failure_rolls_back(
        self, db_session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))

        import hecate.services.plugin.service as svc_module

        real_rmtree = svc_module.shutil.rmtree

        def failing_rmtree(path, *args: Any, **kwargs: Any) -> None:  # noqa: ANN202
            if str(path).endswith(plugin.name):
                raise OSError("disk failure")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(svc_module.shutil, "rmtree", failing_rmtree)
        plugin_id = plugin.id
        with pytest.raises(OSError):
            await service.uninstall_agent_plugin(plugin_id, str(tmp_path / "plugins"))

        # Savepoint rollback: the skill rows are back after the failure.
        skills = (await db_session.execute(select(SkillModel).where(SkillModel.plugin_id == plugin_id))).scalars().all()
        assert len(skills) == 1


class TestOrphanCleanup:
    """Startup orphan-directory cleanup (task 4.5)."""

    async def test_orphan_dirs_removed(self, db_session: AsyncSession, tmp_path: Path) -> None:
        managed = tmp_path / "plugins" / "agent-plugins"
        (managed / "ghost").mkdir(parents=True)
        (managed / "ghost" / "plugin.json").write_text("{}")
        (managed / ".staging-x").mkdir()

        service = PluginService(db_session)
        removed = await service.cleanup_orphan_agent_plugin_dirs(str(tmp_path / "plugins"))
        assert removed == 1
        assert not (managed / "ghost").exists()
        assert (managed / ".staging-x").exists()  # dot-dirs left alone

    async def test_known_dirs_kept(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        await service.install_agent_plugin(**_install_kwargs(tmp_path))
        removed = await service.cleanup_orphan_agent_plugin_dirs(str(tmp_path / "plugins"))
        assert removed == 0
        assert (tmp_path / "plugins" / "agent-plugins" / "docs-helper").is_dir()


class TestVirtualPackages:
    """Bare SKILL.md directories (task 4.6)."""

    async def test_virtual_package_lifecycle(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_bare_package(src)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))

        assert plugin.type == AGENT_PLUGIN_TYPE
        assert plugin.manifest_["virtual"] is True
        skills = (await db_session.execute(select(SkillModel))).scalars().all()
        assert skills[0].name == "deploy"
        assert skills[0].source == "plugin"

        await service.uninstall_agent_plugin(plugin.id, str(tmp_path / "plugins"))
        assert (await db_session.execute(select(SkillModel))).scalars().first() is None


class TestMcpProjection:
    """Enable/disable projection + startup replay (tasks 5.1/5.2)."""

    async def test_enable_disable_projects_servers(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager
    ) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))

        await service.enable_plugin(plugin.id)
        registered = fake_mcp.registry.servers["docs-helper__search"]
        assert registered.endpoint == "https://api.example.com/mcp"
        assert registered.workspace_id == str(WS)

        await service.disable_plugin(plugin.id)
        assert "docs-helper__search" not in fake_mcp.registry.servers

    async def test_startup_replay(self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))
        await service.enable_plugin(plugin.id)
        fake_mcp.registry.servers.clear()

        count = await service.replay_agent_plugin_mcp()
        assert count == 1
        assert "docs-helper__search" in fake_mcp.registry.servers


class TestSkillVisibility:
    """Plugin-status skill filtering (task 5.3)."""

    async def _make_agent(self, db_session: AsyncSession) -> uuid.UUID:
        from hecate.models.agent import AgentModel

        agent = AgentModel(
            name="a1",
            model_config_db={},
            workspace_id=WS,
            skills=["deploy"],
        )
        db_session.add(agent)
        await db_session.flush()
        return agent.id

    async def test_enabled_plugin_skill_visible(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))
        await service.enable_plugin(plugin.id)
        agent_id = await self._make_agent(db_session)

        text = await SkillLoader(db_session).format_skills(agent_id, WS)
        assert "deploy" in text

    async def test_disabled_plugin_skill_hidden(self, db_session: AsyncSession, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write_package(src)
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_install_kwargs(tmp_path))
        await service.enable_plugin(plugin.id)
        await service.disable_plugin(plugin.id)
        agent_id = await self._make_agent(db_session)

        text = await SkillLoader(db_session).format_skills(agent_id, WS)
        assert text == ""

    async def test_user_skill_unaffected_by_plugin_status(self, db_session: AsyncSession) -> None:
        from hecate.models.agent import AgentModel

        db_session.add(
            SkillModel(
                workspace_id=WS,
                name="plain",
                description="plain skill",
                source="user",
                instructions="do things",
            )
        )
        agent = AgentModel(name="a2", model_config_db={}, workspace_id=WS, skills=["plain"])
        db_session.add(agent)
        await db_session.flush()
        text = await SkillLoader(db_session).format_skills(agent.id, WS)
        assert "plain" in text
