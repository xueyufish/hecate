"""PluginService — manages plugin lifecycle and configuration."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.plugin import PluginModel
from hecate.plugin.config import validate_config
from hecate.plugin.loader import (
    discover_plugins,
    load_manifest,
    load_plugin,
    validate_compatibility,
)
from hecate.plugin.manifest import PluginManifest

logger = logging.getLogger(__name__)


class PluginService:
    """Service for plugin lifecycle management.

    Args:
        db: Async SQLAlchemy session.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_plugins(self, workspace_id: uuid.UUID | None = None) -> list[PluginModel]:
        """Return plugins visible to *workspace_id*.

        Platform-level plugins (``workspace_id IS NULL``) are always included.
        Workspace-level plugins are included only when *workspace_id* matches.
        """
        stmt = select(PluginModel).where(PluginModel.deleted_at.is_(None))
        if workspace_id is not None:
            stmt = stmt.where((PluginModel.workspace_id.is_(None)) | (PluginModel.workspace_id == workspace_id))
        else:
            stmt = stmt.where(PluginModel.workspace_id.is_(None))
        result = await self._db.execute(stmt.order_by(PluginModel.name))
        return list(result.scalars().all())

    async def get_plugin(self, plugin_id: uuid.UUID) -> PluginModel | None:
        stmt = select(PluginModel).where(
            PluginModel.id == plugin_id,
            PluginModel.deleted_at.is_(None),
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def enable_plugin(self, plugin_id: uuid.UUID) -> PluginModel:
        plugin = await self.get_plugin(plugin_id)
        if plugin is None:
            msg = f"Plugin {plugin_id} not found"
            raise ValueError(msg)
        plugin.status = "enabled"
        await self._db.flush()
        return plugin

    async def disable_plugin(self, plugin_id: uuid.UUID) -> PluginModel:
        plugin = await self.get_plugin(plugin_id)
        if plugin is None:
            msg = f"Plugin {plugin_id} not found"
            raise ValueError(msg)
        plugin.status = "disabled"
        await self._db.flush()
        return plugin

    async def update_config(self, plugin_id: uuid.UUID, config: dict[str, Any]) -> PluginModel:
        plugin = await self.get_plugin(plugin_id)
        if plugin is None:
            msg = f"Plugin {plugin_id} not found"
            raise ValueError(msg)

        schema = plugin.manifest_.get("config_schema")
        if schema:
            validate_config(config, schema)

        plugin.config = config
        await self._db.flush()
        return plugin

    async def register_discovered_plugins(self, plugins_dir: str | Path) -> dict[str, int]:
        """Discover and register all plugins in *plugins_dir*.

        Returns a summary dict with ``discovered``, ``registered``, and
        ``errors`` counts.
        """
        plugins_dir = Path(plugins_dir)
        manifest_paths = discover_plugins(plugins_dir)
        discovered = len(manifest_paths)
        registered = 0
        errors = 0

        for manifest_path in manifest_paths:
            try:
                manifest = load_manifest(manifest_path)
                validate_compatibility(manifest)
                plugin_instance = load_plugin(manifest)
                if plugin_instance is None:
                    errors += 1
                    continue
                await self._persist_plugin(manifest, plugin_instance)
                registered += 1
            except Exception:
                logger.exception("Failed to register plugin from %s", manifest_path)
                errors += 1

        logger.info(
            "Discovered %d plugins, %d registered, %d errors",
            discovered,
            registered,
            errors,
        )
        return {"discovered": discovered, "registered": registered, "errors": errors}

    async def _persist_plugin(
        self,
        manifest: PluginManifest,
        plugin_instance: Any,
        workspace_id: uuid.UUID | None = None,
    ) -> PluginModel:
        """Create or update a PluginModel from a manifest."""
        existing_stmt = select(PluginModel).where(
            PluginModel.name == manifest.name,
            PluginModel.workspace_id == workspace_id,
            PluginModel.deleted_at.is_(None),
        )
        result = await self._db.execute(existing_stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.version = manifest.version
            existing.type = manifest.type
            existing.entry = manifest.entry
            existing.manifest_ = {
                "type": manifest.type,
                "name": manifest.name,
                "version": manifest.version,
                "api_version": manifest.api_version,
                "min_platform_version": manifest.min_platform_version,
                "description": manifest.description,
                "entry": manifest.entry,
                "permissions": list(manifest.permissions),
                "config_schema": manifest.config_schema,
            }
            await self._db.flush()
            return existing

        model = PluginModel(
            name=manifest.name,
            type=manifest.type,
            version=manifest.version,
            status="installed",
            entry=manifest.entry,
            manifest_={
                "type": manifest.type,
                "name": manifest.name,
                "version": manifest.version,
                "api_version": manifest.api_version,
                "min_platform_version": manifest.min_platform_version,
                "description": manifest.description,
                "entry": manifest.entry,
                "permissions": list(manifest.permissions),
                "config_schema": manifest.config_schema,
            },
            config={},
            workspace_id=workspace_id,
        )
        self._db.add(model)
        await self._db.flush()
        return model

    async def install_plugin_from_bundle(self, bundle_path: str, plugins_dir: str) -> PluginModel:
        """Install a .hecate-plugin bundle."""
        from hecate.plugin.installer import install_plugin as _install

        plugin_name = _install(Path(bundle_path), Path(plugins_dir))
        manifest_path = Path(plugins_dir) / plugin_name / "plugin.yaml"
        manifest = load_manifest(manifest_path)
        return await self._persist_plugin(manifest, None)

    async def uninstall_plugin_by_id(self, plugin_id: uuid.UUID, plugins_dir: str) -> None:
        """Uninstall a plugin by ID. Rejects built-in plugins."""
        plugin = await self.get_plugin(plugin_id)
        if plugin is None:
            msg = f"Plugin {plugin_id} not found"
            raise ValueError(msg)

        if plugin.workspace_id is None and plugin.entry.startswith("python:hecate."):
            msg = "Built-in plugins cannot be uninstalled"
            raise PermissionError(msg)

        from hecate.plugin.installer import uninstall_plugin as _uninstall

        _uninstall(plugin.name, Path(plugins_dir))
        plugin.deleted_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
        await self._db.flush()
