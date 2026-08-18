"""Sandboxed execution of stdio MCP servers from Agent Plugins (5.5c, D6).

stdio entries declared in mcp.json execute arbitrary local subprocesses, so
Hecate runs them inside Docker containers via a generated wrapper command:
the plugin root mounts read-only at ``/plugin-root`` (``${PLUGIN_ROOT}``),
a per-plugin data directory mounts at ``/plugin-data`` (``${PLUGIN_DATA}``),
and the launcher command must be on the configured allowlist (default
``npx``/``uvx``). Policy application failures deny execution — fail-closed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from hecate.plugin.agent_plugins import McpServerSpec, check_stdio_entry

logger = logging.getLogger(__name__)

CONTAINER_PLUGIN_ROOT = "/plugin-root"
CONTAINER_PLUGIN_DATA = "/plugin-data"


class StdioSandboxError(ValueError):
    """Raised when a stdio entry cannot be safely sandboxed (fail-closed)."""


def plugin_data_dir(plugins_dir: str | Path, plugin_name: str) -> Path:
    """Per-plugin persistent data directory (the ``${PLUGIN_DATA}`` volume)."""
    return Path(plugins_dir) / "agent-plugins" / ".data" / plugin_name


def build_sandbox_command(
    plugin_name: str,
    entry: dict[str, Any] | McpServerSpec,
    plugins_dir: str | Path,
    runner_image: str,
    command_allowlist: list[str],
) -> tuple[str, list[str]]:
    """Build the docker wrapper (command, argv) for a stdio MCP entry.

    Raises StdioSandboxError (fail-closed) when the entry fails the command
    allowlist, carries code-expanding args/env, or the runner image is
    unconfigured.
    """
    if not runner_image:
        msg = "stdio sandbox runner image is not configured — fail-closed denial"
        raise StdioSandboxError(msg)

    if isinstance(entry, McpServerSpec):
        spec = entry
    else:
        spec = McpServerSpec(
            server_name=str(entry.get("name", "")),
            transport="stdio",
            endpoint=str(entry.get("endpoint", "")),
            args=list(entry.get("args", [])),
            env=dict(entry.get("env", {})),
            cwd=entry.get("cwd"),
        )

    denial = check_stdio_entry(spec, command_allowlist)
    if denial is not None:
        raise StdioSandboxError(denial)

    plugin_root = Path(plugins_dir) / "agent-plugins" / plugin_name
    data_dir = plugin_data_dir(plugins_dir, plugin_name)
    data_dir.mkdir(parents=True, exist_ok=True)

    workdir = _translate_cwd(spec.cwd, default=CONTAINER_PLUGIN_ROOT)

    argv: list[str] = [
        "run",
        "--rm",
        "--interactive",
        "--workdir",
        workdir,
        "--volume",
        f"{plugin_root}:{CONTAINER_PLUGIN_ROOT}:ro",
        "--volume",
        f"{data_dir}:{CONTAINER_PLUGIN_DATA}",
    ]
    for key, value in spec.env.items():
        argv += ["--env", f"{key}={value}"]
    argv += [runner_image, spec.endpoint, *spec.args]

    logger.info(
        "stdio sandbox wrapper for %s/%s: docker %s",
        plugin_name,
        spec.server_name,
        " ".join(argv[:6]),
    )
    return "docker", argv


def _translate_cwd(cwd: str | None, *, default: str) -> str:
    """Translate a spec ``cwd`` into a container-absolute path."""
    if not cwd:
        return default
    if cwd.startswith("${PLUGIN_ROOT}"):
        return CONTAINER_PLUGIN_ROOT + cwd[len("${PLUGIN_ROOT}") :]
    if cwd.startswith("${PLUGIN_DATA}"):
        return CONTAINER_PLUGIN_DATA + cwd[len("${PLUGIN_DATA}") :]
    if cwd.startswith("./"):
        return f"{default}/{cwd[2:]}"
    return default
