"""Tests for plugin packaging and installer."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
import yaml

from hecate.plugin.packaging import create_bundle, extract_bundle, validate_bundle


def _make_plugin_dir(tmp_path: Path, name: str = "test-plugin", version: str = "1.0.0") -> Path:
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        yaml.dump({"name": name, "version": version, "type": "tool", "entry": "python:m:Cls"}),
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("value = 42\n", encoding="utf-8")
    (plugin_dir / "requirements.txt").write_text("# no deps\n", encoding="utf-8")
    return plugin_dir


# ── 8.1 create_bundle ──────────────────────────────────────────────────


class TestCreateBundle:
    def test_valid_bundle(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        bundle = create_bundle(plugin_dir, tmp_path / "out.hecate-plugin")
        assert bundle.is_file()
        with zipfile.ZipFile(bundle) as zf:
            names = zf.namelist()
            assert "plugin.yaml" in names
            assert "__init__.py" in names
            assert "requirements.txt" in names

    def test_default_output_name(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        bundle = create_bundle(plugin_dir)
        assert bundle.name == "test-plugin.hecate-plugin"

    def test_excludes_pycache(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        (plugin_dir / "__pycache__").mkdir()
        (plugin_dir / "__pycache__" / "x.pyc").write_text("x")
        bundle = create_bundle(plugin_dir, tmp_path / "out.hecate-plugin")
        with zipfile.ZipFile(bundle) as zf:
            assert not any("__pycache__" in n for n in zf.namelist())


# ── 8.2 reject without plugin.yaml ─────────────────────────────────────


def test_create_bundle_rejects_no_manifest(tmp_path):
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    with pytest.raises(ValueError, match="No plugin.yaml"):
        create_bundle(bad_dir)


def test_create_bundle_rejects_no_name(tmp_path):
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "plugin.yaml").write_text("version: 1.0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="name"):
        create_bundle(bad_dir)


# ── 8.3 extract_bundle ─────────────────────────────────────────────────


class TestExtractBundle:
    def test_extract_valid(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        bundle = create_bundle(plugin_dir, tmp_path / "out.hecate-plugin")
        target = tmp_path / "extracted"
        result = extract_bundle(bundle, target)
        assert (result / "plugin.yaml").is_file()
        assert (result / "__init__.py").is_file()

    def test_extract_invalid_zip(self, tmp_path):
        fake = tmp_path / "fake.hecate-plugin"
        fake.write_text("not a zip")
        target = tmp_path / "out"
        with pytest.raises(ValueError, match="Invalid"):
            extract_bundle(fake, target)


# ── 8.4 install_plugin ────────────────────────────────────────────────


class TestInstallPlugin:
    def test_install_creates_dir(self, tmp_path):
        from hecate.plugin.installer import install_plugin

        plugin_dir = _make_plugin_dir(tmp_path)
        bundle = create_bundle(plugin_dir, tmp_path / "out.hecate-plugin")
        plugins_dir = tmp_path / "plugins"
        name = install_plugin(bundle, plugins_dir)
        assert name == "test-plugin"
        assert (plugins_dir / "test-plugin" / "plugin.yaml").is_file()

    def test_install_invalid_bundle(self, tmp_path):
        from hecate.plugin.installer import install_plugin

        fake = tmp_path / "fake.hecate-plugin"
        fake.write_text("x")
        with pytest.raises(ValueError, match="Invalid"):
            install_plugin(fake, tmp_path / "plugins")


# ── 8.5 uninstall_plugin ──────────────────────────────────────────────


class TestUninstallPlugin:
    def test_uninstall_existing(self, tmp_path):
        from hecate.plugin.installer import install_plugin, uninstall_plugin

        plugin_dir = _make_plugin_dir(tmp_path)
        bundle = create_bundle(plugin_dir, tmp_path / "out.hecate-plugin")
        plugins_dir = tmp_path / "plugins"
        install_plugin(bundle, plugins_dir)
        assert uninstall_plugin("test-plugin", plugins_dir) is True
        assert not (plugins_dir / "test-plugin").exists()

    def test_uninstall_nonexistent(self, tmp_path):
        from hecate.plugin.installer import uninstall_plugin

        assert uninstall_plugin("nope", tmp_path / "plugins") is False


# ── 8.6 install upgrade ───────────────────────────────────────────────


def test_install_upgrade(tmp_path):
    from hecate.plugin.installer import install_plugin

    v1_dir = _make_plugin_dir(tmp_path, version="1.0.0")
    bundle_v1 = create_bundle(v1_dir, tmp_path / "v1.hecate-plugin")
    plugins_dir = tmp_path / "plugins"
    install_plugin(bundle_v1, plugins_dir)

    v2_dir = _make_plugin_dir(tmp_path, name="test-plugin-v2", version="2.0.0")
    (v2_dir / "plugin.yaml").write_text(
        yaml.dump({"name": "test-plugin", "version": "2.0.0", "type": "tool", "entry": "python:m:Cls"}),
        encoding="utf-8",
    )
    bundle_v2 = create_bundle(v2_dir, tmp_path / "v2.hecate-plugin")
    install_plugin(bundle_v2, plugins_dir)

    manifest = yaml.safe_load((plugins_dir / "test-plugin" / "plugin.yaml").read_text())
    assert manifest["version"] == "2.0.0"


# ── 8.7 validate_bundle ───────────────────────────────────────────────


class TestValidateBundle:
    def test_valid_bundle(self, tmp_path):
        plugin_dir = _make_plugin_dir(tmp_path)
        bundle = create_bundle(plugin_dir, tmp_path / "out.hecate-plugin")
        assert validate_bundle(bundle) is True

    def test_nonexistent_file(self, tmp_path):
        assert validate_bundle(tmp_path / "nope") is False

    def test_not_a_zip(self, tmp_path):
        fake = tmp_path / "fake.hecate-plugin"
        fake.write_text("x")
        assert validate_bundle(fake) is False


# ── 8.8 REST API ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_delete_plugin(client, db_session):
    from hecate.models.plugin import PluginModel

    plugin = PluginModel(
        name="deletable",
        type="tool",
        version="1.0.0",
        status="installed",
        entry="python:external:Cls",
    )
    db_session.add(plugin)
    await db_session.flush()

    resp = await client.delete(f"/api/plugins/{plugin.id}")
    assert resp.status_code == 200
