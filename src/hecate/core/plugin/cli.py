"""hecate plugin init CLI — scaffold new plugin projects."""

from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

VALID_TYPES = [
    "tool",
    "extension",
    "trigger",
    "model",
    "channel",
    "evaluator",
    "auth_provider",
    "secret_provider",
]

_TEMPLATES: dict[str, dict[str, str]] = {
    "tool": {
        "base_class": "ToolPluginBase",
        "import_line": "from hecate.core.plugin.sdk import ToolPluginBase",
        "class_body": textwrap.dedent("""\
            class {{CLASS_NAME}}(ToolPluginBase):
                @property
                def name(self) -> str:
                    return "{{PLUGIN_NAME}}"

                @property
                def description(self) -> str:
                    return "TODO: Add description"

                async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
                    return {"result": "TODO: Implement"}
            """),
    },
    "extension": {
        "base_class": "ExtensionPluginBase",
        "import_line": "from hecate.core.plugin.sdk import ExtensionPluginBase",
        "class_body": textwrap.dedent("""\
            class {{CLASS_NAME}}(ExtensionPluginBase):
                def on_pre_tool(self, tool_name: str, args: dict[str, Any]) -> None:
                    pass
            """),
    },
    "trigger": {
        "base_class": "TriggerPluginBase",
        "import_line": "from hecate.core.plugin.sdk import TriggerPluginBase",
        "class_body": textwrap.dedent("""\
            class {{CLASS_NAME}}(TriggerPluginBase):
                trigger_type = "webhook"

                async def on_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
                    return {"status": "ok"}
            """),
    },
    "model": {
        "base_class": "ModelPluginBase",
        "import_line": "from hecate.core.plugin.sdk import ModelPluginBase",
        "class_body": textwrap.dedent("""\
            class {{CLASS_NAME}}(ModelPluginBase):
                async def invoke(self, messages: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
                    return {"content": "TODO: Implement"}

                async def embed(self, text: str) -> list[float]:
                    return [0.0]
            """),
    },
    "channel": {
        "base_class": "ChannelBase",
        "import_line": "from hecate.core.plugin.sdk import ChannelBase",
        "class_body": textwrap.dedent("""\
            class {{CLASS_NAME}}(ChannelBase):
                @property
                def name(self) -> str:
                    return "{{PLUGIN_NAME}}"

                @property
                def description(self) -> str:
                    return "TODO"

                @property
                def capabilities(self):
                    from hecate.channel.capabilities import ChannelCapabilities
                    return ChannelCapabilities()

                async def receive(self, raw: object):
                    raise NotImplementedError

                async def respond(self, message_id: str, response: object) -> None:
                    raise NotImplementedError

                async def stream(self, message_id: str, chunks: object) -> None:
                    raise NotImplementedError
            """),
    },
    "evaluator": {
        "base_class": "EvaluatorBase",
        "import_line": "from hecate.core.plugin.sdk import EvaluatorBase",
        "class_body": textwrap.dedent("""\
            class {{CLASS_NAME}}(EvaluatorBase):
                @property
                def name(self) -> str:
                    return "{{PLUGIN_NAME}}"

                @property
                def description(self) -> str:
                    return "TODO"

                async def evaluate(self, input):
                    raise NotImplementedError
            """),
    },
    "auth_provider": {
        "base_class": "AuthProvider",
        "import_line": "from hecate.core.plugin.sdk import AuthProvider",
        "class_body": textwrap.dedent("""\
            class {{CLASS_NAME}}(AuthProvider):
                @property
                def name(self) -> str:
                    return "{{PLUGIN_NAME}}"

                @property
                def description(self) -> str:
                    return "TODO"

                async def authenticate(self, token: str, db):
                    return None
            """),
    },
    "secret_provider": {
        "base_class": "SecretProvider",
        "import_line": "from hecate.core.plugin.sdk import SecretProvider",
        "class_body": textwrap.dedent("""\
            class {{CLASS_NAME}}(SecretProvider):
                async def get_secret(self, key: str) -> str | None:
                    return None

                async def set_secret(self, key: str, value: str) -> None:
                    pass
            """),
    },
}


