"""REST tool execution — spec-driven HTTP against gateway targets.

Executes a projected ``source="rest"`` tool by translating tool call
arguments into a single HTTP request. The translation data (method, path,
argument routing) comes from the projection's ``x-gateway`` extension; the
OpenAPI document is never re-parsed at execution time. The base URL is
always the target's registered ``base_url`` — ``servers`` overrides in the
document are ignored by design (SSRF containment).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from hecate.models.gateway_target import GatewayTargetModel
from hecate.models.tool import ToolModel
from hecate.tools.gateway.errors import GatewayError, RestExecutionError

logger = logging.getLogger(__name__)

_PATH_PARAM = re.compile(r"\{([^}]+)\}")


def _extract_gateway_meta(tool: ToolModel) -> dict[str, Any]:
    meta = (tool.parameters or {}).get("x-gateway")
    if not isinstance(meta, dict) or "method" not in meta or "path" not in meta:
        raise GatewayError(f"Tool '{tool.name}' has no gateway projection metadata")
    return meta


def _credential_headers(target: GatewayTargetModel) -> dict[str, str]:
    """Build outbound headers from the target's brokered credentials."""
    creds = target.credentials or {}
    headers = creds.get("headers") if isinstance(creds, dict) else None
    return {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {}


class RestToolExecutor:
    """Translates gateway tool calls into HTTP requests against a target.

    Args:
        timeout: Per-request timeout in seconds.
        client: Optional pre-configured ``httpx.AsyncClient`` (tests inject
            a ``MockTransport`` client here).
    """

    def __init__(self, timeout: int = 30, client: httpx.AsyncClient | None = None) -> None:
        self._timeout = timeout
        self._client = client

    async def execute(
        self,
        tool: ToolModel,
        target: GatewayTargetModel,
        arguments: dict[str, Any],
    ) -> Any:
        """Execute a rest tool call and return the response payload.

        Args:
            tool: The projected tool row (``source="rest"``).
            target: The owning gateway target.
            arguments: Tool call arguments.

        Returns:
            The parsed JSON response body, or the text body when the
            response is not JSON.

        Raises:
            GatewayError: If the tool has no valid projection.
            RestExecutionError: On upstream errors (non-2xx) or connection
                failures. Never includes credentials.
        """
        meta = _extract_gateway_meta(tool)
        arguments = arguments or {}

        path: str = meta["path"]
        for name in meta.get("path_params", []):
            value = arguments.get(name)
            if value is None:
                raise GatewayError(f"Missing required path parameter '{name}' for tool '{tool.name}'")
            path = path.replace("{" + name + "}", str(value))

        path_param_names = set(meta.get("path_params", []))
        body_param_names = set(meta.get("body_params", []))
        query = {k: v for k, v in arguments.items() if k not in path_param_names and k not in body_param_names}
        body = {k: v for k, v in arguments.items() if k in body_param_names}

        url = target.base_url.rstrip("/") + path
        request_kwargs: dict[str, Any] = {
            "params": query or None,
            "headers": _credential_headers(target) or None,
        }
        if body:
            request_kwargs["json"] = body

        try:
            client = self._client or httpx.AsyncClient(timeout=self._timeout)
            response = await client.request(meta["method"], url, **request_kwargs)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.InvalidURL) as exc:
            logger.warning("Gateway target '%s' connection failure: %s", target.name, type(exc).__name__)
            raise RestExecutionError(
                target_name=target.name,
                operation=tool.name,
                failure_class="connection_failure",
            ) from exc

        if response.status_code >= 400:
            raise RestExecutionError(
                target_name=target.name,
                operation=tool.name,
                failure_class="upstream_error",
                status_code=response.status_code,
            )

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text
