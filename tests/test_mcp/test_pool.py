"""Tests for MCP ConnectionPool."""

from __future__ import annotations

import pytest

from hecate.services.mcp.client import HecateMCPClient
from hecate.services.mcp.errors import MCPConnectionError, MCPErrorCode
from hecate.services.mcp.pool import ConnectionPool


class _FakeClient(HecateMCPClient):
    """Test client that simulates connection state."""

    def __init__(self, connected: bool = True) -> None:
        super().__init__()
        self._fake_connected = connected

    @property
    def connected(self) -> bool:
        return self._fake_connected

    async def health_check(self) -> bool:
        return self._fake_connected

    async def disconnect(self) -> None:
        self._fake_connected = False


async def _make_factory(connected: bool = True):
    """Factory that creates fake clients."""

    async def factory() -> HecateMCPClient:
        return _FakeClient(connected=connected)

    return factory


async def test_borrow_and_return() -> None:
    """Borrow returns a client, return makes it available again."""
    factory = await _make_factory()
    pool = ConnectionPool(
        server_name="test",
        min_size=0,
        max_size=2,
        borrow_timeout=1,
        health_check_interval=0,
        connect_factory=factory,
    )

    client = await pool.borrow()
    assert isinstance(client, HecateMCPClient)

    metrics = pool.metrics
    assert metrics.active == 1
    assert metrics.total == 1

    await pool.return_client(client)

    metrics = pool.metrics
    assert metrics.active == 0
    assert metrics.idle == 1

    await pool.close()


async def test_borrow_reuses_idle() -> None:
    """Borrow reuses idle connections when available."""
    factory = await _make_factory()
    pool = ConnectionPool(
        server_name="test",
        min_size=0,
        max_size=2,
        borrow_timeout=1,
        health_check_interval=0,
        connect_factory=factory,
    )

    client1 = await pool.borrow()
    await pool.return_client(client1)

    client2 = await pool.borrow()
    assert client1 is client2  # Same client reused

    await pool.return_client(client2)
    await pool.close()


async def test_borrow_creates_new_when_below_max() -> None:
    """Borrow creates a new connection when pool is below max."""
    factory = await _make_factory()
    pool = ConnectionPool(
        server_name="test",
        min_size=0,
        max_size=2,
        borrow_timeout=1,
        health_check_interval=0,
        connect_factory=factory,
    )

    client1 = await pool.borrow()
    client2 = await pool.borrow()
    assert client1 is not client2

    metrics = pool.metrics
    assert metrics.active == 2

    await pool.return_client(client1)
    await pool.return_client(client2)
    await pool.close()


async def test_borrow_exhausted_raises() -> None:
    """Borrow raises MCP_POOL_EXHAUSTED when pool is at max and all in use."""
    factory = await _make_factory()
    pool = ConnectionPool(
        server_name="test",
        min_size=0,
        max_size=1,
        borrow_timeout=0.1,
        health_check_interval=0,
        connect_factory=factory,
    )

    client = await pool.borrow()

    with pytest.raises(MCPConnectionError) as exc_info:
        await pool.borrow()

    assert exc_info.value.code == MCPErrorCode.MCP_POOL_EXHAUSTED

    await pool.return_client(client)
    await pool.close()


async def test_remove_client() -> None:
    """Remove_client removes a client from the pool."""
    factory = await _make_factory()
    pool = ConnectionPool(
        server_name="test",
        min_size=0,
        max_size=2,
        borrow_timeout=1,
        health_check_interval=0,
        connect_factory=factory,
    )

    client = await pool.borrow()
    await pool.remove_client(client)

    metrics = pool.metrics
    assert metrics.total == 0

    await pool.close()


async def test_close_stops_health_checks() -> None:
    """Close stops health check task and disconnects all clients."""
    factory = await _make_factory()
    pool = ConnectionPool(
        server_name="test",
        min_size=0,
        max_size=2,
        borrow_timeout=1,
        health_check_interval=60,
        connect_factory=factory,
    )

    client = await pool.borrow()
    await pool.return_client(client)

    await pool.close()

    metrics = pool.metrics
    assert metrics.total == 0