def init_plugin(name: str, plugin_type: str, output_dir: str = ".") -> str:
    """Scaffold a new plugin project directory.

    Returns the path to the created plugin directory.
    """
    if plugin_type not in VALID_TYPES:
        msg = f"Invalid type '{plugin_type}'. Valid types: {', '.join(VALID_TYPES)}"
        raise ValueError(msg)

    plugin_dir = Path(output_dir) / name
    if plugin_dir.exists():
        msg = f"Directory already exists: {plugin_dir}"
        raise ValueError(msg)

    plugin_dir.mkdir(parents=True)

    class_name = "".join(p.capitalize() for p in name.replace("-", "_").split("_"))

    template = _TEMPLATES[plugin_type]
    class_body = template["class_body"].replace("{{CLASS_NAME}}", class_name).replace("{{PLUGIN_NAME}}", name)

    init_lines = [
        f'"""{name} — {plugin_type} plugin."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "",
        f"{template['import_line']}",
        "",
        "",
        class_body,
    ]
    init_content = "\n".join(init_lines)

    (plugin_dir / "__init__.py").write_text(init_content, encoding="utf-8")

    manifest = f"""name: {name}
version: 0.1.0
type: {plugin_type}
api_version: "1.0"
min_platform_version: "0.8.0"
description: "TODO: Add description"
entry: python:{name}:{class_name}
"""
    (plugin_dir / "plugin.yaml").write_text(manifest, encoding="utf-8")

    safe_name = name.replace("-", "_")
    test_lines = [
        f'"""Tests for {name} plugin."""',
        "",
        "from __future__ import annotations",
        "",
        "",
        f"async def test_{safe_name}_instantiable():",
        f"    from {name} import {class_name}",
        "",
        f"    plugin = {class_name}()",
        "    assert plugin is not None",
        "",
    ]
    test_content = "\n".join(test_lines)
    (plugin_dir / f"test_{name.replace('-', '_')}.py").write_text(test_content, encoding="utf-8")

    return str(plugin_dir)


