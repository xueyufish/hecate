"""REST API for MCP connection management.

Provides endpoints for listing connections, viewing connection details,
manual reconnect, and tool cache refresh.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from hecate.services.mcp.connection import MCPClientManager

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

# Module-level MCPClientManager instance
_manager: MCPClientManager | None = None


def get_mcp_manager() -> MCPClientManager:
    """Get or create the module-level MCPClientManager."""
    global _manager  # noqa: PLW0603
    if _manager is None:
        from hecate.core.config import settings

        _manager = MCPClientManager(
            default_timeout=settings.MCP_CLIENT_TIMEOUT,
            pool_min_size=settings.MCP_POOL_MIN_SIZE,
            pool_max_size=settings.MCP_POOL_MAX_SIZE,
            borrow_timeout=settings.MCP_BORROW_TIMEOUT,
            health_check_interval=settings.MCP_HEALTH_CHECK_INTERVAL,
            request_timeout=settings.MCP_REQUEST_TIMEOUT,
            tool_cache_ttl=settings.MCP_TOOL_CACHE_TTL,
            circuit_breaker_threshold=settings.MCP_CIRCUIT_BREAKER_THRESHOLD,
            circuit_breaker_recovery_timeout=settings.MCP_CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
            reconnect_max_retries=settings.MCP_RECONNECT_MAX_RETRIES,
            reconnect_base_delay=settings.MCP_RECONNECT_BASE_DELAY,
            reconnect_max_delay=settings.MCP_RECONNECT_MAX_DELAY,
        )
    return _manager


@router.get("/connections")
async def list_connections() -> list[dict]:
    """List all registered MCP servers with connection status.

    Returns:
        List of connection status dicts.
    """
    manager = get_mcp_manager()
    servers = manager.list_servers()
    results = []
    for server in servers:
        status = await manager.get_connection_status(server.name)
        results.append(status)
    return results


@router.get("/connections/{name}")
async def get_connection(name: str) -> dict:
    """Get detailed status for a single MCP server connection.

    Args:
        name: Server name.

    Returns:
        Connection status dict.

    Raises:
        HTTPException: If server not found.
    """
    manager = get_mcp_manager()
    status = await manager.get_connection_status(name)
    if not status.get("registered"):
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    return status


@router.post("/connections/{name}/reconnect")
async def reconnect_connection(name: str) -> dict:
    """Manually trigger reconnection for an MCP server.

    Args:
        name: Server name.

    Returns:
        Confirmation dict.

    Raises:
        HTTPException: If server not found.
    """
    manager = get_mcp_manager()
    info = manager.get_server_info(name)
    if info is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    await manager.reconnect(name)
    return {"status": "reconnecting", "server": name}


@router.post("/connections/{name}/sync")
async def sync_connection(name: str) -> dict:
    """Refresh the tool cache for an MCP server.

    Args:
        name: Server name.

    Returns:
        Sync result dict with tool count.

    Raises:
        HTTPException: If server not found.
    """
    manager = get_mcp_manager()
    info = manager.get_server_info(name)
    if info is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    # Invalidate cache and re-discover
    manager.registry.invalidate_cache(name)
    try:
        tools = await manager.discover_tools(server_name=name)
        return {"status": "synced", "server": name, "tool_count": len(tools)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to sync tools: {e}") from e
