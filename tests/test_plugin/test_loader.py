"""Tests for plugin loader, config, permission, and service."""

from __future__ import annotations

import uuid

import pytest
import yaml
from jsonschema import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.plugin import PluginModel
from hecate.plugin.loader import (
    _load_python,
    discover_plugins,
    load_plugin,
    validate_compatibility,
)
from hecate.plugin.manifest import PluginManifest
from hecate.services.plugin.service import PluginService

# ── Helpers ──────────────────────────────────────────────────────────────


def _write_plugin_yaml(
    tmp_path,
    name="test-plugin",
    version="1.0.0",
    entry="python:tests.test_plugin.dummy_plugin:DummyPlugin",
    min_platform="",
    api_version="1.0",
    permissions=None,
    config_schema=None,
):
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "name": name,
        "version": version,
        "type": "tool",
        "entry": entry,
        "api_version": api_version,
        "description": f"A {name} plugin",
    }
    if min_platform:
        data["min_platform_version"] = min_platform
    if permissions:
        data["permissions"] = permissions
    if config_schema:
        data["config_schema"] = config_schema
    (plugin_dir / "plugin.yaml").write_text(yaml.dump(data))
    return plugin_dir


class DummyPlugin:
    def __init__(self):
        self._config = {}
        self.enabled = False
        self.disabled = False

    def on_enable(self):
        self.enabled = True

    def on_disable(self):
        self.disabled = True

    def on_config_change(self, new_config):
        self._config = new_config


# ── 10.1 discover_plugins ───────────────────────────────────────────────


class TestDiscoverPlugins:
    def test_discover_valid_plugins(self, tmp_path):
        _write_plugin_yaml(tmp_path, "plugin-a")
        _write_plugin_yaml(tmp_path, "plugin-b")
        (tmp_path / "not-a-plugin").mkdir()
        (tmp_path / "not-a-plugin" / "readme.txt").write_text("skip me")

        results = discover_plugins(tmp_path)
        assert len(results) == 2
        names = {p.parent.name for p in results}
        assert names == {"plugin-a", "plugin-b"}

    def test_discover_empty_dir(self, tmp_path):
        results = discover_plugins(tmp_path)
        assert results == []

    def test_discover_nonexistent_dir(self, tmp_path):
        results = discover_plugins(tmp_path / "nope")
        assert results == []


# ── 10.2 load_plugin / _load_python ─────────────────────────────────────


class TestLoadPython:
    def test_load_valid_module(self):
        instance = _load_python("python:tests.test_plugin.test_loader:DummyPluginForLoad")
        assert type(instance).__name__ == "DummyPluginForLoad"

    def test_load_nonexistent_module(self):
        with pytest.raises((ImportError, ValueError)):
            _load_python("python:nonexistent.module:Foo")

    def test_load_nonexistent_class(self):
        with pytest.raises((AttributeError, ValueError)):
            _load_python("python:tests.test_plugin.test_loader:NoSuchClass")

    def test_load_mcp_entry(self):
        manifest = PluginManifest(
            type="tool",
            name="mcp-p",
            version="1.0.0",
            entry="mcp://http://localhost:9999",
        )
        result = load_plugin(manifest)
        assert result["endpoint"] == "mcp://http://localhost:9999"

    def test_load_bad_prefix(self):
        manifest = PluginManifest(
            type="tool",
            name="bad",
            version="1.0.0",
            entry="ftp://x",
        )
        result = load_plugin(manifest)
        assert result is None


class DummyPluginForLoad:
    pass


# ── 10.3 validate_compatibility ─────────────────────────────────────────


class TestValidateCompatibility:
    def test_compatible_version(self):
        manifest = PluginManifest(
            type="tool",
            name="ok",
            version="1.0.0",
            min_platform_version="0.7.0",
        )
        validate_compatibility(manifest)

    def test_incompatible_version(self):
        manifest = PluginManifest(
            type="tool",
            name="new",
            version="1.0.0",
            min_platform_version="99.0.0",
        )
        with pytest.raises(ValueError, match="requires platform"):
            validate_compatibility(manifest)

    def test_no_min_version(self):
        manifest = PluginManifest(
            type="tool",
            name="any",
            version="1.0.0",
        )
        validate_compatibility(manifest)


