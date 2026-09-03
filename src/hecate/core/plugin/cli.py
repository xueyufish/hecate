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
    parser = argparse.ArgumentParser(description="Hecate plugin CLI")
    sub = parser.add_subparsers(dest="command")

    init_parser = sub.add_parser("init", help="Scaffold a new plugin")
    init_parser.add_argument("name", help="Plugin name (kebab-case)")
    init_parser.add_argument("--type", required=True, choices=VALID_TYPES, help="Plugin type")
    init_parser.add_argument("--output", default=".", help="Output directory")

    pkg_parser = sub.add_parser("package", help="Package a plugin directory into .hecate-plugin")
    pkg_parser.add_argument("dir", help="Plugin directory to package")
    pkg_parser.add_argument("--output", default=None, help="Output file path")

    install_parser = sub.add_parser("install", help="Install a .hecate-plugin bundle")
    install_parser.add_argument("file", help="Path to .hecate-plugin file")
    install_parser.add_argument("--plugins-dir", default="./plugins", help="Plugins directory")

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

        from hecate.core.plugin.packaging import create_bundle

        try:
            output = create_bundle(Path(args.dir), Path(args.output) if args.output else None)
            print(f"Created bundle: {output}")  # noqa: T201
        except ValueError as e:
            print(f"Error: {e}")  # noqa: T201
            raise SystemExit(1) from e
    elif args.command == "install":
        from pathlib import Path

        from hecate.core.plugin.installer import install_plugin

        try:
            name = install_plugin(Path(args.file), Path(args.plugins_dir))
            print(f"Installed plugin: {name}")  # noqa: T201
        except ValueError as e:
            print(f"Error: {e}")  # noqa: T201
            raise SystemExit(1) from e
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
    else:
        parser.print_help()


def _run_agent_install(args: object) -> None:
    """Install an Agent Plugins 1.0 package (feature 5.5c)."""
    import asyncio
    import uuid as _uuid

    from hecate.core.config import settings
    from hecate.core.database import async_session_factory
    from hecate.studio.plugin.service import PluginService

    workspace_id: _uuid.UUID | None = None
    if getattr(args, "workspace", None):
        workspace_id = _uuid.UUID(str(args.workspace))

    async def _install() -> object:
        async with async_session_factory() as session:
            service = PluginService(session)
            plugin = await service.install_agent_plugin(
                source_type=args.source,
                location=args.location,
                plugins_dir=settings.PLUGINS_DIR,
                ref=getattr(args, "ref", None),
                workspace_id=workspace_id,
                installer=getattr(args, "installer", None),
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
    print(f"Installed Agent Plugins package: {plugin.name} v{plugin.version}")  # noqa: T201
    print(f"  origin: {plugin.origin}")  # noqa: T201
    print(f"  skills: {len(skills)}, mcp servers: {len(servers)}")  # noqa: T201
    for entry in [*skills, *servers]:
        print(f"  - {entry['name']}: {entry['status']}")  # noqa: T201


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
