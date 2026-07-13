"""PluginLoader — discovers and loads plugins from the filesystem and MCP endpoints."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

import yaml

from hecate.plugin.manifest import PluginManifest

logger = logging.getLogger(__name__)

_PLATFORM_VERSION = "0.8.0"


def discover_plugins(plugins_dir: Path) -> list[Path]:
    """Scan *plugins_dir* for subdirectories containing ``plugin.yaml``.

    Returns a list of ``plugin.yaml`` paths found. Directories without a
    ``plugin.yaml`` are silently skipped.
    """
    if not plugins_dir.is_dir():
        logger.debug("Plugins directory %s does not exist", plugins_dir)
        return []

    results: list[Path] = []
    for child in sorted(plugins_dir.iterdir()):
        manifest_path = child / "plugin.yaml"
        if child.is_dir() and manifest_path.is_file():
            results.append(manifest_path)
        else:
            logger.debug("Skipping %s — no plugin.yaml", child)
    return results


def load_manifest(manifest_path: Path) -> PluginManifest:
    """Parse a ``plugin.yaml`` into a :class:`PluginManifest`.

    Raises ``ValueError`` when required fields are missing and
    ``yaml.YAMLError`` on invalid YAML syntax.
    """
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"plugin.yaml must be a mapping, got {type(raw).__name__}"
        raise ValueError(msg)

    required = ("name", "version", "type", "entry")
    missing = [f for f in required if not raw.get(f)]
    if missing:
        msg = f"plugin.yaml missing required fields: {', '.join(missing)}"
        raise ValueError(msg)

    return PluginManifest(
        type=raw["type"],
        name=raw["name"],
        version=raw["version"],
        api_version=raw.get("api_version", ""),
        min_platform_version=raw.get("min_platform_version", ""),
        description=raw.get("description", ""),
        entry=raw["entry"],
        permissions=tuple(raw.get("permissions", ())),
        config_schema=raw.get("config_schema"),
    )


def validate_compatibility(manifest: PluginManifest) -> None:
    """Reject plugins whose ``min_platform_version`` exceeds the current version.

    Raises ``ValueError`` on incompatibility.
    """
    if not manifest.min_platform_version:
        return
    from packaging.version import Version

    if Version(manifest.min_platform_version) > Version(_PLATFORM_VERSION):
        msg = (
            f"Plugin {manifest.name} requires platform >= "
            f"{manifest.min_platform_version}, current is {_PLATFORM_VERSION}"
        )
        raise ValueError(msg)


def _load_python(entry: str) -> Any:
    """Load a Python plugin from an ``entry`` string.

    Format: ``python:module:ClassName`` — imports *module*, instantiates
    *ClassName* with no arguments, and returns the instance.

    Raises ``ImportError`` or ``AttributeError`` on failure.
    """
    parts = entry.split(":", 2)
    if len(parts) != 3 or parts[0] != "python":
        msg = f"Invalid python entry format: {entry!r} (expected python:module:Class)"
        raise ValueError(msg)

    _, module_path, class_name = parts
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls()


def _load_mcp(entry: str) -> dict[str, str]:
    """Parse an MCP entry string.

    Format: ``mcp://host:port`` — returns connection metadata.
    Actual MCP Client integration is done by the consumer.
    """
    if not entry.startswith("mcp://"):
        msg = f"Invalid mcp entry format: {entry!r} (expected mcp://endpoint)"
        raise ValueError(msg)
    return {"endpoint": entry}


def _validate_type(manifest: PluginManifest, plugin_instance: Any) -> list[str]:
    """Validate that *plugin_instance* implements the correct ABC for its type."""
    from hecate.plugin.validation import validate_api_surface

    return validate_api_surface(manifest.type, plugin_instance)


def load_plugin(manifest: PluginManifest) -> Any:
    """Dispatch to the appropriate loader based on ``manifest.entry`` prefix.

    Returns the loaded plugin instance (for ``python:``) or connection info
    dict (for ``mcp://``). Returns ``None`` on failure without crashing.
    Also validates the plugin's API surface against its declared type.
    """
    try:
        if manifest.entry.startswith("python:"):
            instance = _load_python(manifest.entry)
            errors = _validate_type(manifest, instance)
            if errors:
                for err in errors:
                    logger.error("Plugin %s type validation failed: %s", manifest.name, err)
                return None
            return instance
        if manifest.entry.startswith("mcp://"):
            return _load_mcp(manifest.entry)
        msg = f"Unknown entry prefix in {manifest.entry!r}"
        raise ValueError(msg)
    except Exception:
        logger.exception("Failed to load plugin %s", manifest.name)
        return None
