"""REST tool execution — spec-driven HTTP against gateway targets.

Executes a projected ``source="rest"`` tool by translating tool call
arguments into a single HTTP request. The translation data (method, path,
argument routing) comes from the projection's ``x-gateway`` extension; the
OpenAPI document is never re-parsed at execution time. The base URL is
always the target's registered ``base_url`` — ``servers`` overrides in the
document are ignored by design (registered target binding).

Request-target containment: a path parameter is DATA, never a route
rewriter — values that could escape the projected path or the target's
origin (traversal, separators, encoded separators, query or fragment
markers) are rejected before sending. Every request disables redirects,
including requests using a caller-supplied client. Upstream 3xx responses
produce structured failures.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx

from hecate.models.gateway_target import GatewayTargetModel
from hecate.models.tool import ToolModel
from hecate.tools.gateway.errors import GatewayError, RestExecutionError
from hecate.tools.gateway.targets import validate_base_url

logger = logging.getLogger(__name__)

_PATH_PARAM = re.compile(r"\{([^}]+)\}")
_PATH_CONTROL = re.compile(r"[?#\\\x00-\x1f\x7f]")
_RESERVED_IDENTITY_HEADERS = (
    "x-hecate-actor",
    "x-hecate-on-behalf-of",
    "x-hecate-delegation-id",
    "x-hecate-deployment",
)


def _extract_gateway_meta(tool: ToolModel) -> dict[str, Any]:
    parameters = tool.parameters
    meta = parameters.get("x-gateway") if isinstance(parameters, dict) else None
    if not isinstance(meta, dict) or "method" not in meta or "path" not in meta:
        raise GatewayError(f"Tool '{tool.name}' has no gateway projection metadata")
    if not isinstance(meta["path"], str) or not meta["path"].startswith("/"):
        raise GatewayError(f"Tool '{tool.name}' has an invalid projected path")
    _validate_path(meta["path"])
    method = meta["method"]
    if not isinstance(method, str) or method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise GatewayError(f"Tool '{tool.name}' has an invalid projected method")
    for field in ("path_params", "query_params", "body_params"):
        names = meta.get(field, [])
        if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
            raise GatewayError(f"Tool '{tool.name}' has invalid parameter routing")
    if set(_PATH_PARAM.findall(meta["path"])) != set(meta.get("path_params", [])):
        raise GatewayError(f"Tool '{tool.name}' has unresolved path parameters")
    return {**meta, "method": method.upper()}


def _decoded_path(value: str) -> str:
    for _ in range(8):
        try:
            decoded = unquote(value, errors="strict")
        except UnicodeError:
            raise GatewayError("Gateway path has invalid encoding") from None
        if decoded == value:
            return value
        value = decoded
    raise GatewayError("Gateway path has excessive nested encoding")


def _validate_path(path: str) -> None:
    decoded = _decoded_path(path)
    if _PATH_CONTROL.search(decoded) or any(segment in {".", ".."} for segment in decoded.split("/")):
        raise GatewayError("Gateway path contains unsafe routing components")


def _safe_path_value(tool: ToolModel, name: str, value: Any) -> str:
    """One path segment's worth of data, validated before substitution."""
    text = str(value)
    decoded = _decoded_path(text)
    if not text or decoded in {".", ".."} or _PATH_CONTROL.search(decoded) or "/" in decoded:
        raise GatewayError(f"Path parameter '{name}' for tool '{tool.name}' must be a single path segment")
    return quote(text, safe="")


def _credential_headers(target: GatewayTargetModel) -> dict[str, str]:
    """Build outbound headers from the target's brokered credentials.

    Reserved service-identity names are dropped case-insensitively:
    identity headers come only from the verified envelope.
    """
    creds = target.credentials or {}
    headers = creds.get("headers") if isinstance(creds, dict) else None
    if not isinstance(headers, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items() if str(k).lower() not in _RESERVED_IDENTITY_HEADERS}


def _validate_target(target: GatewayTargetModel) -> httpx.URL:
    """The registered target's URL at execution time (old rows included)."""
    try:
        raw = str(target.base_url)
        if re.search(r"[\x00-\x20\x7f]", raw):
            raise ValueError("URL contains control characters or spaces")
        parsed = urlsplit(raw)
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError("URL contains credentials, query or fragment")
        validate_base_url(raw)
        _validate_path(parsed.path)
        url = httpx.URL(raw)
    except (ValueError, httpx.InvalidURL, GatewayError):
        raise GatewayError(f"Gateway target '{target.name}' has an unusable base_url") from None
    return url


