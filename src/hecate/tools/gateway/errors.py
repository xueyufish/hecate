"""Gateway structured errors.

Shared exception hierarchy for the MCP Gateway: target registration,
conversion, execution, and boundary authorization all raise subclasses of
:class:`GatewayError` so callers can distinguish gateway failures from
upstream tool failures.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Base class for all MCP Gateway errors."""


class EgressPolicyError(GatewayError):
    """Target base URL violates the egress baseline (HTTPS / non-loopback)."""


class DuplicateTargetError(GatewayError):
    """A target with the same name already exists in the workspace."""


class TargetNotFoundError(GatewayError):
    """The referenced gateway target does not exist."""


class SpecConversionError(GatewayError):
    """The OpenAPI document could not be converted (malformed or empty)."""


class RestExecutionError(GatewayError):
    """REST tool execution failed (upstream error or connection failure).

    Attributes:
        target_name: Name of the gateway target that was called.
        operation: Operation/tool name that was executed.
        failure_class: ``upstream_error`` or ``connection_failure``.
        status_code: HTTP status code when available.
    """

    def __init__(
        self,
        target_name: str,
        operation: str,
        failure_class: str,
        status_code: int | None = None,
    ) -> None:
        self.target_name = target_name
        self.operation = operation
        self.failure_class = failure_class
        self.status_code = status_code
        super().__init__(
            f"REST execution failed for {target_name}__{operation} (class={failure_class}, status={status_code})"
        )
