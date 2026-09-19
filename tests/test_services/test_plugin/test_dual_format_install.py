"""Integration tests for dual-format package install + enable projection (5.5d)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.core.plugin.agent_plugins import AgentPluginValidationError
from hecate.core.plugin.loader import get_dual_format_registry
from hecate.studio.plugin.service import AGENT_PLUGIN_TYPE, PluginService

WS = uuid.UUID("22222222-2222-2222-2222-222222222222")
INSTALLER = "admin@example.com"
SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"


def _write_dual_package(
    root: Path,
    *,
    name: str = "dual-helper",
    ns_manifest: dict[str, Any] | None = None,
    with_code: bool = False,
    malformed: bool = False,
) -> Path:
    (root / "skills" / "deploy").mkdir(parents=True)
    (root / "plugin.json").write_text(
        json.dumps({"$schema": SCHEMA_URL, "name": name, "version": "1.0.0", "description": "dual"})
    )
    (root / "skills" / "deploy" / "SKILL.md").write_text(
        "---\nname: deploy\ndescription: Deploys things\n---\nRun the deploy."
    )
    ns = root / "io.github.xueyufish"
    ns.mkdir()
    if malformed:
        (ns / "plugin.yaml").write_text("::: not yaml [")
    elif ns_manifest is not None:
        import yaml

        (ns / "plugin.yaml").write_text(yaml.safe_dump(ns_manifest))
    if with_code:
        module = ns / "dual_tool"
        module.mkdir()
        (module / "__init__.py").write_text(
            "from hecate.core.plugin.sdk import ToolPluginBase\n"
            "\n"
            "\n"
            "class DualTool(ToolPluginBase):\n"
            "    @property\n"
            "    def name(self):\n"
            '        return "dual"\n'
            "\n"
            "    @property\n"
            "    def description(self):\n"
            '        return "dual tool"\n'
            "\n"
            "    async def execute(self, params):\n"
            '        return {"ok": True}\n'
        )
    return root


class _FakeManager:
    def register_server(self, name, endpoint, transport="http", workspace_id=None, headers=None):  # noqa: ANN001, ANN202
        return SimpleNamespace(name=name)

    def unregister_server(self, name):  # noqa: ANN001, ANN202
        return None


@pytest.fixture
def fake_mcp(monkeypatch: pytest.MonkeyPatch) -> _FakeManager:
    import hecate.tools.api.mcp as mcp_module

    monkeypatch.setattr(mcp_module, "get_mcp_manager", lambda: _FakeManager())
    return _FakeManager()


def _kwargs(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
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


class TestDualFormatInstall:
    async def test_declarative_dual_format_installs_one_row(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager
    ) -> None:
        _write_dual_package(tmp_path / "src", ns_manifest={"type": "tool", "permissions": ["network:https"]})
        plugin = await PluginService(db_session).install_agent_plugin(**_kwargs(tmp_path))

        assert plugin.type == AGENT_PLUGIN_TYPE
        ns = plugin.manifest_["namespace"]
        assert ns["type"] == "tool"
        assert ns["entry"] == ""
        assert ns["component_status"] == "none"
        assert ns["permissions"] == ["network:https"]
        assert plugin.manifest_["components"]["code"] == []
        assert plugin.name == "dual-helper" and plugin.version == "1.0.0"

    async def test_identity_conflict_rejected(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager
    ) -> None:
        _write_dual_package(tmp_path / "src", ns_manifest={"version": "2.0.0"})
        with pytest.raises(AgentPluginValidationError, match="conflicts with plugin.json"):
            await PluginService(db_session).install_agent_plugin(**_kwargs(tmp_path))

    async def test_malformed_namespace_degrades_to_plain(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager
    ) -> None:
        _write_dual_package(tmp_path / "src", malformed=True)
        plugin = await PluginService(db_session).install_agent_plugin(**_kwargs(tmp_path))
        assert "namespace" not in plugin.manifest_
        assert any("namespace manifest ignored" in w for w in plugin.manifest_["warnings"])
        assert plugin.manifest_["components"]["skills"]

    async def test_code_component_skipped_for_workspace_installer(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager
    ) -> None:
        _write_dual_package(
            tmp_path / "src",
            ns_manifest={"type": "tool", "entry": "python:dual_tool:DualTool"},
            with_code=True,
        )
        plugin = await PluginService(db_session).install_agent_plugin(**_kwargs(tmp_path))
        ns = plugin.manifest_["namespace"]
        assert ns["component_status"] == "skipped"
        code_entries = plugin.manifest_["components"]["code"]
        assert code_entries == [{"name": "tool", "status": "skipped", "reason": code_entries[0]["reason"]}]
        assert "platform installer" in code_entries[0]["reason"]
        assert plugin.manifest_["components"]["skills"][0]["status"] == "imported"

    async def test_code_component_registered_for_platform_installer(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "PLUGIN_PYTHON_ENTRY_ALLOWLIST", ["dual_tool"])
        _write_dual_package(
            tmp_path / "src",
            ns_manifest={"type": "tool", "entry": "python:dual_tool:DualTool"},
            with_code=True,
        )
        plugin = await PluginService(db_session).install_agent_plugin(**_kwargs(tmp_path, workspace_id=None))
        assert plugin.manifest_["namespace"]["component_status"] == "registered"
        assert plugin.manifest_["components"]["code"] == [{"name": "tool", "status": "registered"}]

    async def test_code_component_denied_by_t0_allowlist(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "PLUGIN_PYTHON_ENTRY_ALLOWLIST", ["other_module"])
        _write_dual_package(
            tmp_path / "src",
            ns_manifest={"type": "tool", "entry": "python:dual_tool:DualTool"},
            with_code=True,
        )
        plugin = await PluginService(db_session).install_agent_plugin(**_kwargs(tmp_path, workspace_id=None))
        code = plugin.manifest_["components"]["code"]
        assert code[0]["status"] == "skipped"
        assert "PLUGIN_PYTHON_ENTRY_ALLOWLIST" in code[0]["reason"]


class TestPackagedInstallRoundTrip:
    """`hecate plugin package` output installs back as a dual-format package."""

    async def test_emit_dual_format_installs_back(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager
    ) -> None:
        import yaml as _yaml

        from hecate.core.plugin.packaging import emit_dual_format

        src = tmp_path / "src"
        (src / "tool_k").mkdir(parents=True)
        (src / "plugin.yaml").write_text(
            _yaml.safe_dump(
                {"name": "tool-k", "version": "1.0.0", "type": "tool", "entry": "python:tool_k:ToolK"},
                sort_keys=False,
            )
        )
        (src / "tool_k" / "__init__.py").write_text("class ToolK:\n    pass\n")

        tree = emit_dual_format(src)
        plugin = await PluginService(db_session).install_agent_plugin(**_kwargs(tmp_path, location=str(tree)))

        assert plugin.name == "tool-k"
        ns = plugin.manifest_["namespace"]
        assert ns["entry"] == "python:tool_k:ToolK"
        # workspace admin: the code component is skipped, the package still installs
        assert ns["component_status"] == "skipped"
        assert plugin.manifest_["components"]["skills"] == []


class TestDualFormatEnableProjection:
    async def test_enable_loads_code_component_into_registry(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "PLUGIN_PYTHON_ENTRY_ALLOWLIST", ["dual_tool"])
        monkeypatch.setattr(settings, "PLUGINS_DIR", str(tmp_path / "plugins"))
        _write_dual_package(
            tmp_path / "src",
            ns_manifest={"type": "tool", "entry": "python:dual_tool:DualTool"},
            with_code=True,
        )
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_kwargs(tmp_path, workspace_id=None))
        registry = get_dual_format_registry()
        assert registry.get_by_name("tool", plugin.name) is None

        await service.enable_plugin(plugin.id)
        instance = registry.get_by_name("tool", plugin.name)
        assert instance is not None and instance.name == "dual"

        await service.disable_plugin(plugin.id)
        assert registry.get_by_name("tool", plugin.name) is None

    async def test_startup_replay_reregisters_code_component(
        self, db_session: AsyncSession, tmp_path: Path, fake_mcp: _FakeManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "PLUGIN_PYTHON_ENTRY_ALLOWLIST", ["dual_tool"])
        monkeypatch.setattr(settings, "PLUGINS_DIR", str(tmp_path / "plugins"))
        _write_dual_package(
            tmp_path / "src",
            ns_manifest={"type": "tool", "entry": "python:dual_tool:DualTool"},
            with_code=True,
        )
        service = PluginService(db_session)
        plugin = await service.install_agent_plugin(**_kwargs(tmp_path, workspace_id=None))
        await service.enable_plugin(plugin.id)
        get_dual_format_registry().unregister("tool", plugin.name)

        await service.replay_agent_plugin_mcp()
        assert get_dual_format_registry().get_by_name("tool", plugin.name) is not None