def main() -> None:
    from hecate.core.plugin.dual_format import AGENT_PLUGIN_NAMESPACE

    parser = argparse.ArgumentParser(
        description=(
            "Hecate plugin CLI — Hecate-private content lives in the "
            f"{AGENT_PLUGIN_NAMESPACE}/ namespace directory inside Agent Plugins packages (5.5d)."
        )
    )
    sub = parser.add_subparsers(dest="command")

    init_parser = sub.add_parser("init", help="Scaffold a new plugin")
    init_parser.add_argument("name", help="Plugin name (kebab-case)")
    init_parser.add_argument("--type", required=True, choices=VALID_TYPES, help="Plugin type")
    init_parser.add_argument("--output", default=".", help="Output directory")

    pkg_parser = sub.add_parser(
        "package",
        help="Package a plugin directory as a dual-format Agent Plugins tree (5.5d)",
    )
    pkg_parser.add_argument("dir", help="Plugin directory to package")
    pkg_parser.add_argument(
        "--output",
        default=None,
        help="Output directory (default: <dir>.agentplugin); a *.hecate-plugin path produces the ZIP transport",
    )
    pkg_parser.add_argument("--author", default=None, help="Author name for the generated plugin.json")
    pkg_parser.add_argument("--homepage", default=None, help="Homepage URL for the generated plugin.json")
    pkg_parser.add_argument("--repository", default=None, help="Repository URL for the generated plugin.json")

    install_parser = sub.add_parser(
        "install",
        help="Install from directory, git URL, or .hecate-plugin bundle (layout-routed, 5.5d)",
    )
    install_parser.add_argument("location", help="Source directory, git URL, or bundle file path")
    install_parser.add_argument(
        "--source", default="zip", choices=["dir", "git", "zip"], help="Install source type (default: zip)"
    )
    install_parser.add_argument("--ref", help="Git ref (branch/tag) when --source git")
    install_parser.add_argument("--plugins-dir", default="./plugins", help="Plugins directory")
    install_parser.add_argument("--workspace", help="Target workspace UUID for dual-format packages")
    install_parser.add_argument("--installer", help="Installer identifier (matched against platform allowlist)")

    uninstall_parser = sub.add_parser("uninstall", help="Uninstall a plugin")
    uninstall_parser.add_argument("name", help="Plugin name to uninstall")
    uninstall_parser.add_argument("--plugins-dir", default="./plugins", help="Plugins directory")

    agent_install_parser = sub.add_parser("agent-install", help="Install an Agent Plugins 1.0 package (5.5c)")
    agent_install_parser.add_argument(
        "--source", required=True, choices=["dir", "git", "zip"], help="Install source type"
    )
    agent_install_parser.add_argument("location", help="Source path or git URL")
    agent_install_parser.add_argument("--ref", help="Git ref (branch/tag) when source=git")
    agent_install_parser.add_argument("--workspace", help="Target workspace UUID")
    agent_install_parser.add_argument("--installer", help="Installer identifier (matched against platform allowlist)")

    agent_uninstall_parser = sub.add_parser("agent-uninstall", help="Uninstall an Agent Plugins package by ID (5.5c)")
    agent_uninstall_parser.add_argument("plugin_id", help="Plugin UUID to uninstall")

    sub.add_parser("agent-list", help="List installed Agent Plugins packages (5.5c)")

    export_parser = sub.add_parser("export", help="Export workspace skills as an Agent Plugins bundle (5.5d)")
    export_parser.add_argument("--workspace", required=True, help="Workspace UUID")
    export_parser.add_argument("--skill", action="append", default=None, help="Skill name filter (repeatable)")
    export_parser.add_argument("--bundle-name", default=None, help="Bundle name (sanitized to the package grammar)")
    export_parser.add_argument("--output", default=".", help="Output parent directory (default: current directory)")
    export_parser.add_argument("--zip", action="store_true", help="Also produce a ZIP transport next to the bundle")

    args = parser.parse_args()

    if args.command == "init":
        try:
            path = init_plugin(args.name, args.type, args.output)
            print(f"Created plugin at: {path}")  # noqa: T201
        except ValueError as e:
            print(f"Error: {e}")  # noqa: T201
            raise SystemExit(1) from e
    elif args.command == "package":
        from pathlib import Path

        from hecate.core.plugin.packaging import bundle_dual_format, emit_dual_format

        try:
            output = Path(args.output) if args.output else None
            tree = emit_dual_format(
                Path(args.dir),
                None if (output is not None and output.suffix == ".hecate-plugin") else output,
                author=args.author,
                homepage=args.homepage,
                repository=args.repository,
            )
            if output is not None and output.suffix == ".hecate-plugin":
                bundle = bundle_dual_format(tree, output)
                print(f"Created dual-format bundle: {bundle} (tree: {tree})")  # noqa: T201
            else:
                print(f"Created dual-format package directory: {tree}")  # noqa: T201
        except ValueError as e:
            print(f"Error: {e}")  # noqa: T201
            raise SystemExit(1) from e
    elif args.command == "install":
        _run_install(args)
    elif args.command == "uninstall":
        from pathlib import Path

        from hecate.core.plugin.installer import uninstall_plugin

        removed = uninstall_plugin(args.name, Path(args.plugins_dir))
        if removed:
            print(f"Uninstalled plugin: {args.name}")  # noqa: T201
        else:
            print(f"Plugin not found: {args.name}")  # noqa: T201
            raise SystemExit(1)
    elif args.command == "agent-install":
        _run_agent_install(args)
    elif args.command == "agent-uninstall":
        _run_agent_uninstall(args)
    elif args.command == "agent-list":
        _run_agent_list()
    elif args.command == "export":
        _run_export(args)
    else:
        parser.print_help()


def _run_agent_install(args: object) -> None:
    """Install an Agent Plugins 1.0 package (feature 5.5c)."""
    _agent_install(
        source_type=str(args.source),
        location=str(args.location),
        ref=getattr(args, "ref", None),
        workspace=getattr(args, "workspace", None),
        installer=getattr(args, "installer", None),
    )


