"""Structured error codes for MCP connection diagnosis.

Provides AgentArts-style error codes with descriptions and diagnostic hints
for each failure mode encountered during MCP connection lifecycle.
"""

from __future__ import annotations

from enum import StrEnum


class MCPErrorCode(StrEnum):
    """Structured error codes for MCP connection failures.

    Each code maps to a specific failure mode with actionable diagnostic hints.
    """

    # Connection probe errors (two-step probe)
    MCP_DNS_FAILURE = "MCP_DNS_FAILURE"
    """Hostname could not be resolved. Hint: Check the server URL hostname."""

    MCP_CONNECT_TIMEOUT = "MCP_CONNECT_TIMEOUT"
    """TCP connection timed out. Hint: Check network connectivity and firewall rules."""

    MCP_PORT_CLOSED = "MCP_PORT_CLOSED"
    """TCP connection refused — port not listening. Hint: Verify MCP server is running on the expected port."""

    MCP_PATH_NOT_FOUND = "MCP_PATH_NOT_FOUND"
    """HTTP 404 — MCP endpoint path not found. Hint: Verify the MCP server URL path (e.g. /mcp)."""

    MCP_SSL_ERROR = "MCP_SSL_ERROR"
    """SSL/TLS handshake failed. Hint: Check certificate validity and trust chain."""

    MCP_WAF_BLOCKED = "MCP_WAF_BLOCKED"
    """Request blocked by WAF/firewall. Hint: Check WAF rules and IP allowlists."""

    # Pool errors
    MCP_POOL_EXHAUSTED = "MCP_POOL_EXHAUSTED"
    """All connections in pool are in use. Hint: Increase pool max_size."""

    # Connection state errors
    MCP_RECONNECTING = "MCP_RECONNECTING"
    """Connection dropped and auto-reconnection is in progress. Hint: Wait and retry, or check server availability."""

    MCP_CONNECTION_FAILED = "MCP_CONNECTION_FAILED"
    """Connection failed after retries exhausted. Hint: Check server health."""

    MCP_REQUEST_TIMEOUT = "MCP_REQUEST_TIMEOUT"
    """Per-request timeout exceeded. Hint: Check server response time or increase MCP_REQUEST_TIMEOUT."""

    MCP_CIRCUIT_OPEN = "MCP_CIRCUIT_OPEN"
    """Circuit breaker is open due to consecutive failures. Hint: Wait for recovery timeout or manually reconnect."""


# Error descriptions for logging and API responses
ERROR_DESCRIPTIONS: dict[MCPErrorCode, str] = {
    MCPErrorCode.MCP_DNS_FAILURE: "DNS resolution failed",
    MCPErrorCode.MCP_CONNECT_TIMEOUT: "Connection timed out",
    MCPErrorCode.MCP_PORT_CLOSED: "Port closed or not listening",
    MCPErrorCode.MCP_PATH_NOT_FOUND: "MCP endpoint path not found (HTTP 404)",
    MCPErrorCode.MCP_SSL_ERROR: "SSL/TLS handshake failed",
    MCPErrorCode.MCP_WAF_BLOCKED: "Request blocked by WAF or firewall",
    MCPErrorCode.MCP_POOL_EXHAUSTED: "Connection pool exhausted",
    MCPErrorCode.MCP_RECONNECTING: "Connection lost, reconnecting",
    MCPErrorCode.MCP_CONNECTION_FAILED: "Connection failed after retries",
    MCPErrorCode.MCP_REQUEST_TIMEOUT: "Request timed out",
    MCPErrorCode.MCP_CIRCUIT_OPEN: "Circuit breaker open",
}


class MCPConnectionError(Exception):
    """Structured MCP connection error with error code and diagnostic details.

    Args:
        code: The MCP error code.
        message: Human-readable error message.
        details: Additional diagnostic details (e.g. hostname, port, timeout value).
    """

    def __init__(
        self,
        code: MCPErrorCode,
        message: str,
        details: dict[str, str] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, str | dict[str, str]]:
        """Serialize to dict for API responses."""
        return {
            "error_code": self.code.value,
            "message": self.message,
            "description": ERROR_DESCRIPTIONS.get(self.code, ""),
            "details": self.details,
        }
