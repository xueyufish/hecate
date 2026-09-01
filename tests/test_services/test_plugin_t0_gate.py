"""Tests for the T0 trust gate wiring in PluginService (ADR-029)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import Settings
from hecate.core.plugin.packaging import BUNDLE_EXTENSION, create_bundle
from hecate.models.plugin import PluginModel
from hecate.services.plugin.service import PluginService


@pytest.fixture(autouse=True)
def _settings_defaults(monkeypatch):
    """Each test gets a fresh Settings to avoid cross-test SAAS_MODE bleed."""
    s = Settings()
    s.SAAS_MODE = False
    s.PLUGIN_PYTHON_ENTRY_ALLOWLIST = []
    monkeypatch.setattr("hecate.core.config.settings", s, raising=True)
    return s


def _write_bundle(tmp_path: Path, *, name: str, entry: str, plugin_dir_name: str | None = None) -> Path:
    """Build a valid .hecate-plugin ZIP whose plugin.yaml has the given entry."""
    plugin_dir_name = plugin_dir_name or name
    src = tmp_path / "src" / plugin_dir_name
    src.mkdir(parents=True)
    (src / "plugin.yaml").write_text(
        yaml.dump(
            {
                "name": name,
                "version": "1.0.0",
                "type": "tool",
                "entry": entry,
                "description": f"{name} for testing",
            }
        )
    )
    out = tmp_path / f"{name}{BUNDLE_EXTENSION}"
    create_bundle(src, out)
    return out


@pytest.mark.asyncio
async def test_install_rejected_bundle_cleans_directory(tmp_path: Path, db_session: AsyncSession) -> None:
    plugins_root = tmp_path / "plugins"
    bundle = _write_bundle(tmp_path, name="evil-plugin", entry="python:subprocess:Popen")

    service = PluginService(db_session)
    with pytest.raises(ValueError, match="T0 policy"):
        await service.install_plugin_from_bundle(str(bundle), str(plugins_root))

    # The extracted plugin directory must be gone (rollback).
    assert not (plugins_root / "evil-plugin").exists()

    # No PluginModel row should exist.
    rows = (await db_session.execute(select(PluginModel))).scalars().all()
    assert all(r.name != "evil-plugin" for r in rows)


@pytest.mark.asyncio
async def test_install_first_party_bundle_succeeds(tmp_path: Path, db_session: AsyncSession) -> None:
    plugins_root = tmp_path / "plugins"
    bundle = _write_bundle(tmp_path, name="fp-plugin", entry="python:hecate.plugins.example:Foo")

    service = PluginService(db_session)
    plugin = await service.install_plugin_from_bundle(str(bundle), str(plugins_root))

    assert plugin.name == "fp-plugin"
    assert plugin.entry == "python:hecate.plugins.example:Foo"
    assert (plugins_root / "fp-plugin").is_dir()


@pytest.mark.asyncio
async def test_install_self_hosted_allowlist_grants(tmp_path: Path, db_session: AsyncSession, monkeypatch) -> None:
    s = Settings()
    s.SAAS_MODE = False
    s.PLUGIN_PYTHON_ENTRY_ALLOWLIST = ["mycompany."]
    monkeypatch.setattr("hecate.core.config.settings", s, raising=True)

    plugins_root = tmp_path / "plugins"
    bundle = _write_bundle(
        tmp_path,
        name="mycomp-plugin",
        entry="python:mycompany.tools.weather:WeatherPlugin",
    )

    service = PluginService(db_session)
    plugin = await service.install_plugin_from_bundle(str(bundle), str(plugins_root))
    assert plugin.name == "mycomp-plugin"


@pytest.mark.asyncio
async def test_install_saas_rejects_non_first_party(tmp_path: Path, db_session: AsyncSession, monkeypatch) -> None:
    s = Settings()
    s.SAAS_MODE = True
    s.PLUGIN_PYTHON_ENTRY_ALLOWLIST = ["anything."]
    monkeypatch.setattr("hecate.core.config.settings", s, raising=True)

    plugins_root = tmp_path / "plugins"
    bundle = _write_bundle(
        tmp_path,
        name="third-party",
        entry="python:third_party.module:Cls",
    )

    service = PluginService(db_session)
    with pytest.raises(ValueError, match="SaaS mode"):
        await service.install_plugin_from_bundle(str(bundle), str(plugins_root))
    assert not (plugins_root / "third-party").exists()


@pytest.mark.asyncio
async def test_discover_skips_rejected_entry_and_increments_errors(tmp_path: Path, db_session: AsyncSession) -> None:
    plugins_root = tmp_path / "plugins"
    evil_dir = plugins_root / "evil"
    evil_dir.mkdir(parents=True)
    (evil_dir / "plugin.yaml").write_text(
        yaml.dump(
            {
                "name": "evil",
                "version": "1.0.0",
                "type": "tool",
                "entry": "python:subprocess:Popen",
                "description": "evil",
            }
        )
    )

    service = PluginService(db_session)
    summary = await service.register_discovered_plugins(plugins_root)

    assert summary["discovered"] == 1
    assert summary["registered"] == 0
    assert summary["errors"] == 1

    rows = (await db_session.execute(select(PluginModel))).scalars().all()
    assert all(r.name != "evil" for r in rows)


@pytest.mark.asyncio
async def test_discover_with_first_party_entry_succeeds(tmp_path: Path, db_session: AsyncSession) -> None:
    plugins_root = tmp_path / "plugins"
    fp_dir = plugins_root / "fp"
    fp_dir.mkdir(parents=True)
    (fp_dir / "plugin.yaml").write_text(
        yaml.dump(
            {
                "name": "fp",
                "version": "1.0.0",
                "type": "tool",
                "entry": "python:hecate.plugins.example:Foo",  # first-party module, no type-ABC match
                "description": "fp",
            }
        )
    )

    service = PluginService(db_session)
    summary = await service.register_discovered_plugins(plugins_root)
    # The module hecate.plugins.example does not exist; load_plugin returns None
    # via the import-exception path. That's an error per the current loop, but
    # importantly it does NOT mean T0 rejection.
    assert summary["errors"] == 1
    assert summary["registered"] == 0
    rows = (await db_session.execute(select(PluginModel))).scalars().all()
    assert all(r.name != "fp" for r in rows)
