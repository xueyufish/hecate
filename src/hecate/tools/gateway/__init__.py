"""MCP Gateway — federated tool catalog on ``/mcp`` (feature 5.4a).

Modules:
- :mod:`targets` — target registration, egress baseline, credential redaction
- :mod:`converter` — OpenAPI → tool projection
- :mod:`executor` — spec-driven REST execution
- :mod:`federation` — federated catalog listing and call routing
- :mod:`authz` — caller identity resolution and boundary authorization
- :mod:`middleware` — FastMCP hooks (listing merge, call enforcement)
"""

from hecate.tools.gateway.authz import (
    CallerIdentity,
    GatewayAuthorizer,
    resolve_caller_identity,
)
from hecate.tools.gateway.errors import (
    DuplicateTargetError,
    EgressPolicyError,
    GatewayError,
    RestExecutionError,
    SpecConversionError,
    TargetNotFoundError,
)
from hecate.tools.gateway.executor import RestToolExecutor
from hecate.tools.gateway.federation import FederatedCatalog
from hecate.tools.gateway.targets import GatewayTargetService, redact_credentials

__all__ = [
    "CallerIdentity",
    "DuplicateTargetError",
    "EgressPolicyError",
    "FederatedCatalog",
    "GatewayAuthorizer",
    "GatewayError",
    "GatewayTargetService",
    "RestExecutionError",
    "RestToolExecutor",
    "SpecConversionError",
    "TargetNotFoundError",
    "redact_credentials",
    "resolve_caller_identity",
]
