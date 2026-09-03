"""PluginService — manages plugin lifecycle and configuration."""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.plugin.agent_plugins import (
    AgentPluginValidationError,
    ComponentInventory,
    McpServerSpec,
    ScanResult,
    check_size_caps,
    compute_tree_digest,
    detect_package_kind,
    discover_skills,
    materialize_from_dir,
    materialize_from_git,
    materialize_from_zip,
    parse_skill_candidate,
    read_manifest,
    read_mcp_json,
    relocate_snapshot,
    staging_dir,
    tree_size_bytes,
    validate_mcp_json,
    validate_plugin_json,
)
from hecate.core.plugin.config import validate_config
from hecate.core.plugin.loader import (
    PythonEntryPolicy,
    check_python_entry,
    discover_plugins,
    load_manifest,
    load_plugin,
    validate_compatibility,
)
from hecate.core.plugin.manifest import PluginManifest
from hecate.models.plugin import PluginModel
from hecate.models.security_finding import SecurityFindingModel
from hecate.models.skill import SkillModel

logger = logging.getLogger(__name__)

AGENT_PLUGIN_TYPE = "agent-plugin"
AGENT_PLUGINS_SUBDIR = "agent-plugins"


class FeatureDisabledError(ValueError):
    """Raised when the Agent Plugins ingestion switch is off."""