def _agent_install(
    *,
    source_type: str,
    location: str,
    ref: str | None,
    workspace: str | None,
    installer: str | None,
) -> None:
    """Run the Agent Plugins pipeline for an explicit source descriptor."""
    import asyncio
    import uuid as _uuid

    from hecate.core.config import settings
    from hecate.core.database import async_session_factory
    from hecate.studio.plugin.service import PluginService

    workspace_id: _uuid.UUID | None = None
    if workspace:
        workspace_id = _uuid.UUID(str(workspace))

    async def _install() -> object:
        async with async_session_factory() as session:
            service = PluginService(session)
            plugin = await service.install_agent_plugin(
                source_type=source_type,
                location=location,
                plugins_dir=settings.PLUGINS_DIR,
                ref=ref,
                workspace_id=workspace_id,
                installer=installer,
            )
            await session.commit()
            return plugin

    try:
        plugin = asyncio.run(_install())
    except ValueError as e:
        print(f"Error: {e}")  # noqa: T201
        raise SystemExit(1) from e

    skills = plugin.manifest_.get("components", {}).get("skills", [])
    servers = plugin.manifest_.get("components", {}).get("mcp_servers", [])
    namespace = plugin.manifest_.get("namespace") or {}
    print(f"Installed Agent Plugins package: {plugin.name} v{plugin.version}")  # noqa: T201
    print(f"  origin: {plugin.origin}")  # noqa: T201
    print(f"  skills: {len(skills)}, mcp servers: {len(servers)}")  # noqa: T201
    if namespace:
        from hecate.core.plugin.dual_format import AGENT_PLUGIN_NAMESPACE

        print(
            f"  {AGENT_PLUGIN_NAMESPACE}/: type={namespace.get('type') or '-'}, "
            f"entry={namespace.get('entry') or '-'}, component={namespace.get('component_status', 'none')}"
        )  # noqa: T201
    for entry in [*skills, *servers]:
        print(f"  - {entry['name']}: {entry['status']}")  # noqa: T201


def _run_install(args: object) -> None:
    """Install from dir/git/zip with dual-format layout routing (5.5d).

    ZIP is transport only: bundles are materialized and the layout at the
    archive root routes the install — legacy (plugin.yaml) through the
    legacy installer, dual-format (plugin.json) through the Agent Plugins
    pipeline.
    """
    import shutil as _shutil
    import tempfile as _tempfile
    from pathlib import Path

    from hecate.core.plugin.agent_plugins import materialize_from_git, materialize_from_zip
    from hecate.core.plugin.packaging import detect_bundle_layout, detect_layout

    location = Path(str(args.location))
    source = str(args.source)
    staging = Path(_tempfile.mkdtemp(prefix="hecate-install-"))
    try:
        if source == "zip":
            layout = detect_bundle_layout(location)
            if layout == "dual":
                materialize_from_zip(location, staging)
                _agent_install(
                    source_type="dir",
                    location=str(staging),
                    ref=None,
                    workspace=getattr(args, "workspace", None),
                    installer=getattr(args, "installer", None),
                )
                return
            from hecate.core.plugin.installer import install_plugin

            name = install_plugin(location, Path(str(args.plugins_dir)))
            print(f"Installed plugin: {name}")  # noqa: T201
            return

        if source == "dir":
            if not location.is_dir():
                print(f"Error: directory not found: {location}")  # noqa: T201
                raise SystemExit(1)
            if detect_layout(location) == "dual":
                _agent_install(
                    source_type="dir",
                    location=str(location),
                    ref=None,
                    workspace=getattr(args, "workspace", None),
                    installer=getattr(args, "installer", None),
                )
                return
            from hecate.core.plugin.installer import install_plugin_from_directory

            name = install_plugin_from_directory(location, Path(str(args.plugins_dir)))
            print(f"Installed plugin: {name}")  # noqa: T201
            return

        # git: clone once for layout detection; dual-format re-clones through
        # the pipeline so the ref/SHA pin triple is recorded.
        materialize_from_git(str(args.location), staging, ref=getattr(args, "ref", None))
        root = staging
        if detect_layout(root) == "unknown":
            children = [p for p in root.iterdir() if p.is_dir() and p.name != ".git"]
            if len(children) == 1:
                root = children[0]
        if detect_layout(root) == "dual":
            _agent_install(
                source_type="git",
                location=str(args.location),
                ref=getattr(args, "ref", None),
                workspace=getattr(args, "workspace", None),
                installer=getattr(args, "installer", None),
            )
            return
        from hecate.core.plugin.installer import install_plugin_from_directory

        name = install_plugin_from_directory(root, Path(str(args.plugins_dir)))
        print(f"Installed plugin: {name}")  # noqa: T201
    finally:
        _shutil.rmtree(staging, ignore_errors=True)


