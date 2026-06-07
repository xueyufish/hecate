"""MCP Server authentication helpers."""

from __future__ import annotations

import logging

from hecate.core.config import settings

logger = logging.getLogger(__name__)


async def verify_mcp_auth(request_headers: dict[str, str] | None = None) -> str | None:
    """Validate MCP request authentication based on ``MCP_AUTH_TYPE`` setting.

    Args:
        request_headers: HTTP request headers from the MCP client.

    Returns:
        The validated API key or user identifier, or ``None`` if auth is disabled.

    Raises:
        PermissionError: If authentication fails.
    """
    auth_type = settings.MCP_AUTH_TYPE

    if auth_type == "none":
        return None

    if auth_type == "api_key":
        if not request_headers:
            raise PermissionError("No request headers provided for API key auth")
        api_key = request_headers.get("x-api-key", "")
        if not api_key:
            raise PermissionError("Missing x-api-key header")
        if api_key not in settings.api_keys_list:
            raise PermissionError("Invalid API key")
        return api_key

    if auth_type == "jwt":
        token = (request_headers or {}).get("authorization", "").removeprefix("Bearer ").strip()
        if not token:
            raise PermissionError("Missing Authorization header")
        return token

    raise ValueError(f"Unsupported MCP_AUTH_TYPE: {auth_type}")
