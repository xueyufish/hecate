"""Permission checker for plugin permission declarations."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PermissionChecker:
    """Validates that plugin operations match declared permissions.

    Args:
        declared_permissions: Tuple of permission strings declared in the
            plugin manifest (e.g., ``("network:https", "filesystem:read")``).
    """

    def __init__(self, declared_permissions: tuple[str, ...]) -> None:
        self._declared = set(declared_permissions)

    def check(self, permission: str) -> bool:
        """Return ``True`` if *permission* is declared."""
        return permission in self._declared

    def check_or_warn(self, permission: str, plugin_name: str) -> bool:
        """Like :meth:`check` but logs a warning for undeclared permissions."""
        allowed = self.check(permission)
        if not allowed:
            logger.warning(
                "Plugin %s attempted undeclared permission: %s",
                plugin_name,
                permission,
            )
        return allowed