# ── 10.4 PluginService enable/disable ───────────────────────────────────


@pytest.mark.asyncio
async def test_enable_disable_plugin(db_session: AsyncSession):
    plugin = PluginModel(
        name="toggle",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:x:y",
    )
    db_session.add(plugin)
    await db_session.flush()

    service = PluginService(db_session)
    result = await service.enable_plugin(plugin.id)
    assert result.status == "enabled"

    result = await service.disable_plugin(plugin.id)
    assert result.status == "disabled"


@pytest.mark.asyncio
async def test_enable_nonexistent(db_session: AsyncSession):
    service = PluginService(db_session)
    with pytest.raises(ValueError, match="not found"):
        await service.enable_plugin(uuid.uuid4())


# ── 10.5 update_config with schema validation ──────────────────────────


@pytest.mark.asyncio
async def test_update_config_valid(db_session: AsyncSession):
    plugin = PluginModel(
        name="cfg",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:x:y",
        manifest_={
            "config_schema": {
                "type": "object",
                "properties": {"api_key": {"type": "string"}},
                "required": ["api_key"],
            },
        },
    )
    db_session.add(plugin)
    await db_session.flush()

    service = PluginService(db_session)
    result = await service.update_config(plugin.id, {"api_key": "abc"})
    assert result.config == {"api_key": "abc"}


@pytest.mark.asyncio
async def test_update_config_invalid(db_session: AsyncSession):
    plugin = PluginModel(
        name="bad-cfg",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:x:y",
        manifest_={
            "config_schema": {
                "type": "object",
                "properties": {"api_key": {"type": "string"}},
                "required": ["api_key"],
            },
        },
    )
    db_session.add(plugin)
    await db_session.flush()

    service = PluginService(db_session)
    with pytest.raises(ValidationError):
        await service.update_config(plugin.id, {"wrong": "field"})


# ── 10.6 Two-layer scope ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_layer_scope(db_session: AsyncSession):
    ws_id = uuid.uuid4()
    platform_plugin = PluginModel(
        name="global-p",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:x:y",
        workspace_id=None,
    )
    ws_plugin = PluginModel(
        name="ws-p",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:x:y",
        workspace_id=ws_id,
    )
    other_ws_plugin = PluginModel(
        name="other-p",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:x:y",
        workspace_id=uuid.uuid4(),
    )
    db_session.add_all([platform_plugin, ws_plugin, other_ws_plugin])
    await db_session.flush()

    service = PluginService(db_session)
    plugins = await service.list_plugins(workspace_id=ws_id)
    names = {p.name for p in plugins}
    assert "global-p" in names
    assert "ws-p" in names
    assert "other-p" not in names

    platform_plugins = await service.list_plugins(workspace_id=None)
    p_names = {p.name for p in platform_plugins}
    assert "global-p" in p_names
    assert "ws-p" not in p_names


# ── 10.7 REST API ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_list_plugins(client, db_session: AsyncSession):
    plugin = PluginModel(
        name="api-p",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:x:y",
    )
    db_session.add(plugin)
    await db_session.flush()

    resp = await client.get("/api/plugins")
    assert resp.status_code == 200
    data = resp.json()
    assert any(p["name"] == "api-p" for p in data)


@pytest.mark.asyncio
async def test_api_enable_disable(client, db_session: AsyncSession):
    plugin = PluginModel(
        name="toggle-api",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:x:y",
    )
    db_session.add(plugin)
    await db_session.flush()

    resp = await client.post(f"/api/plugins/{plugin.id}/enable")
    assert resp.status_code == 200
    assert resp.json()["status"] == "enabled"

    resp = await client.post(f"/api/plugins/{plugin.id}/disable")
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


@pytest.mark.asyncio
async def test_api_update_config(client, db_session: AsyncSession):
    plugin = PluginModel(
        name="cfg-api",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:x:y",
        manifest_={
            "config_schema": {
                "type": "object",
                "properties": {"api_key": {"type": "string"}},
                "required": ["api_key"],
            },
        },
    )
    db_session.add(plugin)
    await db_session.flush()

    resp = await client.put(
        f"/api/plugins/{plugin.id}/config",
        json={"config": {"api_key": "secret"}},
    )
    assert resp.status_code == 200
    assert resp.json()["config"] == {"api_key": "secret"}
