"""First-party compatibility baseline (spec: mcp-server gateway scenarios).

With the gateway disabled (default) the tool catalog must be byte-identical
to pre-gateway behavior; with the gateway enabled but no targets registered
the first-party set is unchanged as well.
"""

from __future__ import annotations

from fastmcp import Client

from hecate.tools.mcp.server import create_mcp_server

_FIRST_PARTY_TOOLS = {
    "session_create",
    "agent_chat",
    "session_list",
    "session_resume",
    "conversation_history",
    "agent_list",
    "agent_create",
    "agent_update",
    "agent_delete",
    "knowledge_list",
    "knowledge_search",
    "knowledge_create",
    "knowledge_ingest",
    "tool_list",
    "tool_execute",
    "tool_create",
}


async def test_gateway_off_first_party_catalog_unchanged() -> None:
    server = create_mcp_server(gateway_enabled=False)
    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
    assert tools == _FIRST_PARTY_TOOLS


async def test_gateway_on_without_targets_first_party_unchanged(monkeypatch) -> None:
    from hecate.core.config import settings

    monkeypatch.setattr(settings, "MCP_AUTH_TYPE", "none")
    from tests.conftest import test_session_factory

    monkeypatch.setattr("hecate.tools.gateway.middleware.async_session_factory", test_session_factory)
    monkeypatch.setattr("hecate.tools.mcp.server.async_session_factory", test_session_factory)
    server = create_mcp_server(gateway_enabled=True)
    async with Client(server) as client:
        tools = {t.name for t in await client.list_tools()}
    assert tools == _FIRST_PARTY_TOOLS
