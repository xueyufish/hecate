"""Tests for plugin loader, config, permission, and service."""

from __future__ import annotations

import uuid

import pytest
import yaml
from jsonschema import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.plugin import PluginModel
from hecate.plugin.loader import (
    PythonEntryPolicy,
    _load_python,
    check_python_entry,
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
        policy = PythonEntryPolicy(saas_mode=False)
        instance = _load_python("python:tests.test_plugin.test_loader:DummyPluginForLoad", policy)
        assert type(instance).__name__ == "DummyPluginForLoad"

    def test_load_nonexistent_module(self):
        policy = PythonEntryPolicy(saas_mode=False)
        with pytest.raises((ImportError, ValueError)):
            _load_python("python:nonexistent.module:Foo", policy)

    def test_load_nonexistent_class(self):
        policy = PythonEntryPolicy(saas_mode=False)
        with pytest.raises((AttributeError, ValueError)):
            _load_python("python:tests.test_plugin.test_loader:NoSuchClass", policy)

    def test_load_mcp_entry(self):
        manifest = PluginManifest(
            type="tool",
            name="mcp-p",
            version="1.0.0",
            entry="mcp://http://localhost:9999",
        )
        result = load_plugin(manifest, PythonEntryPolicy(saas_mode=False))
        assert result["endpoint"] == "mcp://http://localhost:9999"

    def test_load_bad_prefix(self):
        manifest = PluginManifest(
            type="tool",
            name="bad",
            version="1.0.0",
            entry="ftp://x",
        )
        result = load_plugin(manifest, PythonEntryPolicy(saas_mode=False))
        assert result is None


class DummyPluginForLoad:
    pass


# ── T0 trust gate (ADR-029) ─────────────────────────────────────────────


class TestCheckPythonEntry:
    def test_first_party_module_allowed_in_saas(self):
        policy = PythonEntryPolicy(saas_mode=True)
        assert check_python_entry("python:hecate.plugins.foo:Foo", policy) is None

    def test_first_party_root_allowed(self):
        policy = PythonEntryPolicy(saas_mode=True)
        assert check_python_entry("python:hecate:Foo", policy) is None

    def test_saas_rejects_non_first_party(self):
        policy = PythonEntryPolicy(saas_mode=True, allowed_prefixes=("anything.",))
        reason = check_python_entry("python:my_plugin:Foo", policy)
        assert reason is not None
        assert "SaaS mode" in reason

    def test_self_hosted_default_deny(self):
        policy = PythonEntryPolicy(saas_mode=False, allowed_prefixes=())
        reason = check_python_entry("python:my_plugin:Foo", policy)
        assert reason is not None
        assert "default-deny" in reason

    def test_allowlist_prefix_grants(self):
        policy = PythonEntryPolicy(saas_mode=False, allowed_prefixes=("mycompany.",))
        assert check_python_entry("python:mycompany.tools.x:Foo", policy) is None

    def test_allowlist_without_dot_grants(self):
        policy = PythonEntryPolicy(saas_mode=False, allowed_prefixes=("mycompany",))
        assert check_python_entry("python:mycompany.tools.x:Foo", policy) is None

    def test_allowlist_does_not_cross_segment(self):
        policy = PythonEntryPolicy(saas_mode=False, allowed_prefixes=("mycompany.",))
        reason = check_python_entry("python:mycompanyevil.x:Foo", policy)
        assert reason is not None

    def test_mcp_entry_not_gated(self):
        policy = PythonEntryPolicy(saas_mode=True)
        assert check_python_entry("mcp://host:1234", policy) is None


class TestLoadPluginGate:
    def test_first_party_loads_in_both_modes(self):
        # Tests module existing under tests.* is irrelevant; the gate allows
        # any first-party-prefixed module to reach import. We verify by
        # monkey-patching importlib.import_module and asserting it was called
        # (i.e. the gate did not short-circuit).
        import hecate.plugin.loader as loader_mod

        called: list[str] = []

        def fake_import(name, package=None):  # noqa: ARG001
            called.append(name)
            import sys as _sys

            return _sys.modules.setdefault(name, type("M", (), {})())

        original = loader_mod.importlib.import_module
        loader_mod.importlib.import_module = fake_import
        try:
            for saas in (False, True):
                manifest = PluginManifest(
                    type="tool",
                    name="fp",
                    version="1.0.0",
                    entry="python:hecate.plugins.example:Foo",
                )
                load_plugin(manifest, PythonEntryPolicy(saas_mode=saas))
        finally:
            loader_mod.importlib.import_module = original
        assert "hecate.plugins.example" in called

    def test_rejected_entry_returns_none_without_import(self):
        # subprocess.Popen is a real, importable stdlib class — pre-gate this
        # would instantiate it (T0 RCE). The gate must short-circuit before
        # importlib.import_module is reached.
        manifest = PluginManifest(
            type="tool",
            name="evil",
            version="1.0.0",
            entry="python:subprocess:Popen",
        )
        result = load_plugin(manifest, PythonEntryPolicy(saas_mode=False))
        assert result is None

    def test_rejected_entry_does_not_invoke_import_module(self, monkeypatch):
        import hecate.plugin.loader as loader_mod

        called = False

        def boom(name, package=None):  # noqa: ARG001
            called = True  # noqa: F841
            raise AssertionError("importlib.import_module must not be called for rejected entries")

        monkeypatch.setattr(loader_mod.importlib, "import_module", boom)

        manifest = PluginManifest(
            type="tool",
            name="evil",
            version="1.0.0",
            entry="python:subprocess:Popen",
        )
        assert load_plugin(manifest, PythonEntryPolicy(saas_mode=True)) is None
        assert called is False

    def test_rejected_entry_logs_t0_error(self, caplog):
        manifest = PluginManifest(
            type="tool",
            name="evil",
            version="1.0.0",
            entry="python:subprocess:Popen",
        )
        with caplog.at_level("ERROR", logger="hecate.plugin.loader"):
            load_plugin(manifest, PythonEntryPolicy(saas_mode=False))
        assert any("T0 policy" in rec.message for rec in caplog.records)


def test_policy_from_settings():
    class FakeSettings:
        SAAS_MODE = True
        PLUGIN_PYTHON_ENTRY_ALLOWLIST = ["mycompany."]

    p = PythonEntryPolicy.from_settings(FakeSettings())
    assert p.saas_mode is True
    assert p.allowed_prefixes == ("mycompany.",)

    class DefaultSettings:
        SAAS_MODE = False
        PLUGIN_PYTHON_ENTRY_ALLOWLIST = []

    p2 = PythonEntryPolicy.from_settings(DefaultSettings())
    assert p2.saas_mode is False
    assert p2.allowed_prefixes == ()


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
