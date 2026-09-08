"""MCP boundary authentication and authorization.

Resolves the caller identity behind an MCP request (workspace-scoped DB
API keys, with legacy environment keys as platform fallback) and enforces
the tool policy pipeline at the gateway boundary — ``tools/list``
visibility filtering and ``tools/call`` execution decisions. Pipeline
exceptions fail closed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.models.api_key import ApiKeyScope
from hecate.tools.policy import (
    PolicyContext,
    PolicyDecision,
    ToolPolicyPipeline,
)
from hecate.tools.policy.policy_layers import (
    PluginAvailabilityLayer,
    ProfileLayer,
    SecurityLayer,
    VisibilityLayer,
)

logger = logging.getLogger(__name__)


@dataclass
class CallerIdentity:
    """The resolved identity behind an MCP request.

    ``workspace_id`` is set only for workspace-scoped keys; platform and
    system identities see the first-party catalog (system keys additionally
    see platform-owned federated targets).
    """

    workspace_id: str | None
    scope: str
    authenticated: bool

    @property
    def include_platform_federated(self) -> bool:
        """Whether platform-owned federated targets are visible."""
        return self.scope == "system"


_PLATFORM_IDENTITY = CallerIdentity(workspace_id=None, scope="platform", authenticated=True)


async def resolve_caller_identity(
    db: AsyncSession,
    request_headers: dict[str, str] | None,
) -> CallerIdentity:
    """Resolve the caller identity from request headers.

    Resolution order under ``MCP_AUTH_TYPE=api_key``: DB-backed key
    (workspace or system scope) first, then the legacy environment key
    list (platform scope). Under ``jwt`` the presence of a bearer token
    is required but no workspace is derived (platform scope). Under
    ``none`` every caller is platform.

    Raises:
        PermissionError: If authentication fails.
    """
    auth_type = settings.MCP_AUTH_TYPE

    if auth_type == "none":
        return _PLATFORM_IDENTITY

    if auth_type == "api_key":
        raw = (request_headers or {}).get("x-api-key", "")
        if not raw:
            raise PermissionError("Missing x-api-key header")
        # Function-local: the tools domain must not depend on enterprise at
        # module level (layering guard); the verifier is a runtime seam.
        from hecate.enterprise.auth.api_key_service import ApiKeyService

        key = await ApiKeyService().verify_key(db, raw)
        if key is not None:
            if key.scope == ApiKeyScope.WORKSPACE and key.workspace_id is not None:
                return CallerIdentity(workspace_id=str(key.workspace_id), scope="workspace", authenticated=True)
            return CallerIdentity(workspace_id=None, scope="system", authenticated=True)
        if raw in settings.api_keys_list:
            return _PLATFORM_IDENTITY
        raise PermissionError("Invalid API key")

    if auth_type == "jwt":
        token = (request_headers or {}).get("authorization", "").removeprefix("Bearer ").strip()
        if not token:
            raise PermissionError("Missing Authorization header")
        return _PLATFORM_IDENTITY

    raise ValueError(f"Unsupported MCP_AUTH_TYPE: {auth_type}")


class GatewayAuthorizer:
    """Applies the tool policy pipeline at the gateway boundary.

    Layer stack: PluginAvailability → Profile → Visibility → Security.
    The Mode layer is agent-permission semantics and does not apply to
    the gateway path. ``REQUIRE_APPROVAL`` and ``EXECUTE_SANDBOX`` deny on
    this path — external MCP clients cannot satisfy either. Any pipeline
    exception fails closed (deny).
    """

    def __init__(self, pipeline: ToolPolicyPipeline | None = None) -> None:
        if pipeline is not None:
            self._pipeline = pipeline
        else:
            self._pipeline = ToolPolicyPipeline(
                layers=[
                    PluginAvailabilityLayer(),
                    ProfileLayer(),
                    VisibilityLayer(),
                    SecurityLayer(),
                ]
            )

    @staticmethod
    def _context(identity: CallerIdentity) -> PolicyContext:
        return PolicyContext(workspace_id=identity.workspace_id)

    def filter_catalog(
        self,
        tools: list[dict[str, Any]],
        identity: CallerIdentity,
    ) -> list[dict[str, Any]]:
        """Return the subset of ``tools`` visible to the caller (HIDE/DENY removed)."""
        try:
            return self._pipeline.evaluate_visibility(tools, self._context(identity))
        except Exception:  # noqa: BLE001 — visibility failure fails closed
            logger.exception("Gateway catalog filtering failed closed for workspace=%s", identity.workspace_id)
            return []

    def authorize(
        self,
        tool: dict[str, Any],
        identity: CallerIdentity,
    ) -> tuple[bool, str]:
        """Execution-time decision for a single tool call.

        Returns:
            ``(allowed, reason)``.
        """
        from hecate.tools.policy import ToolInfo

        info = ToolInfo(
            name=tool.get("name", ""),
            source=tool.get("source", "builtin"),
            risk_level=tool.get("risk_level", "low"),
            approval_required=bool(tool.get("approval_required", False)),
            sandbox_enabled=bool(tool.get("sandbox_enabled", False)),
            available_when=tool.get("available_when"),
            mcp_server=tool.get("mcp_server"),
            tool_def=tool,
        )
        try:
            decision, _ = self._pipeline.evaluate_execution(info, self._context(identity))
        except Exception:  # noqa: BLE001 — evaluation failure fails closed
            logger.exception("Gateway authorization failed closed for tool '%s'", tool.get("name"))
            return False, "policy evaluation failed"
        if decision in (PolicyDecision.ALLOW, PolicyDecision.PASSTHROUGH):
            return True, ""
        return False, f"denied by policy ({decision.value})"
