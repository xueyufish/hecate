"""Tests for MCP MCPClientManager (connection.py)."""

from __future__ import annotations

import pytest

from hecate.services.mcp.circuit_breaker import CircuitState
from hecate.services.mcp.client import HecateMCPClient
from hecate.services.mcp.connection import MCPClientManager
from hecate.services.mcp.errors import MCPConnectionError, MCPErrorCode


class _FakeClient(HecateMCPClient):
    """Test client with configurable behavior."""

    def __init__(self, connected: bool = True, fail_call: bool = False) -> None:
        super().__init__()
        self._fake_connected = connected
        self._fail_call = fail_call

    @property
    def connected(self) -> bool:
        return self._fake_connected

    async def health_check(self) -> bool:
        return self._fake_connected

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        if self._fail_call:
            raise RuntimeError("Tool call failed")
        return f"result:{tool_name}"

    async def list_tools(self) -> list:
        return [{"name": "tool1"}, {"name": "tool2"}]

    async def disconnect(self) -> None:
        self._fake_connected = False


async def test_register_and_list() -> None:
    """Register server and list it."""
    manager = MCPClientManager(health_check_interval=0)
    manager.register_server("srv1", "http://localhost:8001", "http", "ws-1")

    servers = manager.list_servers()
    assert len(servers) == 1
    assert servers[0].name == "srv1"


async def test_unregister() -> None:
    """Unregister server cleans up resources."""
    manager = MCPClientManager(health_check_interval=0)
    manager.register_server("srv1", "http://localhost:8001", "http")

    info = manager.unregister_server("srv1")
    assert info is not None
    assert manager.get_server_info("srv1") is None


async def test_call_tool_circuit_open() -> None:
    """call_tool raises MCP_CIRCUIT_OPEN when circuit breaker is open."""
    manager = MCPClientManager(
        health_check_interval=0,
        circuit_breaker_threshold=1,
        circuit_breaker_recovery_timeout=999,
    )
    manager.register_server("srv1", "http://localhost:8001", "http")

    # Open the circuit
    cb = manager._circuit_breakers["srv1"]
    cb.record_failure()

    # The circuit breaker check happens before pool creation,
    # so we should get MCP_CIRCUIT_OPEN without any TCP probe
    with pytest.raises(MCPConnectionError) as exc_info:
        await manager.call_tool("srv1", "tool1", {})

    assert exc_info.value.code == MCPErrorCode.MCP_CIRCUIT_OPEN


async def test_call_tool_server_not_registered() -> None:
    """call_tool raises error for unregistered server."""
    manager = MCPClientManager(health_check_interval=0)

    with pytest.raises(MCPConnectionError) as exc_info:
        await manager.call_tool("unknown", "tool1", {})

    assert exc_info.value.code == MCPErrorCode.MCP_CONNECTION_FAILED


async def test_get_connection_status() -> None:
    """Get connection status returns detailed info."""
    manager = MCPClientManager(health_check_interval=0)
    manager.register_server("srv1", "http://localhost:8001", "http", "ws-1")

    status = await manager.get_connection_status("srv1")
    assert status["registered"] is True
    assert status["name"] == "srv1"
    assert status["endpoint"] == "http://localhost:8001"
    assert status["circuit_state"] == "closed"


async def test_get_connection_status_unknown() -> None:
    """Get connection status for unknown server returns registered=False."""
    manager = MCPClientManager(health_check_interval=0)

    status = await manager.get_connection_status("unknown")
    assert status["registered"] is False


async def test_reconnect_resets_state() -> None:
    """Manual reconnect resets pool and circuit breaker."""
    manager = MCPClientManager(health_check_interval=0)
    manager.register_server("srv1", "http://localhost:8001", "http")

    cb = manager._circuit_breakers["srv1"]
    cb.record_failure()
    cb.record_failure()

    await manager.reconnect("srv1")

    assert cb.state == CircuitState.CLOSED


async def test_tcp_probe_dns_failure() -> None:
    """TCP probe raises MCP_DNS_FAILURE for invalid hostname."""
    manager = MCPClientManager(health_check_interval=0)

    with pytest.raises(MCPConnectionError) as exc_info:
        await manager._tcp_probe("http://this-host-does-not-exist.invalid:8001/mcp", "http")

    assert exc_info.value.code == MCPErrorCode.MCP_DNS_FAILURE


async def test_tcp_probe_port_closed() -> None:
    """TCP probe raises MCP_PORT_CLOSED for closed port."""
    manager = MCPClientManager(health_check_interval=0)

    with pytest.raises(MCPConnectionError) as exc_info:
        await manager._tcp_probe("http://localhost:1/mcp", "http")

    # Could be PORT_CLOSED or CONNECT_TIMEOUT depending on environment
    assert exc_info.value.code in (MCPErrorCode.MCP_PORT_CLOSED, MCPErrorCode.MCP_CONNECT_TIMEOUT)