def _run_export(args: object) -> None:
    """Export workspace skills as an Agent Plugins bundle (feature 5.5d)."""
    import asyncio
    import uuid as _uuid

    from hecate.core.database import async_session_factory
    from hecate.studio.plugin.export_service import SkillExportService

    workspace_id = _uuid.UUID(str(args.workspace))
    skill_names: list[str] | None = getattr(args, "skill", None)
    bundle_name: str | None = getattr(args, "bundle_name", None)

    async def _export() -> object:
        async with async_session_factory() as session:
            service = SkillExportService(session)
            plan = await service.preview_export(workspace_id, bundle_name=bundle_name, skill_names=skill_names)
            print(  # noqa: T201
                f"Export plan: bundle {plan.bundle_name!r}, {len(plan.entries)} skill(s), {plan.total_bytes} bytes"
            )
            for warning in plan.warnings:
                print(f"  warning: {warning}")  # noqa: T201
            return await service.execute_export(
                workspace_id,
                str(args.output),
                bundle_name=bundle_name,
                skill_names=skill_names,
                with_zip=bool(getattr(args, "zip", False)),
            )

    try:
        result = asyncio.run(_export())
    except ValueError as e:
        print(f"Error: {e}")  # noqa: T201
        raise SystemExit(1) from e
    print(f"Exported bundle: {result.bundle_dir}")  # noqa: T201
    if result.zip_path is not None:
        print(f"Exported zip transport: {result.zip_path}")  # noqa: T201


def _run_agent_uninstall(args: object) -> None:
    """Uninstall an Agent Plugins package by ID (feature 5.5c)."""
    import asyncio
    import uuid as _uuid

    from hecate.core.config import settings
    from hecate.core.database import async_session_factory
    from hecate.studio.plugin.service import PluginService

    async def _uninstall() -> None:
        async with async_session_factory() as session:
            service = PluginService(session)
            await service.uninstall_agent_plugin(_uuid.UUID(str(args.plugin_id)), settings.PLUGINS_DIR)
            await session.commit()

    try:
        asyncio.run(_uninstall())
    except ValueError as e:
        print(f"Error: {e}")  # noqa: T201
        raise SystemExit(1) from e
    print(f"Uninstalled Agent Plugins package: {args.plugin_id}")  # noqa: T201


def _run_agent_list() -> None:
    """List installed Agent Plugins packages (feature 5.5c)."""
    import asyncio

    from sqlalchemy import select

    from hecate.core.database import async_session_factory
    from hecate.models.plugin import PluginModel
    from hecate.studio.plugin.service import AGENT_PLUGIN_TYPE

    async def _list() -> list[object]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(PluginModel).where(
                    PluginModel.type == AGENT_PLUGIN_TYPE,
                    PluginModel.deleted_at.is_(None),
                )
            )
            return list(result.scalars().all())

    for plugin in asyncio.run(_list()):
        scope = str(plugin.workspace_id) if plugin.workspace_id else "platform"
        print(f"{plugin.id}  {plugin.name} v{plugin.version}  [{plugin.status}]  ({scope})")  # noqa: T201


if __name__ == "__main__":
    main()