class ScanBlockedError(ValueError):
    """Raised when content scanning blocks an install or enable (fail-closed).

    Carries the scan findings so API layers can return them to the caller.
    """

    def __init__(self, message: str, findings: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.findings = findings or []


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
        if plugin.type == AGENT_PLUGIN_TYPE:
            await self._rescan_on_enable(plugin)
        plugin.status = "enabled"
        await self._db.flush()

        # Register MCP server if plugin entry starts with mcp://
        if plugin.entry and plugin.entry.startswith("mcp://"):
            from hecate.api.management.mcp import get_mcp_manager

            manager = get_mcp_manager()
            endpoint = plugin.entry.removeprefix("mcp://")
            manager.register_server(
                name=plugin.name,
                endpoint=endpoint,
                transport="http",
                workspace_id=str(plugin.workspace_id) if plugin.workspace_id else None,
            )
        elif plugin.type == AGENT_PLUGIN_TYPE:
            await self._project_agent_plugin_mcp(plugin, register=True)

        return plugin

    async def disable_plugin(self, plugin_id: uuid.UUID) -> PluginModel:
        plugin = await self.get_plugin(plugin_id)
        if plugin is None:
            msg = f"Plugin {plugin_id} not found"
            raise ValueError(msg)
        plugin.status = "disabled"
        await self._db.flush()

        # Unregister MCP server if plugin entry starts with mcp://
        if plugin.entry and plugin.entry.startswith("mcp://"):
            from hecate.api.management.mcp import get_mcp_manager

            manager = get_mcp_manager()
            manager.unregister_server(plugin.name)
        elif plugin.type == AGENT_PLUGIN_TYPE:
            await self._project_agent_plugin_mcp(plugin, register=False)

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
        ``errors`` counts. Plugins whose ``python:`` entry is denied by the
        T0 trust gate are counted as errors and skipped without persisting.
        """
        from hecate.core.config import settings

        plugins_dir = Path(plugins_dir)
        manifest_paths = discover_plugins(plugins_dir)
        discovered = len(manifest_paths)
        registered = 0
        errors = 0
        policy = PythonEntryPolicy.from_settings(settings)

        for manifest_path in manifest_paths:
            try:
                manifest = load_manifest(manifest_path)
                validate_compatibility(manifest)
                plugin_instance = load_plugin(manifest, policy)
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
        """Install a .hecate-plugin bundle.

        If the bundle declares a ``python:`` entry that fails the T0 trust
        gate (ADR-029), the just-extracted plugin directory is removed and a
        ValueError is raised identifying the gate and the remediation.
        """
        from hecate.core.config import settings
        from hecate.core.plugin.installer import install_plugin as _install

        plugins_root = Path(plugins_dir)
        bundle = Path(bundle_path)
        plugin_name = _install(bundle, plugins_root)
        manifest_path = plugins_root / plugin_name / "plugin.yaml"
        manifest = load_manifest(manifest_path)

        policy = PythonEntryPolicy.from_settings(settings)
        rejection = check_python_entry(manifest.entry, policy)
        if rejection is not None:
            shutil.rmtree(plugins_root / plugin_name, ignore_errors=True)
            raise ValueError(rejection)

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

        from hecate.core.plugin.installer import uninstall_plugin as _uninstall

        _uninstall(plugin.name, Path(plugins_dir))
        plugin.deleted_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
        await self._db.flush()

    # ------------------------------------------------------------------
    # Agent Plugins 1.0 ingestion (feature 5.5c)
    # ------------------------------------------------------------------

    def _agent_plugins_root(self, plugins_dir: str | Path) -> Path:
        return Path(plugins_dir) / AGENT_PLUGINS_SUBDIR

    async def install_agent_plugin(
        self,
        source_type: str,
        location: str,
        plugins_dir: str | Path,
        ref: str | None = None,
        workspace_id: uuid.UUID | None = None,
        installer: str | None = None,
        max_package_mb: int | None = None,
        max_workspace_mb: int | None = None,
        ingestion_enabled: bool | None = None,
        platform_installers: list[str] | None = None,
        saas_mode: bool | None = None,
    ) -> PluginModel:
        """Install an Agent Plugins 1.0 package from dir/git/zip source.

        Runs the full pipeline: master switch → materialize → closed-manifest
        validation → path containment → size caps → component discovery →
        trust dispatch → content scan (fail-closed verdict) → transactional
        persistence. Component-level failures skip-and-continue; package-level
        failures raise and leave no trace (staging directory removed).
        """
        from hecate.core.config import settings

        if ingestion_enabled is None:
            ingestion_enabled = settings.AGENT_PLUGINS_INGESTION_ENABLED
        if not ingestion_enabled:
            msg = "Agent Plugins ingestion is disabled (AGENT_PLUGINS_INGESTION_ENABLED)"
            raise FeatureDisabledError(msg)
        if platform_installers is None:
            platform_installers = settings.PLATFORM_PLUGIN_INSTALLERS
        if saas_mode is None:
            saas_mode = settings.SAAS_MODE
        if max_package_mb is None:
            max_package_mb = settings.AGENT_PLUGIN_MAX_PACKAGE_MB
        if max_workspace_mb is None:
            max_workspace_mb = settings.AGENT_PLUGIN_MAX_WORKSPACE_MB

        root = self._agent_plugins_root(plugins_dir)
        staging = staging_dir(root, "pending")

        try:
            # --- Materialize (source-specific) ---
            if source_type == "dir":
                descriptor = materialize_from_dir(Path(location), staging)
                package_root = staging
            elif source_type == "zip":
                descriptor = materialize_from_zip(Path(location), staging)
                package_root = staging
            elif source_type == "git":
                descriptor = materialize_from_git(location, staging, ref=ref)
                from hecate.core.plugin.agent_plugins import _locate_package_root

                package_root = _locate_package_root(staging)
            else:  # pragma: no cover - guarded by API schema
                msg = f"Unsupported source type {source_type!r}"
                raise AgentPluginValidationError(msg)

            # --- Classify + validate manifest ---
            kind = detect_package_kind(package_root)
            warnings: list[str] = []
            if kind == "agent-plugin":
                result = validate_plugin_json(read_manifest(package_root))
                warnings = list(result.warnings)
                name = result.manifest["name"]
                schema_version = result.schema_version
                manifest_json = dict(result.manifest)
            else:
                # Virtual package: identity synthesized from directory name.
                name = package_root.name
                schema_version = "1.0.0"
                manifest_json = {"name": name, "virtual": True}

            if descriptor.type != "git":
                # git materialization already computed the digest; dir/zip do it here
                descriptor.content_digest = compute_tree_digest(package_root)

            # --- Size caps (workspace aggregate) ---
            workspace_usage = await self._workspace_agent_plugin_usage(plugins_dir, workspace_id)
            check_size_caps(
                package_root,
                max_package_bytes=max_package_mb * 1024 * 1024,
                workspace_usage_bytes=workspace_usage,
                max_workspace_bytes=max_workspace_mb * 1024 * 1024,
            )

            # --- Reinstall / collision policy (design D9) ---
            existing = await self._find_agent_plugin(name, workspace_id)
            if existing is not None:
                existing_origin = existing.origin or ""
                same_origin = existing_origin.startswith(f"{descriptor.type}:{descriptor.location}")
                if not same_origin:
                    msg = (
                        f"Package name {name!r} already installed from a different "
                        f"origin ({existing_origin.split(':')[1] if ':' in existing_origin else existing_origin!r})"
                    )
                    raise AgentPluginValidationError(msg)

            # --- Component discovery ---
            inventory = ComponentInventory()
            discovered = discover_skills(package_root)
            candidates = []
            for d in discovered:
                try:
                    candidates.append((d, parse_skill_candidate(d)))
                except ValueError as e:
                    warnings.append(f"skill {d.dir_name!r}: {e}")
                    inventory.add_skill(d.dir_name, "skipped", str(e))

            # Cross-origin collision: plugin skill name vs existing non-plugin skill
            if candidates:
                names = [c.name for _, c in candidates]
                collisions = await self._existing_skill_names(
                    names, workspace_id, exclude_plugin_id=existing.id if existing else None
                )
                if collisions:
                    msg = f"Skill name collision with existing skills: {sorted(collisions)}"
                    raise AgentPluginValidationError(msg)

            # --- mcp.json translation ---
            mcp_outcome = None
            mcp_specs: list[McpServerSpec] = []
            if kind == "agent-plugin":
                mcp_data = read_mcp_json(package_root)
                if mcp_data is not None:
                    mcp_outcome = validate_mcp_json(mcp_data, schema_version)
                    mcp_specs = mcp_outcome.servers

            # --- Trust dispatch (design D5) ---
            stdio_allowed = (
                workspace_id is None and installer is not None and installer in platform_installers and not saas_mode
            )
            for skill_dir, _candidate in candidates:
                inventory.add_skill(skill_dir.dir_name, "imported")
            for spec in mcp_specs:
                if spec.transport == "http":
                    inventory.add_mcp_server(
                        spec.server_name,
                        "registered",
                    )
                elif spec.transport == "stdio":
                    if stdio_allowed:
                        inventory.add_mcp_server(spec.server_name, "registered")
                    else:
                        reason = (
                            "SaaS mode: stdio entries skipped"
                            if saas_mode
                            else "stdio requires platform installer (config allowlist)"
                        )
                        inventory.add_mcp_server(spec.server_name, "skipped", reason)
            if mcp_outcome is not None:
                warnings.extend(mcp_outcome.warnings)
                if mcp_outcome.disabled_reason:
                    warnings.append(mcp_outcome.disabled_reason)

            # --- Content scan (5.13a): fail-closed verdict enforcement ---
            scan, _suppressed = await self._run_install_scan(package_root, descriptor.content_digest)
            if scan.verdict == "block":
                await self._project_blocked_attempt(
                    name=name,
                    origin=descriptor.to_origin(),
                    workspace_id=workspace_id,
                    content_hash=descriptor.content_digest,
                    scan=scan,
                )
                msg = f"Content scan verdict: block ({len(scan.findings)} findings)"
                raise ScanBlockedError(msg, scan.findings)

            # --- Persist (single transaction; upsert semantics) ---
            origin_str = descriptor.to_origin()
            if existing is not None:
                plugin = existing
                await self._delete_plugin_skills(plugin.id)
                plugin.version = str(manifest_json.get("version", "0.0.0"))
                plugin.status = "installed"
                plugin.origin = origin_str
                plugin.content_hash = descriptor.content_digest
                plugin.scan_result = None
            else:
                plugin = PluginModel(
                    name=name,
                    type=AGENT_PLUGIN_TYPE,
                    version=str(manifest_json.get("version", "0.0.0")),
                    status="installed",
                    entry="",
                    workspace_id=workspace_id,
                    origin=origin_str,
                    content_hash=descriptor.content_digest,
                    scan_result=None,
                )
                self._db.add(plugin)
            await self._db.flush()

            plugin.manifest_ = {
                **manifest_json,
                "kind": kind,
                "components": {
                    "skills": inventory.skills,
                    "mcp_servers": self._mcp_inventory_entries(mcp_specs, stdio_allowed, mcp_outcome),
                },
                "warnings": warnings,
                "virtual": kind == "virtual",
            }
            plugin.scan_result = self._scan_result_dict(scan, _suppressed)
            await self._db.flush()

            # --- Import skills ---
            skill_workspace = workspace_id or uuid.UUID(int=0)
            for _skill_dir, candidate in candidates:
                allowed = candidate.extra.get("allowed-tools") or []
                metadata = dict(candidate.extra.get("metadata") or {})
                if "license" in candidate.extra:
                    metadata["license"] = candidate.extra["license"]
                if "compatibility" in candidate.extra:
                    metadata["compatibility"] = candidate.extra["compatibility"]
                skill = SkillModel(
                    workspace_id=skill_workspace,
                    name=candidate.name,
                    description=candidate.description,
                    source="plugin",
                    instructions=candidate.instructions,
                    allowed_tools=list(allowed) if isinstance(allowed, list) else [allowed],
                    metadata_=metadata,
                    origin=origin_str,
                    plugin_id=plugin.id,
                )
                self._db.add(skill)
            await self._db.flush()

            # --- Ops Center projection (5.13a) ---
            await self._project_for_plugin(plugin, scan, phase="install")

            # --- Commit snapshot to its managed home (dir+row pairing) ---
            final_dir = root / name
            if final_dir.exists():
                shutil.rmtree(final_dir)
            relocate_snapshot(package_root, final_dir)
            if staging != package_root and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

            logger.info(
                "Installed Agent Plugins package %r (%d skills, %d mcp servers)",
                name,
                len(candidates),
                len(inventory.mcp_servers),
            )
            return plugin
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _mcp_inventory_entries(
        self,
        specs: list[McpServerSpec],
        stdio_allowed: bool,  # noqa: FBT001
        outcome: Any,
    ) -> list[dict[str, Any]]:
        """Build MCP component entries carrying the data needed for
        enable-time projection (registration name, endpoint, transport)."""
        entries: list[dict[str, Any]] = []
        for spec in specs:
            entry: dict[str, Any] = {
                "name": spec.server_name,
                "transport": spec.transport,
                "endpoint": spec.endpoint,
            }
            if spec.transport == "http":
                entry["status"] = "registered"
                entry["headers"] = spec.headers
            elif stdio_allowed:
                entry["status"] = "registered"
                entry.update({"args": spec.args, "env": spec.env, "cwd": spec.cwd})
            else:
                entry["status"] = "skipped"
                entry["reason"] = "stdio requires platform installer"
            entries.append(entry)
        if outcome is not None and getattr(outcome, "disabled_reason", None):
            entries.append({"name": "*", "status": "disabled", "reason": outcome.disabled_reason})
        return entries

    async def _find_agent_plugin(self, name: str, workspace_id: uuid.UUID | None) -> PluginModel | None:
        stmt = select(PluginModel).where(
            PluginModel.name == name,
            PluginModel.type == AGENT_PLUGIN_TYPE,
            PluginModel.deleted_at.is_(None),
        )
        if workspace_id is None:
            stmt = stmt.where(PluginModel.workspace_id.is_(None))
        else:
            stmt = stmt.where(PluginModel.workspace_id == workspace_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def _existing_skill_names(
        self,
        names: list[str],
        workspace_id: uuid.UUID | None,
        exclude_plugin_id: uuid.UUID | None,
    ) -> list[str]:
        """Non-plugin skills (or other plugins' skills) colliding on name."""
        stmt = select(SkillModel.name).where(
            SkillModel.name.in_(names),
            SkillModel.deleted_at.is_(None),
        )
        if workspace_id is None:
            stmt = stmt.where(SkillModel.workspace_id == uuid.UUID(int=0))
        else:
            stmt = stmt.where(SkillModel.workspace_id.in_([workspace_id, uuid.UUID(int=0)]))
        if exclude_plugin_id is not None:
            stmt = stmt.where(SkillModel.plugin_id.is_not(exclude_plugin_id))
        else:
            stmt = stmt.where(SkillModel.plugin_id.is_(None))
        result = await self._db.execute(stmt)
        return [r[0] for r in result.all()]

    async def _delete_plugin_skills(self, plugin_id: uuid.UUID) -> None:
        stmt = select(SkillModel).where(SkillModel.plugin_id == plugin_id)
        result = await self._db.execute(stmt)
        for skill in result.scalars().all():
            await self._db.delete(skill)
        await self._db.flush()

    async def _workspace_agent_plugin_usage(self, plugins_dir: str | Path, workspace_id: uuid.UUID | None) -> int:
        """Aggregate on-disk size of installed agent-plugin packages in scope."""
        root = self._agent_plugins_root(plugins_dir)
        stmt = select(PluginModel.name).where(
            PluginModel.type == AGENT_PLUGIN_TYPE,
            PluginModel.deleted_at.is_(None),
        )
        if workspace_id is None:
            stmt = stmt.where(PluginModel.workspace_id.is_(None))
        else:
            stmt = stmt.where(PluginModel.workspace_id == workspace_id)
        result = await self._db.execute(stmt)
        usage = 0
        for (name,) in result.all():
            pkg_dir = root / name
            if pkg_dir.is_dir():
                usage += tree_size_bytes(pkg_dir)
        return usage

    async def uninstall_agent_plugin(self, plugin_id: uuid.UUID, plugins_dir: str | Path) -> None:
        """Uninstall an agent-plugin package: delete skills, unregister MCP,
        soft-delete the row, remove the directory — one transaction; a failure
        at any step rolls the database changes back to the savepoint."""
        plugin = await self.get_plugin(plugin_id)
        if plugin is None or plugin.type != AGENT_PLUGIN_TYPE:
            msg = f"Agent plugin {plugin_id} not found"
            raise ValueError(msg)

        async with self._db.begin_nested():
            await self._delete_plugin_skills(plugin.id)
            await self._project_agent_plugin_mcp(plugin, register=False)
            import datetime as _dt

            plugin.deleted_at = _dt.datetime.now(_dt.UTC)
            await self._db.flush()

            pkg_dir = self._agent_plugins_root(plugins_dir) / plugin.name
            if pkg_dir.exists():
                shutil.rmtree(pkg_dir)  # failure rolls back to the savepoint

    async def _project_agent_plugin_mcp(
        self,
        plugin: PluginModel,
        register: bool,  # noqa: FBT001
    ) -> None:
        """Register/unregister manifest-carried MCP servers (design D3).

        http/sse entries register directly; stdio entries register through
        the sandboxed docker wrapper (fail-closed on policy failure).
        """
        components = (plugin.manifest_ or {}).get("components", {})
        servers = components.get("mcp_servers", [])
        active = [s for s in servers if s.get("status") == "registered"]
        if not active:
            return

        from hecate.api.management.mcp import get_mcp_manager

        manager = get_mcp_manager()
        ws = str(plugin.workspace_id) if plugin.workspace_id else None
        for server in active:
            full_name = f"{plugin.name}__{server['name']}"
            if not register:
                manager.unregister_server(full_name)
                continue
            if server.get("transport") == "http":
                manager.register_server(
                    name=full_name,
                    endpoint=server["endpoint"],
                    transport="http",
                    workspace_id=ws,
                    headers=server.get("headers") or None,
                )
            elif server.get("transport") == "stdio":
                endpoint, args = self._stdio_sandbox_argv(plugin.name, server)
                manager.register_server(
                    name=full_name,
                    endpoint=endpoint,
                    transport="stdio",
                    workspace_id=ws,
                    args=args,
                )

    @staticmethod
    def _stdio_sandbox_argv(plugin_name: str, server: dict[str, Any]) -> tuple[str, list[str]]:
        """Build the fail-closed sandbox wrapper for a stdio entry."""
        from hecate.core.config import settings
        from hecate.core.plugin.stdio_sandbox import build_sandbox_command

        try:
            return build_sandbox_command(
                plugin_name,
                server,
                settings.PLUGINS_DIR,
                settings.AGENT_PLUGIN_RUNNER_IMAGE,
                settings.AGENT_PLUGIN_STDIO_COMMAND_ALLOWLIST,
            )
        except ValueError as e:
            # fail-closed: policy failure denies execution (Codex 0.147 pattern)
            logger.error("stdio sandbox denied for %s/%s: %s", plugin_name, server.get("name"), e)
            raise

    async def replay_agent_plugin_mcp(self) -> int:
        """Startup replay: re-register MCP servers for enabled packages."""
        stmt = select(PluginModel).where(
            PluginModel.type == AGENT_PLUGIN_TYPE,
            PluginModel.status == "enabled",
            PluginModel.deleted_at.is_(None),
        )
        result = await self._db.execute(stmt)
        count = 0
        for plugin in result.scalars().all():
            await self._project_agent_plugin_mcp(plugin, register=True)
            count += 1
        return count

    async def cleanup_orphan_agent_plugin_dirs(self, plugins_dir: str | Path) -> int:
        """Remove managed package directories without a matching row."""
        root = self._agent_plugins_root(plugins_dir)
        if not root.is_dir():
            return 0
        stmt = select(PluginModel.name).where(
            PluginModel.type == AGENT_PLUGIN_TYPE,
            PluginModel.deleted_at.is_(None),
        )
        result = await self._db.execute(stmt)
        known = {name for (name,) in result.all()}
        removed = 0
        for child in root.iterdir():
            if child.is_dir() and not child.name.startswith(".") and child.name not in known:
                shutil.rmtree(child)
                removed += 1
                logger.info("Removed orphan agent-plugin directory %s", child)
        return removed

    # ------------------------------------------------------------------
    # Content scanning (feature 5.13a)
    # ------------------------------------------------------------------

    @staticmethod
    def _scan_result_dict(scan: ScanResult, suppressed: int) -> dict[str, Any]:
        return {
            "verdict": scan.verdict,
            "findings": scan.findings,
            "scanner_version": scan.scanner_version,
            "scanned_at": datetime.now(UTC).isoformat(),
            "acked_suppressed": suppressed,
        }

    async def _run_install_scan(self, package_root: Path, content_hash: str | None) -> tuple[ScanResult, int]:
        """Run the rule-engine scan stage; any scanner failure rejects the install."""
        from hecate.core.plugin.content_scanner import RuleEngineScanStage

        try:
            scan = RuleEngineScanStage().scan(package_root)
        except Exception as e:
            msg = f"Content scan failed (fail-closed): {e}"
            raise ScanBlockedError(msg) from e
        return await self._suppress_acked(scan, content_hash)

    async def _suppress_acked(self, scan: ScanResult, content_hash: str | None) -> tuple[ScanResult, int]:
        """Drop acknowledged warn-or-lower findings for identical content.

        Acknowledgments key on (content hash, rule id); findings at or above
        the blocking threshold are never suppressible.
        """
        if not scan.findings or not content_hash:
            return scan, 0
        from hecate.core.config import settings
        from hecate.core.plugin.content_scanner import SEVERITY_ORDER, compute_verdict

        threshold = SEVERITY_ORDER.get(settings.AGENT_PLUGIN_SCAN_BLOCK_AT, SEVERITY_ORDER["high"])
        rule_ids = {f["rule_id"] for f in scan.findings}
        acked = await self._acked_rule_ids(content_hash, rule_ids)
        if not acked:
            return scan, 0
        kept: list[dict[str, Any]] = []
        suppressed = 0
        for f in scan.findings:
            if f["rule_id"] in acked and SEVERITY_ORDER.get(f["severity"], 1) < threshold:
                suppressed += 1
            else:
                kept.append(f)
        if suppressed:
            scan.findings = kept
            scan.verdict = compute_verdict(kept, settings.AGENT_PLUGIN_SCAN_BLOCK_AT)
        return scan, suppressed

    async def _acked_rule_ids(self, content_hash: str, rule_ids: set[str]) -> set[str]:
        stmt = select(SecurityFindingModel).where(SecurityFindingModel.rule_name.in_(rule_ids))
        rows = (await self._db.execute(stmt)).scalars().all()
        return {
            r.rule_name
            for r in rows
            if (r.source_event or {}).get("content_hash") == content_hash
            and (r.metadata_ or {}).get("acknowledged") is True
        }

    def _finding_row(
        self,
        *,
        name: str,
        origin: str | None,
        workspace_id: uuid.UUID | None,
        content_hash: str | None,
        scan: ScanResult,
        phase: str,
        finding: dict[str, Any],
    ) -> SecurityFindingModel:
        return SecurityFindingModel(
            org_id=workspace_id or uuid.UUID(int=0),
            workspace_id=workspace_id,
            user_id=None,
            rule_name=finding["rule_id"][:100],
            severity=finding["severity"],
            message=(
                f"{finding.get('description') or finding['category']} in {finding['file']}"
                f" [{finding.get('transform', 'none')}]"
            ),
            source_event={
                "phase": phase,
                "plugin": name,
                "origin": origin,
                "content_hash": content_hash,
                "scanner_version": scan.scanner_version,
            },
            metadata_={"finding": finding},
        )

    async def _project_for_plugin(self, plugin: PluginModel, scan: ScanResult, phase: str) -> int:
        """Project scan findings for an installed package.

        Idempotent per (plugin name, content hash, scanner version, rule) —
        rescans with an unchanged dedup key create no duplicate rows.
        """
        if not scan.findings:
            return 0
        rules = {f["rule_id"] for f in scan.findings}
        stmt = select(SecurityFindingModel).where(SecurityFindingModel.rule_name.in_(rules))
        rows = (await self._db.execute(stmt)).scalars().all()
        existing: set[str] = set()
        for r in rows:
            se = r.source_event or {}
            if (
                se.get("plugin") == plugin.name
                and se.get("content_hash") == plugin.content_hash
                and se.get("scanner_version") == scan.scanner_version
            ):
                existing.add(r.rule_name)
        created = 0
        for f in scan.findings:
            if f["rule_id"] in existing:
                continue
            self._db.add(
                self._finding_row(
                    name=plugin.name,
                    origin=plugin.origin,
                    workspace_id=plugin.workspace_id,
                    content_hash=plugin.content_hash,
                    scan=scan,
                    phase=phase,
                    finding=f,
                )
            )
            created += 1
        if created:
            await self._db.flush()
        return created

    async def _project_blocked_attempt(
        self,
        *,
        name: str,
        origin: str,
        workspace_id: uuid.UUID | None,
        content_hash: str | None,
        scan: ScanResult,
    ) -> int:
        """Record a blocked install attempt; idempotent per (name, origin, rule)."""
        if not scan.findings:
            return 0
        rules = {f["rule_id"] for f in scan.findings}
        stmt = select(SecurityFindingModel).where(SecurityFindingModel.rule_name.in_(rules))
        rows = (await self._db.execute(stmt)).scalars().all()
        existing: set[str] = set()
        for r in rows:
            se = r.source_event or {}
            if se.get("plugin") == name and se.get("origin") == origin and se.get("phase") == "install-blocked":
                existing.add(r.rule_name)
        created = 0
        for f in scan.findings:
            if f["rule_id"] in existing:
                continue
            self._db.add(
                self._finding_row(
                    name=name,
                    origin=origin,
                    workspace_id=workspace_id,
                    content_hash=content_hash,
                    scan=scan,
                    phase="install-blocked",
                    finding=f,
                )
            )
            created += 1
        if created:
            await self._db.flush()
        return created

    async def _rescan_on_enable(self, plugin: PluginModel) -> None:
        """Re-scan on enable when the stored result is stale (5.13a D7).

        Covers rule evolution (scanner version drift) and backfills packages
        installed during the 5.5c no-op era (null scan result). A block
        verdict refuses the enable.
        """
        from hecate.core.plugin.content_scanner import SCANNER_VERSION, RuleEngineScanStage

        stored = plugin.scan_result or {}
        if stored.get("scanner_version") == SCANNER_VERSION:
            return
        from hecate.core.config import settings

        pkg_dir = self._agent_plugins_root(settings.PLUGINS_DIR) / plugin.name
        if not pkg_dir.is_dir():
            logger.warning("Cannot rescan %s: package directory missing", plugin.name)
            return
        try:
            scan = RuleEngineScanStage().scan(pkg_dir)
        except Exception as e:
            msg = f"Content rescan failed (fail-closed): {e}"
            raise ScanBlockedError(msg) from e
        scan, suppressed = await self._suppress_acked(scan, plugin.content_hash)
        if scan.verdict == "block":
            msg = f"Content rescan verdict: block ({len(scan.findings)} findings)"
            raise ScanBlockedError(msg, scan.findings)
        plugin.scan_result = self._scan_result_dict(scan, suppressed)
        await self._db.flush()
        await self._project_for_plugin(plugin, scan, phase="enable")
