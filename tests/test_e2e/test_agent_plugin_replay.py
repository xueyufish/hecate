"""F4 — Agent Plugin MCP registrations survive an app restart.

Without replay, every restart loses in-process MCP registrations: the
plugin row is still enabled in the DB, but the agent_plugin_mcp
registry is empty until the next install/disable/enable cycle. The
lifespan calls replay_agent_plugin_mcp() exactly to prevent this. A
regression here means a fresh deploy ships a dead agent-plugin
ecosystem on first boot — full outage for every tenant.

The test installs a minimal Agent Plugins 1.0 package (one http MCP
server, no stdio to avoid the 9.4c docker dependency), then calls
replay_agent_plugin_mcp() directly and asserts the server is registered
in the process-wide MCPConnectionManager.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from hecate.api.management.mcp import get_mcp_manager
from hecate.core.config import settings as _settings  # noqa: F401  (settings import for install_agent_plugin)
from hecate.services.plugin.service import PluginService

_MINIMAL_MANIFEST = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": "docs-helper",
    "version": "0.1.0",
    "description": "replay test fixture",
    "author": {"name": "test", "email": "t@example.com"},
}


def _write_package(package_root: Path) -> Path:
    """Write a minimal installable Agent Plugins 1.0 package on disk."""
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "plugin.json").write_text(
        """{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
  "name": "docs-helper",
  "version": "0.1.0",
  "description": "replay test fixture",
  "author": {"name": "test", "email": "t@example.com"}
}
"""
    )
    (package_root / "mcp.json").write_text(
        """{
  "mcpServers": {
    "docs-search": {
      "type": "streamable-http",
      "url": "https://example.invalid/mcp"
    }
  }
}
"""
    )
    return package_root


async def test_replay_re_registers_http_mcp_server_from_db(db_session, tmp_path: Path) -> None:
    """Install → reset in-memory registry → replay → server is back.

    Simulates the production restart path: a plugin row exists and is
    enabled in the DB, but the process-wide MCPConnectionManager has
    been freshly constructed (no prior registrations). The lifespan
    calls replay_agent_plugin_mcp() to rebuild it — this test asserts
    that call restores the server.
    """
    package_root = _write_package(tmp_path / "docs-helper")
    plugins_dir = str(tmp_path)

    service = PluginService(db_session)
    plugin = await service.install_agent_plugin(
        source_type="dir",
        location=str(package_root),
        plugins_dir=plugins_dir,
        workspace_id=uuid.uuid4(),
    )
    assert plugin.status == "installed"
    await db_session.refresh(plugin)
    assert plugin.manifest_ is not None
    components = (plugin.manifest_ or {}).get("components", {})
    assert any(s.get("status") == "registered" for s in components.get("mcp_servers", [])), (
        f"manifest_.components.mcp_servers missing 'registered' entry; got {components}"
    )

    # Enable so the row enters the replay filter (status='enabled').
    await service.enable_plugin(plugin.id)
    await db_session.flush()

    # Simulate a restart by clearing the in-memory MCPConnectionManager
    # (the process-wide singleton keeps growing otherwise across tests).
    manager = get_mcp_manager()
    full_name = "docs-helper__docs-search"
    manager.unregister_server(full_name)
    assert manager.get_server_info(full_name) is None

    replayed = await service.replay_agent_plugin_mcp()
    assert replayed >= 1

    registered = manager.get_server_info(full_name)
    assert registered is not None, (
        f"replay did not re-register {full_name}; manager servers: {[s.name for s in manager.list_servers()]}"
    )
    assert registered.endpoint == "https://example.invalid/mcp"