class RestToolExecutor:
    """Translates gateway tool calls into HTTP requests against a target.

    Args:
        timeout: Per-request timeout in seconds.
        client: Optional pre-configured ``httpx.AsyncClient`` (tests inject
            a ``MockTransport`` client here). The caller owns its lifecycle.
            Redirects are disabled for each execution request.
    """

    def __init__(self, timeout: int = 30, client: httpx.AsyncClient | None = None) -> None:
        self._timeout = timeout
        self._client = client

    async def execute(
        self,
        tool: ToolModel,
        target: GatewayTargetModel,
        arguments: dict[str, Any],
        identity_headers: dict[str, str] | None = None,
    ) -> Any:
        """Execute a rest tool call and return the response payload.

        Returns the parsed JSON response body, or the text body when the
        response is not JSON.

        Raises:
            GatewayError: If the tool has no valid projection, a path
                parameter could re-route the request, the target's
                registered URL is unusable.
            RestExecutionError: On upstream errors (non-2xx, redirects) or
                connection failures. Never includes credentials.
        """
        meta = _extract_gateway_meta(tool)
        arguments = arguments or {}
        base_url = _validate_target(target)

        path: str = meta["path"]
        for name in meta.get("path_params", []):
            value = arguments.get(name)
            if value is None:
                raise GatewayError(f"Missing required path parameter '{name}' for tool '{tool.name}'")
            path = path.replace("{" + name + "}", _safe_path_value(tool, name, value))
        if "{" in path or "}" in path:
            raise GatewayError(f"Tool '{tool.name}' has unresolved path parameters")

        path_param_names = set(meta.get("path_params", []))
        body_param_names = set(meta.get("body_params", []))
        query = {k: v for k, v in arguments.items() if k not in path_param_names and k not in body_param_names}
        body = {k: v for k, v in arguments.items() if k in body_param_names}

        prefix = base_url.raw_path.rstrip(b"/")
        url = base_url.copy_with(raw_path=prefix + httpx.URL(path=path).raw_path)
        # The final request target must stay inside the registered origin.
        if (url.scheme, url.host, url.port) != (base_url.scheme, base_url.host, base_url.port):
            raise GatewayError(f"Gateway target '{target.name}' request escaped the registered origin")
        if not url.raw_path.startswith(prefix + b"/"):
            raise GatewayError(f"Gateway target '{target.name}' request escaped the registered base path")
        # Verified service identity rides dedicated headers added by the
        # caller from the authenticated envelope — never from tool
        # arguments; credentials stay proxy-side only.
        headers = _credential_headers(target)
        request_kwargs: dict[str, Any] = {
            "params": query or None,
            "headers": headers or None,
        }
        if body:
            request_kwargs["json"] = body

        client = self._client or httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        try:
            try:
                request = client.build_request(meta["method"], url, **request_kwargs)
                # Client defaults participate in header merging too. Remove
                # asserted identities after merging, without mutating a shared client.
                for name in _RESERVED_IDENTITY_HEADERS:
                    request.headers.pop(name, None)
                request.headers.update(
                    {
                        name.lower(): value
                        for name, value in (identity_headers or {}).items()
                        if name.lower() in _RESERVED_IDENTITY_HEADERS
                    }
                )
                request.headers["host"] = url.netloc.decode("ascii")
                response = await client.send(request, follow_redirects=False)
            except (httpx.RequestError, httpx.InvalidURL, UnicodeError, ValueError) as exc:
                logger.warning("Gateway target '%s' connection failure: %s", target.name, type(exc).__name__)
                raise RestExecutionError(
                    target_name=target.name,
                    operation=tool.name,
                    failure_class="connection_failure",
                ) from None
        finally:
            if self._client is None:
                await client.aclose()

        if 300 <= response.status_code < 400:
            # A redirect would move the request (and its credentials) to a
            # second target: structured refusal, zero follow-up requests.
            raise RestExecutionError(
                target_name=target.name,
                operation=tool.name,
                failure_class="redirect_not_followed",
                status_code=response.status_code,
            )
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
