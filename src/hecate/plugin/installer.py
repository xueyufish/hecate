"""Plugin installer — install and uninstall .hecate-plugin bundles."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import yaml

from hecate.plugin.packaging import extract_bundle, validate_bundle

logger = logging.getLogger(__name__)


def install_plugin(bundle_path: Path, plugins_dir: Path) -> str:
    """Install a .hecate-plugin bundle to *plugins_dir*.

    Extracts the bundle, installs Python dependencies if requirements.txt
    exists, and returns the plugin name.
    """
    if not validate_bundle(bundle_path):
        msg = f"Invalid bundle file: {bundle_path}"
        raise ValueError(msg)

    plugins_dir.mkdir(parents=True, exist_ok=True)

    with __import__("zipfile").ZipFile(bundle_path, "r") as zf:
        names = zf.namelist()
        manifest_entry = next((n for n in names if n.endswith("plugin.yaml")), None)
        if manifest_entry is None:
            msg = "Bundle contains no plugin.yaml"
            raise ValueError(msg)
        manifest_raw = yaml.safe_load(zf.read(manifest_entry))

    plugin_name = manifest_raw.get("name", bundle_path.stem)
    plugin_dir = plugins_dir / plugin_name

    if plugin_dir.exists():
        logger.info("Upgrading plugin '%s' — overwriting existing directory", plugin_name)
        shutil.rmtree(plugin_dir)

    plugin_dir.mkdir(parents=True)
    extract_bundle(bundle_path, plugin_dir)

    _install_dependencies(plugin_dir)

    logger.info("Installed plugin '%s' to %s", plugin_name, plugin_dir)
    return plugin_name


def uninstall_plugin(plugin_name: str, plugins_dir: Path) -> bool:
    """Remove a plugin directory from *plugins_dir*.

    Returns True if the plugin was found and removed, False otherwise.
    """
    plugin_dir = plugins_dir / plugin_name
    if not plugin_dir.exists():
        logger.warning("Plugin '%s' not found in %s", plugin_name, plugins_dir)
        return False

    shutil.rmtree(plugin_dir)
    logger.info("Uninstalled plugin '%s' from %s", plugin_name, plugins_dir)
    return True


def _install_dependencies(plugin_dir: Path) -> None:
    """Install Python dependencies from requirements.txt if present."""
    req_file = plugin_dir / "requirements.txt"
    if not req_file.is_file():
        return

    logger.info("Installing dependencies from %s", req_file)
    result = subprocess.run(  # noqa: S603
        ["/usr/bin/env", "uv", "pip", "install", "-r", str(req_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        logger.error("Failed to install dependencies for plugin: %s", result.stderr)
    else:
        logger.info("Dependencies installed successfully")
